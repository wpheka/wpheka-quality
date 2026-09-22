# Changelog

## 1.7.0

### Added

- **`WPHEKA_CODERABBIT_MODE=base-commit`, for reviewing against a commit rather
  than a branch.** The existing `base` mode diffs against a branch tip, which is
  the wrong question for anything that reviews repeatedly: on a repository whose
  HEAD *is* `main`, a diff against `main` is empty, and the review comes back
  clean because it saw nothing at all.

  What a rotation actually asks is "what has changed since we last looked here",
  and that is a commit. `WPHEKA_CODERABBIT_BASE_COMMIT` names it, and the check
  runs `cr review --agent --base-commit`. Reviewing the whole history instead
  would re-report work already seen and run into the free tier's 150-file
  ceiling.

  The mode refuses before spending anything when there is nothing to review: an
  absent or unparseable ref, a commit that no longer exists, or a diff that is
  empty are each recorded as `SKIPPED` with the reason. The free tier allows
  three reviews an hour, rolling and shared across every repository, and a
  review killed halfway still costs one — so being told "nothing changed" is not
  worth a slot.

## 1.6.0

### Added

- **`deprecations` — the category the other checks cannot see.** A deprecated
  call is valid PHP. It parses, it passes every sniff, and it keeps working
  right up to the release that removes it. `docs/wordpress-notes.md` has listed
  "Deprecated APIs" under what these checks do not catch since the beginning;
  on a payment gateway running on live stores, the release that finally removes
  one is a fatal error during checkout.

  The corpus is built from the WordPress and WooCommerce **installed on this
  machine**, not from a list shipped here. A hardcoded list is wrong the day
  after it is written; reading the installed copies means it refreshes itself
  when they update and describes the versions actually in use. It currently
  yields 429 functions: 327 from WordPress across four files, 102 from
  WooCommerce.

  Both projects mark a deprecation two ways and both use both — WooCommerce
  names the function explicitly 23 times and uses `__FUNCTION__` the other 83 —
  so reading one form finds a fifth of them. Because the marker sits in the
  deprecated function itself, the version and the suggested replacement come out
  with the name, which is what makes a finding actionable rather than merely
  true.

  Two mistakes are worth recording because the scanner made both before it
  worked. A method definition is not a call: `public function get_settings(...)`
  was reported as a call to the WordPress function of that name, three times on
  a real portfolio, all three wrong. And a method does **not** shadow a global
  function: treating any defined name as shadowing meant a file defining a
  `get_settings()` method silently swallowed a genuine call to the deprecated
  WordPress one. Three plugins here define exactly that method.

### Fixed

- **semgrep without a network was recorded as a failure, not a skip.** A
  registry ruleset has to be downloaded before anything can be scanned, so
  offline there is nothing to scan with. semgrep then spends about 100 seconds
  on DNS timeouts and exits 2 having written no report and — under `--quiet` —
  no output whatsoever.

  That was recorded `FAIL`, which reads as "found problems" rather than "never
  ran". Observed in production: a laptop woke, the scheduled run fired before
  the network was up, and semgrep contributed nothing to seven repositories in
  one night while the finding total simply went down and the report looked
  healthier than the day before.

  A remote ruleset is now probed first, with five seconds and the same rule
  `url_headers` follows: a failure means "could not tell", never "the code is
  fine". The check skips with a reason instead. A local ruleset needs no network
  and is never probed. As a side effect an offline run costs 2 seconds rather
  than 103.

## 1.5.0

Four defects with one shape: a check reported success while no evidence had been
produced. Three were found in a single sitting while building a scheduler on top
of this engine, which is the argument for scheduling it.

### Added

