"""Unit tests for the host-side factory scripts.

Run with: python3 -m unittest discover -s tests
"""

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
FEATURES = ROOT / "features"
sys.path.insert(0, str(SCRIPTS))

import apply_fragments  # noqa: E402
import make_bundle  # noqa: E402

# Upstream sdkconfig.release-defaults.in as of release-0.7.
TEMPLATE = """CONFIG_PARTITION_TABLE_CUSTOM=y
CONFIG_ESPTOOLPY_FLASHSIZE_4MB=y
CONFIG_MBEDTLS_ECP_FIXED_POINT_OPTIM=y
CONFIG_COMPILER_OPTIMIZATION_PERF=y
CONFIG_COMPILER_OPTIMIZATION_ASSERTIONS_SILENT=y
CONFIG_LWIP_IPV6=n
CONFIG_PARTITION_TABLE_CUSTOM_FILENAME="@AVM_PARTITION_TABLE_FILENAME@"
CONFIG_APP_REPRODUCIBLE_BUILD=y
"""

PARTITIONS = """# Name,   Type, SubType, Offset,  Size, Flags
nvs,      data, nvs,       0x9000,     0x6000,
phy_init, data, phy,       0xf000,     0x1000,
factory,  app,  factory,  0x10000,   0x1C0000,
boot.avm,  data, phy,     0x1D0000,    0x80000,
main.avm, data, phy,     0x250000,   0x100000
"""

SDKCONFIG = (
    "#\n"
    "# Automatically generated file. DO NOT EDIT.\n"
    "# Espressif IoT Development Framework (ESP-IDF) 5.5.4 Project Configuration\n"
    "#\n"
    'CONFIG_IDF_TARGET="esp32s3"\n'
    'CONFIG_APP_PROJECT_VER="nightly-0.7+20260915.02e1603"\n'
    'CONFIG_PARTITION_TABLE_CUSTOM_FILENAME="partitions.csv"\n'
    "CONFIG_SPIRAM=y\n"
)

STEM = "AtomVM-esp32s3-atomgl-ipv6-libsodium-psram-nightly-0.7"
ELIXIR_STEM = "AtomVM-esp32s3-atomgl-ipv6-libsodium-psram-elixir-nightly-0.7"
FULL_PROFILE_FRAGMENTS = ("ipv6", "libsodium", "psram")


