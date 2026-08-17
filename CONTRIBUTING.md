# Contributing

## Running the tests

```bash
python3 tests/test_runner.py            # all
python3 tests/test_runner.py -k syntax  # subset
```

The suite needs `php` and `git`; integration tests skip themselves without
them. Everything else is optional and exercised through fixtures.

## The invariants

These are what the engine is *for*. A change that breaks one is a bug even if
the tests pass, so add a test rather than adjust one:

1. A check's status comes from its exit code, never from grepping its log.
   Log scraping cannot distinguish "no errors" from "the tool never ran".
2. Every check runs with the target repository as its working directory.
3. A tool that could not run reports `TIMEOUT` or `ERROR`, never `PASS`.
4. Configuration is data. It is never `eval`'d.
5. A `commands:` block from the reviewed repository requires explicit opt-in.
6. Every skip records a reason.
7. Tool output embedded in `report.html` cannot escape its context.
8. Secret values discovered by scanners never reach any artifact.
9. Every value interpolated into a command string is shell input, and is
   validated at startup rather than at the point of use. Single-quoting an
   interpolation is not validation: a quote inside the value ends the quoted
   context. Validating at the point of use also means the guard vanishes
   whenever that branch is skipped.

## Regression tests

Any bug fix gets a test named `test_regression_*`, with a comment saying what
used to happen. The existing ones read like this:

```python
def test_regression_syntax_error_is_caught_from_a_foreign_cwd(self):
    # The pre-1.0 engine fed repo-relative paths to `php -l` running in the caller's cwd.
    # Every file failed to open, no "Parse error" text was produced, and the
    # check reported PASS on a repository that could not even parse.
```

The comment is the point. It stops someone "simplifying" the fix later.

## Portability

The engine targets **bash 3.2** (the macOS system shell) and **Python 3.8**:

- no associative arrays, `mapfile` or `readarray`
- no `readlink -f`, `sed -i` without an argument, or other GNU-only flags
- no reliance on coreutils `timeout` (there is a fallback watchdog)
- standard library only in Python; PyYAML is used if present, never required

CI runs the suite on both Ubuntu and macOS for this reason.

## Adding a check

1. Add it to `ALL_CHECKS` and to `KNOWN_CHECKS` in `bin/config-loader.py`.
2. Gate it on `check_enabled`, and `skip` with a reason in every path where it
   cannot run.
3. Decide its status from the exit code. If the tool uses a non-standard
   convention, pass explicit `pass_codes` and comment what they mean.
4. If it emits a machine-readable report, add a parser in
   `bin/render-report.py` and normalise its severity vocabulary.
5. Document it in `docs/tool-matrix.md`.
6. Add tests, including one for the "tool is absent" path.

## Style

Match the surrounding code. Comments explain *why*, not what — most of the
existing ones record a failure mode that is not obvious from the code.