- **CodeRabbit findings now reach the report.** `PARSERS` had no
  `parse_coderabbit`, and the check ran `cr review` with no output-format flag,
  so findings landed in the raw log and nowhere else — not `findings.json`, not
  `sarif.json`, not the severity counts, not `--fail-on-severity`, not the
  baseline. A run could print ten checks passed and `Findings extracted: 0`
  while CodeRabbit had reported a real defect in that same run.

  The CLI advertises `--agent`, which emits newline-delimited JSON, and the
  check now uses it. Three things about that format are worth recording,
  because none of them are documented anywhere obvious. A finding carries
  `severity`, `fileName` and `codegenInstructions` and **no line number field**
  — the location is in prose, `"In @file.php at line 8"`. Every
  `codegenInstructions` is prefixed with the same prompt-injection guard
  paragraph, which is stripped before it can dominate the message and the
  fingerprint hashed from it. And the closing `complete` event states its own
  finding count.

  That count is reconciled against what was parsed, and a disagreement is
  itself reported as a finding. A parser that quietly read fewer would recreate
  this exact bug one level down.

  The pre-`--agent` terminal transcript is still parsed, ANSI and OSC-8
  hyperlink escapes included, so report directories already on disk still
  render. A fixture of each format is included; the plain one is a real
  23-finding review.

- **A bundled gitleaks ruleset, because the defaults are not enough here.**
  With its stock rules gitleaks scanned a repository holding a live WooCommerce
  consumer key and secret in a tracked file, across every commit, and reported
  `no leaks found`. `generic-api-key` does not fire on a `ck_`/`cs_` pair, and
  those are the most likely secret in a WooCommerce portfolio — every store
  integration and every release pipeline authenticates with one.

  `config/gitleaks-wpheka.toml` extends the defaults rather than replacing them
  and adds the two rules. Precedence mirrors phpcs: a repository's own
  `.gitleaks.toml` wins, then `WPHEKA_GITLEAKS_CONFIG`, then the bundled file.
  The ruleset in use is printed for the same reason the phpcs one is — a
  ruleset that silently drops rules is indistinguishable from a history with no
  secrets in it.

### Fixed

- **phpstan ran with PHP's default memory limit.** Inheriting 128M, a project
  carrying WordPress and WooCommerce stubs needs roughly 512M before the
  analysis completes at all. Below that a worker dies, PHPStan writes a
  *generic* error with `file_errors 0`, and because findings are an accepted
  exit code the check scored `PASS` having analysed nothing. It held for nine
  days on a real project before anyone noticed.

  `WPHEKA_PHPSTAN_MEMORY_LIMIT` defaults to 1G and is validated at startup
  exactly as `WPHEKA_PHPCS_MEMORY_LIMIT` is, and for the same reason: the value
  is interpolated into a command string. It is passed as `--memory-limit`
  rather than `php -d`, because the binary re-execs its own workers and only
  the CLI option reaches them, which is where the crash was. A generic error is
  now called out in the run output; it does not change the status, since the
  status machinery treats findings and crashes alike, so the warning makes the
  crash visible rather than scoring it. Reading `totals.errors` from
  `phpstan.json` remains the reliable check.

- **A repository's `exclude` replaced the default list instead of adding to
  it.** `merge_config` assigns lists wholesale, so naming one directory of your
  own silently dropped `vendor/`, `node_modules/`, `dist/`, `build/` and
  `.git/` along with it. phpcs then tokenised a minified JavaScript bundle
  under `node_modules` until PHP exhausted a 1 GB limit and reported `ERROR`
  having reviewed nothing — on a plugin processing live card payments, which
  consequently had no phpcs coverage at all. With the defaults restored that
  check completes in one second and reports 122 findings.

  `.wpheka-quality.yml.example` has always described these as "already excluded
  by default", so this makes the loader agree with its own documentation rather
  than changing a deliberate contract. Two repositories had already worked
  around it by re-listing the defaults by hand, which is what a usability trap
  looks like from the outside.

- **`relative_to()` corrupted paths beginning with a dot.** It ended in
  `lstrip("./")`, which takes a character set rather than a prefix, so
  `.claude/notes.md` was reported as `claude/notes.md` — a path that does not
  exist. It affected every parser; the CodeRabbit one surfaced it because it is
  the only one handed already-relative paths.

## 1.4.0

### Added

- **`i18n_pot` — the translation template must match the code.** A `.pot` goes
  stale silently, and one plugin shipped or nearly shipped a stale one three
  releases running, each time because regenerating it is a step a person has to
  remember. The check generates a fresh template and compares it with the one in
  the repository.

  Compared by **msgid, not file contents**. A `.pot` carries `#:` source
  references and a creation date that change whenever anything moves, so a
  content diff would report drift on every run — and a check that always
  complains is one people learn to ignore. Missing strings are `MEDIUM`; strings
  the code no longer contains are `LOW`, since they mislead translators without
  breaking anything.

  Honours the configured `exclude` list, so a bundled framework is not scanned
  for strings belonging to its own repository. Skips when there is no `.pot`, no
  wp-cli, or no plugin header — a repository that has never had a template is not
  failing, it simply has nothing to compare.

