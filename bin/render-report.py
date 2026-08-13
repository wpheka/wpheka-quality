#!/usr/bin/env python3
"""
WPHEKA Quality report renderer.

Reads results.tsv plus whatever structured tool output the run produced, and
writes: summary.md, full-review.md, results.json, findings.json, sarif.json,
environment.json, repository.json and a standalone report.html.

Everything written here must be safe to open offline and safe to hand to
someone else: no external network requests, no unescaped tool output.
"""

import argparse
import csv
import datetime as dt
import hashlib
import html
import json
import os
import pathlib
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

# A single oversized log (semgrep on a monorepo, a runaway test suite) must not
# produce a report.html too large to open.
MAX_EMBEDDED_LOG_BYTES = 256 * 1024
MAX_FINDINGS_IN_HTML = 500
MAX_FINDINGS_IN_MARKDOWN = 100

SEVERITY_ORDER = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"]
SEVERITY_RANK = {name: idx for idx, name in enumerate(SEVERITY_ORDER)}

FAILING_STATUSES = ("FAIL", "TIMEOUT", "ERROR")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def normalize_severity(raw, default="MEDIUM"):
    """Map every tool's vocabulary onto one shared scale."""
    if raw is None:
        return default
    value = str(raw).strip().upper()
    aliases = {
        "ERROR": "HIGH",
        "WARNING": "MEDIUM",
        "WARN": "MEDIUM",
        "NOTICE": "LOW",
        "INFORMATION": "INFO",
        "INFORMATIONAL": "INFO",
        "NONE": "INFO",
        "BLOCKER": "CRITICAL",
        "MAJOR": "HIGH",
        "MINOR": "LOW",
    }
    value = aliases.get(value, value)
    return value if value in SEVERITY_RANK else default


def relative_to(repo, path):
    if not path:
        return ""
    text = str(path)
    try:
        return str(pathlib.Path(text).resolve().relative_to(repo))
    except (ValueError, OSError):
        prefix = str(repo) + os.sep
        if text.startswith(prefix):
            return text[len(prefix):]
        return text.lstrip("./")


def fingerprint(finding):
    """
    Stable identity for baselining. Deliberately excludes the line number so a
    finding survives edits elsewhere in the file, and normalises digits in the
    message so counts and identifiers do not fork the fingerprint.
    """
    message = re.sub(r"\d+", "N", str(finding.get("message") or ""))
    parts = [
        str(finding.get("tool") or ""),
        str(finding.get("file") or ""),
        str(finding.get("source") or ""),
        message.strip().lower(),
    ]
    return hashlib.sha1("|".join(parts).encode("utf-8", "replace")).hexdigest()[:16]


def read_json(path):
    try:
        if path.exists() and path.stat().st_size > 0:
            return json.loads(path.read_text(errors="replace"))
    except (ValueError, OSError):
        pass
    return None


def json_for_html(payload):
    """
    Embed JSON inside a <script> block safely.

    json.dumps does not escape '</script>', so a tool log containing that string
    would close the block early and let the rest of the log execute as markup.
    """
    text = json.dumps(payload)
    return (text.replace("<", "\\u003c")
                .replace(">", "\\u003e")
                .replace("&", "\\u0026")
                .replace("\u2028", "\\u2028")
                .replace("\u2029", "\\u2029"))


def esc(value):
    return html.escape(str(value if value is not None else ""), quote=True)


# ---------------------------------------------------------------------------
# Tool output parsers. Each returns a list of normalised findings.
# ---------------------------------------------------------------------------

def parse_phpcs(raw_dir, repo):
    data = read_json(raw_dir / "phpcs.json")
    out = []
    if not isinstance(data, dict):
        return out
    for file_path, file_data in (data.get("files") or {}).items():
        for msg in (file_data or {}).get("messages", []) or []:
            out.append({
                "tool": "phpcs",
                "severity": normalize_severity(msg.get("type"), "MEDIUM"),
                "file": relative_to(repo, file_path),
                "line": msg.get("line"),
                "column": msg.get("column"),
                "message": msg.get("message"),
                "source": msg.get("source"),
                "fixable": bool(msg.get("fixable")),
            })
    return out


def parse_phpstan(raw_dir, repo):
    data = read_json(raw_dir / "phpstan.json")
    out = []
    if not isinstance(data, dict):
        return out
    for file_path, file_data in (data.get("files") or {}).items():
        for msg in (file_data or {}).get("messages", []) or []:
            out.append({
                "tool": "phpstan",
                "severity": "HIGH",
                "file": relative_to(repo, file_path),
                "line": msg.get("line"),
                "message": msg.get("message"),
                "source": msg.get("identifier") or "phpstan",
            })
    for msg in (data.get("errors") or []):
        out.append({
            "tool": "phpstan",
            "severity": "HIGH",
            "file": "",
            "line": None,
            "message": str(msg),
            "source": "phpstan.global",
        })
    return out


