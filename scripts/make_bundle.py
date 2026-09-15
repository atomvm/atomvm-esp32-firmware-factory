#!/usr/bin/env python3
"""Assemble the release bundle for one AtomVM ESP32 image.

usage: make_bundle.py --stem STEM --chip CHIP --img IMG --sdkconfig SDKCONFIG
                      --partitions-dir DIR --out OUTDIR [--source-date-epoch N]
                      [--flasher-args build/flasher_args.json]

Writes OUTDIR/STEM.zip containing exactly, in this order:
  STEM.img, STEM.img.sha256, sdkconfig, partitions.csv, FLASH.txt
DEFLATE only, fixed timestamps, no extra attributes: identical inputs give an
identical bundle, and OTP's zip module can read it.
"""

import argparse
import hashlib
import json
import re
import sys
import time
import zipfile
from pathlib import Path

# Bootloader offset per chip: the offset the whole image is flashed at.
FLASH_OFFSETS = {
    "esp32": 0x1000,
    "esp32s2": 0x1000,
    "esp32s3": 0x0,
    "esp32c2": 0x0,
    "esp32c3": 0x0,
    "esp32c5": 0x2000,
    "esp32c6": 0x0,
    "esp32c61": 0x0,
    "esp32h2": 0x0,
    "esp32p4": 0x2000,
}

MEMBER_NAMES = ("{stem}.img", "{stem}.img.sha256", "sdkconfig", "partitions.csv", "FLASH.txt")

DEFAULT_EPOCH = 315532800  # 1980-01-01, the earliest zip timestamp


def sdkconfig_value(text, key):
    match = re.search(rf"^{re.escape(key)}=(.*)$", text, re.MULTILINE)
    return match.group(1).strip().strip('"') if match else None


def partition_offset(csv_text, name):
    """Offset column of the partition called `name`, as written in the CSV."""
    for raw in csv_text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        fields = [field.strip() for field in line.split(",")]
        if fields[0] == name and len(fields) > 3:
            return fields[3]
    return None


def flash_txt(stem, chip, offset, flash_settings, main_avm):
    """Flashing instructions shipped as FLASH.txt."""
    settings = ""
    if flash_settings:
        settings = (f"    --flash_mode {flash_settings['flash_mode']}"
                    f" --flash_freq {flash_settings['flash_freq']} --flash_size detect \\\n")
    main_line = f"Application partition (main.avm): {main_avm}\n" if main_avm else ""
    return (
        f"AtomVM firmware image: {stem}.img\n"
        f"Chip: {chip}\n"
        f"Flash offset: {offset:#x}\n"
        f"{main_line}"
        "\n"
        "The image contains the bootloader, the partition table, the AtomVM virtual\n"
        "machine and boot.avm, laid out for a 4 MB flash. Flash the whole file at the\n"
        "offset above. The partition table stored at 0x8000 is the source of truth for\n"
        "the layout; partitions.csv in this bundle is the table the image was built with.\n"
        "\n"
        "Erase the flash first when switching from another firmware:\n"
        f"  esptool.py --chip {chip} --port /dev/ttyUSB0 --baud 921600 erase_flash\n"
        "\n"
        "Flash (use the serial port your board shows up as, e.g. /dev/ttyACM0 for native USB):\n"
        f"  esptool.py --chip {chip} --port /dev/ttyUSB0 --baud 921600 \\\n"
        "    --before default_reset --after hard_reset write_flash \\\n"
        f"{settings}"
        f"    {offset:#x} {stem}.img\n"
        "\n"
        f"Verify the extracted image: sha256sum -c {stem}.img.sha256\n"
    )


def build_bundle(stem, chip, img_path, sdkconfig_path, partitions_dir, out_dir,
                 epoch=DEFAULT_EPOCH, flasher_args_path=None):
    """Write OUTDIR/STEM.zip; return (path, image sha256, bundle sha256)."""
    if chip not in FLASH_OFFSETS:
        raise ValueError(f"unknown chip {chip!r}")
    offset = FLASH_OFFSETS[chip]

    flash_settings = None
    if flasher_args_path:
        flasher = json.loads(Path(flasher_args_path).read_text())
        built_offset = int(flasher["bootloader"]["offset"], 16)
        if built_offset != offset:
            raise ValueError(f"bootloader offset {built_offset:#x} in {flasher_args_path}"
                             f" does not match the {chip} table value {offset:#x}")
        flash_settings = flasher.get("flash_settings")

    img = Path(img_path).read_bytes()
    img_sha = hashlib.sha256(img).hexdigest()
    sdkconfig = Path(sdkconfig_path).read_text()
    csv_name = sdkconfig_value(sdkconfig, "CONFIG_PARTITION_TABLE_CUSTOM_FILENAME") or "partitions.csv"
    csv_text = (Path(partitions_dir) / csv_name).read_text()
    main_avm = partition_offset(csv_text, "main.avm")

    members = [
        (f"{stem}.img", img),
        (f"{stem}.img.sha256", f"{img_sha}  {stem}.img\n".encode()),
        ("sdkconfig", sdkconfig.encode()),
        ("partitions.csv", csv_text.encode()),
        ("FLASH.txt", flash_txt(stem, chip, offset, flash_settings, main_avm).encode()),
    ]
    assert [name for name, _ in members] == [name.format(stem=stem) for name in MEMBER_NAMES]

    date_time = time.gmtime(max(epoch, DEFAULT_EPOCH))[:6]
    out_path = Path(out_dir) / f"{stem}.zip"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_path, "w") as bundle:
        for name, data in members:
            info = zipfile.ZipInfo(name, date_time=date_time)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o644 << 16
            bundle.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    return out_path, img_sha, hashlib.sha256(out_path.read_bytes()).hexdigest()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--stem", required=True, help="image name without extension")
    parser.add_argument("--chip", required=True, choices=sorted(FLASH_OFFSETS))
    parser.add_argument("--img", required=True, type=Path)
    parser.add_argument("--sdkconfig", required=True, type=Path, help="resolved sdkconfig of the build")
    parser.add_argument("--partitions-dir", required=True, type=Path,
                        help="directory holding the partition CSV named in the sdkconfig")
    parser.add_argument("--out", required=True, type=Path, help="output directory")
    parser.add_argument("--source-date-epoch", type=int, default=DEFAULT_EPOCH,
                        help="timestamp for the zip members (default: 1980-01-01)")
    parser.add_argument("--flasher-args", type=Path,
                        help="build/flasher_args.json, to cross-check the offset and record flash settings")
    args = parser.parse_args(argv)

    out_path, img_sha, bundle_sha = build_bundle(
        args.stem, args.chip, args.img, args.sdkconfig, args.partitions_dir, args.out,
        epoch=args.source_date_epoch, flasher_args_path=args.flasher_args)
    print(f"{img_sha}  {args.stem}.img")
    print(f"{bundle_sha}  {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