- **`url_headers` — the URLs a plugin advertises must resolve.** Plugin Check
  validates the donate link's syntax and that `Domain Path` names a real folder,
  but nothing checks that `Plugin URI` leads anywhere. A plugin shipped a
  `Plugin URI` returning 404 and needed a same-day patch release to correct it,
  because wordpress.org reads plugin headers from the stable tag rather than from
  trunk — so the broken link could not be fixed without a new version.

  Checks `Plugin URI`, `Author URI` and the readme's `Donate link`. Falls back to
  `GET` when a server refuses `HEAD`, which some do.

  **Offline is reported as "could not tell", never as a failure.** If no URL can
  be reached at all the check records SKIPPED rather than failing a build for
  having no network; a gate that fails on a train is a gate people stop running.
  A URL that answers with 404 while others answer 200 is a finding, because that
  is the URL being wrong rather than the network being absent.

Both default to enabled, and both were verified against the defect they exist to
catch: the pot check fails when a real string is removed from a real template,
and the URL check fails on the exact `Plugin URI` that forced the patch release.
Both return to passing when the fault is corrected.

## 1.3.3

### Fixed

- **`exclude` did nothing for plugin-check either.** 1.3.2 fixed this for phpcs;
  plugin-check walks the directory the same way and was still reporting a
  bundled framework's findings against the plugin that bundles it. Measured on
  `wpheka-request-for-quote`: six `DirectDB` findings from bundled
  `Database/Repository.php` and `Schema.php`, code reviewed in its own
  repository. After the fix, zero — while plugin-check still reports its 44
  findings on the plugin's own code, so the change narrowed the check rather
  than silencing it.

  Excludes become `--exclude-directories`. That option takes bare directory
  names, so an entry naming a file, or containing a path separator, is skipped
  rather than passed through as something plugin-check would quietly fail to
  match. The existing retry-without-`--slug` fallback now also drops
  `--exclude-directories`, since older plugin-check releases reject unknown
  options before running anything.

  Two tools ignoring the same configuration key, found a few hours apart,
  suggests the key needs a single place that applies it rather than each check
  remembering to.

## 1.3.2

### Fixed

- **`exclude` did nothing for phpcs.** The `php_syntax` check builds a file list
  and filters it against `exclude`; phpcs was handed the directory instead and
  walked straight through it. A plugin bundling code it does not own — a
  framework reviewed in its own repository, under its own ruleset — had that
  code's findings reported against the plugin.

  Measured on a plugin bundling the WPHEKA framework: 109 findings, 23 of them
  from `framework/`, every one a `WordPress.Files.FileName` sniff firing on
  deliberately PSR-4 filenames. After the fix, 86 findings and none from
  `framework/`, with the plugin's own 78 phpcs findings untouched.

  Noise like that is worse than it looks. It is not merely 23 rows to scroll
  past: it teaches the reader that this report contains findings they are
  supposed to ignore, which is the habit that loses the twenty-fourth.

  Excludes are translated into phpcs `--ignore` patterns, two per entry, since
  an exclude may name a directory or a single file. An entry containing a comma
  is dropped rather than passed through, because phpcs would read the comma as a
  pattern separator and split it into two wrong patterns.

## 1.3.1

### Fixed

- **A rate-limited CodeRabbit review was reported as `FAIL`.** The CLI exits 1
  both when it finds problems and when it refuses to review at all, so "you have
  used all 3 included reviews" was recorded as a defect nobody found. Any gate
  running more than three times an hour then went red for a reason that was not
  the code — and a permanently red check teaches people to ignore red exactly as
  a permanently skipped one teaches them to ignore the skip list.

  A tool that declined to review has not passed and has not failed. It is now
  recorded as `SKIPPED` with the reason, which is the status this engine already
  uses for "that area is unreviewed", and the summary lists it among the
  unreviewed areas.

  `run_check()` takes an optional log pattern and reason for this. Status still
  comes from exit codes everywhere it can — but a tool that reports "rate
  limited" and "I found bugs" with the same code leaves no other signal, and
  calling the first one FAIL asserts a verdict nobody produced.

