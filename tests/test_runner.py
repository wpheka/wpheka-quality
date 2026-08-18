#!/usr/bin/env python3
"""
Test suite for the WPHEKA Quality engine.

Every test named test_regression_* pins a defect found in the pre-1.0 engine.
They exist so the same failure cannot return silently.

Run:  python3 tests/test_runner.py            (all)
      python3 tests/test_runner.py -k syntax  (subset)
"""

import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

TEST_DIR = pathlib.Path(__file__).resolve().parent
ROOT_DIR = TEST_DIR.parent
RUNNER = ROOT_DIR / "bin" / "wpheka-quality"
LOADER = ROOT_DIR / "bin" / "config-loader.py"
RENDERER = ROOT_DIR / "bin" / "render-report.py"
FIXTURE_DIR = TEST_DIR / "fixtures" / "sample-plugin"

HAVE_PHP = shutil.which("php") is not None
HAVE_GIT = shutil.which("git") is not None


def find_phpcs():
    """phpcs may be global or Composer-local. The engine checks both, so the
    tests must too, or they skip silently on a repo that keeps it in
    vendor/bin -- and a silent skip is what this project refuses elsewhere."""
    found = shutil.which("phpcs")
    if found:
        return found
    local = ROOT_DIR / "vendor" / "bin" / "phpcs"
    return str(local) if local.is_file() and os.access(str(local), os.X_OK) else None


def run(cmd, **kwargs):
    kwargs.setdefault("capture_output", True)
    kwargs.setdefault("text", True)
    kwargs.setdefault("timeout", 300)
    return subprocess.run([str(c) for c in cmd], **kwargs)


