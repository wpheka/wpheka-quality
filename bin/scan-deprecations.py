#!/usr/bin/env python3
"""Find calls to functions WordPress or WooCommerce have deprecated.

The gap this fills, named in docs/wordpress-notes.md under what the other checks
do not catch: a deprecated call is still valid PHP, still parses, still passes
every sniff, and keeps working right up until the release that removes it. On a
payment gateway running on live stores, that release is a fatal error during
checkout.

The corpus is built from the WordPress and WooCommerce installed on this machine,
not from a list shipped here. A hardcoded list is wrong the day after it is
written; reading the installed copies means the corpus refreshes itself whenever
those update, and it describes the versions actually in use rather than whichever
ones someone had when they wrote the list down.

Both projects mark a deprecation in the function that is being deprecated:

    _deprecated_function( __FUNCTION__, '1.5.1', 'get_post()' );      WordPress
    wc_deprecated_function( 'woocommerce_show_messages', '2.1', ... ); WooCommerce

so the version and the suggested replacement come out with the name, which is
what makes a finding actionable rather than merely true.
"""

import argparse
import json
import os
import pathlib
import re
import sys

# Where each project keeps its deprecations, relative to a WordPress root.
WORDPRESS_SOURCES = (
    "wp-includes/deprecated.php",
    "wp-includes/ms-deprecated.php",
    "wp-includes/pluggable-deprecated.php",
    "wp-admin/includes/deprecated.php",
)
WOOCOMMERCE_SOURCES = (
    "wp-content/plugins/woocommerce/includes/wc-deprecated-functions.php",
)

# Modifiers included on purpose. Requiring `function` at the start of the line
# missed every `public function get_settings(...)`, so method definitions were
# reported as calls to the WordPress function of the same name -- three findings
# on this portfolio, all three wrong.
FUNCTION_DEF_RE = re.compile(
    r"^[ \t]*(?P<modifiers>(?:(?:public|protected|private|static|final|abstract)[ \t]+)*)"
    r"function[ \t]+&?(?P<name>[a-zA-Z_][a-zA-Z0-9_]*)[ \t]*\(", re.M)

# Both projects mark a deprecation two ways, and both use both. WooCommerce
# names the function explicitly 23 times and uses __FUNCTION__ the other 83, so
# matching only one form finds a fifth of them.
ENCLOSING_RE = re.compile(
    r"(?:wc_)?_?deprecated_function\(\s*__FUNCTION__\s*,\s*['\"]([^'\"]+)['\"]"
    r"(?:\s*,\s*(?:__\(\s*)?['\"]([^'\"]*)['\"])?", re.I)
NAMED_RE = re.compile(
    r"(?:wc_)?_?deprecated_function\(\s*['\"]([a-zA-Z_][a-zA-Z0-9_]*)['\"]\s*,"
    r"\s*['\"]([^'\"]+)['\"]"
    r"(?:\s*,\s*['\"]([^'\"]*)['\"])?", re.I)

# Names that would generate noise rather than findings: the reporting helpers
# themselves, and PHP constructs that look like calls.
NEVER_REPORT = {
    "_deprecated_function", "_deprecated_argument", "_deprecated_file",
    "_deprecated_hook", "wc_deprecated_function", "wc_deprecated_argument",
    "wc_deprecated_hook", "apply_filters_deprecated", "do_action_deprecated",
    "array", "isset", "unset", "empty", "list", "echo", "print", "return",
    "if", "elseif", "while", "for", "foreach", "switch", "catch", "function",
    "fn", "match", "exit", "die", "include", "require", "include_once",
    "require_once", "and", "or", "xor", "new", "clone", "yield", "use",
}


def strip_php_noise(text):
    """Blank out comments and string bodies, keeping every newline in place.

    Line numbers have to survive, because a finding that points at the wrong
    line is worse than no finding. Everything removed is replaced by spaces of
    the same length rather than deleted.
    """
    out = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if ch == "/" and nxt == "*":
            end = text.find("*/", i + 2)
            end = n if end == -1 else end + 2
            out.append("".join(c if c == "\n" else " " for c in text[i:end]))
            i = end
        elif (ch == "/" and nxt == "/") or ch == "#":
            end = text.find("\n", i)
            end = n if end == -1 else end
            out.append(" " * (end - i))
            i = end
        elif ch in "'\"":
            quote, j = ch, i + 1
            while j < n:
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == quote:
                    j += 1
                    break
                j += 1
            out.append("".join(c if c == "\n" else " " for c in text[i:j]))
            i = j
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _enclosing_functions(text):
    """[(name, start_offset, end_offset)] for every top-level function."""
    spans = []
    matches = list(FUNCTION_DEF_RE.finditer(text))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        spans.append((match.group("name"), match.start(), end))
    return spans


