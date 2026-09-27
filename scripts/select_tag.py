#!/usr/bin/env python3
"""Choose the next AtomVM release tag to build.

usage: select_tag.py FLOOR UPSTREAM_TAGS PUBLISHED_TAGS

UPSTREAM_TAGS and PUBLISHED_TAGS are files with one tag per line: the tags of
the AtomVM repository and the tags of the releases already published by the
factory. Prints the oldest upstream tag not below FLOOR that has no published
release, or nothing when every such tag is published.

Tags are ordered by semantic version precedence, so v0.7.0-beta.0 comes before
v0.7.0-rc.1 and v0.7.0-rc.10 after v0.7.0-rc.9. Tags not shaped like
vMAJOR.MINOR.PATCH[-PRERELEASE] are ignored.
"""

import argparse
import re
import sys
from pathlib import Path

TAG_RE = re.compile(
    r"^v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$")


def version_key(tag):
    """Return a sort key following semver precedence, or None for other tags."""
    match = TAG_RE.match(tag)
    if not match:
        return None
    major, minor, patch, pre = match.groups()
    if pre is None:
        # a release sorts after all of its pre-releases
        pre_key = (1,)
    else:
        # numeric identifiers sort numerically and before alphanumeric ones
        pre_key = (0,) + tuple(
            (0, int(ident), "") if ident.isdigit() else (1, 0, ident)
            for ident in pre.split("."))
    return (int(major), int(minor), int(patch), pre_key)


def select(floor, upstream, published):
    """Return the oldest pending tag, or None."""
    floor_key = version_key(floor)
    if floor_key is None:
        raise ValueError(f"floor is not a version tag: {floor!r}")
    published = set(published)
    pending = [
        (key, tag) for tag, key in ((tag, version_key(tag)) for tag in upstream)
        if key is not None and key >= floor_key and tag not in published]
    return min(pending)[1] if pending else None


def read_tags(path):
    return [line.strip() for line in path.read_text().splitlines() if line.strip()]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("floor")
    parser.add_argument("upstream", type=Path)
    parser.add_argument("published", type=Path)
    args = parser.parse_intermixed_args(argv)

    try:
        tag = select(args.floor, read_tags(args.upstream), read_tags(args.published))
    except ValueError as err:
        print(f"select_tag: {err}", file=sys.stderr)
        return 1
    if tag:
        print(tag)
    return 0


if __name__ == "__main__":
    sys.exit(main())
