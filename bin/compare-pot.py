#!/usr/bin/env python3
"""Compare a checked-in .pot against a freshly generated one.

Why msgids and not the whole file: a .pot carries `#:` source references and a
POT-Creation-Date that change whenever code moves or the clock ticks. Diffing
those reports drift on every run and teaches people to ignore the check. What
matters is whether every translatable string in the code is in the template.

Exit 0 when they agree, 1 when they do not, 2 when it could not tell.
"""
import json
import re
import sys
from pathlib import Path

MSGID = re.compile(r'^msgid\s+"(.*)"\s*$')
CONT = re.compile(r'^"(.*)"\s*$')


def msgids(path):
    """Every msgid in a .pot, including multi-line ones, minus the header."""
    ids, current = set(), None
    for raw in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        m = MSGID.match(raw)
        if m:
            if current is not None:
                ids.add(current)
            current = m.group(1)
            continue
        if current is not None:
            c = CONT.match(raw)
            if c:
                current += c.group(1)
                continue
            ids.add(current)
            current = None
    if current is not None:
        ids.add(current)
    ids.discard("")          # the header entry
    return ids


def main():
    if len(sys.argv) != 5:
        print("usage: compare-pot.py <checked-in.pot> <fresh.pot> <out.json> <rel-path>", file=sys.stderr)
        return 2

    checked_in, fresh, out_path, rel = sys.argv[1:5]
    try:
        have, want = msgids(checked_in), msgids(fresh)
    except OSError as exc:
        print("could not read a .pot: %s" % exc, file=sys.stderr)
        return 2

    missing = sorted(want - have)
    stale = sorted(have - want)
    findings = []

    for s in missing:
        findings.append({
            "severity": "MEDIUM",
            "file": rel,
            "line": None,
            "source": "i18n_pot.missing",
            "message": 'Translatable string is not in the template: "%s"' % s[:160],
        })
    for s in stale:
        findings.append({
            "severity": "LOW",
            "file": rel,
            "line": None,
            "source": "i18n_pot.stale",
            "message": 'Template has a string the code no longer contains: "%s"' % s[:160],
        })

    Path(out_path).write_text(json.dumps(findings, indent=1), encoding="utf-8")

    if missing:
        print("%d translatable string(s) missing from %s." % (len(missing), rel), file=sys.stderr)
        for s in missing[:10]:
            print("  missing: %s" % s[:120], file=sys.stderr)
        if len(missing) > 10:
            print("  ... and %d more" % (len(missing) - 10), file=sys.stderr)
    if stale:
        print("%d string(s) in %s no longer exist in the code." % (len(stale), rel), file=sys.stderr)

    if missing or stale:
        print("Regenerate with: wp i18n make-pot . %s" % rel, file=sys.stderr)
        return 1

    print("%s matches the code (%d strings)." % (rel, len(have)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