class ApplyFragmentsTest(unittest.TestCase):
    def merge(self, fragments, extra=()):
        return apply_fragments.merge(TEMPLATE, fragments, list(extra))

    def test_fragment_replaces_base_assignment(self):
        lines = self.merge([("ipv6", ["CONFIG_LWIP_IPV6=y"])]).splitlines()
        self.assertEqual(lines.count("CONFIG_LWIP_IPV6=y"), 1)
        self.assertNotIn("CONFIG_LWIP_IPV6=n", lines)
        self.assertEqual(lines[-1], "CONFIG_LWIP_IPV6=y")
        self.assertIn('CONFIG_PARTITION_TABLE_CUSTOM_FILENAME="@AVM_PARTITION_TABLE_FILENAME@"', lines)

    def test_set_lines_follow_fragments_and_replace_not_set(self):
        template = TEMPLATE + "# CONFIG_APP_PROJECT_VER_FROM_CONFIG is not set\n"
        stamp = 'CONFIG_APP_PROJECT_VER="nightly-0.7+20260915.02e1603"'
        lines = apply_fragments.merge(
            template, [("psram", ["CONFIG_SPIRAM=y"])],
            ["CONFIG_APP_PROJECT_VER_FROM_CONFIG=y", stamp]).splitlines()
        self.assertNotIn("# CONFIG_APP_PROJECT_VER_FROM_CONFIG is not set", lines)
        self.assertEqual(lines[-3:], ["CONFIG_SPIRAM=y", "CONFIG_APP_PROJECT_VER_FROM_CONFIG=y", stamp])

    def test_rejects_non_assignment_lines(self):
        for bad in ["# comment", "", "SPIRAM=y", "CONFIG_lower=y", "CONFIG_X"]:
            with self.assertRaises(apply_fragments.FragmentError, msg=bad):
                self.merge([("bad", [bad])])

    def test_rejects_at_sign(self):
        with self.assertRaises(apply_fragments.FragmentError):
            self.merge([("bad", ['CONFIG_X="@Y@"'])])

    def test_rejects_key_assigned_twice(self):
        with self.assertRaises(apply_fragments.FragmentError):
            self.merge([("a", ["CONFIG_SPIRAM=y"]), ("b", ["CONFIG_SPIRAM=n"])])
        with self.assertRaises(apply_fragments.FragmentError):
            self.merge([("a", ["CONFIG_SPIRAM=y"])], ["CONFIG_SPIRAM=y"])

    def test_app_project_ver_length(self):
        self.merge([], ['CONFIG_APP_PROJECT_VER="' + "x" * 31 + '"'])
        with self.assertRaises(apply_fragments.FragmentError):
            self.merge([], ['CONFIG_APP_PROJECT_VER="' + "x" * 32 + '"'])

    def test_repository_fragments_lint(self):
        fragments = sorted(FEATURES.glob("*.sdkconfig"))
        self.assertTrue(fragments)
        for path in fragments:
            self.merge([(path.name, path.read_text().splitlines())])

    def test_full_profile_fragments_are_disjoint(self):
        fragments = [(name, (FEATURES / f"{name}.sdkconfig").read_text().splitlines())
                     for name in FULL_PROFILE_FRAGMENTS]
        lines = self.merge(fragments).splitlines()
        for expected in ("CONFIG_SPIRAM=y", "CONFIG_LWIP_IPV6=y", "CONFIG_ESP_MAIN_TASK_STACK_SIZE=16384"):
            self.assertEqual(lines.count(expected), 1)
        self.assertNotIn("CONFIG_LWIP_IPV6=n", lines)

    def test_cli_rewrites_in_place(self):
        with tempfile.TemporaryDirectory() as tmp:
            template = Path(tmp) / "sdkconfig.defaults.in"
            template.write_text(TEMPLATE)
            fragment = Path(tmp) / "ipv6.sdkconfig"
            fragment.write_text("CONFIG_LWIP_IPV6=y\n")
            proc = subprocess.run(
                [sys.executable, str(SCRIPTS / "apply_fragments.py"), str(template),
                 "--set", "CONFIG_APP_PROJECT_VER_FROM_CONFIG=y", str(fragment)],
                capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertTrue(template.read_text().endswith(
                "CONFIG_LWIP_IPV6=y\nCONFIG_APP_PROJECT_VER_FROM_CONFIG=y\n"))

    def test_cli_reports_bad_fragment(self):
        with tempfile.TemporaryDirectory() as tmp:
            template = Path(tmp) / "sdkconfig.defaults.in"
            template.write_text(TEMPLATE)
            fragment = Path(tmp) / "bad.sdkconfig"
            fragment.write_text("# not allowed\n")
            proc = subprocess.run(
                [sys.executable, str(SCRIPTS / "apply_fragments.py"), str(template), str(fragment)],
                capture_output=True, text=True)
            self.assertEqual(proc.returncode, 1)
            self.assertIn("not a CONFIG_* assignment", proc.stderr)
            self.assertEqual(template.read_text(), TEMPLATE)


BOOTLOADER = b"\xe9" + bytes(range(1, 256)) * 77
PARTITION_TABLE = bytes(range(256)) * 12
APP = b"\xe9\x06\x02\x2f" + bytes(range(256)) * 400
BOOT_LIBRARY = b"#!/usr/bin/env AtomVM\n\0\0" + bytes(range(255, -1, -1)) * 300
PARTS = ("bootloader.bin", "partition-table.bin", "atomvm-esp32.bin", "esp32boot.avm")
DEBUG_FILES = ("atomvm-esp32.elf", "atomvm-esp32.map", "bootloader/bootloader.elf", "bootloader/bootloader.map",
               "prefix_map_gdbinit")
APP_ELF = b"\x7fELF\x01\x01\x01\x00" + bytes(range(256)) * 50
ENTRIES = ("bootloader", "partition-table", "app", "boot.avm")


def flip_byte(data, index):
    return data[:index] + bytes([data[index] ^ 0xFF]) + data[index + 1:]


def mkimage(parts, base=0):
    """Concatenate (offset, data) parts with 0xFF gaps, as AtomVM's mkimage does."""
    image = bytearray()
    for offset, data in sorted(parts):
        image += b"\xff" * (offset - base - len(image))
        image += data
    return bytes(image)


class BundleTestCase(unittest.TestCase):
    """Fake ESP-IDF build tree laid out like CI's; holds no tests itself."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.esp32_dir = self.tmp / "AtomVM" / "src" / "platforms" / "esp32"
        self.build_dir = self.esp32_dir / "build"
        self.libs_dir = self.tmp / "AtomVM" / "build" / "libs" / "esp32boot"
        for directory in (self.build_dir / "bootloader", self.build_dir / "partition_table", self.libs_dir):
            directory.mkdir(parents=True)
        (self.esp32_dir / "sdkconfig").write_text(SDKCONFIG)
        (self.esp32_dir / "partitions.csv").write_text(PARTITIONS)
        (self.build_dir / "bootloader" / "bootloader.bin").write_bytes(BOOTLOADER)
        (self.build_dir / "partition_table" / "partition-table.bin").write_bytes(PARTITION_TABLE)
        (self.build_dir / "atomvm-esp32.bin").write_bytes(APP)
        (self.libs_dir / "esp32boot.avm").write_bytes(BOOT_LIBRARY)
        (self.build_dir / "atomvm-esp32.elf").write_bytes(APP_ELF)
        (self.build_dir / "atomvm-esp32.map").write_text("Linker script and memory map\n")
        (self.build_dir / "bootloader" / "bootloader.elf").write_bytes(b"\x7fELF" + bytes(range(255, -1, -1)) * 8)
        (self.build_dir / "bootloader" / "bootloader.map").write_text("bootloader memory map\n")
        (self.build_dir / "prefix_map_gdbinit").write_text("set substitute-path /IDF /opt/esp/idf\n")
        self.flasher_args = {
            "write_flash_args": ["--flash_mode", "dio", "--flash_size", "4MB", "--flash_freq", "80m"],
            "flash_settings": {"flash_mode": "dio", "flash_size": "4MB", "flash_freq": "80m"},
            "flash_files": {"0x0": "bootloader/bootloader.bin", "0x10000": "atomvm-esp32.bin",
                            "0x8000": "partition_table/partition-table.bin",
                            "0x1d0000": "../../../../build/libs/esp32boot/esp32boot.avm"},
            "bootloader": {"offset": "0x0", "file": "bootloader/bootloader.bin", "encrypted": "false"},
            "app": {"offset": "0x10000", "file": "atomvm-esp32.bin", "encrypted": "false"},
            "partition-table": {"offset": "0x8000", "file": "partition_table/partition-table.bin",
                                "encrypted": "false"},
            "boot.avm": {"offset": "0x1d0000", "file": "../../../../build/libs/esp32boot/esp32boot.avm",
                         "encrypted": "false"},
            "extra_esptool_args": {"after": "hard_reset", "before": "default_reset", "stub": True,
                                   "chip": "esp32s3"},
        }
        self.img = self.build_dir / f"{STEM}.img"
        self.save_flasher_args()
        self.rebuild_image()

    def save_flasher_args(self):
        (self.build_dir / "flasher_args.json").write_text(json.dumps(self.flasher_args, indent=4))

    def part_path(self, entry):
        return self.build_dir / self.flasher_args[entry]["file"]

    def rebuild_image(self):
        self.img.write_bytes(mkimage([(int(self.flasher_args[entry]["offset"], 16),
                                       self.part_path(entry).read_bytes()) for entry in ENTRIES]))

    def use_boot_library(self, name):
        (self.libs_dir / name).write_bytes(BOOT_LIBRARY)
        self.flasher_args["boot.avm"]["file"] = f"../../../../build/libs/esp32boot/{name}"
        self.save_flasher_args()
        self.rebuild_image()

    def bundle(self, **overrides):
        args = dict(stem=STEM, chip="esp32s3", img_path=self.img, sdkconfig_path=self.esp32_dir / "sdkconfig",
                    partitions_dir=self.esp32_dir, build_dir=self.build_dir, out_dir=self.tmp / "dist",
                    epoch=1789430400)
        args.update(overrides)
        return make_bundle.build_bundle(**args)

    def flash_txt(self, **overrides):
        out, _, _ = self.bundle(**overrides)
        with zipfile.ZipFile(out) as bundle:
            return bundle.read("FLASH.txt").decode()

    def rewrite_zip(self, src, transform, compression=zipfile.ZIP_DEFLATED):
        """Copy a zip, passing each member through transform(name, data); None drops it."""
        dst = self.tmp / "rewritten.zip"
        with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w", compression) as zout:
            for name in zin.namelist():
                data = transform(name, zin.read(name))
                if data is not None:
                    zout.writestr(name, data)
        return dst


class MakeBundleTest(BundleTestCase):
    def test_members_in_order_and_checksum(self):
        out, img_sha, _ = self.bundle()
        with zipfile.ZipFile(out) as bundle:
            debug_names = [Path(name).name for name in DEBUG_FILES]
            self.assertEqual(bundle.namelist(), [f"{STEM}.img", f"{STEM}.img.sha256", "sdkconfig",
                                                 "partitions.csv", "FLASH.txt", *PARTS, *debug_names,
                                                 "SHA256SUMS"])
            for info in bundle.infolist():
                self.assertEqual(info.compress_type, zipfile.ZIP_DEFLATED)
                self.assertEqual(info.extra, b"")
                self.assertEqual(info.date_time, (2026, 9, 15, 0, 0, 0))
            self.assertEqual(bundle.read(f"{STEM}.img"), self.img.read_bytes())
            self.assertEqual(bundle.read(f"{STEM}.img.sha256").decode(), f"{img_sha}  {STEM}.img\n")
            self.assertEqual(bundle.read("sdkconfig").decode(), SDKCONFIG)
            self.assertEqual(bundle.read("partitions.csv").decode(), PARTITIONS)
            for name, entry in zip(PARTS, ENTRIES):
                self.assertEqual(bundle.read(name), self.part_path(entry).read_bytes())
            for relative in DEBUG_FILES:
                self.assertEqual(bundle.read(Path(relative).name), (self.build_dir / relative).read_bytes())
            summed = [f"{STEM}.img", "sdkconfig", "partitions.csv", "FLASH.txt", *PARTS, *debug_names]
            self.assertEqual(bundle.read("SHA256SUMS").decode(),
                             "".join(f"{hashlib.sha256(bundle.read(name)).hexdigest()}  {name}\n"
                                     for name in summed))
            flash = bundle.read("FLASH.txt").decode()
        self.assertEqual(img_sha, hashlib.sha256(self.img.read_bytes()).hexdigest())
        self.assertIn("Chip: esp32s3\n", flash)
        self.assertIn("Flash offset: 0x0\n", flash)
        self.assertIn("Application partition (main.avm): 0x250000\n", flash)
        self.assertIn("--flash_mode dio --flash_freq 80m --flash_size detect", flash)
        self.assertIn(f"    0x0 {STEM}.img\n", flash)
        self.assertIn("erases the NVS partition", flash)
        self.assertIn("sha256sum -c SHA256SUMS", flash)

    def test_deterministic(self):
        out1, _, sha1 = self.bundle()
        out2, _, sha2 = self.bundle(out_dir=self.tmp / "dist2")
        self.assertEqual(out1.read_bytes(), out2.read_bytes())
        self.assertEqual(sha1, sha2)
        self.assertEqual(sha1, hashlib.sha256(out1.read_bytes()).hexdigest())

    def test_partition_csv_follows_sdkconfig(self):
        (self.esp32_dir / "sdkconfig").write_text(
            SDKCONFIG.replace('"partitions.csv"', '"partitions-elixir.csv"'))
        (self.esp32_dir / "partitions-elixir.csv").write_text(PARTITIONS.replace("0x250000", "0x300000"))
        out, _, _ = self.bundle()
        with zipfile.ZipFile(out) as bundle:
            self.assertIn("0x300000", bundle.read("partitions.csv").decode())
            self.assertIn("Application partition (main.avm): 0x300000\n", bundle.read("FLASH.txt").decode())

    def test_flash_txt_install(self):
        flash = self.flash_txt()
        self.assertIn("AtomVM build: nightly-0.7+20260915.02e1603\n", flash)
        self.assertIn("ESP-IDF: 5.5.4\n", flash)
        self.assertIn("  esptool.py --chip esp32s3 --port /dev/ttyUSB0 --baud 921600 erase_flash\n", flash)
        self.assertIn("    --flash_mode dio --flash_freq 80m --flash_size detect \\\n"
                      f"    0x0 {STEM}.img\n", flash)
        self.assertIn("erases the NVS partition", flash)

    def test_flash_txt_update(self):
        flash = self.flash_txt()
        self.assertIn(
            "  esptool.py --chip esp32s3 --port /dev/ttyUSB0 --baud 921600 --after no_reset \\\n"
            "    verify_flash 0x8000 partition-table.bin && \\\n"
            "  esptool.py --chip esp32s3 --port /dev/ttyUSB0 --baud 921600 \\\n"
            "    --before default_reset --after hard_reset write_flash \\\n"
            "    0x10000 atomvm-esp32.bin 0x1d0000 esp32boot.avm\n", flash)
        self.assertIn("newer ESP-IDF than this\n  image (5.5.4)", flash)
        self.assertIn('"boot: ESP-IDF v5.5.4 2nd stage bootloader"', flash)
        self.assertIn("  0x0       bootloader.bin\n  0x8000    partition-table.bin\n"
                      "  0x10000   atomvm-esp32.bin\n  0x1d0000  esp32boot.avm\n", flash)

    def test_flash_txt_follows_layout_and_flavor(self):
        self.flasher_args["boot.avm"]["offset"] = "0x180000"
        self.use_boot_library("elixir_esp32boot.avm")
        flash = self.flash_txt(stem=ELIXIR_STEM)
        self.assertIn("    0x10000 atomvm-esp32.bin 0x180000 elixir_esp32boot.avm\n", flash)
        self.assertIn("  0x180000  elixir_esp32boot.avm\n", flash)

    def test_unknown_stamp_and_idf_version(self):
        (self.esp32_dir / "sdkconfig").write_text('CONFIG_PARTITION_TABLE_CUSTOM_FILENAME="partitions.csv"\n')
        flash = self.flash_txt()
        self.assertIn("AtomVM build: unknown\n", flash)
        self.assertIn("ESP-IDF: unknown\n", flash)

    def test_flash_txt_debugging(self):
        flash = self.flash_txt()
        elf_sha = hashlib.sha256(APP_ELF).hexdigest()
        self.assertIn(f'"ELF file SHA256:  {elf_sha[:9]}..."', flash)
        self.assertIn(f"  {elf_sha}\n", flash)
        self.assertIn("  python -m esp_idf_monitor --port /dev/ttyUSB0 --target esp32s3 \\\n"
                      "    atomvm-esp32.elf bootloader.elf\n", flash)
        self.assertIn("  addr2line -pfiaC -e atomvm-esp32.elf <address>...\n", flash)
        self.assertIn('then "source" it in GDB', flash)

    def test_rejects_missing_debug_file(self):
        (self.build_dir / "bootloader" / "bootloader.map").unlink()
        with self.assertRaisesRegex(make_bundle.BundleError, "bootloader.map not found"):
            self.bundle()

    def test_boot_library_keeps_its_name(self):
        self.use_boot_library("elixir_esp32boot.avm")
        out, _, _ = self.bundle(stem=ELIXIR_STEM)
        with zipfile.ZipFile(out) as bundle:
            self.assertIn("elixir_esp32boot.avm", bundle.namelist())
            self.assertNotIn("esp32boot.avm", bundle.namelist())

    def test_offset_table(self):
        self.assertEqual(make_bundle.FLASH_OFFSETS["esp32"], 0x1000)
        self.assertEqual(make_bundle.FLASH_OFFSETS["esp32s2"], 0x1000)
        self.assertEqual(make_bundle.FLASH_OFFSETS["esp32c5"], 0x2000)
        self.assertEqual(make_bundle.FLASH_OFFSETS["esp32p4"], 0x2000)
        self.assertEqual(len(make_bundle.FLASH_OFFSETS), 10)
        with self.assertRaises(ValueError):
            self.bundle(chip="esp8266")
        self.flasher_args["bootloader"]["offset"] = "0x1000"
        self.save_flasher_args()
        with self.assertRaisesRegex(make_bundle.BundleError, "table value 0x0"):
            self.bundle()

    def test_rejects_flash_entry_changes(self):
        cases = [
            ("phy entry", lambda a: a.update(phy={"offset": "0xf000", "file": "phy_init_data.bin"}),
             "unexpected flash entries phy; the bundle covers bootloader, partition-table, app, boot.avm"),
            ("nvs entry", lambda a: a.update(nvs={"offset": "0x9000", "file": "nvs.bin"}),
             "unexpected flash entries nvs"),
            ("missing entry", lambda a: a.pop("boot.avm"), "missing flash entries boot.avm"),
            ("missing file", lambda a: a["boot.avm"].update(file="missing/esp32boot.avm"),
             "boot.avm file .*/missing/esp32boot.avm not found"),
        ]
        original = json.dumps(self.flasher_args)
        for label, change, pattern in cases:
            with self.subTest(label):
                self.flasher_args = json.loads(original)
                change(self.flasher_args)
                self.save_flasher_args()
                with self.assertRaisesRegex(make_bundle.BundleError, pattern):
                    self.bundle()

    def test_rejects_part_missing_from_image(self):
        image = self.img.read_bytes()
        for label, index, pattern in (("app", 0x10000 + 5, "atomvm-esp32.bin differs from the image at 0x10000"),
                                      ("boot", 0x1d0000 + 30, "esp32boot.avm differs from the image at 0x1d0000")):
            with self.subTest(label):
                self.img.write_bytes(flip_byte(image, index))
                with self.assertRaisesRegex(make_bundle.BundleError, pattern):
                    self.bundle()

    def run_cli(self, build_dir=True):
        args = [sys.executable, str(SCRIPTS / "make_bundle.py"), "--stem", STEM, "--chip", "esp32s3",
                "--img", str(self.img), "--sdkconfig", str(self.esp32_dir / "sdkconfig"),
                "--partitions-dir", str(self.esp32_dir), "--out", str(self.tmp / "dist"),
                "--source-date-epoch", "1789430400"]
        if build_dir:
            args += ["--build-dir", str(self.build_dir)]
        return subprocess.run(args, capture_output=True, text=True, cwd=self.tmp)

    def test_cli(self):
        proc = self.run_cli()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue((self.tmp / "dist" / f"{STEM}.zip").exists())
        self.assertIn(f"  {STEM}.img\n", proc.stdout)

    def test_cli_requires_build_dir(self):
        proc = self.run_cli(build_dir=False)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("--build-dir", proc.stderr)

    def test_cli_reports_bundle_error(self):
        self.flasher_args["phy"] = {"offset": "0xf000", "file": "phy_init_data.bin"}
        self.save_flasher_args()
        proc = self.run_cli()
        self.assertEqual(proc.returncode, 1)
        self.assertTrue(proc.stderr.startswith("make_bundle: "), proc.stderr)
        self.assertIn("unexpected flash entries phy", proc.stderr)


@unittest.skipUnless(shutil.which("escript"), "escript not available")
class VerifyBundleTest(BundleTestCase):
    script = SCRIPTS / "verify_bundle.escript"

    def verify(self, bundle, stem=STEM):
        return subprocess.run(["escript", str(self.script), str(bundle), stem],
                              capture_output=True, text=True)

    def test_accepts_good_bundle(self):
        out, img_sha, _ = self.bundle()
        proc = self.verify(out)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(img_sha, proc.stdout)
        for entry in ENTRIES:
            self.assertIn(hashlib.sha256(self.part_path(entry).read_bytes()).hexdigest(), proc.stdout)
        self.assertIn(f"atomvm-esp32.elf: {len(APP_ELF)} bytes, sha256 {hashlib.sha256(APP_ELF).hexdigest()}",
                      proc.stdout)

    def test_accepts_elixir_bundle(self):
        self.use_boot_library("elixir_esp32boot.avm")
        out, _, _ = self.bundle(stem=ELIXIR_STEM)
        proc = self.verify(out, ELIXIR_STEM)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_rejects_wrong_members(self):
        out, _, _ = self.bundle()
        for stem in ("AtomVM-esp32-nightly-0.7", ELIXIR_STEM):
            with self.subTest(stem):
                proc = self.verify(out, stem)
                self.assertEqual(proc.returncode, 1)
                self.assertIn("members", proc.stderr)
        dropped = self.rewrite_zip(out, lambda name, data: None if name == "esp32boot.avm" else data)
        proc = self.verify(dropped)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("members", proc.stderr)

    def test_rejects_tampered_members(self):
        out, _, _ = self.bundle()
        for member in (f"{STEM}.img", "atomvm-esp32.bin", "FLASH.txt", "atomvm-esp32.elf"):
            with self.subTest(member):
                tampered = self.rewrite_zip(
                    out, lambda name, data: flip_byte(data, len(data) // 2) if name == member else data)
                proc = self.verify(tampered)
                self.assertEqual(proc.returncode, 1)
                self.assertIn("sha256_mismatch", proc.stderr)

    def test_rejects_sha256sums_listing(self):
        out, _, _ = self.bundle()
        shortened = self.rewrite_zip(
            out, lambda name, data: b"".join(data.splitlines(True)[:-1]) if name == "SHA256SUMS" else data)
        proc = self.verify(shortened)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("sha256_names", proc.stderr)

    def test_rejects_unsupported_compression(self):
        try:
            import lzma  # noqa: F401
        except ImportError:
            self.skipTest("lzma not available")
        out, _, _ = self.bundle()
        lzma_zip = self.rewrite_zip(out, lambda name, data: data, zipfile.ZIP_LZMA)
        proc = self.verify(lzma_zip)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("unzip", proc.stderr)


if __name__ == "__main__":
    unittest.main()