def parse_semgrep(raw_dir, repo):
    data = read_json(raw_dir / "semgrep.json")
    out = []
    if not isinstance(data, dict):
        return out
    for item in data.get("results") or []:
        extra = item.get("extra") or {}
        metadata = extra.get("metadata") or {}
        severity = metadata.get("impact") or extra.get("severity")
        out.append({
            "tool": "semgrep",
            "severity": normalize_severity(severity, "MEDIUM"),
            "file": relative_to(repo, item.get("path")),
            "line": (item.get("start") or {}).get("line"),
            "column": (item.get("start") or {}).get("col"),
            "message": (extra.get("message") or "").strip(),
            "source": item.get("check_id"),
            "cwe": metadata.get("cwe"),
        })
    return out


def parse_gitleaks(raw_dir, repo):
    data = read_json(raw_dir / "gitleaks.json")
    out = []
    if not isinstance(data, list):
        return out
    for item in data:
        out.append({
            "tool": "gitleaks",
            "severity": "CRITICAL",
            "file": relative_to(repo, item.get("File")),
            "line": item.get("StartLine"),
            # The Secret field is intentionally never copied into the report.
            "message": "Potential secret: %s" % (item.get("Description") or "unknown rule"),
            "source": item.get("RuleID"),
            "commit": item.get("Commit"),
        })
    return out


def parse_composer_audit(raw_dir, repo):
    data = read_json(raw_dir / "composer-audit.json")
    out = []
    if not isinstance(data, dict):
        return out
    for package, advisories in (data.get("advisories") or {}).items():
        if isinstance(advisories, dict):
            advisories = list(advisories.values())
        for advisory in advisories or []:
            if not isinstance(advisory, dict):
                continue
            out.append({
                "tool": "composer-audit",
                "severity": normalize_severity(advisory.get("severity"), "HIGH"),
                "file": "composer.lock",
                "line": None,
                "message": "%s: %s (affected: %s)" % (
                    package,
                    advisory.get("title") or "known vulnerability",
                    advisory.get("affectedVersions") or "unspecified",
                ),
                "source": advisory.get("cve") or advisory.get("advisoryId") or "composer-audit",
                "link": advisory.get("link"),
            })
    for package, issues in (data.get("abandoned") or {}).items():
        out.append({
            "tool": "composer-audit",
            "severity": "LOW",
            "file": "composer.json",
            "line": None,
            "message": "Package %s is abandoned (suggested replacement: %s)" % (package, issues or "none"),
            "source": "composer.abandoned",
        })
    return out


def parse_plugin_check(raw_dir, repo):
    """
    `wp plugin check --format=json` does not emit one JSON document. It writes a
    `FILE: <path>` header followed by a JSON array, once per file, and wp-cli
    interleaves PHP notices on stdout. Parse the blocks rather than the whole
    file, and fall back to a plain array if a future version emits one.
    """
    path = raw_dir / "plugin-check.json"
    out = []
    if not path.exists() or path.stat().st_size == 0:
        return out

    def add(entries, current_file):
        for item in entries or []:
            if not isinstance(item, dict):
                continue
            out.append({
                "tool": "plugin-check",
                "severity": normalize_severity(item.get("type"), "MEDIUM"),
                "file": relative_to(repo, item.get("file") or current_file),
                "line": item.get("line") or None,
                "column": item.get("column") or None,
                "message": item.get("message"),
                "source": item.get("code"),
            })

    whole = read_json(path)
    if isinstance(whole, list):
        add(whole, "")
        return out

    current = ""
    for line in path.read_text(errors="replace").splitlines():
        stripped = line.strip()
        if stripped.startswith("FILE:"):
            current = stripped[5:].strip()
            continue
        if not stripped.startswith("["):
            continue  # wp-cli notices and blank lines
        try:
            add(json.loads(stripped), current)
        except ValueError:
            continue
    return out


def parse_phpunit(raw_dir, repo):
    """Turn JUnit failures into findings so test breakage ranks with the rest."""
    path = raw_dir / "phpunit-junit.xml"
    out = []
    if not path.exists() or path.stat().st_size == 0:
        return out
    try:
        tree = ET.parse(str(path))
    except ET.ParseError:
        return out
    for case in tree.iter("testcase"):
        for kind in ("failure", "error"):
            node = case.find(kind)
            if node is None:
                continue
            out.append({
                "tool": "phpunit",
                "severity": "HIGH",
                "file": relative_to(repo, case.get("file") or ""),
                "line": case.get("line"),
                "message": "%s::%s %s" % (
                    case.get("class") or "",
                    case.get("name") or "",
                    (node.get("message") or kind).strip().splitlines()[0] if (node.get("message") or "").strip() else kind,
                ),
                "source": "phpunit.%s" % kind,
            })
    return out


PARSERS = (
    parse_phpcs,
    parse_phpstan,
    parse_semgrep,
    parse_gitleaks,
    parse_composer_audit,
    parse_plugin_check,
    parse_phpunit,
)


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------

