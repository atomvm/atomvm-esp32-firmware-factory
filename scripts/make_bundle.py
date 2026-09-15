#!/usr/bin/env python3
"""Assemble the release bundle for one AtomVM ESP32 image.

usage: make_bundle.py --stem STEM --chip CHIP --img IMG --sdkconfig SDKCONFIG
                      --partitions-dir DIR --build-dir BUILD --out OUTDIR
                      [--source-date-epoch N]

BUILD is the ESP-IDF build directory: the flashed files and their offsets are
read from BUILD/flasher_args.json, and the ELF, map and GDB files from their
usual places in it.

Writes OUTDIR/STEM.zip containing exactly, in this order:
  STEM.img, STEM.img.sha256, sdkconfig, partitions.csv, FLASH.txt,
  bootloader.bin, partition-table.bin, atomvm-esp32.bin, the boot library
  (esp32boot.avm, or elixir_esp32boot.avm for -elixir images),
  atomvm-esp32.elf, atomvm-esp32.map, bootloader.elf, bootloader.map,
  prefix_map_gdbinit, SHA256SUMS
The binaries are the parts of the image. DEFLATE only, fixed timestamps, no
extra attributes: identical inputs give an identical bundle, and OTP's zip
module can read it.
"""

import argparse
import hashlib
import json
import re
import sys
import time
import zipfile
from collections import namedtuple
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

# Entries of flasher_args.json that make up the image, in flash order, and the
# names their files get in the bundle; the boot library keeps its own name.
FLASH_ENTRIES = ("bootloader", "partition-table", "app", "boot.avm")
PART_NAMES = {"bootloader": "bootloader.bin", "partition-table": "partition-table.bin", "app": "atomvm-esp32.bin"}

# Debug files, relative to the build directory; they keep their base names.
DEBUG_FILES = ("atomvm-esp32.elf", "atomvm-esp32.map", "bootloader/bootloader.elf", "bootloader/bootloader.map",
               "prefix_map_gdbinit")

DEFAULT_EPOCH = 315532800  # 1980-01-01, the earliest zip timestamp

Part = namedtuple("Part", "name offset data")


class BundleError(ValueError):
    """The build outputs cannot make a consistent bundle."""


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


def idf_version(sdkconfig):
    """ESP-IDF version from the header ESP-IDF writes at the top of sdkconfig."""
    match = re.search(r"^# Espressif IoT Development Framework \(ESP-IDF\) (\S+) Project Configuration$",
                      sdkconfig, re.MULTILINE)
    return match.group(1) if match else "unknown"


def read_flash_parts(build_dir):
    """Flash settings and the flashed parts listed in BUILD/flasher_args.json."""
    path = Path(build_dir) / "flasher_args.json"
    if not path.is_file():
        raise BundleError(f"{path} not found")
    flasher = json.loads(path.read_text())
    entries = {key for key, value in flasher.items() if isinstance(value, dict) and "offset" in value}
    unexpected = sorted(entries - set(FLASH_ENTRIES))
    if unexpected:
        raise BundleError(f"{path}: unexpected flash entries {', '.join(unexpected)};"
                          f" the bundle covers {', '.join(FLASH_ENTRIES)}")
    missing = [entry for entry in FLASH_ENTRIES if entry not in entries]
    if missing:
        raise BundleError(f"{path}: missing flash entries {', '.join(missing)}")
    parts = []
    for entry in FLASH_ENTRIES:
        file = path.parent / flasher[entry]["file"]
        if not file.is_file():
            raise BundleError(f"{path}: {entry} file {file} not found")
        parts.append(Part(PART_NAMES.get(entry, file.name), int(flasher[entry]["offset"], 16), file.read_bytes()))
    return flasher["flash_settings"], parts


def read_debug_files(build_dir):
    """The debug files as (name, data)."""
    members = []
    for relative in DEBUG_FILES:
        file = Path(build_dir) / relative
        if not file.is_file():
            raise BundleError(f"{file} not found")
        members.append((file.name, file.read_bytes()))
    return members


def check_parts_in_image(img_name, img, parts, base):
    """Each part must be in the image at its offset: install and update write the same bytes."""
    for part in parts:
        start = part.offset - base
        if img[start:start + len(part.data)] != part.data:
            raise BundleError(f"{img_name}: {part.name} differs from the image at {part.offset:#x}")


def sha256sums(members):
    """sha256sum -c compatible listing of (name, data) members."""
    return "".join(f"{hashlib.sha256(data).hexdigest()}  {name}\n" for name, data in members).encode()


