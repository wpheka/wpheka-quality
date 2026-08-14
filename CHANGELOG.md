# Changelog

## 1.2.1

### Fixed

- **`WPHEKA_PHPCS_MEMORY_LIMIT` was validated inside the phpcs check**, so on a
  machine without phpcs the check was skipped and the guard never ran. No
  injection was possible there — the value is only interpolated when phpcs
  actually runs — but a guard that disappears on some hosts is not a guard.
  It is now validated at startup with the other inputs, and rejects bad values
  regardless of which tools are installed.

  Caught by the Python 3.8 CI job, which runs without phpcs and is the only job
  that exercises that path.

### Testing

- The minimum-Python job now installs WPCS. Without it, the two phpcs ruleset
  tests skipped silently on that job, leaving the rulesets unverified on the
  interpreter the README claims to support.

51 tests, green on Ubuntu, macOS, and Python 3.8.

## 1.2.0

Behaviour change: phpcs now runs a ruleset with formatting-only sniffs
excluded. Existing baselines should be regenerated.

### Changed

- **phpcs no longer reports formatting by default.** `config/phpcs-default.xml`
  is WPCS with layout sniffs removed, and is used when a repository does not
  ship its own ruleset.

  Measured on a real plugin, the full standard produced 1427 findings, of which
  roughly 1200 were indentation, alignment and bracket spacing.
  `Generic.WhiteSpace.DisallowSpaceIndent` alone was 41% of the report. Buried
  underneath were 32 missing or recommended nonce checks, 17 unescaped outputs
  and 7 direct database queries. The default ruleset reports 157 findings and
  loses none of those 49.

  The engine's own documentation already said not to let low-severity WPCS
  findings hide a critical defect. It was creating that exact problem.

  Nothing excluded can describe a bug; every removed sniff reports on the shape
  of the source rather than its behaviour. `--all-sniffs` applies WPCS
  untouched, and a repository's own ruleset always takes precedence. The
  ruleset in use is printed beside the check, because one that silently drops
  sniffs is indistinguishable from a clean codebase.

- **`summary.md` separates checks that found problems from checks that produced
  no verdict.** `ERROR` and `TIMEOUT` now sit with `SKIPPED` under "Unreviewed
  areas" instead of beside `FAIL`. A tool that was killed halfway did not
  review the code and should not read as though it did.

### Fixed

- **phpcs ran with PHP's default memory limit** and exhausts it on a large
  tree, surfacing as a fatal error rather than as findings. It now runs with
  `memory_limit=1G`, overridable via `WPHEKA_PHPCS_MEMORY_LIMIT`.

### Security

- **`WPHEKA_PHPCS_MEMORY_LIMIT` could execute arbitrary commands.** The value
  was interpolated straight into the phpcs command string, which runs through
  `bash -c`, so `WPHEKA_PHPCS_MEMORY_LIMIT='1G; rm -rf ~'` ran that command.
  Introduced by the memory-limit fix above and caught by review before release.
  The value is now validated as a PHP memory-limit literal.

  This is the same class of defect as the config `eval` the engine was built to
  avoid, which is a reminder that the rule has to be applied to every new
  interpolation, not just the one that prompted it.

### Added

- `--all-sniffs`, and the two bundled rulesets it selects between.
- Tests asserting both rulesets load with no unknown sniff names — a single
  invalid name makes phpcs abort, so the check would report a tool failure
  instead of reviewing anything — and that the default ruleset drops whitespace
  findings while keeping `EscapeOutput`.
- A test asserting no-verdict checks are reported separately from failures.

- A regression test proving the memory-limit value cannot reach the shell, and
  one asserting valid literals are still accepted.

51 tests, green on Ubuntu, macOS, and Python 3.8.

### Notes

phpcs success codes are allow-listed (`0`, `1`, `2`) rather than failure codes
deny-listed. That is deliberate: a tool failure is exit 3 on phpcs 3.x and exit
16 on phpcs 4.x (verified against 4.0.4), and both already report `FAIL`
without version-specific handling.

### Upgrading

Reports will be substantially shorter. If you gate on a baseline, regenerate
it:

```bash
wpheka-quality --repo . --baseline .wpheka-baseline.json --write-baseline
```