def load_results(run_dir):
    """
    Read results.tsv. The current format is 5 columns; 4-column rows from an
    older run are still accepted so historical report directories re-render.
    """
    rows = []
    path = run_dir / "results.tsv"
    if not path.exists():
        return rows
    with path.open(newline="") as fh:
        for row in csv.reader(fh, delimiter="\t"):
            if len(row) == 5:
                name, status, log, code, detail = row
            elif len(row) == 4:
                name, status, log, code = row
                detail = ""
            else:
                continue
            rows.append({
                "check": name,
                "status": status,
                "log": log,
                "exit_code": code,
                "detail": detail,
            })
    return rows


def collect_findings(raw_dir, repo):
    findings = []
    for parser in PARSERS:
        try:
            findings.extend(parser(raw_dir, repo))
        except Exception as exc:  # a malformed report must not lose the others
            findings.append({
                "tool": "renderer",
                "severity": "LOW",
                "file": "",
                "line": None,
                "message": "Could not parse output for %s: %s" % (parser.__name__, exc),
                "source": "renderer.parse-error",
            })
    return findings


def dedupe(findings):
    """
    Collapse findings that different tools reported at the same place. The
    surviving record keeps the highest severity and lists every reporting tool,
    which is the correlation the review report asks for.
    """
    merged = {}
    order = []
    for finding in findings:
        key = (
            finding.get("file") or "",
            finding.get("line"),
            re.sub(r"\s+", " ", str(finding.get("message") or "")).strip().lower(),
        )
        if key in merged:
            existing = merged[key]
            tools = set(existing.get("tools") or [existing.get("tool")])
            tools.add(finding.get("tool"))
            existing["tools"] = sorted(t for t in tools if t)
            if SEVERITY_RANK.get(finding.get("severity"), 99) < SEVERITY_RANK.get(existing.get("severity"), 99):
                existing["severity"] = finding["severity"]
            existing["corroborated"] = len(existing["tools"]) > 1
        else:
            record = dict(finding)
            record["tools"] = [finding.get("tool")] if finding.get("tool") else []
            record["corroborated"] = False
            merged[key] = record
            order.append(key)
    result = [merged[k] for k in order]
    for item in result:
        item["fingerprint"] = fingerprint(item)
    result.sort(key=lambda f: (
        SEVERITY_RANK.get(f.get("severity"), 99),
        f.get("file") or "",
        f.get("line") or 0,
    ))
    return result


def apply_baseline(findings, baseline_path):
    """Mark findings already present in the baseline so only new ones surface."""
    known = set()
    data = read_json(pathlib.Path(baseline_path))
    if isinstance(data, dict):
        known = set(data.get("fingerprints") or [])
    elif isinstance(data, list):
        known = set(data)
    new = []
    for finding in findings:
        if finding["fingerprint"] in known:
            finding["baselined"] = True
        else:
            finding["baselined"] = False
            new.append(finding)
    return new


def severity_counts(findings):
    counts = dict((name, 0) for name in SEVERITY_ORDER)
    for finding in findings:
        counts[finding.get("severity", "MEDIUM")] = counts.get(finding.get("severity", "MEDIUM"), 0) + 1
    return counts


def read_log(path_text):
    path = pathlib.Path(path_text)
    if not path.exists() or not path.is_file():
        return None
    try:
        size = path.stat().st_size
        with path.open(errors="replace") as fh:
            if size > MAX_EMBEDDED_LOG_BYTES:
                head = fh.read(MAX_EMBEDDED_LOG_BYTES)
                return "%s\n\n[... truncated: %d of %d bytes shown. Full log: %s ...]" % (
                    head, MAX_EMBEDDED_LOG_BYTES, size, path,
                )
            return fh.read()
    except OSError:
        return None


def to_sarif(findings, repo, version):
    """Minimal SARIF 2.1.0 so CI can ingest findings as code scanning alerts."""
    level_map = {"CRITICAL": "error", "HIGH": "error", "MEDIUM": "warning",
                 "LOW": "note", "INFO": "note"}
    rules = {}
    results = []
    for finding in findings:
        rule_id = str(finding.get("source") or finding.get("tool") or "wpheka-quality")
        if rule_id not in rules:
            rules[rule_id] = {
                "id": rule_id,
                "shortDescription": {"text": rule_id},
                "properties": {"tags": [str(finding.get("tool") or "wpheka-quality")]},
            }
        location = {
            "physicalLocation": {
                "artifactLocation": {"uri": finding.get("file") or "unknown"},
            }
        }
        try:
            line = int(finding.get("line"))
            if line > 0:
                location["physicalLocation"]["region"] = {"startLine": line}
        except (TypeError, ValueError):
            pass
        results.append({
            "ruleId": rule_id,
            "level": level_map.get(finding.get("severity"), "warning"),
            "message": {"text": str(finding.get("message") or "")},
            "locations": [location],
            "partialFingerprints": {"wphekaQualityFingerprint": finding.get("fingerprint", "")},
        })
    return {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [{
            "tool": {"driver": {
                "name": "WPHEKA Quality",
                "version": version,
                "informationUri": "https://github.com/wpheka/wpheka-quality",
                "rules": list(rules.values()),
            }},
            "results": results,
        }],
    }


