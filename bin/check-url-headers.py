#!/usr/bin/env python3
"""Check that the URLs a plugin advertises actually resolve.

Plugin Check validates the donate link's *syntax* and that Domain Path names a
real folder, but nothing checks that Plugin URI leads anywhere. A wpheka plugin
shipped a Plugin URI returning 404 and needed a same-day patch release to correct
it, because wordpress.org reads plugin headers from the stable tag rather than
from trunk.

Offline is not a failure. If every URL fails to connect the check reports that it
could not tell (exit 2) rather than failing a build for having no network; a
gate that cannot run on a train teaches people to skip it.

Exit 0 when every URL resolves, 1 when one does not, 2 when it could not tell.
"""
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

TIMEOUT = 15
AGENT = "wpheka-quality URL header check"

HEADERS = (
    ("Plugin URI", "plugin"),
    ("Author URI", "plugin"),
    ("Donate link", "readme"),
)


def main_plugin_file(repo):
    for path in sorted(Path(repo).glob("*.php")):
        head = path.read_text(encoding="utf-8", errors="replace")[:8192]
        if re.search(r"^\s*\*?\s*Plugin Name\s*:", head, re.M | re.I):
            return path
    return None


def declared_urls(repo):
    """Every URL the plugin advertises, with where it was declared."""
    found = []
    plugin = main_plugin_file(repo)
    readme = Path(repo) / "readme.txt"

    sources = {}
    if plugin is not None:
        sources["plugin"] = (plugin, plugin.read_text(encoding="utf-8", errors="replace")[:8192])
    if readme.exists():
        sources["readme"] = (readme, readme.read_text(encoding="utf-8", errors="replace")[:8192])

    for label, where in HEADERS:
        if where not in sources:
            continue
        path, text = sources[where]
        m = re.search(r"^\s*\*?\s*%s\s*:\s*(\S+)\s*$" % re.escape(label), text, re.M | re.I)
        if not m:
            continue
        url = m.group(1).strip()
        if url.lower().startswith(("http://", "https://")):
            found.append((label, url, path.name, text[:m.start()].count("\n") + 1))
    return found


def probe(url):
    """(status, error). status None means the connection itself failed."""
    for method in ("HEAD", "GET"):
        req = urllib.request.Request(url, method=method, headers={"User-Agent": AGENT})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return resp.status, None
        except urllib.error.HTTPError as exc:
            if method == "HEAD" and exc.code in (403, 405, 501):
                continue          # some servers refuse HEAD; a GET is fair
            return exc.code, None
        except Exception as exc:  # DNS, TLS, refused, timeout
            if method == "GET":
                return None, str(exc)[:120]
    return None, "unreachable"


def main():
    if len(sys.argv) != 3:
        print("usage: check-url-headers.py <repo> <out.json>", file=sys.stderr)
        return 2

    repo, out_path = sys.argv[1], sys.argv[2]
    urls = declared_urls(repo)
    if not urls:
        print("no URL headers declared", file=sys.stderr)
        return 2

    findings, unreachable, ok = [], 0, 0
    for label, url, filename, line in urls:
        status, err = probe(url)
        if status is None:
            unreachable += 1
            print("  %-12s %s -> could not connect (%s)" % (label, url, err), file=sys.stderr)
            continue
        if status >= 400:
            findings.append({
                "severity": "MEDIUM",
                "file": filename,
                "line": line,
                "source": "url_headers.unresolvable",
                "message": '%s points at %s, which returns HTTP %d. '
                           'wordpress.org shows this to users, and reads it from the stable tag.'
                           % (label, url, status),
            })
            print("  %-12s %s -> HTTP %d" % (label, url, status), file=sys.stderr)
        else:
            ok += 1
            print("  %-12s %s -> HTTP %d" % (label, url, status))

    Path(out_path).write_text(json.dumps(findings, indent=1), encoding="utf-8")

    # Every URL failed to connect and none answered: assume the network, not the URLs.
    if unreachable and ok == 0 and not findings:
        print("no URL could be reached; treating as offline rather than broken", file=sys.stderr)
        return 2

    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