Keeping the old baseline is harmless but pointless: it holds fingerprints for
formatting findings that are no longer reported. To keep the previous
behaviour, pass `--all-sniffs` or set `WPHEKA_PHPCS_STANDARD=WordPress`.

## 1.1.1

### Fixed

- **The `coderabbit` check could never pass.** It invoked
  `cr --plain --type uncommitted`, and the CodeRabbit CLI has neither option —
  scope is a boolean flag on the `review` subcommand. Every run died with a
  usage error in roughly two seconds, so the check was dead weight on any
  install with a current CLI, and a repository that gated on it could not go
  green. Both invocations now use `cr review --uncommitted` and
  `cr review --base <branch>`.

  The engine failed loudly rather than reporting a false pass, which is the
  behaviour its design rules ask for — but a check that always fails teaches
  people to ignore it, which costs the same in the end.

  `skills/wpheka-quality/SKILL.md` documented the same wrong syntax and is
  corrected, with the whole-repository form (`--base-commit` with the root
  commit) added since reviewing an entire repository is otherwise unobvious.

## 1.1.0

Fixes from the first external code review of the 1.0.0 tree.

### Fixed

- **`gitleaks` ignored its own non-git handling.** `GITLEAKS_SRC` was computed
  and then never used, so directories outside a git work tree were scanned
  without `--no-git` — the exact case the branch existed for.
- **The gitleaks installer downloaded a URL that does not exist.** Release
  assets embed the version (`gitleaks_8.30.1_linux_x64.tar.gz`), so the
  `/latest/download/` shortcut returned HTTP 404 and every Linux install
  silently ended up without gitleaks. The installer now resolves the tag first.
  The same URL was wrong in `docs/ci.md`.
- **`php_syntax` decided `ERROR` by grepping its log** for "Could not open input
  file". That is status-by-log-scraping, the practice the engine refuses to
  accept from any other check. Readability is now established before linting,
  so the lint's exit code is the only thing that decides pass or fail.
- **`repository_integrity` misreported a custom `--output-dir`.** The snapshot
  filtered the literal default report path, so writing reports anywhere else
  inside the repository looked like the engine had modified the working tree.
  The exclusion is now derived from the resolved run directory.
- **`render-report.py --write-baseline` without `--baseline` crashed** with a
  bare `TypeError`. The renderer is documented as independently runnable, so it
  now validates the combination itself.
- **`commands:` overrides for `gitleaks` and `plugin_check` were accepted by the
  config loader and then ignored** by the runner. Both are now honoured.

### Testing

- `test_severity_gate_fails_the_run_on_a_critical_finding` asserted the CLI
  exited **zero** and then only checked the renderer's counting, so it never
  exercised the gate its name described. It now drives the CLI end to end, with
  a companion test for `--fail-on-severity none`.
- Added a regression test for a custom `--output-dir` inside the repository.
- Added a CI job running the suite on Python 3.8. The README claimed 3.8
  support while every runner used something far newer, leaving the claim
  untested.

### Documentation

- `docs/tool-matrix.md` mapped `MAJOR` to `MEDIUM`; the code maps it to `HIGH`.
- `docs/ci.md` invoked `wpheka-quality/bin/...` without ever checking out the
  engine, so the published recipe could not run. It now checks out a pinned tag.
- `docs/security-notes.md` claimed the process *group* is terminated on timeout;
  the implementation signals the process and its direct children.
- `docs/architecture.md` called the renderer "pure" while it regenerates
  timestamps and git metadata on every render.
- `docs/woocommerce-notes.md` implied direct `get_post_meta` reads merely fail
  on HPOS installs. Compatibility-mode synchronisation may be disabled,
  incomplete or stale, so such reads can also return outdated values.
- `docs/ci.md` cited "`--fail-on` base comparisons" as a reason for full clone
  depth. No such feature exists; only gitleaks history scanning needs it.

### Not changed

The review also recommended removing the Composer install from the self-test
workflow, citing the AGENTS.md rule "Never update or install dependencies".
That rule governs engine behaviour *during a review*, not CI provisioning.
GitHub runners do not ship WPCS, so following it would break the immediately
following step that asserts the WordPress standard is registered.

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