# ---------------------------------------------------------------------------
# HTML report
# ---------------------------------------------------------------------------

HTML_STYLE = """
:root {
  --bg:#0f172a; --card:#1e293b; --border:#334155; --text:#f8fafc;
  --muted:#94a3b8; --accent:#38bdf8; --pass:#22c55e; --fail:#ef4444;
  --skipped:#eab308; --warning:#a855f7; --info:#06b6d4; --critical:#f43f5e;
}
* { box-sizing:border-box; margin:0; padding:0; }
body {
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  background:var(--bg); color:var(--text); line-height:1.5; padding:2rem 1rem;
}
code, pre { font-family:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,monospace; }
.container { max-width:1280px; margin:0 auto; }
header { padding-bottom:1.5rem; border-bottom:1px solid var(--border); margin-bottom:2rem; }
h1 { font-size:1.6rem; font-weight:700; }
.subtitle { color:var(--muted); font-size:.9rem; margin-top:.4rem; word-break:break-word; }
.badge {
  display:inline-block; padding:.2rem .55rem; border-radius:9999px;
  font-size:.72rem; font-weight:600; text-transform:uppercase; letter-spacing:.04em;
}
.badge-pass{background:rgba(34,197,94,.15);color:var(--pass);border:1px solid rgba(34,197,94,.3);}
.badge-fail,.badge-timeout,.badge-error{background:rgba(239,68,68,.15);color:var(--fail);border:1px solid rgba(239,68,68,.3);}
.badge-skipped{background:rgba(234,179,8,.15);color:var(--skipped);border:1px solid rgba(234,179,8,.3);}
.badge-warning{background:rgba(168,85,247,.15);color:var(--warning);border:1px solid rgba(168,85,247,.3);}
.badge-info{background:rgba(6,182,212,.15);color:var(--info);border:1px solid rgba(6,182,212,.3);}
.badge-critical{background:rgba(244,63,94,.18);color:var(--critical);border:1px solid rgba(244,63,94,.35);}
.badge-high{background:rgba(239,68,68,.15);color:var(--fail);border:1px solid rgba(239,68,68,.3);}
.badge-medium{background:rgba(234,179,8,.15);color:var(--skipped);border:1px solid rgba(234,179,8,.3);}
.badge-low{background:rgba(148,163,184,.15);color:var(--muted);border:1px solid rgba(148,163,184,.3);}
.stats { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:1rem; margin-bottom:2rem; }
.stat { background:var(--card); border:1px solid var(--border); border-radius:.75rem; padding:1.1rem; text-align:center; }
.stat-num { font-size:1.9rem; font-weight:700; }
.stat-label { color:var(--muted); font-size:.78rem; text-transform:uppercase; font-weight:600; letter-spacing:.04em; }
.section { background:var(--card); border:1px solid var(--border); border-radius:.75rem; padding:1.25rem; margin-bottom:2rem; }
.section-header { display:flex; justify-content:space-between; align-items:center; gap:1rem; flex-wrap:wrap; margin-bottom:1rem; }
.section-title { font-size:1.1rem; font-weight:600; }
.controls { display:flex; gap:.5rem; align-items:center; flex-wrap:wrap; }
button.filter-btn, .search-input {
  background:var(--bg); border:1px solid var(--border); color:var(--muted);
  padding:.35rem .7rem; border-radius:.375rem; font-size:.82rem; font-weight:500;
}
button.filter-btn { cursor:pointer; }
button.filter-btn[aria-pressed="true"], button.filter-btn:hover { color:var(--text); border-color:var(--accent); background:rgba(56,189,248,.12); }
.search-input { color:var(--text); min-width:180px; }
.table-wrap { overflow-x:auto; }
table { width:100%; border-collapse:collapse; text-align:left; font-size:.88rem; }
th { background:var(--bg); color:var(--muted); padding:.65rem .8rem; font-weight:600; font-size:.75rem; text-transform:uppercase; border-bottom:1px solid var(--border); white-space:nowrap; }
td { padding:.7rem .8rem; border-bottom:1px solid var(--border); vertical-align:top; }
tr:last-child td { border-bottom:none; }
td .msg { max-width:60ch; }
.btn-view { background:var(--accent); color:#0f172a; border:none; padding:.28rem .55rem; border-radius:.25rem; font-weight:600; font-size:.74rem; cursor:pointer; }
.empty { color:var(--muted); font-size:.9rem; }
dialog { background:var(--card); color:var(--text); border:1px solid var(--border); border-radius:.75rem; width:min(92vw,900px); max-height:85vh; padding:0; }
dialog::backdrop { background:rgba(0,0,0,.7); }
.modal-header { padding:.9rem 1.2rem; border-bottom:1px solid var(--border); display:flex; justify-content:space-between; align-items:center; gap:1rem; }
.modal-body { padding:1.2rem; overflow:auto; max-height:70vh; }
.modal-close { background:none; border:none; color:var(--muted); font-size:1.4rem; cursor:pointer; line-height:1; }
pre.log { font-size:.8rem; background:var(--bg); padding:1rem; border-radius:.5rem; color:#e2e8f0; white-space:pre-wrap; overflow-wrap:anywhere; }
footer { color:var(--muted); font-size:.8rem; text-align:center; padding-top:1rem; }
"""

