# Changelog

## 1.0.0 — first public release

A review-first quality engine for WordPress and WooCommerce repositories. It
drives `php -l`, PHPCS/WPCS, PHPStan, PHPUnit, Composer audit, Semgrep,
Gitleaks, Plugin Check and CodeRabbit, then normalises, deduplicates and
correlates their output into one report.

### Core behaviour

- **A `PASS` means the check actually ran.** Status comes from exit codes, never
  from pattern-matching a log. Grepping output for "Parse error" cannot tell a
  clean run apart from one where the tool never started.
- **`TIMEOUT` and `ERROR` are distinct from both pass and fail.** A check that
  was killed or could not start produced no evidence either way, and collapsing
  it into either bucket is how a broken run gets read as a clean one.
- **Every skip records a reason**, and the summary lists skipped checks as
  unreviewed areas rather than folding them into a clean total.
- **Every check is bounded by a timeout**, with a fallback watchdog where
  coreutils `timeout` is unavailable.

### Security

The repository under review is treated as untrusted input.

- Configuration is parsed as data and never `eval`'d.
- A `commands:` block inside the reviewed repository is ignored unless
  `--allow-repo-commands` is passed; an explicit `--config` file stays trusted.
- Tool output embedded in `report.html` is escaped so it cannot escape its
  context, and the page makes no network requests.
- Secret values found by scanners never reach any artifact — only the rule and
  location are recorded.

### Reporting

- `findings.json` with findings deduplicated across tools and severities
  normalised onto one scale, so a semgrep `ERROR` and a phpcs `ERROR` rank
  consistently.
- Cross-tool correlation: findings at the same location merge, keeping the
  highest severity and listing every reporting tool.
- SARIF 2.1.0 output for GitHub code scanning.
- Standalone `report.html`, plus `summary.md` and a sectioned `full-review.md`
  for the reviewing agent.

### Adoption and CI

- Baselines (`--baseline`, `--write-baseline`) for existing codebases.
  Fingerprints exclude line numbers, so a finding survives unrelated edits.
- `--fail-on-severity`, because phpcs and semgrep exit 0 while reporting
  serious findings.
- `--doctor`, `--only`, `--skip`, `--list-checks`, `--dry-run`, `--jobs`.
- `bin/install-tools.sh` for the toolchain on macOS and Linux.

### Defects fixed while hardening for release

The engine grew from an internal prototype. These were found and fixed on the
way to 1.0.0; each has a `test_regression_*` test so it cannot return quietly.

**Checks that could report `PASS` without having run**

- `php_syntax` passed on repositories that could not parse. Repo-relative paths
  were handed to `php -l` running in the *caller's* working directory, so every
  file failed to open, producing "Could not open input file" — text matching
  neither string the check grepped for.
- A real parse error was recorded as `TIMEOUT` on Linux. `php -l` exits 255 and
  GNU `xargs` converts that to its own exit 124, the same code `timeout` uses
  for a kill. BSD `xargs` returns 1, so this only appeared on Linux.
- `plugin_check` passed when the command did not exist; wp-cli exited 1 with
  "'check' is not a registered subcommand" and that code was accepted.
- `[[ -f "$REPO/"*.php ]]` never matched, because `[[ ]]` does not glob.

**Security**

- Arbitrary code execution from a repository config. The runner used
  `eval "$(config-loader --format env)"`, so a `commands:` entry executed at
  config-load time, before any check ran.
- XSS in `report.html`. Logs were embedded with `json.dumps`, which does not
  escape `</script>`, so a scanned file containing that string could break out
  of the script block.
- Report pages loaded external fonts, signalling what was being scanned.

**Other**

- `--repo` with no value died with `$2: unbound variable`.
- Skip reasons were written into the evidence-path column, producing entries
  like `tool-results/PHPStan unavailable` and dead log links.
- YAML lists were silently discarded, so `exclude:` never worked.
- Tool version probes were counted as checks, inflating the `PASS` total.
- Six tools were run twice each to produce both JSON and text output.
- `phpcs` ran with no standard when the repository had no ruleset.
- Resolving the script's own directory failed when invoked through a symlink.
- Composer 2.2+ blocked the codesniffer plugin during installation, so a fresh
  machine ended up with phpcs but no WordPress standard.

### Notes

- Semgrep runs with `--config p/php --metrics=off`. `--config auto` requires
  metrics upload, which sends data about scanned code to a third party.
- `commands:` entries run verbatim; the engine appends nothing. Write reports to
  `$WPHEKA_RAW_DIR` to keep structured findings.
- npm checks are opt-in and additionally skip when `node_modules` is absent;
  the engine never installs dependencies.
- Targets bash 3.2 and Python 3.8. Verified on Linux and macOS in CI.