def flash_txt(stem, chip, base, flash_settings, parts, main_avm, stamp, idf, elf_sha):
    """Instructions shipped as FLASH.txt: install the image, or update an installation."""
    bootloader, table, vm, boot = parts
    port = f"--chip {chip} --port /dev/ttyUSB0 --baud 921600"
    contents = "".join(f"  {part.offset:<#10x}{part.name}\n" for part in parts)
    return (
        f"AtomVM firmware image: {stem}.img\n"
        f"Chip: {chip}\n"
        f"AtomVM build: {stamp}\n"
        f"ESP-IDF: {idf}\n"
        f"Flash offset: {base:#x}\n"
        f"Application partition (main.avm): {main_avm}\n"
        "\n"
        "Run the commands below from the extracted bundle, with the serial port your\n"
        "board shows up as (e.g. /dev/ttyACM0 for native USB) instead of /dev/ttyUSB0.\n"
        "Check the extracted files first:\n"
        "  sha256sum -c SHA256SUMS\n"
        "\n"
        "Install\n"
        "-------\n"
        "\n"
        "The image contains the bootloader, the partition table, the AtomVM virtual\n"
        "machine and boot.avm, laid out for a 4 MB flash. Flash the whole file at the\n"
        "offset above. The partition table stored at 0x8000 is the source of truth for\n"
        "the layout; partitions.csv in this bundle is the table the image was built with.\n"
        "\n"
        "The gaps between those parts are filled with 0xFF, so flashing the image also\n"
        "erases the NVS partition: Wi-Fi settings and data stored by applications are\n"
        "lost, and RF calibration runs again the next time the radio starts. The\n"
        "phy_init partition is erased too; this image does not use it. main.avm is left\n"
        "untouched.\n"
        "\n"
        "Erase the flash first when switching from another firmware:\n"
        f"  esptool.py {port} erase_flash\n"
        "\n"
        "Flash:\n"
        f"  esptool.py {port} \\\n"
        "    --before default_reset --after hard_reset write_flash \\\n"
        f"    --flash_mode {flash_settings['flash_mode']}"
        f" --flash_freq {flash_settings['flash_freq']} --flash_size detect \\\n"
        f"    {base:#x} {stem}.img\n"
        "\n"
        "Update an existing AtomVM installation\n"
        "--------------------------------------\n"
        "\n"
        f"This writes only the AtomVM virtual machine ({vm.name}) and its boot\n"
        f"library ({boot.name}), and keeps the bootloader, the partition table, NVS\n"
        "and main.avm. Install the image instead when one of these rules does not hold:\n"
        "- The partition table on the board must be identical to this image's. The\n"
        "  first command below checks it; on \"Verification failed\" nothing is written.\n"
        "- The bootloader on the board must not come from a newer ESP-IDF than this\n"
        f"  image ({idf}): a bootloader cannot start an app built with an older\n"
        "  ESP-IDF. The board prints its bootloader version at reset, in a line like\n"
        f"  \"boot: ESP-IDF v{idf} 2nd stage bootloader\".\n"
        f"- {vm.name} and {boot.name} are always written together: both come from\n"
        "  the same AtomVM commit.\n"
        "If the update is interrupted, run it again.\n"
        "\n"
        f"  esptool.py {port} --after no_reset \\\n"
        f"    verify_flash {table.offset:#x} {table.name} && \\\n"
        f"  esptool.py {port} \\\n"
        "    --before default_reset --after hard_reset write_flash \\\n"
        f"    {vm.offset:#x} {vm.name} {boot.offset:#x} {boot.name}\n"
        "\n"
        "Contents\n"
        "--------\n"
        "\n"
        "The binaries are the parts of the image, byte for byte, at these offsets:\n"
        f"{contents}"
        "\n"
        "Debugging\n"
        "---------\n"
        "\n"
        "atomvm-esp32.elf and bootloader.elf hold the symbols of this image, and the\n"
        "map files show where the linker placed each function and variable. The board\n"
        "prints the start of the app's ELF SHA-256 at boot and on panics, in a line like\n"
        f"\"ELF file SHA256:  {elf_sha[:9]}...\". It must match this image's ELF:\n"
        f"  {elf_sha}\n"
        "\n"
        "Decode backtraces while the board runs, from an ESP-IDF environment:\n"
        f"  python -m esp_idf_monitor --port /dev/ttyUSB0 --target {chip} \\\n"
        "    atomvm-esp32.elf bootloader.elf\n"
        "\n"
        "Decode addresses from a saved log with the toolchain's addr2line\n"
        "(xtensa-<chip>-elf- or riscv32-esp-elf-):\n"
        "  addr2line -pfiaC -e atomvm-esp32.elf <address>...\n"
        "\n"
        "GDB finds sources by their build-time paths, listed in prefix_map_gdbinit:\n"
        "ESP-IDF and the AtomVM ESP32 components were recorded under placeholder paths\n"
        "that the file maps back to the build's paths. Replace those build paths in\n"
        "the file with your own ESP-IDF and AtomVM checkouts, then \"source\" it in GDB\n"
        "and add a \"set substitute-path\" rule from the AtomVM build path to your\n"
        "checkout for AtomVM's core sources.\n"
    )