def build_corpus(wordpress_root):
    """{function: {since, replacement, project}} from the installed copies."""
    root = pathlib.Path(wordpress_root)
    corpus = {}
    sources_seen = []

    targets = [(root / rel, "WordPress") for rel in WORDPRESS_SOURCES]
    targets += [(root / rel, "WooCommerce") for rel in WOOCOMMERCE_SOURCES]

    for path, project in targets:
        if not path.is_file():
            continue
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        sources_seen.append(str(path))

        # Form one: the call names the function it is deprecating.
        for found in NAMED_RE.finditer(text):
            name = found.group(1)
            if name not in corpus:
                corpus[name] = {"since": found.group(2),
                                "replacement": (found.group(3) or "").strip(),
                                "project": project}

        # Form two: the call says __FUNCTION__ and means the one it sits in.
        for name, start, end in _enclosing_functions(text):
            if name in corpus:
                continue
            found = ENCLOSING_RE.search(text, start, end)
            if found:
                corpus[name] = {"since": found.group(1),
                                "replacement": (found.group(2) or "").strip(),
                                "project": project}

    for name in NEVER_REPORT:
        corpus.pop(name, None)
    return corpus, sources_seen


def scan_file(path, corpus, call_re):
    """Findings for one PHP file."""
    try:
        raw = path.read_text(errors="replace")
    except OSError:
        return []
    if not any(name in raw for name in ("(",)):
        return []

    text = strip_php_noise(raw)
    # Only *plain* functions shadow a global one. A method named get_settings
    # does not: a bare get_settings() inside a class still calls WordPress's,
    # since reaching the method needs $this-> or self::.
    #
    # Treating methods as shadowing hid a real deprecated call in a file that
    # happened to define a method of the same name -- and three plugins here
    # define exactly that method, so a genuine call in any of them would have
    # been swallowed silently.
    defined_here = {m.group("name") for m in FUNCTION_DEF_RE.finditer(text)
                    if not m.group("modifiers").strip()}

    findings = []
    for match in call_re.finditer(text):
        name = match.group("name")
        if name in defined_here:
            continue
        before = text[max(0, match.start() - 2):match.start()]
        # ->name( and ::name( are method calls on some object, not this function.
        if before.endswith("->") or before.endswith("::"):
            continue
        # `function name(` is a definition, whatever modifiers precede it. A
        # definition is never a call, so this holds even where the name check
        # above does not.
        preceding = text[max(0, match.start() - 40):match.start()]
        if re.search(r"\bfunction[ \t]+&?$", preceding):
            continue
        entry = corpus[name]
        line = text.count("\n", 0, match.start()) + 1
        replacement = (" Use %s instead." % entry["replacement"]) if entry["replacement"] else ""
        findings.append({
            "severity": "MEDIUM",
            "file": str(path),
            "line": line,
            "source": "deprecations.%s" % entry["project"].lower(),
            "message": "%s() was deprecated in %s %s.%s"
                       % (name, entry["project"], entry["since"], replacement),
        })
    return findings


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", required=True, help="repository to scan")
    parser.add_argument("--wordpress-root", required=True,
                        help="a WordPress install to build the corpus from")
    parser.add_argument("--out", help="write findings JSON here")
    parser.add_argument("--exclude", action="append", default=[],
                        help="path fragment to skip; repeatable")
    args = parser.parse_args(argv)

    corpus, sources = build_corpus(args.wordpress_root)
    if not corpus:
        # No corpus means nothing was compared, which is not the same as nothing
        # being wrong. Exit 2 so the caller can record it as unreviewed.
        sys.stderr.write(
            "scan-deprecations: no deprecated functions found under %s; "
            "is this a WordPress install?\n" % args.wordpress_root)
        return 2

    call_re = re.compile(
        r"\b(?P<name>%s)\s*\(" % "|".join(sorted(map(re.escape, corpus), key=len, reverse=True)))

    repo = pathlib.Path(args.repo)
    excludes = [e.strip().strip("/") for e in args.exclude if e.strip()]
    findings = []
    scanned = 0
    for path in sorted(repo.rglob("*.php")):
        relative = path.relative_to(repo).as_posix()
        if any(relative == e or relative.startswith(e + "/") or ("/" + e + "/") in ("/" + relative)
               for e in excludes):
            continue
        scanned += 1
        for finding in scan_file(path, corpus, call_re):
            finding["file"] = relative
            findings.append(finding)

    if args.out:
        pathlib.Path(args.out).write_text(json.dumps(findings, indent=2) + "\n")

    sys.stderr.write("scan-deprecations: %d deprecated function(s) known from %d source file(s); "
                     "%d PHP file(s) scanned; %d finding(s)\n"
                     % (len(corpus), len(sources), scanned, len(findings)))
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
