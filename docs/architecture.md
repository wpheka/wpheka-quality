# Architecture

## 1. Deterministic engine

`bin/wpheka-quality` — bash 3.2 compatible, so it runs on a stock macOS shell.

Responsibilities:

- resolve configuration and profiles
- capture environment and tool versions
- execute objective checks under a timeout, with the repository as cwd
- preserve raw output
- record `PASS` / `FAIL` / `TIMEOUT` / `ERROR` / `SKIPPED` / `WARNING`
- verify the working tree was not mutated
- delegate rendering, then apply the exit gates

It performs no AI reasoning and makes no judgment about whether a finding is
real.

### Invariants

These are the properties the tests exist to protect:

1. **A check's status comes from its exit code**, never from grepping its log
   for an error string. Log scraping cannot distinguish "no errors" from
   "the tool never ran".
2. **Every check runs with the repository as its working directory.** Relative
   paths from `git ls-files` are only meaningful there.
3. **A tool that could not run does not pass.** `TIMEOUT` and `ERROR` exist so
   that outcome has somewhere to go besides `PASS`.
4. **Configuration is data.** It is never `eval`'d, and names are validated
   against a fixed pattern before export.
5. **Every skip records a reason**, and skipped checks are surfaced as
   unreviewed areas rather than folded into a clean total.

## 2. Report renderer

`bin/render-report.py` — standard library only, Python 3.8+.

Responsibilities:

- parse each tool's native report into a common finding shape
- normalise severity across tools onto one scale
- merge findings that describe the same defect at the same location
- apply the baseline
- emit `summary.md`, `full-review.md`, `findings.json`, `results.json`,
  `sarif.json` and a self-contained `report.html`

Findings are re-derived deterministically from the run directory, so an old run
can be re-rendered after a parser improvement without re-running any tool. Run
metadata (timestamps, current git branch and commit) is regenerated at render
time and will differ between renders of the same run.

### Why the parsers are defensive

Tool output is untrusted input in two senses. It can be malformed — a truncated
JSON file from a killed process — and it can contain hostile content, since it
quotes source from the repository under review. A parser that throws must not
lose the other tools' findings, and a log containing `</script>` must not break
out of the HTML report.

## 3. Configuration loader

`bin/config-loader.py`

Uses PyYAML when installed and falls back to a parser covering the documented
subset: nested maps, block sequences, scalars, comments. The fallback raises on
input it cannot represent, so a typo surfaces as an error rather than as a
silently dropped setting.

Output for the shell is `--format env0`: NUL-delimited `KEY=VALUE` pairs. The
runner reads them in a loop and validates each name. Nothing is `eval`'d.

Precedence, later winning:

1. built-in defaults
2. profile preset (`config/profiles/*.yml`)
3. config file (`--config`, or `.wpheka-quality.yml` in the repo)
4. `--only` / `--skip` / other CLI flags

## 4. Agent Skill

`skills/wpheka-quality/SKILL.md`

Responsibilities:

- understand project context
- inspect findings and failing checks
- trace source-level behaviour
- validate WordPress/WooCommerce semantics
- correlate duplicates the renderer could not
- classify, prioritise, and produce the final review

## 5. AGENTS.md

Standing rules and boundaries for any agent operating in the repository.

## 6. Why this separation

Linters are good at objective, local patterns. An AI agent is good at context
and cross-file reasoning. Neither replaces the other.

The deterministic layer produces evidence that is reproducible and auditable.
The Skill produces judgment, which is neither — so it must always be traceable
back to the evidence. Keeping them apart is what makes it possible to tell
which is which when reading a report.