HTML_SCRIPT = """
(function () {
  var logs = window.__WPHEKA_LOGS__ || {};
  var dialog = document.getElementById('logModal');
  var state = { status: 'ALL', query: '' };

  document.addEventListener('click', function (event) {
    var viewBtn = event.target.closest('[data-log]');
    if (viewBtn) {
      var name = viewBtn.getAttribute('data-log');
      document.getElementById('modalTitle').textContent = name;
      document.getElementById('modalContent').textContent =
        logs[name] || 'No output was recorded for this check.';
      if (typeof dialog.showModal === 'function') { dialog.showModal(); }
      else { dialog.setAttribute('open', ''); }
      return;
    }
    if (event.target.closest('[data-close]')) {
      if (typeof dialog.close === 'function') { dialog.close(); }
      else { dialog.removeAttribute('open'); }
    }
  });

  function applyFilters() {
    var rows = document.querySelectorAll('#checksTable tbody tr');
    Array.prototype.forEach.call(rows, function (row) {
      var statusOk = state.status === 'ALL' || row.getAttribute('data-status') === state.status;
      var queryOk = !state.query || row.getAttribute('data-name').indexOf(state.query) !== -1;
      row.hidden = !(statusOk && queryOk);
    });
  }

  Array.prototype.forEach.call(document.querySelectorAll('[data-filter]'), function (btn) {
    btn.addEventListener('click', function () {
      state.status = btn.getAttribute('data-filter');
      Array.prototype.forEach.call(document.querySelectorAll('[data-filter]'), function (other) {
        other.setAttribute('aria-pressed', String(other === btn));
      });
      applyFilters();
    });
  });

  var search = document.getElementById('checkSearch');
  if (search) {
    search.addEventListener('input', function () {
      state.query = search.value.toLowerCase();
      applyFilters();
    });
  }

  var findingSearch = document.getElementById('findingSearch');
  if (findingSearch) {
    findingSearch.addEventListener('input', function () {
      var q = findingSearch.value.toLowerCase();
      Array.prototype.forEach.call(
        document.querySelectorAll('#findingsTable tbody tr'),
        function (row) { row.hidden = q && row.getAttribute('data-search').indexOf(q) === -1; }
      );
    });
  }
})();
"""


