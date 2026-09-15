#!/usr/bin/env python3
"""Apply feature fragments to an AtomVM ESP32 sdkconfig defaults template.

usage: apply_fragments.py TEMPLATE [--set CONFIG_KEY=VALUE]... [FRAGMENT]...

TEMPLATE (normally sdkconfig.defaults.in, copied from
sdkconfig.release-defaults.in) is rewritten in place: every key assigned by a
fragment or by --set is first removed from it, then the fragment lines and the
--set lines are appended, so no key is ever assigned twice.

Fragments (features/<name>.sdkconfig) may contain only CONFIG_* assignments and
must not assign the same key twice between them. '@' is rejected everywhere
because AtomVM feeds the template through CMake's configure_file(@ONLY).
"""

import argparse
import re
import sys
from pathlib import Path

ASSIGN_RE = re.compile(r"^(CONFIG_[A-Z0-9_]+)=(.+)$")
NOT_SET_RE = re.compile(r"^# (CONFIG_[A-Z0-9_]+) is not set$")

# esp_app_desc_t.version is char[32]; a longer value is a compile error.
APP_PROJECT_VER_KEY = "CONFIG_APP_PROJECT_VER"
APP_PROJECT_VER_MAX = 31


class FragmentError(Exception):
    """A fragment or --set line is not acceptable."""


def parse_assignments(lines, source):
    """Validate fragment lines and return them as a list of (key, line)."""
    result = []
    for lineno, raw in enumerate(lines, 1):
        line = raw.rstrip("\r\n")
        where = f"{source}:{lineno}"
        match = ASSIGN_RE.match(line)
        if not match:
            raise FragmentError(f"{where}: not a CONFIG_* assignment: {line!r}")
        if "@" in line:
            raise FragmentError(f"{where}: '@' is not allowed (configure_file substitution)")
        key, value = match.groups()
        if key == APP_PROJECT_VER_KEY and len(value.strip('"')) > APP_PROJECT_VER_MAX:
            raise FragmentError(f"{where}: {key} is longer than {APP_PROJECT_VER_MAX} characters")
        result.append((key, line))
    return result


def merge(template_text, fragments, extra):
    """Return the new template text.

    fragments: list of (source_name, [line, ...]); extra: raw --set lines.
    """
    assignments = []
    for source, lines in fragments:
        assignments.extend(parse_assignments(lines, source))
    assignments.extend(parse_assignments(extra, "--set"))

    seen = {}
    for key, line in assignments:
        if key in seen:
            raise FragmentError(f"{key} assigned twice: {seen[key]!r} and {line!r}")
        seen[key] = line

    kept = []
    for raw in template_text.splitlines():
        match = ASSIGN_RE.match(raw) or NOT_SET_RE.match(raw)
        if match and match.group(1) in seen:
            continue
        kept.append(raw)

    return "\n".join(kept + [line for _, line in assignments]) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("template", type=Path)
    parser.add_argument("fragments", nargs="*", type=Path)
    parser.add_argument(
        "--set", dest="extra", action="append", default=[], metavar="CONFIG_KEY=VALUE",
        help="generated assignment to append after the fragments (may be repeated)")
    parser.add_argument("--output", type=Path, help="write here instead of rewriting TEMPLATE")
    args = parser.parse_intermixed_args(argv)

    fragments = [(str(path), path.read_text().splitlines()) for path in args.fragments]
    try:
        text = merge(args.template.read_text(), fragments, args.extra)
    except FragmentError as err:
        print(f"apply_fragments: {err}", file=sys.stderr)
        return 1
    (args.output or args.template).write_text(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
