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
    'CONFIG_IDF_TARGET="esp32s3"\n'
    'CONFIG_PARTITION_TABLE_CUSTOM_FILENAME="partitions.csv"\n'
    "CONFIG_SPIRAM=y\n"
)

STEM = "AtomVM-esp32s3-atomgl-ipv6-libsodium-psram-nightly-0.7"
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


class BundleTestCase(unittest.TestCase):
    """Shared bundle fixture; holds no tests itself."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.img = self.tmp / f"{STEM}.img"
        self.img.write_bytes(bytes(range(256)) * 64 + b"\xff" * 4096)
        (self.tmp / "sdkconfig").write_text(SDKCONFIG)
        (self.tmp / "partitions.csv").write_text(PARTITIONS)
        self.flasher = self.tmp / "flasher_args.json"
        self.flasher.write_text(json.dumps({
            "bootloader": {"offset": "0x0"},
            "flash_settings": {"flash_mode": "dio", "flash_size": "4MB", "flash_freq": "80m"},
        }))

    def bundle(self, **overrides):
        args = dict(stem=STEM, chip="esp32s3", img_path=self.img,
                    sdkconfig_path=self.tmp / "sdkconfig", partitions_dir=self.tmp,
                    out_dir=self.tmp / "dist", epoch=1789430400,
                    flasher_args_path=self.flasher)
        args.update(overrides)
        return make_bundle.build_bundle(**args)


class MakeBundleTest(BundleTestCase):
    def test_members_in_order_and_checksum(self):
        out, img_sha, _ = self.bundle()
        with zipfile.ZipFile(out) as bundle:
            self.assertEqual(bundle.namelist(),
                             [f"{STEM}.img", f"{STEM}.img.sha256", "sdkconfig", "partitions.csv", "FLASH.txt"])
            for info in bundle.infolist():
                self.assertEqual(info.compress_type, zipfile.ZIP_DEFLATED)
                self.assertEqual(info.extra, b"")
                self.assertEqual(info.date_time, (2026, 9, 15, 0, 0, 0))
            self.assertEqual(bundle.read(f"{STEM}.img"), self.img.read_bytes())
            self.assertEqual(bundle.read(f"{STEM}.img.sha256").decode(), f"{img_sha}  {STEM}.img\n")
            self.assertEqual(bundle.read("sdkconfig").decode(), SDKCONFIG)
            self.assertEqual(bundle.read("partitions.csv").decode(), PARTITIONS)
            flash = bundle.read("FLASH.txt").decode()
        self.assertEqual(img_sha, hashlib.sha256(self.img.read_bytes()).hexdigest())
        self.assertIn("Chip: esp32s3\n", flash)
        self.assertIn("Flash offset: 0x0\n", flash)
        self.assertIn("Application partition (main.avm): 0x250000\n", flash)
        self.assertIn("--flash_mode dio --flash_freq 80m --flash_size detect", flash)
        self.assertIn(f"    0x0 {STEM}.img\n", flash)
        self.assertIn("erases the NVS partition", flash)

    def test_deterministic(self):
        out1, _, sha1 = self.bundle()
        out2, _, sha2 = self.bundle(out_dir=self.tmp / "dist2")
        self.assertEqual(out1.read_bytes(), out2.read_bytes())
        self.assertEqual(sha1, sha2)
        self.assertEqual(sha1, hashlib.sha256(out1.read_bytes()).hexdigest())

    def test_partition_csv_follows_sdkconfig(self):
        (self.tmp / "sdkconfig").write_text(
            SDKCONFIG.replace('"partitions.csv"', '"partitions-elixir.csv"'))
        (self.tmp / "partitions-elixir.csv").write_text(PARTITIONS.replace("0x250000", "0x300000"))
        out, _, _ = self.bundle()
        with zipfile.ZipFile(out) as bundle:
            self.assertIn("0x300000", bundle.read("partitions.csv").decode())
            self.assertIn("Application partition (main.avm): 0x300000\n", bundle.read("FLASH.txt").decode())

    def test_offset_table(self):
        self.assertEqual(make_bundle.FLASH_OFFSETS["esp32"], 0x1000)
        self.assertEqual(make_bundle.FLASH_OFFSETS["esp32s2"], 0x1000)
        self.assertEqual(make_bundle.FLASH_OFFSETS["esp32c5"], 0x2000)
        self.assertEqual(make_bundle.FLASH_OFFSETS["esp32p4"], 0x2000)
        self.assertEqual(len(make_bundle.FLASH_OFFSETS), 10)
        with self.assertRaises(ValueError):
            self.bundle(chip="esp8266")

    def test_flasher_args_offset_mismatch(self):
        self.flasher.write_text(json.dumps({"bootloader": {"offset": "0x1000"}}))
        with self.assertRaises(ValueError):
            self.bundle()

    def test_cli(self):
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS / "make_bundle.py"), "--stem", STEM, "--chip", "esp32s3",
             "--img", str(self.img), "--sdkconfig", str(self.tmp / "sdkconfig"),
             "--partitions-dir", str(self.tmp), "--out", str(self.tmp / "dist"),
             "--flasher-args", str(self.flasher), "--source-date-epoch", "1789430400"],
            capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue((self.tmp / "dist" / f"{STEM}.zip").exists())
        self.assertIn(f"  {STEM}.img\n", proc.stdout)


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

    def test_rejects_wrong_members(self):
        out, _, _ = self.bundle()
        proc = self.verify(out, "AtomVM-esp32-nightly-0.7")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("members", proc.stderr)

    def test_rejects_tampered_image(self):
        out, _, _ = self.bundle()
        tampered = self.tmp / "tampered.zip"
        with zipfile.ZipFile(out) as src, zipfile.ZipFile(tampered, "w", zipfile.ZIP_DEFLATED) as dst:
            for name in src.namelist():
                data = src.read(name)
                if name.endswith(".img"):
                    data = data[:-1] + b"\x00"
                dst.writestr(name, data)
        proc = self.verify(tampered)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("sha256_mismatch", proc.stderr)

    def test_rejects_unsupported_compression(self):
        try:
            import lzma  # noqa: F401
        except ImportError:
            self.skipTest("lzma not available")
        out, _, _ = self.bundle()
        lzma_zip = self.tmp / "lzma.zip"
        with zipfile.ZipFile(out) as src, zipfile.ZipFile(lzma_zip, "w", zipfile.ZIP_LZMA) as dst:
            for name in src.namelist():
                dst.writestr(name, src.read(name))
        proc = self.verify(lzma_zip)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("unzip", proc.stderr)


if __name__ == "__main__":
    unittest.main()
