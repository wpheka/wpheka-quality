# WordPress review notes

Areas the deterministic tools cover only partially, and where the reviewing
agent has to look at the source.

## What the linters do catch

PHPCS with WPCS covers escaping, sanitization, i18n, prepared statements and
naming. Plugin Check adds wordpress.org distribution requirements. Treat both
as coverage of *form*, not of *behaviour*.

## What they do not catch

- **Hook timing** — a callback registered after the action already fired never
  runs, and nothing flags it.
- **Duplicate registration** — the same callback added twice, or a hook added
  inside a function that runs more than once.
- **Deprecated APIs** — still valid PHP, still parses, still passes sniffs.
- **Options API misuse** — autoloaded options holding large payloads, or a
  value written on every request.
- **Transients and cache** — a transient with no expiry, or a cache key that
  varies per request and never hits.
- **Cron** — an event scheduled but never unscheduled on deactivation, or work
  that exceeds a request's time budget.
- **Admin boundaries** — capability checks that use the wrong capability, or
  that guard the UI but not the handler.
- **Multisite** — assumptions about a single site, `switch_to_blog` without a
  matching restore, network-level options.
- **Enqueue and dependencies** — missing dependency declarations, scripts
  enqueued on every screen, missing version strings that break caching.
- **Direct SQL** — correct escaping but wrong semantics: unindexed lookups,
  queries in loops, `posts_per_page => -1`.
- **Object cache behaviour** — code correct with no persistent cache and wrong
  with one, or the reverse.

## Uninstall and lifecycle

Activation, deactivation and uninstall paths are rarely exercised and rarely
tested. Check that uninstall removes what it created, and that deactivation
does not remove data a user expects to survive a plugin update.

## Internationalization

Text domain mismatches are caught by sniffs, but not:

- a translated string built by concatenation, which cannot be translated properly
- placeholders reordered in a translation
- `load_plugin_textdomain` called at the wrong hook
