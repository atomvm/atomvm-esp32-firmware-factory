#!/usr/bin/env python3
"""List the images to build as GitHub Actions matrix entries.

usage: images.py IMAGES [--features-only]

IMAGES (images.txt) names one image per line as AtomVM-<chip>[-<feature>...],
the image file name without the version, optionally followed by -Os to build
that image optimized for size; '#' starts a comment. Every image gives two
entries, without and with Elixir support:
  name       the image name, ending in -elixir for the Elixir image
  chip       the ESP-IDF target
  features   the feature tokens, separated by spaces
  fragments  the sdkconfig fragments of those features, relative to the
             directory of IMAGES and separated by spaces: for each feature
             features/<feature>.<chip>.sdkconfig, or else
             features/<feature>.sdkconfig, where it exists
  elixir     true for the Elixir image
  level      -Os, or empty for the optimization level of the release defaults

--features-only leaves out the images without features: upstream AtomVM
publishes those for its release tags.

The entries are printed as a JSON list on one line.
"""

import argparse
import json
import sys
from pathlib import Path


class ImagesError(ValueError):
    """A line of the images file does not name an image."""


def parse(text, source):
    """Return (name, chip, features, level) for every image in the text."""
    images = []
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        name, *options = line.split()
        tokens = name.split("-")
        if tokens[0] != "AtomVM" or len(tokens) < 2 or not all(tokens):
            raise ImagesError(f"{source}:{lineno}: not an image name AtomVM-<chip>[-<feature>...]: {name!r}")
        level = ""
        for option in options:
            if option != "-Os":
                raise ImagesError(f"{source}:{lineno}: unknown option {option!r}")
            level = option
        images.append((name, tokens[1], tokens[2:], level))
    return images


def fragments(base_dir, chip, features):
    """The fragment files of the features on the chip, relative to base_dir."""
    paths = []
    for feature in features:
        for path in (Path("features", f"{feature}.{chip}.sdkconfig"), Path("features", f"{feature}.sdkconfig")):
            if (Path(base_dir) / path).is_file():
                paths.append(path.as_posix())
                break
    return paths


def entries(images, base_dir, features_only=False):
    """The matrix entries of the images, two per image."""
    result = []
    for name, chip, features, level in images:
        if features_only and not features:
            continue
        for elixir in (False, True):
            result.append({
                "name": name + ("-elixir" if elixir else ""),
                "chip": chip,
                "features": " ".join(features),
                "fragments": " ".join(fragments(base_dir, chip, features)),
                "elixir": elixir,
                "level": level,
            })
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("images", type=Path)
    parser.add_argument("--features-only", action="store_true", help="leave out the images without features")
    args = parser.parse_intermixed_args(argv)

    try:
        images = parse(args.images.read_text(), args.images)
    except ImagesError as err:
        print(f"images: {err}", file=sys.stderr)
        return 1
    print(json.dumps(entries(images, args.images.parent, args.features_only), separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
