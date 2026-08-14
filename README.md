# WPHEKA Quality

[![Self test](https://github.com/wpheka/wpheka-quality/actions/workflows/self-test.yml/badge.svg)](https://github.com/wpheka/wpheka-quality/actions/workflows/self-test.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

A review-first quality and AI-review system for WordPress, WooCommerce plugin
and theme repositories.

Runs the checks you would run by hand — `php -l`, PHPCS/WPCS, PHPStan, PHPUnit,
Composer audit, Semgrep, Gitleaks, Plugin Check, CodeRabbit — collects their
output into one normalised, deduplicated report, and refuses to call anything a
pass that did not actually run.

## What this is

Three separate responsibilities, deliberately not merged:

1. **Deterministic engine** (`bin/wpheka-quality`) — runs objective checks,
   parses structured findings, preserves raw evidence. No judgment.
2. **Agent Skill** (`skills/wpheka-quality/SKILL.md`) — asks an AI agent to
   investigate, verify against source, correlate and prioritise. No new evidence.
3. **Configuration** (`.wpheka-quality.yml`, `config/profiles/*.yml`) —
   project-specific facts and boundaries.

Linters are good at objective patterns; an AI agent is good at context and
cross-file reasoning. Neither replaces the other, and neither should be trusted
to declare a bug on its own.

## Design rules

- **A missing tool is SKIPPED, never PASS.** Every skip records why, and the
  summary lists skipped checks as unreviewed areas rather than burying them.
- **A check that could not run does not pass.** `TIMEOUT` and `ERROR` are
  distinct statuses; neither is treated as a clean result.
- **Status comes from exit codes, not log scraping.** Pattern-matching a log for
  "Parse error" cannot tell a clean run from a broken one.
- **The repository under review is untrusted input.** See
  [docs/security-notes.md](docs/security-notes.md).
- **Review-only.** The engine never fixes, installs, commits, pushes or deploys.

## Requirements

- bash 3.2+ (the macOS system shell is fine)
- python3 3.8+ (standard library only; PyYAML is used if present, not required)
- git

Every check tool is optional. Absent tools are skipped with a reason.

## Usage

```bash
# Audit the current directory
bin/wpheka-quality --repo .

# Use a profile preset
bin/wpheka-quality --repo . --profile woocommerce-plugin

# See what would run, without running it
bin/wpheka-quality --repo . --list-checks

# Run one check
bin/wpheka-quality --repo . --only phpcs

# CI gate: fail on any HIGH or worse finding
bin/wpheka-quality --repo . --fail-on-severity high --format json
```

### Options

| Flag | Description |
|---|---|
| `--repo PATH` | Target repository (default: cwd) |
| `--config FILE` | Explicit config file. Trusted: its `commands:` block is honoured |
| `--profile NAME` | `woocommerce-plugin`, `wordpress-plugin`, `wordpress-theme` |
| `--only LIST` | Comma-separated checks to run exclusively |
| `--skip LIST` | Comma-separated checks to skip |
| `--fail-on LEVEL` | Exit non-zero on check status: `none`, `error`, `warning` |
| `--fail-on-severity SEV` | Exit non-zero on findings at/above `critical`, `high`, `medium`, `low`, `none` |
| `--baseline FILE` | Suppress findings recorded in a baseline |
| `--write-baseline` | Record the current findings to `--baseline` and exit 0 |
| `--jobs N` | Parallelism for file-level checks (default: CPU count) |
| `--all-sniffs` | Report every WPCS sniff, including whitespace and layout |
| `--allow-repo-commands` | Honour a `commands:` block in the target repo's own config |
| `--format` | Terminal output: `text`, `json`, `html` |
| `--output-dir PATH` | Report directory |
| `--list-checks` / `--dry-run` | Print the resolved plan and exit |
| `--no-color`, `--quiet`, `-v`, `-h` | |

Both `--flag value` and `--flag=value` are accepted.

### Exit codes

| Code | Meaning |
|---|---|
| 0 | Gate satisfied |
| 1 | A check failed, or a finding met `--fail-on-severity` |
| 2 | Usage or configuration error |
| 3 | Report rendering failed |
| 130 | Interrupted — a partial report is still written |

## Adopting on an existing repository

A legacy codebase will produce thousands of standards findings on day one.
Record them, then gate only on what is new:

```bash
bin/wpheka-quality --repo . --baseline .wpheka-baseline.json --write-baseline
git add .wpheka-baseline.json

# From now on, only new findings surface
bin/wpheka-quality --repo . --baseline .wpheka-baseline.json
```

Fingerprints exclude line numbers and normalise digits in messages, so a
finding survives unrelated edits that shift it up or down the file.

## Checks

| Check | Runs when | Status source |
|---|---|---|
| `php_syntax` | `php` present and PHP files tracked | `php -l` exit code |
| `git_diff_check` | Inside a git work tree | `git diff --check` |
| `phpcs` | phpcs on PATH or in `vendor/bin` | 0/1/2 = ran; anything else = tool failure |
| `phpstan` | phpstan present **and** a `phpstan.neon(.dist)` exists | exit code |
| `phpunit` | phpunit present **and** a `phpunit.xml(.dist)` exists | exit code |
| `composer_validate` | `composer.json` present | exit code |
| `composer_audit` | `composer.lock` present | exit code |
| `semgrep` | semgrep on PATH | exit code |
| `gitleaks` | gitleaks on PATH | exit code |
| `plugin_check` | wp-cli with plugin-check, target is a plugin dir | exit code |
| `npm_lint` / `npm_test` | Opt-in, script exists, `node_modules` present | exit code |
| `coderabbit` | `cr` or `coderabbit` on PATH | exit code |
| `repository_integrity` | Inside a git work tree | working tree diff |

`php_syntax` runs from the repository root over a NUL-delimited file list, so
paths containing spaces are handled, and a file that cannot be opened is an
`ERROR` rather than a silent pass.

## Report output

```text
.wpheka-quality-reports/YYYY-MM-DD-HHMMSS-PID/
├── summary.md          # status counts, skipped checks, failing checks
├── full-review.md      # sectioned template for the reviewing agent
├── report.html         # standalone dashboard; no network requests
├── results.json        # per-check status, exit code and reason
├── findings.json       # deduped, severity-normalised findings
├── sarif.json          # SARIF 2.1.0 for CI code scanning
├── environment.json    # engine, python and tool versions
├── repository.json     # branch and commit
└── tool-results/       # raw logs and every tool's native report
```

Findings from different tools that describe the same defect at the same
location are merged; the survivor keeps the highest severity and lists every
reporting tool, so corroboration is visible rather than counted twice.

Severities are normalised across tools onto `CRITICAL | HIGH | MEDIUM | LOW |
INFO`, so a semgrep `ERROR` and a phpcs `ERROR` rank consistently.

Checks that produced **no verdict** — `SKIPPED`, `ERROR`, `TIMEOUT` — are listed
under "Unreviewed areas" rather than beside checks that found problems. A tool
that was killed halfway did not review your code, and should not read as though
it did.

## phpcs rulesets

By default the engine runs phpcs with formatting-only sniffs excluded
(`config/phpcs-default.xml`). Measured on a real plugin, the full WordPress
standard produced 1427 findings, of which ~1200 were indentation and bracket
spacing; the default ruleset reports 157 and loses none of the 49 security and
correctness findings buried underneath.

| Situation | Ruleset used |
|---|---|
| Repository ships `phpcs.xml(.dist)` or `.phpcs.xml(.dist)` | The repository's own, always |
| `WPHEKA_PHPCS_STANDARD` set | That standard |
| `--all-sniffs` | `config/phpcs-all-sniffs.xml` — WPCS untouched |
| Otherwise | `config/phpcs-default.xml` |

Which one was used is printed beside the check, because a ruleset that silently
drops sniffs is indistinguishable from a codebase with no problems.

Use `--all-sniffs` when the question is conformance — a wordpress.org
submission, or a formatting pass with phpcbf. Do not use it to hunt for defects.

phpcs runs with `memory_limit=1G` (`WPHEKA_PHPCS_MEMORY_LIMIT` to change it);
PHP's default exhausts on a large tree and surfaces as a fatal error rather
than as findings.

## CI

`sarif.json` uploads to GitHub code scanning directly. See
[docs/ci.md](docs/ci.md) for a working workflow.

## Running the tests

```bash
python3 tests/test_runner.py            # all
python3 tests/test_runner.py -k syntax  # subset
```

Tests named `test_regression_*` pin defects found in the pre-1.0 engine while
hardening it for release. They exist so those failures cannot return quietly.

## Installation

```bash
git clone https://github.com/wpheka/wpheka-quality.git
cd wpheka-quality

# Install the check tools and link the CLI into ~/.local/bin.
# Idempotent: anything already present is left alone.
./bin/install-tools.sh

# Confirm what is available. A run with nothing installed exits 0
# having reviewed nothing.
wpheka-quality --doctor
```

Works on macOS and Linux. `--dry-run` shows what would be installed without
installing it; `--no-link` skips the symlink.

To install only the Agent Skill:

```bash
./bin/install-global-skill.sh   # installs to ~/.agents/skills/wpheka-quality/
```

For a repository-local variant, place the Skill at
`.agents/skills/wpheka-quality/SKILL.md`.

## Documentation

| Document | Covers |
|---|---|
| [docs/ci.md](docs/ci.md) | GitHub Actions, exit codes, baselines |
| [docs/tool-matrix.md](docs/tool-matrix.md) | Each tool, its status source, severity mapping |
| [docs/architecture.md](docs/architecture.md) | How the pieces fit and why they are separate |
| [docs/security-notes.md](docs/security-notes.md) | The engine's own threat model |
| [docs/overnight.md](docs/overnight.md) | Unattended runs |
| [docs/wordpress-notes.md](docs/wordpress-notes.md) | What the linters miss in WordPress code |
| [docs/woocommerce-notes.md](docs/woocommerce-notes.md) | HPOS, Blocks, gateways, order lifecycle |

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). The engine targets bash 3.2 and Python
3.8, and every bug fix ships with a `test_regression_*` test.

To report a vulnerability, see [SECURITY.md](SECURITY.md).

## License

MIT. See [LICENSE](LICENSE).

## Safety model

Review-only by default. The engine does not modify source, run PHPCBF or other
fixers, update or install dependencies, commit, push, deploy, or change
production configuration.