def render_html(run_dir, repo, rows, findings, counts, sev_counts, repo_info,
                tool_logs, versions, version, baselined_count):
    status_counts = {}
    for row in rows:
        status_counts[row["status"]] = status_counts.get(row["status"], 0) + 1

    parts = []
    parts.append("<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n")
    parts.append('<meta name="viewport" content="width=device-width, initial-scale=1">\n')
    parts.append("<title>WPHEKA Quality — %s</title>\n" % esc(repo.name))
    parts.append("<style>%s</style>\n</head>\n<body>\n<div class=\"container\">\n" % HTML_STYLE)

    parts.append("<header><h1>WPHEKA Quality Report</h1><div class=\"subtitle\">")
    parts.append("Repository <strong>%s</strong>" % esc(repo))
    if repo_info.get("branch"):
        parts.append(" &middot; branch <code>%s</code>" % esc(repo_info["branch"]))
    if repo_info.get("head"):
        parts.append(" &middot; commit <code>%s</code>" % esc(repo_info["head"][:8]))
    parts.append(" &middot; generated %s &middot; engine v%s" % (esc(repo_info.get("generated_at", "")), esc(version)))
    parts.append("</div></header>\n")

    failing = sum(status_counts.get(s, 0) for s in FAILING_STATUSES)
    stats = [
        ("Checks run", len(rows), "var(--accent)"),
        ("Passed", status_counts.get("PASS", 0), "var(--pass)"),
        ("Failed", failing, "var(--fail)"),
        ("Skipped", status_counts.get("SKIPPED", 0), "var(--skipped)"),
        ("Findings", len(findings), "var(--warning)"),
    ]
    parts.append('<div class="stats">')
    for label, value, color in stats:
        parts.append('<div class="stat"><div class="stat-num" style="color:%s">%s</div>'
                     '<div class="stat-label">%s</div></div>' % (color, value, esc(label)))
    parts.append("</div>\n")

    if any(sev_counts.values()):
        parts.append('<div class="stats">')
        for name in SEVERITY_ORDER:
            parts.append('<div class="stat"><div class="stat-num">%d</div>'
                         '<div class="stat-label">%s</div></div>' % (sev_counts.get(name, 0), esc(name.title())))
        parts.append("</div>\n")

    if baselined_count:
        parts.append('<div class="section"><span class="badge badge-info">baseline</span> '
                     '<span class="empty">%d finding(s) suppressed by the baseline file.</span></div>\n'
                     % baselined_count)

    # Checks
    parts.append('<div class="section"><div class="section-header">'
                 '<div class="section-title">Deterministic checks</div><div class="controls">')
    for label in ("ALL", "PASS", "FAIL", "SKIPPED", "WARNING"):
        count = len(rows) if label == "ALL" else status_counts.get(label, 0)
        pressed = "true" if label == "ALL" else "false"
        parts.append('<button class="filter-btn" data-filter="%s" aria-pressed="%s">%s (%d)</button>'
                     % (label, pressed, esc(label.title()), count))
    parts.append('<input type="search" class="search-input" id="checkSearch" placeholder="Filter checks">')
    parts.append('</div></div><div class="table-wrap"><table id="checksTable"><thead><tr>'
                 '<th>Check</th><th>Status</th><th>Exit</th><th>Detail</th><th>Log</th>'
                 '</tr></thead><tbody>')
    for row in rows:
        has_log = row["check"] in tool_logs
        action = ('<button class="btn-view" data-log="%s">View</button>' % esc(row["check"])) if has_log \
            else '<span class="empty">&mdash;</span>'
        parts.append(
            '<tr data-status="%s" data-name="%s">'
            '<td><code>%s</code></td>'
            '<td><span class="badge badge-%s">%s</span></td>'
            '<td><code>%s</code></td>'
            '<td class="msg">%s</td>'
            '<td>%s</td></tr>'
            % (esc(row["status"]), esc(row["check"].lower()), esc(row["check"]),
               esc(row["status"].lower()), esc(row["status"]), esc(row["exit_code"]),
               esc(row["detail"]), action)
        )
    parts.append("</tbody></table></div></div>\n")

    # Findings
    parts.append('<div class="section"><div class="section-header">'
                 '<div class="section-title">Structured findings (%d)</div>'
                 '<div class="controls">'
                 '<input type="search" class="search-input" id="findingSearch" placeholder="Filter findings">'
                 '</div></div>' % len(findings))
    if findings:
        parts.append('<div class="table-wrap"><table id="findingsTable"><thead><tr>'
                     '<th>Severity</th><th>Tool</th><th>Location</th><th>Rule</th><th>Message</th>'
                     '</tr></thead><tbody>')
        for finding in findings[:MAX_FINDINGS_IN_HTML]:
            location = finding.get("file") or "(project)"
            if finding.get("line"):
                location = "%s:%s" % (location, finding["line"])
            tools = ", ".join(finding.get("tools") or [finding.get("tool") or ""])
            searchable = " ".join(str(x).lower() for x in (
                finding.get("severity"), tools, location, finding.get("source"), finding.get("message")
            ) if x)
            parts.append(
                '<tr data-search="%s">'
                '<td><span class="badge badge-%s">%s</span></td>'
                '<td><code>%s</code>%s</td>'
                '<td><code>%s</code></td>'
                '<td><code>%s</code></td>'
                '<td class="msg">%s</td></tr>'
                % (esc(searchable), esc(str(finding.get("severity", "")).lower()),
                   esc(finding.get("severity")), esc(tools),
                   ' <span class="badge badge-info">x%d</span>' % len(finding.get("tools") or [])
                   if finding.get("corroborated") else "",
                   esc(location), esc(finding.get("source") or ""), esc(finding.get("message") or ""))
            )
        parts.append("</tbody></table></div>")
        if len(findings) > MAX_FINDINGS_IN_HTML:
            parts.append('<p class="empty">Showing the %d highest-severity findings of %d. '
                         'The complete set is in findings.json.</p>'
                         % (MAX_FINDINGS_IN_HTML, len(findings)))
    else:
        parts.append('<p class="empty">No structured findings were produced. '
                     'Check the skipped checks above before reading this as a clean result.</p>')
    parts.append("</div>\n")

    # Tool versions
    if versions:
        parts.append('<div class="section"><div class="section-title">Tool versions</div>'
                     '<div class="table-wrap"><table><thead><tr><th>Tool</th><th>Version</th></tr></thead><tbody>')
        for tool, ver in versions:
            parts.append("<tr><td><code>%s</code></td><td>%s</td></tr>" % (esc(tool), esc(ver)))
        parts.append("</tbody></table></div></div>\n")

    parts.append('<dialog id="logModal"><div class="modal-header"><h2 id="modalTitle">Log</h2>'
                 '<button class="modal-close" data-close aria-label="Close">&times;</button></div>'
                 '<div class="modal-body"><pre class="log" id="modalContent"></pre></div></dialog>\n')
    parts.append('<footer>Generated by WPHEKA Quality v%s. This report is evidence, not a verdict: '
                 'skipped checks and unverified findings still need review.</footer>\n' % esc(version))
    parts.append("</div>\n")
    parts.append("<script>window.__WPHEKA_LOGS__ = %s;</script>\n" % json_for_html(tool_logs))
    parts.append("<script>%s</script>\n</body>\n</html>\n" % HTML_SCRIPT)
    return "".join(parts)


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------

