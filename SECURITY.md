# Security policy

## Reporting a vulnerability

Report privately via [GitHub Security Advisories](https://github.com/wpheka/wpheka-quality/security/advisories/new).
Please do not open a public issue for a vulnerability.

Include what you have: affected version, reproduction steps, and impact. A
proof of concept helps but is not required.

## Scope

This tool runs other programs against repositories that may be untrusted. The
security-relevant surface is:

- **Configuration handling** — a config file must never achieve code execution
  outside the documented `commands:` opt-in.
- **Report generation** — tool output is embedded in `report.html`; it must not
  be able to escape its context.
- **Secret handling** — values discovered by scanners must never reach any
  generated artifact.
- **Command construction** — repository-controlled data must not be able to
  inject into a check's command line.

In scope: anything letting a reviewed repository execute code, exfiltrate data,
or read files outside itself, without the operator opting in.

Out of scope: findings from the tools this engine drives (report those
upstream), and the documented behaviour of `--allow-repo-commands`, which
executes repository-authored commands by design.

## Threat model

**A repository under review is untrusted input.** You may be auditing a client
handoff, a contractor's branch, or code you have never read.

Concretely:

- A `commands:` block inside the reviewed repository is ignored unless
  `--allow-repo-commands` is passed.
- Configuration is parsed into NUL-delimited pairs and never `eval`'d.
- `report.html` makes no network requests and escapes embedded tool output.
- Gitleaks findings carry the rule and location; the secret value is dropped.
- Every check runs under a timeout.

See [docs/security-notes.md](docs/security-notes.md) for detail, including the
known limits — chiefly that checks execute tools which may themselves live in
the repository under review.

## Supported versions

The latest minor release receives security fixes.
