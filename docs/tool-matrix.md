# Tool matrix

No single tool is sufficient for WordPress/WooCommerce work. The value comes
from combining orthogonal signals, then verifying the ones that matter.

| Tool | What it catches | What decides its status | Default |
|---|---|---|---|
| `php -l` | Parse/syntax failures | exit code, per file | Yes |
| `git diff --check` | Whitespace damage, conflict markers | exit code | Yes |
| PHPCS/WPCS | WordPress standards, escaping, i18n, common misuse | exit 3 = tool failure | Yes |
| PHPStan | Type and control-flow defects | exit code | Needs `phpstan.neon` |
| PHPUnit | Behavioural regressions | exit code + JUnit XML | Needs `phpunit.xml` |
| Composer validate | Package metadata problems | exit code | Needs `composer.json` |
| Composer audit | Known dependency vulnerabilities | exit code | Needs `composer.lock` |
| Semgrep | Security and data-flow patterns | exit code | If installed |
| Gitleaks | Secrets in history and worktree | exit code | If installed |
| Plugin Check | wordpress.org requirements | exit code | Needs a WP install |
| npm lint/test | Blocks and frontend checks | exit code | Opt-in |
| CodeRabbit | Contextual AI review | exit code | If installed |
| Agent Skill | Cross-tool verification and prioritisation | judgment | Yes |

## Status vocabulary

| Status | Meaning |
|---|---|
| `PASS` | The tool ran and its exit code was acceptable |
| `FAIL` | The tool ran and reported failure |
| `TIMEOUT` | The tool exceeded its limit and was terminated — it proved nothing |
| `ERROR` | The tool could not run, or its inputs were unusable |
| `SKIPPED` | Not applicable or not installed; the reason is always recorded |
| `WARNING` | Ran, but something about the run needs attention |

`TIMEOUT` and `ERROR` are deliberately distinct from both `PASS` and `FAIL`.
A check that never completed is not evidence of anything, and collapsing it
into either bucket is how a broken run gets mistaken for a clean one.

## Which tools produce structured findings

These write a machine-readable report that is parsed into `findings.json`:

| Tool | Report file | Notes |
|---|---|---|
| PHPCS | `phpcs.json` | One run emits both the human log and the JSON |
| PHPStan | `phpstan.json` | |
| Semgrep | `semgrep.json` | |
| Gitleaks | `gitleaks.json` | The secret value itself is never copied out |
| Composer audit | `composer-audit.json` | Advisories and abandoned packages |
| Plugin Check | `plugin-check.json` | Per-file JSON blocks, not one document |
| PHPUnit | `phpunit-junit.xml` | Failures become findings so they rank alongside the rest |

`git diff --check`, `composer validate` and `npm` contribute status only.

## Severity normalisation

Each tool has its own vocabulary. They are mapped onto one scale so results
rank consistently:

| Reported | Normalised |
|---|---|
| `BLOCKER` | `CRITICAL` |
| `ERROR`, `MAJOR` | `HIGH` |
| `WARNING`, `WARN` | `MEDIUM` |
| `NOTICE`, `MINOR` | `LOW` |
| `INFO`, `INFORMATIONAL`, `NONE` | `INFO` |
| Gitleaks findings | `CRITICAL` (always) |

Without this, a semgrep `ERROR` and a phpcs `ERROR` would sort differently for
no reason other than which tool found them.

## Correlation

Findings from different tools at the same file, line and message are merged.
The survivor keeps the highest severity and lists every reporting tool, so two
tools agreeing reads as corroboration rather than as two separate problems.

PHPCS and Plugin Check overlap heavily — Plugin Check runs WPCS sniffs
internally — so this typically merges a large fraction of their output.

## Overriding a tool's invocation

A `commands:` entry replaces the engine's command line entirely; nothing is
appended. Write your own machine-readable report to `$WPHEKA_RAW_DIR` to keep
structured findings:

```yaml
commands:
  phpcs: "vendor/bin/phpcs -q --report-full --report-json=$WPHEKA_RAW_DIR/phpcs.json ."
```

A `commands:` block inside the reviewed repository is ignored unless
`--allow-repo-commands` is passed. See [security-notes.md](security-notes.md).