def render_summary_md(repo, repo_info, rows, findings, sev_counts, version, baselined_count):
    status_counts = {}
    for row in rows:
        status_counts[row["status"]] = status_counts.get(row["status"], 0) + 1
    failing = sum(status_counts.get(s, 0) for s in FAILING_STATUSES)

    lines = [
        "# WPHEKA Quality Review",
        "",
        "**Repository:** `%s`  " % repo,
        "**Branch:** `%s` | **HEAD:** `%s`  " % (repo_info.get("branch") or "n/a",
                                                 (repo_info.get("head") or "n/a")[:8]),
        "**Run:** `%s` | **Engine:** `v%s`" % (repo_info.get("generated_at", ""), version),
        "",
        "## Check status",
        "",
        "| Status | Count |",
        "|---|---:|",
    ]
    for status in ("PASS", "FAIL", "TIMEOUT", "ERROR", "WARNING", "SKIPPED"):
        lines.append("| %s | %d |" % (status, status_counts.get(status, 0)))
    lines += ["", "## Finding severity", "", "| Severity | Count |", "|---|---:|"]
    for name in SEVERITY_ORDER:
        lines.append("| %s | %d |" % (name, sev_counts.get(name, 0)))
    if baselined_count:
        lines += ["", "_%d finding(s) suppressed by the baseline._" % baselined_count]

    lines += ["", "## Checks", "", "| Check | Status | Exit | Detail |", "|---|---|---|---|"]
    for row in rows:
        lines.append("| `%s` | **%s** | `%s` | %s |" % (
            row["check"], row["status"], row["exit_code"],
            row["detail"].replace("|", "\\|") or "",
        ))

    skipped = [r for r in rows if r["status"] == "SKIPPED"]
    if skipped:
        lines += ["", "## Skipped checks", "",
                  "These produced **no evidence**. Treat the areas they cover as unreviewed.", ""]
        for row in skipped:
            lines.append("- `%s` — %s" % (row["check"], row["detail"] or "no reason recorded"))

    failed = [r for r in rows if r["status"] in FAILING_STATUSES]
    if failed:
        lines += ["", "## Failing checks", ""]
        for row in failed:
            lines.append("- `%s` — %s (exit `%s`) — see `tool-results/%s`" % (
                row["check"], row["status"], row["exit_code"], pathlib.Path(row["log"]).name,
            ))

    lines += [
        "", "## Next step: AI investigation", "",
        "This file is the deterministic baseline only. Nothing here is a confirmed bug.",
        "The Agent Skill must now:", "",
        "1. Inspect every failing and skipped check and state its impact.",
        "2. Verify each CRITICAL/HIGH finding against the actual source.",
        "3. Separate real defects from tool false positives.",
        "4. Correlate findings that describe one underlying defect.",
        "5. Produce the prioritised release-risk assessment in `full-review.md`.",
        "",
        "Findings extracted: **%d**." % len(findings),
        "",
    ]
    return "\n".join(lines)