def load_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location("wq_config_loader", str(LOADER))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_git_repo(path, files):
    path.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        target = path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    if HAVE_GIT:
        env = dict(os.environ, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null")
        run(["git", "init", "-q", str(path)], env=env)
        run(["git", "-C", str(path), "add", "-A"], env=env)
        run(["git", "-C", str(path), "-c", "user.email=t@example.test",
             "-c", "user.name=test", "commit", "-qm", "init"], env=env)
    return path


BASE_SKIPS = "--skip=coderabbit,semgrep,gitleaks,phpstan,phpunit,composer_validate,composer_audit,plugin_check,npm_lint,npm_test"

# `skipped 'phpcs not installed'` reads in a CI summary exactly like passing, so
# if an install step ever breaks the phpcs tests quietly stop running and the
# job stays green. CI sets this to make that state fail loudly instead.
if os.environ.get("WPHEKA_TESTS_REQUIRE_PHPCS") == "1" and find_phpcs() is None:
    sys.exit("WPHEKA_TESTS_REQUIRE_PHPCS=1 but no phpcs on PATH or in vendor/bin")


# ---------------------------------------------------------------------------
# Configuration loader
# ---------------------------------------------------------------------------

class TestYamlParser(unittest.TestCase):
    def setUp(self):
        self.mod = load_module()

    def test_nested_maps_and_scalars(self):
        data = self.mod.parse_yaml_fallback(
            'project_type: woocommerce-plugin\nphp:\n  minimum: "8.1"\n'
            'woocommerce:\n  hpos: true\n  blocks: false\n')
        self.assertEqual(data["project_type"], "woocommerce-plugin")
        self.assertEqual(data["php"]["minimum"], "8.1")
        self.assertIs(data["woocommerce"]["hpos"], True)
        self.assertIs(data["woocommerce"]["blocks"], False)

    def test_regression_list_values_are_not_dropped(self):
        # The pre-1.0 engine hit `continue` on every "- item" line, so exclude: lists were
        # parsed as nothing at all and silently ignored.
        data = self.mod.parse_yaml_fallback("exclude:\n  - vendor/\n  - node_modules/\n")
        self.assertEqual(data["exclude"], ["vendor/", "node_modules/"])

    def test_flush_indented_list_does_not_corrupt_siblings(self):
        data = self.mod.parse_yaml_fallback(
            "exclude:\n- vendor/\n- tests/\nproject_type: wordpress-theme\n")
        self.assertEqual(data["exclude"], ["vendor/", "tests/"])
        self.assertEqual(data["project_type"], "wordpress-theme")

    def test_hash_inside_quoted_value_is_not_a_comment(self):
        data = self.mod.parse_yaml_fallback('commands:\n  phpcs: "phpcs --x=a#b"\n')
        self.assertEqual(data["commands"]["phpcs"], "phpcs --x=a#b")

    def test_malformed_input_raises_instead_of_vanishing(self):
        for bad in ("a:\n\tb: 1\n", "bare line\n", "k:\n  x: 1\n  - item\n"):
            with self.assertRaises(self.mod.ConfigError):
                self.mod.parse_yaml_fallback(bad)


class TestConfigLoader(unittest.TestCase):
    def test_defaults(self):
        res = run([sys.executable, LOADER, "--repo", FIXTURE_DIR, "--format", "json"])
        self.assertEqual(res.returncode, 0, res.stderr)
        data = json.loads(res.stdout)
        self.assertEqual(data["project_type"], "wordpress-plugin")
        self.assertTrue(data["checks"]["php_syntax"])
        self.assertFalse(data["checks"]["phpcs"])

    def test_unknown_profile_is_an_error_not_a_silent_default(self):
        res = run([sys.executable, LOADER, "--repo", FIXTURE_DIR,
                   "--profile", "does-not-exist", "--format", "json"])
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("unknown profile", res.stderr)

    def test_profile_then_repo_config_precedence(self):
        res = run([sys.executable, LOADER, "--repo", FIXTURE_DIR,
                   "--profile", "woocommerce-plugin", "--format", "json"])
        self.assertEqual(res.returncode, 0, res.stderr)
        data = json.loads(res.stdout)
        # The repo's own config is applied last and wins.
        self.assertEqual(data["project_type"], "wordpress-plugin")
        self.assertFalse(data["checks"]["phpcs"])

    def test_unknown_check_is_reported_not_silently_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = pathlib.Path(tmp) / "c.yml"
            cfg.write_text("checks:\n  not_a_real_check: true\n")
            res = run([sys.executable, LOADER, "--repo", tmp, "--config", cfg, "--format", "json"])
            self.assertEqual(res.returncode, 0)
            self.assertIn("unknown check", json.dumps(json.loads(res.stdout)["warnings"]))

    def test_check_aliases_expand(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = pathlib.Path(tmp) / "c.yml"
            cfg.write_text("checks:\n  npm: true\n  diff_check: false\n")
            res = run([sys.executable, LOADER, "--repo", tmp, "--config", cfg, "--format", "json"])
            data = json.loads(res.stdout)
            self.assertTrue(data["checks"]["npm_lint"])
            self.assertTrue(data["checks"]["npm_test"])
            self.assertFalse(data["checks"]["git_diff_check"])

    def test_regression_env_output_is_nul_delimited_not_shell_code(self):
        # The pre-1.0 engine emitted `export K="..."` and the runner ran it through eval,
        # so a repository config could execute arbitrary shell at load time.
        with tempfile.TemporaryDirectory() as tmp:
            cfg = pathlib.Path(tmp) / "c.yml"
            cfg.write_text('commands:\n  phpcs: "$(touch /tmp/wq-must-not-exist)x"\n')
            res = run([sys.executable, LOADER, "--repo", tmp, "--config", cfg, "--format", "env0"])
            self.assertEqual(res.returncode, 0)
            self.assertNotIn("export ", res.stdout)
            self.assertIn("\0", res.stdout)
            for pair in [p for p in res.stdout.split("\0") if p]:
                self.assertRegex(pair.split("=", 1)[0], r"^WPHEKA_[A-Z0-9_]+$")

    def test_regression_repo_commands_are_untrusted_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            (pathlib.Path(tmp) / ".wpheka-quality.yml").write_text(
                'commands:\n  phpcs: "echo pwned"\n')
            res = run([sys.executable, LOADER, "--repo", tmp, "--format", "json"])
            data = json.loads(res.stdout)
            self.assertEqual(data["commands"], {})
            self.assertTrue(any("ignored" in w for w in data["warnings"]))

            opted_in = run([sys.executable, LOADER, "--repo", tmp,
                            "--allow-repo-commands", "--format", "json"])
            self.assertEqual(json.loads(opted_in.stdout)["commands"]["phpcs"], "echo pwned")


# ---------------------------------------------------------------------------
# Report renderer
# ---------------------------------------------------------------------------

class TestRenderer(unittest.TestCase):
    def render(self, tmp, rows, logs=None, extra_args=()):
        run_dir = pathlib.Path(tmp)
        raw = run_dir / "tool-results"
        raw.mkdir(parents=True, exist_ok=True)
        (run_dir / "results.tsv").write_text("".join("\t".join(r) + "\n" for r in rows))
        for name, content in (logs or {}).items():
            (raw / name).write_text(content)
        res = run([sys.executable, RENDERER, run_dir, FIXTURE_DIR, "json",
                   "--version", "test"] + list(extra_args))
        self.assertEqual(res.returncode, 0, res.stderr)
        return run_dir, json.loads(res.stdout)

    def test_all_artifacts_are_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir, _ = self.render(
                tmp, [["php_syntax", "PASS", str(pathlib.Path(tmp) / "tool-results/a.log"), "0", "2 files"]],
                {"a.log": "ok"})
            for name in ("summary.md", "full-review.md", "results.json", "findings.json",
                         "sarif.json", "environment.json", "repository.json", "report.html"):
                self.assertTrue((run_dir / name).exists(), "missing %s" % name)

    def test_regression_script_tag_in_log_cannot_break_out(self):
        # The pre-1.0 engine embedded logs via json.dumps, which does not escape "</script>".
        payload = "before </script><img src=x onerror=alert(1)> after"
        with tempfile.TemporaryDirectory() as tmp:
            log = str(pathlib.Path(tmp) / "tool-results/evil.log")
            run_dir, _ = self.render(tmp, [["evil", "FAIL", log, "1", "d"]], {"evil.log": payload})
            html = (run_dir / "report.html").read_text()
            self.assertNotIn("</script><img", html)
            self.assertIn("\\u003c", html)
            self.assertEqual(html.count("<script"), html.count("</script>"))

    def test_regression_skip_reason_is_not_treated_as_a_log_path(self):
        # The pre-1.0 engine wrote the skip reason into the evidence column, so the report
        # showed "tool-results/PHPStan unavailable" and offered a dead log link.
        with tempfile.TemporaryDirectory() as tmp:
            run_dir, data = self.render(tmp, [["phpstan", "SKIPPED", "-", "-", "phpstan not installed"]])
            self.assertEqual(data["results"][0]["detail"], "phpstan not installed")
            html = (run_dir / "report.html").read_text()
            self.assertNotIn("tool-results/phpstan not installed", html)
            self.assertIn("phpstan not installed", html)

    def test_oversized_log_is_truncated(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = str(pathlib.Path(tmp) / "tool-results/big.log")
            run_dir, _ = self.render(tmp, [["big", "FAIL", log, "1", "d"]], {"big.log": "A" * 900_000})
            html = (run_dir / "report.html").read_text()
            self.assertIn("truncated", html)
            self.assertLess(len(html), 600_000)

    def test_report_html_makes_no_external_requests(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir, _ = self.render(tmp, [["a", "PASS", "-", "0", ""]])
            html = (run_dir / "report.html").read_text()
            for token in ("http://", "https://fonts", "//fonts.googleapis", "cdn."):
                self.assertNotIn(token, html.replace("https://json.schemastore.org", ""))

    def test_findings_are_deduped_and_severity_normalized(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = pathlib.Path(tmp) / "tool-results"
            raw.mkdir(parents=True)
            (raw / "phpcs.json").write_text(json.dumps({"files": {"a.php": {"messages": [
                {"type": "ERROR", "line": 5, "column": 1, "message": "Same issue",
                 "source": "R.One"}]}}}))
            (raw / "semgrep.json").write_text(json.dumps({"results": [
                {"path": "a.php", "start": {"line": 5, "col": 1},
                 "extra": {"severity": "WARNING", "message": "Same issue"}}]}))
            (pathlib.Path(tmp) / "results.tsv").write_text("phpcs\tPASS\t-\t0\t\n")
            res = run([sys.executable, RENDERER, tmp, FIXTURE_DIR, "json", "--version", "test"])
            self.assertEqual(res.returncode, 0, res.stderr)
            findings = json.loads(res.stdout)["findings"]
            self.assertEqual(len(findings), 1, "identical findings should merge")
            self.assertEqual(findings[0]["severity"], "HIGH")  # highest wins
            self.assertTrue(findings[0]["corroborated"])
            self.assertEqual(sorted(findings[0]["tools"]), ["phpcs", "semgrep"])

    def test_gitleaks_secret_value_is_never_written_to_the_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = pathlib.Path(tmp) / "tool-results"
            raw.mkdir(parents=True)
            (raw / "gitleaks.json").write_text(json.dumps([
                {"File": "cfg.php", "StartLine": 3, "Description": "AWS key",
                 "RuleID": "aws", "Secret": "AKIAVERYSECRETVALUE"}]))
            (pathlib.Path(tmp) / "results.tsv").write_text("gitleaks\tFAIL\t-\t1\t\n")
            res = run([sys.executable, RENDERER, tmp, FIXTURE_DIR, "json", "--version", "test"])
            self.assertNotIn("AKIAVERYSECRETVALUE", res.stdout)
            self.assertNotIn("AKIAVERYSECRETVALUE", (pathlib.Path(tmp) / "report.html").read_text())
            findings = json.loads(res.stdout)["findings"]
            self.assertEqual(findings[0]["severity"], "CRITICAL")

    def test_plugin_check_block_format_is_parsed(self):
        # `wp plugin check --format=json` emits a "FILE: path" header followed
        # by a JSON array per file, and wp-cli interleaves PHP notices, so the
        # output is not a single JSON document.
        raw_output = (
            "Deprecated: something in wp-cli on line 12\n"
            "FILE: /abs/plugin/includes/admin.php\n"
            '[{"line":64,"column":75,"type":"ERROR","code":"WordPress.WP.I18n.TextDomainMismatch",'
            '"message":"Mismatched text domain.","docs":""}]\n'
            "FILE: plugin.zip\n"
            '[{"line":0,"column":0,"type":"ERROR","code":"compressed_files",'
            '"message":"Compressed files are not permitted.","docs":""}]\n'
        )
        with tempfile.TemporaryDirectory() as tmp:
            raw = pathlib.Path(tmp) / "tool-results"
            raw.mkdir(parents=True)
            (raw / "plugin-check.json").write_text(raw_output)
            (pathlib.Path(tmp) / "results.tsv").write_text("plugin_check\tPASS\t-\t0\t\n")
            res = run([sys.executable, RENDERER, tmp, FIXTURE_DIR, "json", "--version", "test"])
            findings = json.loads(res.stdout)["findings"]
            self.assertEqual(len(findings), 2)
            codes = sorted(f["source"] for f in findings)
            self.assertEqual(codes, ["WordPress.WP.I18n.TextDomainMismatch", "compressed_files"])
            self.assertTrue(any(f["file"].endswith("admin.php") for f in findings))

    def test_malformed_tool_json_does_not_lose_other_findings(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = pathlib.Path(tmp) / "tool-results"
            raw.mkdir(parents=True)
            (raw / "semgrep.json").write_text("{ this is not json")
            (raw / "phpcs.json").write_text(json.dumps({"files": {"a.php": {"messages": [
                {"type": "ERROR", "line": 1, "message": "Real", "source": "R"}]}}}))
            (pathlib.Path(tmp) / "results.tsv").write_text("phpcs\tPASS\t-\t0\t\n")
            res = run([sys.executable, RENDERER, tmp, FIXTURE_DIR, "json", "--version", "test"])
            self.assertEqual(res.returncode, 0, res.stderr)
            messages = [f["message"] for f in json.loads(res.stdout)["findings"]]
            self.assertIn("Real", messages)

    def test_sarif_output_is_well_formed(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = pathlib.Path(tmp) / "tool-results"
            raw.mkdir(parents=True)
            (raw / "phpcs.json").write_text(json.dumps({"files": {"a.php": {"messages": [
                {"type": "ERROR", "line": 7, "message": "Bad", "source": "Rule.Id"}]}}}))
            (pathlib.Path(tmp) / "results.tsv").write_text("phpcs\tPASS\t-\t0\t\n")
            run([sys.executable, RENDERER, tmp, FIXTURE_DIR, "json", "--version", "test"])
            sarif = json.loads((pathlib.Path(tmp) / "sarif.json").read_text())
            self.assertEqual(sarif["version"], "2.1.0")
            result = sarif["runs"][0]["results"][0]
            self.assertEqual(result["ruleId"], "Rule.Id")
            self.assertEqual(result["level"], "error")
            self.assertEqual(
                result["locations"][0]["physicalLocation"]["region"]["startLine"], 7)

    def test_baseline_round_trip_suppresses_known_findings(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = pathlib.Path(tmp) / "tool-results"
            raw.mkdir(parents=True)
            (raw / "phpcs.json").write_text(json.dumps({"files": {"a.php": {"messages": [
                {"type": "ERROR", "line": 3, "message": "Old issue", "source": "R.A"}]}}}))
            (pathlib.Path(tmp) / "results.tsv").write_text("phpcs\tPASS\t-\t0\t\n")
            baseline = pathlib.Path(tmp) / "baseline.json"

            run([sys.executable, RENDERER, tmp, FIXTURE_DIR, "json", "--version", "test",
                 "--baseline", baseline, "--write-baseline"])
            self.assertTrue(baseline.exists())

            res = run([sys.executable, RENDERER, tmp, FIXTURE_DIR, "json", "--version", "test",
                       "--baseline", baseline])
            data = json.loads(res.stdout)
            self.assertEqual(data["findings"], [])
            self.assertEqual(data["baselined"], 1)

            # A genuinely new issue still surfaces through the baseline.
            (raw / "phpcs.json").write_text(json.dumps({"files": {"a.php": {"messages": [
                {"type": "ERROR", "line": 3, "message": "Old issue", "source": "R.A"},
                {"type": "ERROR", "line": 9, "message": "Brand new issue", "source": "R.B"}]}}}))
            res = run([sys.executable, RENDERER, tmp, FIXTURE_DIR, "json", "--version", "test",
                       "--baseline", baseline])
            data = json.loads(res.stdout)
            self.assertEqual([f["message"] for f in data["findings"]], ["Brand new issue"])

    def test_baseline_fingerprint_survives_line_number_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = pathlib.Path(tmp) / "tool-results"
            raw.mkdir(parents=True)
            (pathlib.Path(tmp) / "results.tsv").write_text("phpcs\tPASS\t-\t0\t\n")
            baseline = pathlib.Path(tmp) / "b.json"

            (raw / "phpcs.json").write_text(json.dumps({"files": {"a.php": {"messages": [
                {"type": "ERROR", "line": 3, "message": "Issue", "source": "R.A"}]}}}))
            run([sys.executable, RENDERER, tmp, FIXTURE_DIR, "json", "--version", "test",
                 "--baseline", baseline, "--write-baseline"])

            # Same issue, moved down the file by an unrelated edit.
            (raw / "phpcs.json").write_text(json.dumps({"files": {"a.php": {"messages": [
                {"type": "ERROR", "line": 41, "message": "Issue", "source": "R.A"}]}}}))
            res = run([sys.executable, RENDERER, tmp, FIXTURE_DIR, "json", "--version", "test",
                       "--baseline", baseline])
            self.assertEqual(json.loads(res.stdout)["findings"], [])

    def test_regression_no_verdict_checks_are_not_listed_as_failures(self):
        # ERROR and TIMEOUT mean the tool reached no conclusion. Listing them
        # beside FAIL reads as "it ran and found problems", which is the exact
        # confusion the engine exists to prevent.
        with tempfile.TemporaryDirectory() as tmp:
            raw = pathlib.Path(tmp) / "tool-results"
            raw.mkdir(parents=True)
            (raw / "a.log").write_text("x")
            log = str(raw / "a.log")
            (pathlib.Path(tmp) / "results.tsv").write_text(
                "phpcs\tFAIL\t%s\t1\tfound issues\n"
                "semgrep\tTIMEOUT\t%s\t124\texceeded 900s\n"
                "php_syntax\tERROR\t%s\t1\tunreadable files\n"
                "gitleaks\tSKIPPED\t-\t-\tgitleaks not installed\n" % (log, log, log))
            res = run([sys.executable, RENDERER, tmp, FIXTURE_DIR, "json", "--version", "test"])
            self.assertEqual(res.returncode, 0, res.stderr)
            summary = (pathlib.Path(tmp) / "summary.md").read_text()

            found = summary.split("## Checks that found problems", 1)[1].split("##", 1)[0]
            unreviewed = summary.split("## Unreviewed areas", 1)[1]

            self.assertIn("phpcs", found)
            for check in ("semgrep", "php_syntax", "gitleaks"):
                self.assertNotIn(check, found, "%s produced no verdict" % check)
                self.assertIn(check, unreviewed)
            self.assertIn("reviewed nothing", unreviewed)

    def test_legacy_four_column_results_still_render(self):
        with tempfile.TemporaryDirectory() as tmp:
            (pathlib.Path(tmp) / "tool-results").mkdir()
            (pathlib.Path(tmp) / "results.tsv").write_text("old\tPASS\t-\t0\n")
            res = run([sys.executable, RENDERER, tmp, FIXTURE_DIR, "json", "--version", "test"])
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertEqual(json.loads(res.stdout)["results"][0]["check"], "old")


# ---------------------------------------------------------------------------
# CLI behaviour
# ---------------------------------------------------------------------------

class TestCli(unittest.TestCase):
    def test_version_and_help(self):
        self.assertEqual(run([RUNNER, "--version"]).returncode, 0)
        self.assertEqual(run([RUNNER, "--help"]).returncode, 0)

    def test_regression_missing_flag_value_is_a_clear_error(self):
        # The pre-1.0 engine ran under `set -u` and died with "$2: unbound variable".
        res = run([RUNNER, "--repo"])
        self.assertEqual(res.returncode, 2)
        self.assertIn("requires a value", res.stderr)
        self.assertNotIn("unbound variable", res.stderr)

    def test_invalid_values_are_rejected(self):
        for args, expect in (
            (["--format", "xml"], "invalid --format"),
            (["--fail-on", "sometimes"], "invalid --fail-on"),
            (["--fail-on-severity", "spicy"], "invalid --fail-on-severity"),
            (["--jobs", "0"], "--jobs"),
            (["--only", "not_a_check"], "unknown check"),
            (["--repo", "/definitely/not/here"], "does not exist"),
            (["--write-baseline"], "requires --baseline"),
        ):
            res = run([RUNNER] + args)
            self.assertEqual(res.returncode, 2, "%s should be rejected" % args)
            self.assertIn(expect, res.stderr)

    def test_regression_runs_through_a_symlink_on_path(self):
        # Installing the CLI means symlinking it into ~/.local/bin. Resolving
        # ROOT from BASH_SOURCE without following the link pointed the script at
        # the link's directory, where config-loader.py does not exist.
        with tempfile.TemporaryDirectory() as tmp:
            link = pathlib.Path(tmp) / "wpheka-quality"
            link.symlink_to(RUNNER)
            res = run([link, "--version"])
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertNotIn("unknown", res.stdout)
            self.assertNotIn("missing", res.stderr)

    def test_regression_phpcs_memory_limit_cannot_inject_a_command(self):
        # The 1.2.0 work interpolated WPHEKA_PHPCS_MEMORY_LIMIT straight into
        # the phpcs command string, which runs through `bash -c`. Exporting
        # "1G; touch FILE" executed that command -- the same class of defect as
        # the config eval this engine was built to avoid.
        marker = pathlib.Path(tempfile.gettempdir()) / "wq-memlimit-injection.txt"
        if marker.exists():
            marker.unlink()
        res = run([RUNNER, "--repo", str(FIXTURE_DIR), "--only", "phpcs", "--no-color", "--quiet"],
                  env=dict(os.environ,
                           WPHEKA_PHPCS_MEMORY_LIMIT="1G; touch %s" % marker))
        self.assertFalse(marker.exists(), "memory limit value reached the shell")
        self.assertEqual(res.returncode, 2)
        self.assertIn("invalid WPHEKA_PHPCS_MEMORY_LIMIT", res.stderr)

    def test_phpcs_memory_limit_accepts_valid_php_literals(self):
        for value in ("1G", "512M", "2048", "-1"):
            res = run([RUNNER, "--repo", str(FIXTURE_DIR), "--list-checks"],
                      env=dict(os.environ, WPHEKA_PHPCS_MEMORY_LIMIT=value))
            self.assertEqual(res.returncode, 0, "%s should be accepted: %s" % (value, res.stderr))

    def test_regression_env_values_cannot_inject_commands(self):
        # Every value interpolated into a command string is shell input.
        # WPHEKA_PHPCS_STANDARD was unquoted; WPHEKA_SEMGREP_CONFIG was wrapped
        # in single quotes, which a quote inside the value simply ends.
        cases = [
            ("WPHEKA_PHPCS_STANDARD", "WordPress; touch %s", "phpcs"),
            ("WPHEKA_SEMGREP_CONFIG", "p/php'; touch %s; echo '", "semgrep"),
        ]
        for var, template, check in cases:
            marker = pathlib.Path(tempfile.gettempdir()) / ("wq-inj-%s.txt" % check)
            if marker.exists():
                marker.unlink()
            res = run([RUNNER, "--repo", str(FIXTURE_DIR), "--only", check,
                       "--no-color", "--quiet"],
                      env=dict(os.environ, **{var: template % marker}))
            self.assertFalse(marker.exists(), "%s reached the shell" % var)
            self.assertEqual(res.returncode, 2, "%s should be rejected" % var)
            self.assertIn("invalid %s" % var, res.stderr)

    def test_env_values_accept_legitimate_input(self):
        for var, value in (("WPHEKA_PHPCS_STANDARD", "WordPress-Extra"),
                           ("WPHEKA_SEMGREP_CONFIG", "p/php"),
                           ("WPHEKA_SEMGREP_CONFIG", "https://example.test/r.yml"),
                           ("WPHEKA_WP_PATH", "/tmp/a path/wp")):
            res = run([RUNNER, "--repo", str(FIXTURE_DIR), "--list-checks"],
                      env=dict(os.environ, **{var: value}))
            self.assertEqual(res.returncode, 0, "%s=%s rejected: %s" % (var, value, res.stderr))

    def test_regression_wp_path_rejects_a_quote(self):
        res = run([RUNNER, "--repo", str(FIXTURE_DIR), "--list-checks"],
                  env=dict(os.environ, WPHEKA_WP_PATH="/tmp/x'y"))
        self.assertEqual(res.returncode, 2)
        self.assertIn("invalid WPHEKA_WP_PATH", res.stderr)

    def test_bundled_phpcs_rulesets_are_valid(self):
        # Every excluded sniff name must exist, or phpcs aborts with
        # "Referenced sniff ... does not exist" and the check reports a tool
        # failure instead of reviewing anything.
        phpcs = find_phpcs()
        if not phpcs:
            self.skipTest("phpcs not installed (checked PATH and vendor/bin)")
        with tempfile.TemporaryDirectory() as tmp:
            target = pathlib.Path(tmp) / "t.php"
            target.write_text("<?php\n$a = 1;\n")
            for name in ("phpcs-default.xml", "phpcs-all-sniffs.xml"):
                ruleset = ROOT_DIR / "config" / name
                self.assertTrue(ruleset.exists(), "%s missing" % name)
                res = run([phpcs, "--standard=%s" % ruleset, "-q", str(target)])
                self.assertNotIn("does not exist", res.stdout + res.stderr,
                                 "%s references a sniff that is not installed" % name)
                self.assertLessEqual(res.returncode, 2,
                                     "%s failed to load: %s" % (name, res.stdout))

    def test_default_ruleset_drops_layout_but_keeps_security_sniffs(self):
        phpcs = find_phpcs()
        if not phpcs:
            self.skipTest("phpcs not installed (checked PATH and vendor/bin)")
        # Badly formatted (tabs/spaces, spacing) *and* genuinely unsafe: the
        # unescaped echo of a superglobal must survive the exclusions.
        source = (
            "<?php\n"
            "function wq_demo() {\n"
            "    $x = array( 1,2 );\n"
            "    echo $_GET['q'];\n"
            "}\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            target = pathlib.Path(tmp) / "demo.php"
            target.write_text(source)

            def sniffs(ruleset):
                res = run([phpcs, "--standard=%s" % (ROOT_DIR / "config" / ruleset),
                           "--report=json", "-q", str(target)])
                try:
                    data = json.loads(res.stdout)
                except ValueError:
                    self.fail("phpcs produced no JSON for %s: %s" % (ruleset, res.stdout[:200]))
                return [m["source"] for f in data.get("files", {}).values()
                        for m in f.get("messages", [])]

            default = sniffs("phpcs-default.xml")
            everything = sniffs("phpcs-all-sniffs.xml")

            self.assertLess(len(default), len(everything),
                            "the default ruleset must be quieter than all sniffs")
            self.assertTrue(any("WhiteSpace" in s or "Spacing" in s for s in everything),
                            "fixture should trip layout sniffs under the full standard")
            self.assertFalse(any("WhiteSpace" in s for s in default),
                             "whitespace sniffs must be excluded by default: %s" % default)
            self.assertTrue(any("EscapeOutput" in s for s in default),
                            "security sniffs must survive the exclusions: %s" % default)

    def test_doctor_reports_toolchain(self):
        res = run([RUNNER, "--doctor"])
        self.assertEqual(res.returncode, 0, res.stderr)
        for tool in ("php", "phpcs", "semgrep", "gitleaks", "python3"):
            self.assertIn(tool, res.stdout)

    def test_list_checks_reports_the_plan_without_running_tools(self):
        res = run([RUNNER, "--repo", FIXTURE_DIR, "--list-checks"])
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("php_syntax", res.stdout)
        self.assertIn("phpcs", res.stdout)
        self.assertIn("disabled", res.stdout)


@unittest.skipUnless(HAVE_PHP and HAVE_GIT, "php and git are required")
class TestIntegration(unittest.TestCase):
    def audit(self, repo, extra=()):
        out = pathlib.Path(tempfile.mkdtemp())
        res = run([RUNNER, "--repo", repo, "--output-dir", out,
                   "--no-color", "--quiet", BASE_SKIPS] + list(extra))
        rows = {}
        tsv = out / "results.tsv"
        if tsv.exists():
            for line in tsv.read_text().splitlines():
                cols = line.split("\t")
                rows[cols[0]] = cols[1]
        return res, out, rows

    def test_regression_syntax_error_is_caught_from_a_foreign_cwd(self):
        # The pre-1.0 engine fed repo-relative paths to `php -l` running in the caller's
        # cwd. Every file failed to open, no "Parse error" text was produced,
        # and the check reported PASS on a repository that could not even parse.
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_git_repo(pathlib.Path(tmp) / "r", {
                "ok.php": "<?php\n$a = 1;\n",
                "sub dir/broken file.php": "<?php $x=;;;\n",
            })
            res, out, rows = self.audit(repo, ["--skip=phpcs"])
            self.assertEqual(rows.get("php_syntax"), "FAIL",
                             "a real parse error must not report PASS")
            self.assertNotEqual(res.returncode, 0)
            log = (out / "tool-results" / "php_syntax.log").read_text()
            self.assertIn("broken file.php", log,
                          "paths containing spaces must survive the file list")
            self.assertNotIn("Could not open input file", log)

    def test_regression_parse_error_is_not_misreported_as_a_timeout(self):
        # `php -l` exits 255 on a parse error, and GNU xargs converts any 255
        # into its own exit 124 — the same code `timeout` uses for a kill. On
        # Linux that turned a real syntax error into TIMEOUT, i.e. "the check
        # never ran". BSD xargs on macOS returns 1, so this only failed on CI.
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_git_repo(pathlib.Path(tmp) / "r", {
                "broken.php": "<?php $x=;;;\n",
            })
            _, out, rows = self.audit(repo, ["--skip=phpcs"])
            self.assertEqual(rows.get("php_syntax"), "FAIL")
            self.assertNotEqual(
                rows.get("php_syntax"), "TIMEOUT",
                "a fast-failing parse error must never be recorded as a timeout")

    def test_timeout_status_requires_the_clock_to_agree(self):
        # Defence in depth for the above: a command that exits 124 quickly is a
        # failure, not a timeout, because a timeout cannot precede its limit.
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_git_repo(pathlib.Path(tmp) / "r", {"a.php": "<?php\n$a = 1;\n"})
            cfg = pathlib.Path(tmp) / "c.yml"
            cfg.write_text(
                "timeouts:\n  phpcs: 300\n"
                "checks:\n  php_syntax: false\n  git_diff_check: false\n  phpcs: true\n"
                "commands:\n  phpcs: \"exit 124\"\n")
            out = pathlib.Path(tempfile.mkdtemp())
            run([RUNNER, "--repo", repo, "--config", cfg, "--output-dir", out,
                 "--no-color", "--quiet", BASE_SKIPS], timeout=60)
            rows = dict(line.split("\t")[:2]
                        for line in (out / "results.tsv").read_text().splitlines())
            self.assertEqual(rows.get("phpcs"), "FAIL",
                             "exit 124 after 0s is a failure, not a timeout")

    def test_clean_repository_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_git_repo(pathlib.Path(tmp) / "r", {"plugin.php": "<?php\n$a = 1;\n"})
            res, out, rows = self.audit(repo, ["--skip=phpcs"])
            self.assertEqual(rows.get("php_syntax"), "PASS")
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            self.assertTrue((out / "report.html").exists())

    def test_regression_unreadable_files_report_error_not_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_git_repo(pathlib.Path(tmp) / "r", {"a.php": "<?php\n$a = 1;\n"})
            # Track a file, then delete it: git still lists it, php cannot open it.
            (repo / "a.php").unlink()
            res, out, rows = self.audit(repo, ["--skip=phpcs"])
            self.assertIn(rows.get("php_syntax"), ("ERROR", "FAIL"))
            self.assertNotEqual(rows.get("php_syntax"), "PASS")

    def test_regression_report_directory_does_not_look_like_a_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_git_repo(pathlib.Path(tmp) / "r", {"a.php": "<?php\n$a = 1;\n"})
            # Default output location: inside the repository being reviewed.
            res = run([RUNNER, "--repo", repo, "--no-color", "--quiet", BASE_SKIPS, "--skip=phpcs"])
            reports = sorted((repo / ".wpheka-quality-reports").iterdir())
            tsv = reports[-1] / "results.tsv"
            statuses = dict(line.split("\t")[:2] for line in tsv.read_text().splitlines())
            self.assertEqual(statuses.get("repository_integrity"), "PASS")

    def test_timeout_is_enforced_and_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_git_repo(pathlib.Path(tmp) / "r", {"a.php": "<?php\n$a = 1;\n"})
            cfg = pathlib.Path(tmp) / "slow.yml"
            cfg.write_text(
                "timeouts:\n  phpcs: 2\n"
                "checks:\n  php_syntax: false\n  git_diff_check: false\n  phpcs: true\n"
                "commands:\n  phpcs: \"sleep 90\"\n")
            out = pathlib.Path(tempfile.mkdtemp())
            res = run([RUNNER, "--repo", repo, "--config", cfg, "--output-dir", out,
                       "--no-color", "--quiet", BASE_SKIPS], timeout=60)
            rows = dict(line.split("\t")[:2] for line in (out / "results.tsv").read_text().splitlines())
            self.assertEqual(rows.get("phpcs"), "TIMEOUT")
            self.assertNotEqual(res.returncode, 0)

    def test_regression_config_file_cannot_execute_shell_at_load_time(self):
        marker = pathlib.Path(tempfile.gettempdir()) / "wq-rce-marker.txt"
        if marker.exists():
            marker.unlink()
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_git_repo(pathlib.Path(tmp) / "r", {"a.php": "<?php\n$a = 1;\n"})
            (repo / ".wpheka-quality.yml").write_text(
                'commands:\n  phpcs: "$(touch %s)true"\n' % marker)
            self.audit(repo, ["--skip=phpcs"])
            self.assertFalse(marker.exists(),
                             "a repository config must never execute shell during config load")

    def test_skip_reasons_are_explicit_for_every_skipped_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_git_repo(pathlib.Path(tmp) / "r", {"a.php": "<?php\n$a = 1;\n"})
            _, out, _ = self.audit(repo, ["--skip=phpcs"])
            for line in (out / "results.tsv").read_text().splitlines():
                cols = line.split("\t")
                if cols[1] == "SKIPPED":
                    self.assertTrue(cols[4].strip(), "check %s was skipped with no reason" % cols[0])

    def test_severity_gate_fails_the_run_on_a_critical_finding(self):
        # Exercises the CLI gate end to end, not just the renderer's counting.
        # A command override writes the report gitleaks would have written, so
        # the check produces a CRITICAL finding without gitleaks being installed.
        payload = json.dumps([{"File": "a.php", "StartLine": 1,
                               "Description": "key", "RuleID": "r"}])
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_git_repo(pathlib.Path(tmp) / "r", {"a.php": "<?php\n$a = 1;\n"})
            cfg = pathlib.Path(tmp) / "c.yml"
            cfg.write_text(
                "checks:\n  php_syntax: false\n  git_diff_check: false\n  gitleaks: true\n"
                "commands:\n  gitleaks: \"printf '%s' > $WPHEKA_RAW_DIR/gitleaks.json\"\n"
                % payload.replace('"', '\\"'))

            out = pathlib.Path(tempfile.mkdtemp())
            res = run([RUNNER, "--repo", repo, "--config", cfg, "--output-dir", out,
                       "--no-color", "--quiet", "--fail-on-severity", "critical",
                       "--skip=coderabbit,semgrep,phpstan,phpunit,composer_validate,"
                       "composer_audit,plugin_check,npm_lint,npm_test,phpcs"])
            findings = json.loads((out / "findings.json").read_text())
            self.assertEqual([f["severity"] for f in findings], ["CRITICAL"])
            self.assertNotEqual(res.returncode, 0,
                                "a CRITICAL finding must fail the run under --fail-on-severity critical")

    def test_severity_gate_none_allows_a_critical_finding_through(self):
        payload = json.dumps([{"File": "a.php", "StartLine": 1,
                               "Description": "key", "RuleID": "r"}])
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_git_repo(pathlib.Path(tmp) / "r", {"a.php": "<?php\n$a = 1;\n"})
            cfg = pathlib.Path(tmp) / "c.yml"
            cfg.write_text(
                "checks:\n  php_syntax: false\n  git_diff_check: false\n  gitleaks: true\n"
                "commands:\n  gitleaks: \"printf '%s' > $WPHEKA_RAW_DIR/gitleaks.json\"\n"
                % payload.replace('"', '\\"'))
            out = pathlib.Path(tempfile.mkdtemp())
            res = run([RUNNER, "--repo", repo, "--config", cfg, "--output-dir", out,
                       "--no-color", "--quiet", "--fail-on-severity", "none",
                       "--skip=coderabbit,semgrep,phpstan,phpunit,composer_validate,"
                       "composer_audit,plugin_check,npm_lint,npm_test,phpcs"])
            self.assertEqual(res.returncode, 0)

    def test_regression_a_tool_that_never_ran_is_error_not_fail(self):
        # phpcs exit 3 (3.x) / 16 (4.x) / 255 (PHP fatal) mean it never started.
        # run_check filed every non-pass code as FAIL, so a phpcs that died on a
        # bad standard was reported as having read the code and disliked it --
        # the engine's own central promise, broken by its own runner.
        if find_phpcs() is None:
            self.skipTest("phpcs not installed")
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_git_repo(pathlib.Path(tmp) / "r", {"a.php": "<?php\n$a = 1;\n"})
            out = pathlib.Path(tempfile.mkdtemp())
            run([RUNNER, "--repo", repo, "--only", "phpcs", "--output-dir", out,
                 "--no-color", "--quiet"],
                env=dict(os.environ, WPHEKA_PHPCS_STANDARD="DefinitelyNotAStandard"))
            rows = dict(line.split("\t")[:2]
                        for line in (out / "results.tsv").read_text().splitlines())
            self.assertEqual(rows.get("phpcs"), "ERROR",
                             "a tool that could not start must not report FAIL")
            # And it must be reported as unreviewed, not as a finding.
            summary = (out / "summary.md").read_text()
            self.assertIn("phpcs", summary.split("## Unreviewed areas", 1)[1])

    def test_regression_all_sniffs_warns_when_repository_ruleset_wins(self):
        # The flag sat below the repository-ruleset branch, so on a target with
        # its own phpcs.xml it was accepted and silently did nothing.
        if find_phpcs() is None:
            self.skipTest("phpcs not installed")
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_git_repo(pathlib.Path(tmp) / "r", {
                "a.php": "<?php\n$a = 1;\n",
                "phpcs.xml": '<?xml version="1.0"?>\n<ruleset name="x">'
                             '<rule ref="PSR12"/></ruleset>\n',
            })
            out = pathlib.Path(tempfile.mkdtemp())
            res = run([RUNNER, "--repo", repo, "--only", "phpcs", "--all-sniffs",
                       "--output-dir", out, "--no-color", "--quiet"])
            self.assertIn("--all-sniffs ignored", res.stderr,
                          "an explicit instruction must take effect or say why not")

    def test_regression_coderabbit_skips_when_only_untracked_files_changed(self):
        # The gate used git_status_snapshot, which lists untracked files (-uall).
        # `cr review --uncommitted` cannot see those, so an untracked file was
        # enough to start a review of zero lines that exited 0 and recorded PASS.
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_git_repo(pathlib.Path(tmp) / "r", {"a.php": "<?php\n$a = 1;\n"})
            (repo / "untracked.txt").write_text("not tracked\n")
            bin_dir = pathlib.Path(tmp) / "bin"
            bin_dir.mkdir()
            stub = bin_dir / "cr"
            stub.write_text("#!/bin/sh\nexit 0\n")
            stub.chmod(0o755)
            out = pathlib.Path(tempfile.mkdtemp())
            run([RUNNER, "--repo", repo, "--only", "coderabbit", "--output-dir", out,
                 "--no-color", "--quiet"],
                env=dict(os.environ, PATH="%s:%s" % (bin_dir, os.environ.get("PATH", ""))))
            cols = [l.split("\t") for l in (out / "results.tsv").read_text().splitlines()
                    if l.startswith("coderabbit")][0]
            self.assertEqual(cols[1], "SKIPPED",
                             "reviewing zero lines must not record PASS")
            self.assertIn("tracked", cols[4])

    def test_regression_custom_output_dir_inside_repo_is_not_a_mutation(self):
        # The integrity snapshot filtered the literal default report path, so a
        # custom --output-dir inside the repository looked like the engine had
        # modified the working tree.
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_git_repo(pathlib.Path(tmp) / "r", {"a.php": "<?php\n$a = 1;\n"})
            out = repo / "build" / "quality-reports"
            res = run([RUNNER, "--repo", repo, "--output-dir", out,
                       "--no-color", "--quiet", BASE_SKIPS, "--skip=phpcs"])
            rows = dict(line.split("\t")[:2]
                        for line in (out / "results.tsv").read_text().splitlines())
            self.assertEqual(rows.get("repository_integrity"), "PASS",
                             "writing reports inside the repo must not read as a mutation")

    def test_non_git_directory_is_handled(self):
        with tempfile.TemporaryDirectory() as tmp:
            plain = pathlib.Path(tmp) / "plain"
            plain.mkdir()
            (plain / "a.php").write_text("<?php\n$a = 1;\n")
            res, out, rows = self.audit(plain, ["--skip=phpcs"])
            self.assertEqual(rows.get("php_syntax"), "PASS")
            self.assertEqual(rows.get("git_diff_check"), "SKIPPED")
            self.assertEqual(rows.get("repository_integrity"), "SKIPPED")

    @unittest.skipUnless(HAVE_GIT, "git required")
    def test_regression_coderabbit_is_invoked_with_flags_the_cli_accepts(self):
        # The pre-1.1.1 engine ran `cr --plain --type uncommitted`. The CodeRabbit
        # CLI has neither option -- scope is a boolean flag on the `review`
        # subcommand -- so every run died with a usage error in about two seconds
        # and the check could never pass. It failed loudly rather than silently,
        # but the check was dead weight on every install with a current CLI.
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            repo = make_git_repo(root / "r", {"a.php": "<?php\n$a = 1;\n"})

            # An uncommitted edit, or the engine skips before invoking anything.
            (repo / "a.php").write_text("<?php\n$a = 2;\n")

            # A stub `cr` that records its argv instead of reviewing anything.
            bin_dir = root / "bin"
            bin_dir.mkdir()
            argv_log = root / "argv.txt"
            stub = bin_dir / "cr"
            stub.write_text('#!/bin/sh\nprintf "%%s\\n" "$*" > "%s"\nexit 0\n' % argv_log)
            stub.chmod(0o755)

            out = pathlib.Path(tempfile.mkdtemp())
            env = dict(os.environ, PATH="%s:%s" % (bin_dir, os.environ.get("PATH", "")))
            run([RUNNER, "--repo", repo, "--output-dir", out, "--no-color",
                 "--quiet", "--only", "coderabbit"], env=env)

            self.assertTrue(argv_log.exists(),
                            "coderabbit check never invoked the cli")
            argv = argv_log.read_text().strip()

            self.assertIn("review", argv, "scope flags belong to the review subcommand")
            self.assertIn("--uncommitted", argv)
            self.assertNotIn("--plain", argv, "no such option; it is a usage error")
            self.assertNotIn("--type", argv, "no such option; it is a usage error")

    @unittest.skipUnless(HAVE_GIT, "git required")
    def test_regression_a_rate_limited_review_is_not_a_failure(self):
        # CodeRabbit exits 1 both when it finds problems and when it refuses to
        # review at all, so the engine reported "used all 3 included reviews" as
        # FAIL -- asserting a verdict nobody produced. Any gate running more than
        # three times an hour then went red for a reason that was not the code,
        # which teaches people to ignore red. A tool that declined to review has
        # not passed and has not failed: SKIPPED is the status that says the area
        # is unreviewed, and the summary lists it as such.
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            repo = make_git_repo(root / "r", {"a.php": "<?php\n$a = 1;\n"})
            (repo / "a.php").write_text("<?php\n$a = 2;\n")

            bin_dir = root / "bin"
            bin_dir.mkdir()
            stub = bin_dir / "cr"
            stub.write_text(
                "#!/bin/sh\n"
                "echo '  x Review limit reached'\n"
                "echo \"You've used all free CLI reviews for now.\"\n"
                "echo 'Error: Rate limit exceeded'\n"
                "exit 1\n")
            stub.chmod(0o755)

            out = pathlib.Path(tempfile.mkdtemp())
            env = dict(os.environ, PATH="%s:%s" % (bin_dir, os.environ.get("PATH", "")))
            res = run([RUNNER, "--repo", repo, "--output-dir", out, "--no-color",
                       "--quiet", "--only", "coderabbit"], env=env)

            rows = {}
            for line in (out / "results.tsv").read_text().splitlines():
                cols = line.split("\t")
                rows[cols[0]] = (cols[1], cols[4] if len(cols) > 4 else "")

            self.assertIn("coderabbit", rows)
            status, detail = rows["coderabbit"]
            self.assertEqual(status, "SKIPPED",
                             "a refused review must not be reported as a defect")
            self.assertIn("limit", detail.lower())
            self.assertEqual(res.returncode, 0,
                             "an unreviewed area must not fail the gate as if it found bugs")

    def test_empty_repository_does_not_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "empty"
            repo.mkdir()
            run(["git", "init", "-q", str(repo)])
            res, out, rows = self.audit(repo, ["--skip=phpcs"])
            self.assertEqual(rows.get("php_syntax"), "SKIPPED")
            self.assertTrue((out / "summary.md").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