def write_zip(out_path, members, epoch):
    """Write (name, data) members as a deterministic, DEFLATE-only zip."""
    date_time = time.gmtime(max(epoch, DEFAULT_EPOCH))[:6]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_path, "w") as bundle:
        for name, data in members:
            info = zipfile.ZipInfo(name, date_time=date_time)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o644 << 16
            bundle.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)


def build_bundle(stem, chip, img_path, sdkconfig_path, partitions_dir, build_dir, out_dir,
                 epoch=DEFAULT_EPOCH):
    """Write OUTDIR/STEM.zip; return (path, image sha256, bundle sha256)."""
    if chip not in FLASH_OFFSETS:
        raise BundleError(f"unknown chip {chip!r}")
    base = FLASH_OFFSETS[chip]
    flash_settings, parts = read_flash_parts(build_dir)
    if parts[0].offset != base:
        raise BundleError(f"bootloader offset {parts[0].offset:#x} in flasher_args.json"
                          f" does not match the {chip} table value {base:#x}")
    img = Path(img_path).read_bytes()
    check_parts_in_image(Path(img_path).name, img, parts, base)

    img_sha = hashlib.sha256(img).hexdigest()
    sdkconfig = Path(sdkconfig_path).read_text()
    csv_name = sdkconfig_value(sdkconfig, "CONFIG_PARTITION_TABLE_CUSTOM_FILENAME") or "partitions.csv"
    csv_text = (Path(partitions_dir) / csv_name).read_text()
    main_avm = partition_offset(csv_text, "main.avm")
    stamp = sdkconfig_value(sdkconfig, "CONFIG_APP_PROJECT_VER") or "unknown"
    debug_members = read_debug_files(build_dir)
    elf_sha = hashlib.sha256(debug_members[0][1]).hexdigest()

    summed = [
        (f"{stem}.img", img),
        ("sdkconfig", sdkconfig.encode()),
        ("partitions.csv", csv_text.encode()),
        ("FLASH.txt", flash_txt(stem, chip, base, flash_settings, parts, main_avm, stamp,
                                idf_version(sdkconfig), elf_sha).encode()),
    ] + [(part.name, part.data) for part in parts] + debug_members
    members = ([summed[0], (f"{stem}.img.sha256", f"{img_sha}  {stem}.img\n".encode())]
               + summed[1:] + [("SHA256SUMS", sha256sums(summed))])

    out_path = Path(out_dir) / f"{stem}.zip"
    write_zip(out_path, members, epoch)
    return out_path, img_sha, hashlib.sha256(out_path.read_bytes()).hexdigest()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--stem", required=True, help="image name without extension")
    parser.add_argument("--chip", required=True, choices=sorted(FLASH_OFFSETS))
    parser.add_argument("--img", required=True, type=Path)
    parser.add_argument("--sdkconfig", required=True, type=Path, help="resolved sdkconfig of the build")
    parser.add_argument("--partitions-dir", required=True, type=Path,
                        help="directory holding the partition CSV named in the sdkconfig")
    parser.add_argument("--build-dir", required=True, type=Path,
                        help="ESP-IDF build directory, holding flasher_args.json")
    parser.add_argument("--out", required=True, type=Path, help="output directory")
    parser.add_argument("--source-date-epoch", type=int, default=DEFAULT_EPOCH,
                        help="timestamp for the zip members (default: 1980-01-01)")
    args = parser.parse_args(argv)

    try:
        out_path, img_sha, bundle_sha = build_bundle(
            args.stem, args.chip, args.img, args.sdkconfig, args.partitions_dir, args.build_dir,
            args.out, epoch=args.source_date_epoch)
    except BundleError as err:
        print(f"make_bundle: {err}", file=sys.stderr)
        return 1
    print(f"{img_sha}  {args.stem}.img")
    print(f"{bundle_sha}  {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
