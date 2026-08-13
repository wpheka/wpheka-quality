# Running in CI

The engine is designed to be a gate: it exits non-zero when the configured
threshold is crossed, and writes SARIF that GitHub ingests as code scanning
alerts.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Gate satisfied |
| 1 | A check failed, or a finding met `--fail-on-severity` |
| 2 | Usage or configuration error |
| 3 | Report rendering failed |
| 130 | Interrupted; a partial report is still written |

Two independent gates, both active at once:

- `--fail-on` looks at **check status**. `error` (default) fails on
  `FAIL`/`TIMEOUT`/`ERROR`; `warning` also fails on `WARNING`; `none` never fails.
- `--fail-on-severity` looks at **finding severity**. Default `critical`.

Both exist because a tool can exit 0 while reporting something serious. phpcs
and semgrep report findings without failing; only the severity gate catches
those.

## Skipped checks are not passes

A missing tool produces `SKIPPED`, which does not fail the build. That is
deliberate — but it means a CI job with no tools installed exits 0 while
reviewing nothing.

Verify the toolchain in CI before trusting a green run:

```bash
wpheka-quality --doctor
```

## GitHub Actions

```yaml
name: Quality

on:
  pull_request:
  push:
    branches: [main, master]

permissions:
  contents: read
  security-events: write   # required to upload SARIF

jobs:
  quality:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5
        with:
          fetch-depth: 0   # gitleaks scans history; a shallow clone hides it

      - name: Check out the quality engine
        uses: actions/checkout@v5
        with:
          repository: wpheka/wpheka-quality
          ref: v1.1.0            # pin a tag; the engine is a gate, not a moving target
          path: wpheka-quality

      - uses: shivammathur/setup-php@v2
        with:
          php-version: '8.1'
          tools: composer, phpstan

      - name: Install WordPress coding standards
        run: |
          # Composer 2.2+ blocks plugins unless allowed, and this one is what
          # registers WPCS with phpcs. Without it phpcs runs against PSR
          # defaults and quietly reports the wrong things.
          composer global config --no-plugins \
            allow-plugins.dealerdirect/phpcodesniffer-composer-installer true
          composer global require --no-interaction --no-progress \
            squizlabs/php_codesniffer \
            wp-coding-standards/wpcs \
            dealerdirect/phpcodesniffer-composer-installer
          echo "$(composer global config bin-dir --absolute)" >> "$GITHUB_PATH"
          phpcs -i | grep -q WordPress   # fail loudly if the standard is missing

      - name: Install scanners
        run: |
          python3 -m pip install --quiet semgrep
          GITLEAKS_VERSION=8.30.1
          curl -sSfL "https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}/gitleaks_${GITLEAKS_VERSION}_linux_x64.tar.gz" \
            | tar -xz -C /usr/local/bin gitleaks

      - name: Check toolchain
        run: wpheka-quality/bin/wpheka-quality --doctor

      - name: Run quality review
        run: |
          wpheka-quality/bin/wpheka-quality \
            --repo . \
            --profile woocommerce-plugin \
            --baseline .wpheka-baseline.json \
            --fail-on-severity high \
            --output-dir "$RUNNER_TEMP/quality" \
            --no-color

      - name: Upload SARIF
        if: always()
        uses: github/codeql-action/upload-sarif@v3
        with:
          sarif_file: ${{ runner.temp }}/quality/sarif.json

      - name: Upload full report
        if: always()
        uses: actions/upload-artifact@v5
        with:
          name: quality-report
          path: ${{ runner.temp }}/quality/
```

`if: always()` on both upload steps matters: the run you most want the report
from is the one that failed.

## Adopting on an existing repository

An established codebase produces thousands of standards findings on the first
run. Gating on all of them at once is not actionable. Record them once, commit
the baseline, and gate only on what is new:

```bash
wpheka-quality --repo . --baseline .wpheka-baseline.json --write-baseline
git add .wpheka-baseline.json
git commit -m "Record quality baseline"
```

Fingerprints exclude line numbers and normalise digits in messages, so a
finding survives edits that move it within its file. Moving it to a *different*
file, or changing its message, produces a new fingerprint — which is usually
what you want.

Shrink the baseline as debt is paid down; regenerating it wholesale hides
regressions, so prefer removing entries over rewriting the file.

## Timeouts

Every check is bounded (`--list-checks` shows the resolved values). A hung tool
is recorded as `TIMEOUT` and fails the run under the default `--fail-on error`,
rather than hanging the job until the CI-level timeout kills it and loses the
report.

Tune per check when a suite is genuinely slow:

```yaml
timeouts:
  default: 900
  phpunit: 3600
  semgrep: 1800
```

## Notes on individual tools

- **semgrep**: the engine uses `--config p/php --metrics=off`. `--config auto`
  requires metrics upload, which sends data about the scanned code to a third
  party. Override with `WPHEKA_SEMGREP_CONFIG` or a `.semgrep.yml` in the repo.
- **plugin_check**: requires a WordPress installation, found by walking up from
  the plugin directory or via `WPHEKA_WP_PATH`. In CI, install WordPress or
  leave this check skipped.
- **coderabbit**: rate limited and needs authentication. Usually better left to
  local or nightly runs than per-PR CI; the engine warns when it detects a rate
  limit response so its partial output is not mistaken for a clean review.
- **npm_lint / npm_test**: opt-in, because they execute scripts the repository
  authored. Also skipped when `node_modules` is absent; the engine never
  installs dependencies.