## 1.3.0

Nine defects found by auditing this engine against its sibling. Each was
reproduced here before being fixed.

### Security

- **Command injection via three environment variables.**
  `WPHEKA_PHPCS_STANDARD` was interpolated unquoted; `WPHEKA_SEMGREP_CONFIG` was
  wrapped in single quotes, which a quote inside the value simply ends;
  `WPHEKA_WP_PATH` had the same shape but was not reachable in practice. All
  three are now validated at startup.

  The 1.2.1 memory-limit guard fixed one variable. This is the same defect at
  the interpolation point two lines above it, which is why the rule is now an
  invariant in `CONTRIBUTING.md` rather than a note about one value: every
  value interpolated into a command string is shell input, validated at
  startup, never at the point of use.

  These are operator input rather than repository input, so the exposure is
  smaller than the config path the engine already defends. It matters where the
  environment comes from CI configuration, a shared profile or a wrapper
  script.

### Fixed

- **A phpcs that never ran was reported as `FAIL`.** `run_check` filed every
  non-pass exit code as a failure, so phpcs dying on a bad standard (exit 3 on
  3.x, 16 on 4.x) or on a PHP fatal (255, usually memory exhaustion) was
  recorded as having read the code and reached a verdict.

  This is the engine's central promise broken by its own runner, and the
  comment above the phpcs block already said 3 and 16 mean the tool did not
  run — the knowledge was in the comment and not in the code. `run_check` now
  takes an optional list of exit codes meaning "could not complete", recorded
  as `ERROR` and reported under "Unreviewed areas". The list is per-check
  because exit 255 from `php -l` is a genuine parse error.

- **`plugin_check` reported text domain mismatches that were not real.**
  plugin-check reads the target argument as the plugin slug, so passing `.`
  made it expect every text domain to equal `.`. On a real plugin that was 23
  of 55 findings, every one an artifact, and indistinguishable from a genuine
  i18n defect. The slug is now stated explicitly.

  Verified that nothing is lost: the non-i18n findings are unchanged, and one
  legitimate finding appears that could not be evaluated before — the
  wordpress.org restricted-term rule applied to the slug itself. Older
  plugin-check releases without `--slug` are detected and retried without it.

- **`coderabbit` could pass having reviewed nothing.** The gate used a snapshot
  that lists untracked files, which `cr review --uncommitted` cannot see, so an
  untracked file was enough to start a review of zero lines that exited 0 and
  recorded `PASS`. It now gates on `git diff --quiet HEAD`, which is what that
  command actually reviews.

- **`--all-sniffs` was accepted and silently ignored** on a repository carrying
  its own `phpcs.xml`. The precedence is deliberate and stays; the silence does
  not. Both `--all-sniffs` and `WPHEKA_PHPCS_STANDARD` now warn when the
  repository's ruleset wins.

- **`plugin_check` blamed the wrong thing when WordPress could not boot.**
  `wp cli has-command` fails identically whether plugin-check is inactive or
  the database is unreachable, and the skip reason sent people to reinstall a
  plugin they already had. `core is-installed` is probed first.

### Testing

- The suite aborts when `WPHEKA_TESTS_REQUIRE_PHPCS=1` and no phpcs is found,
  which both CI jobs now set. `skipped 'phpcs not installed'` reads in a CI
  summary exactly like passing, so a broken install step would otherwise stop
  the phpcs tests running while the job stayed green.
- Regression tests for the injections, the `ERROR` status, the `--all-sniffs`
  warning and the coderabbit gate.

57 tests, green on Ubuntu, macOS, and Python 3.8.

### Upgrading

`plugin_check` output changes: text domain findings caused by the old
invocation disappear, and one restricted-term finding may appear. Regenerate
baselines that include plugin_check findings.

A phpcs that fails to start now reports `ERROR` rather than `FAIL`. Both fail
the run under the default `--fail-on error`, so gating is unaffected.

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