def render_full_md(repo, repo_info, rows, findings, sev_counts, version, versions):
    lines = [
        "# WPHEKA Full Review",
        "",
        "**Repository:** `%s`  " % repo,
        "**Run:** `%s` | **Engine:** `v%s`" % (repo_info.get("generated_at", ""), version),
        "",
        "> Sections 5 onward are for the reviewing agent. Leave a section empty",
        "> rather than filling it with unverified tool output.",
        "",
        "## 1. Executive summary",
        "",
        "_Populate after source-level investigation._",
        "",
        "## 2. Repository context",
        "",
        "- Branch: `%s`" % (repo_info.get("branch") or "n/a"),
        "- HEAD: `%s`" % (repo_info.get("head") or "n/a"),
        "",
        "## 3. Tool matrix",
        "",
        "| Tool | Version |",
        "|---|---|",
    ]
    for tool, ver in versions:
        lines.append("| `%s` | %s |" % (tool, ver))

    lines += ["", "## 4. Deterministic checks", "",
              "| Check | Status | Exit | Detail |", "|---|---|---|---|"]
    for row in rows:
        lines.append("| `%s` | **%s** | `%s` | %s |" % (
            row["check"], row["status"], row["exit_code"], row["detail"].replace("|", "\\|")))

    lines += ["", "## 5. Extracted findings (%d)" % len(findings), ""]
    if findings:
        lines += ["| Severity | Tool(s) | Location | Rule | Message |", "|---|---|---|---|---|"]
        for finding in findings[:MAX_FINDINGS_IN_MARKDOWN]:
            location = finding.get("file") or "(project)"
            if finding.get("line"):
                location = "%s:%s" % (location, finding["line"])
            lines.append("| **%s** | `%s` | `%s` | `%s` | %s |" % (
                finding.get("severity", ""),
                ", ".join(finding.get("tools") or []),
                location,
                finding.get("source") or "",
                str(finding.get("message") or "").replace("|", "\\|").replace("\n", " "),
            ))
        if len(findings) > MAX_FINDINGS_IN_MARKDOWN:
            lines.append("")
            lines.append("_Showing %d of %d. Full set in `findings.json`._"
                         % (MAX_FINDINGS_IN_MARKDOWN, len(findings)))
    else:
        lines.append("_No structured findings were parsed._")

    for heading in ("6. Critical findings", "7. High findings", "8. Medium findings",
                    "9. Low findings", "10. False positives", "11. Correlated findings",
                    "12. Security review", "13. WordPress review", "14. WooCommerce review",
                    "15. Theme review", "16. Performance review", "17. Test gaps",
                    "18. Recommended remediation order", "19. Release readiness"):
        lines += ["", "## %s" % heading, ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="WPHEKA Quality report renderer")
    parser.add_argument("run_dir")
    parser.add_argument("repo")
    parser.add_argument("format", nargs="?", default="text", choices=["text", "json", "html"])
    parser.add_argument("--version", default="unknown")
    parser.add_argument("--baseline", default=None)
    parser.add_argument("--write-baseline", action="store_true")
    args = parser.parse_args()

    # The shell wrapper checks this too, but the renderer is documented as
    # runnable on its own against an existing report directory, and without the
    # check it dies on pathlib.Path(None) with a bare TypeError.
    if args.write_baseline and not args.baseline:
        sys.stderr.write("render-report: --write-baseline requires --baseline FILE\n")
        return 2

    run_dir = pathlib.Path(args.run_dir).resolve()
    repo = pathlib.Path(args.repo).resolve()
    raw_dir = run_dir / "tool-results"

    rows = load_results(run_dir)
    generated_at = dt.datetime.now().astimezone().isoformat(timespec="seconds")

    repo_info = {"generated_at": generated_at}
    for key, cmd in (
        ("root", ["git", "-C", str(repo), "rev-parse", "--show-toplevel"]),
        ("branch", ["git", "-C", str(repo), "branch", "--show-current"]),
        ("head", ["git", "-C", str(repo), "rev-parse", "HEAD"]),
    ):
        try:
            repo_info[key] = subprocess.check_output(
                cmd, text=True, stderr=subprocess.DEVNULL, timeout=30).strip()
        except (subprocess.SubprocessError, OSError):
            repo_info[key] = ""
    (run_dir / "repository.json").write_text(json.dumps(repo_info, indent=2))

    versions = []
    versions_path = raw_dir / "tool-versions.tsv"
    if versions_path.exists():
        for line in versions_path.read_text(errors="replace").splitlines():
            if "\t" in line:
                tool, ver = line.split("\t", 1)
                versions.append((tool, ver))

    environment = {
        "generated_at": generated_at,
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "repository": str(repo),
        "runner_version": args.version,
        "tools": dict(versions),
    }
    (run_dir / "environment.json").write_text(json.dumps(environment, indent=2))

    findings = dedupe(collect_findings(raw_dir, repo))

    if args.write_baseline:
        baseline = {
            "generated_at": generated_at,
            "repository": str(repo),
            "runner_version": args.version,
            "fingerprints": sorted(f["fingerprint"] for f in findings),
        }
        pathlib.Path(args.baseline).write_text(json.dumps(baseline, indent=2))

    baselined_count = 0
    if args.baseline and not args.write_baseline and pathlib.Path(args.baseline).exists():
        before = len(findings)
        findings = apply_baseline(findings, args.baseline)
        baselined_count = before - len(findings)

    sev_counts = severity_counts(findings)
    counts = {}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1

    (run_dir / "results.json").write_text(json.dumps(rows, indent=2))
    (run_dir / "findings.json").write_text(json.dumps(findings, indent=2))
    (run_dir / "sarif.json").write_text(json.dumps(to_sarif(findings, repo, args.version), indent=2))

    tool_logs = {}
    for row in rows:
        if row["log"] and row["log"] != "-":
            content = read_log(row["log"])
            if content is not None:
                tool_logs[row["check"]] = content

    summary_md = render_summary_md(repo, repo_info, rows, findings, sev_counts,
                                   args.version, baselined_count)
    (run_dir / "summary.md").write_text(summary_md)
    (run_dir / "full-review.md").write_text(
        render_full_md(repo, repo_info, rows, findings, sev_counts, args.version, versions))
    (run_dir / "report.html").write_text(
        render_html(run_dir, repo, rows, findings, counts, sev_counts, repo_info,
                    tool_logs, versions, args.version, baselined_count))

    if args.format == "json":
        print(json.dumps({
            "repository": str(repo),
            "run_dir": str(run_dir),
            "status_counts": counts,
            "severity_counts": sev_counts,
            "baselined": baselined_count,
            "results": rows,
            "findings": findings,
        }, indent=2))
    elif args.format == "html":
        print(str(run_dir / "report.html"))
    else:
        print(summary_md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
