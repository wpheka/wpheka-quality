# Overnight review

## Use a dedicated worktree

An overnight run should never share a working tree with active development. A
tool that reads files while you edit them produces findings against code that
no longer exists.

```bash
git worktree add ../project-quality-review HEAD
wpheka-quality --repo ../project-quality-review --profile woocommerce-plugin
```

The `repository_integrity` check confirms the tree was unchanged during the
run. In a shared worktree it reports `WARNING`, and every finding in that run
should be treated as provisional.

## Nightly wrapper

```bash
/path/to/wpheka-quality/bin/nightly-review.sh ../project-quality-review
```

## CodeRabbit modes

Uncommitted work:

```bash
export WPHEKA_CODERABBIT_MODE=uncommitted
```

Branch comparison:

```bash
export WPHEKA_CODERABBIT_MODE=base
export WPHEKA_CODERABBIT_BASE=main   # auto-detects main/master/develop if unset
```

CodeRabbit is rate limited. When a run hits the limit the engine warns, because
a truncated review that looks clean is worse than no review.

## Timeouts

An unattended run must not hang until morning on one stuck tool. Every check is
bounded; raise limits for genuinely slow suites rather than removing them:

```yaml
timeouts:
  default: 900
  phpunit: 3600
  semgrep: 1800
  coderabbit: 1800
```

A check that hits its limit is recorded as `TIMEOUT` — distinct from both a
pass and a failure, because it produced no evidence either way.

## Interrupting

`Ctrl-C` writes a partial report from the checks that finished and exits 130.
The report is still valid for those checks; the rest are simply absent.

## Before leaving it running

- The machine must stay awake. `caffeinate -i wpheka-quality --repo ...` on macOS.
- Confirm the toolchain first: `wpheka-quality --doctor`. A run with nothing
  installed exits 0 having reviewed nothing.
- Do not give an overnight agent production credentials.
- The engine does not modify source, commit, push or deploy.
