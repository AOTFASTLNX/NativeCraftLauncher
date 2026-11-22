"""Regression coverage for the C0 iOS harness driver.

The interesting failures here are silent ones: signing a macOS object into an iOS app, or
reporting an IPA as installable when the embedded profile lists no devices.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch
import tempfile
import subprocess
import shutil
import plistlib
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from build_c0_harness import (DEFAULT_DYLIB, parse_platform, parse_profile_devices,
                              provisioned_devices, validate_dylib, archive, export_archive,validate_shader_bundle,verify_simulator_embedded_libraries)  # noqa: E402
import build_c0_harness as harness  # noqa: E402
from game_versions import DEFAULT_VERSION  # noqa: E402
import json
import hashlib

class EmbeddedLibraryTests(unittest.TestCase):
    def test_native_input_must_actually_be_copied_to_app(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);app=root/'App.app';(app/'Frameworks').mkdir(parents=True)
            record={'path':'/input/libopenal.dylib','sha256':hashlib.sha256(b'library fixture').hexdigest()}
            with self.assertRaisesRegex(ValueError,'missing or changed'):verify_simulator_embedded_libraries(app,[record])
            target=app/'Frameworks/libopenal.dylib';target.write_bytes(b'library fixture')
            self.assertIn('libopenal.dylib',verify_simulator_embedded_libraries(app,[record]))
            target.write_bytes(b'modified')
            with self.assertRaisesRegex(ValueError,'missing or changed'):verify_simulator_embedded_libraries(app,[record])

class ShaderBundleTests(unittest.TestCase):
    def fixture(self,root):
        directory=root/'bundle';directory.mkdir();key='a'*64;binary=directory/(key+'.metallib');binary.write_bytes(b'first-party metadata fixture')
        index=directory/'index.json';index.write_text(json.dumps({'schema':1,'programs':[{'key':key,'status':'TRANSLATED','metallibSHA256':hashlib.sha256(binary.read_bytes()).hexdigest()}]}))
        (root/'result.json').write_text(json.dumps({'status':'VANILLA_SHADERS_PRECOMPILED','sdk':'iphonesimulator','indexSHA256':hashlib.sha256(index.read_bytes()).hexdigest()}))
        return directory,binary
    def test_shader_bundle_sdk_is_checked(self):
        with tempfile.TemporaryDirectory() as temp:
            directory,_=self.fixture(Path(temp));self.assertEqual(validate_shader_bundle(directory,True)['programs'],1)
            with self.assertRaisesRegex(ValueError,'selected SDK'):validate_shader_bundle(directory,False)
    def test_modified_metallib_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            directory,binary=self.fixture(Path(temp));binary.write_bytes(b'modified')
            with self.assertRaisesRegex(ValueError,'fingerprint changed'):validate_shader_bundle(directory,True)
    def test_unexpected_shader_bundle_files_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            directory,_=self.fixture(Path(temp));(directory/'unrelated').write_text('unexpected')
            with self.assertRaisesRegex(ValueError,'Unexpected'):validate_shader_bundle(directory,True)
    def test_exfat_sidecars_do_not_hide_modified_or_missing_shader_content(self):
        with tempfile.TemporaryDirectory() as temp:
            directory,binary=self.fixture(Path(temp))
            (directory/('._'+binary.name)).write_bytes(b'AppleDouble metadata')
            (directory/'.DS_Store').write_bytes(b'Finder metadata')
            self.assertEqual(validate_shader_bundle(directory,True)['programs'],1)
            binary.write_bytes(b'modified real shader')
            with self.assertRaisesRegex(ValueError,'fingerprint changed'):validate_shader_bundle(directory,True)
            binary.unlink()
            with self.assertRaises(FileNotFoundError):validate_shader_bundle(directory,True)


class OutputPreservationTests(unittest.TestCase):
    def test_independent_log_writers_preserve_host_exit_record(self):
        compiler = shutil.which("clang")
        if not compiler:
            self.skipTest("host C compiler unavailable")
        source = Path(__file__).resolve().parent.parent / "interop/diagnostics/C0LogAppendProbe.c"
        with tempfile.TemporaryDirectory() as temp:
            binary = Path(temp) / "append-log-probe"
            subprocess.run([compiler, "-std=c11", "-Wall", "-Wextra", "-Werror",
                            "-fsanitize=address,undefined", str(source), "-o", str(binary)],
                           check=True, capture_output=True, timeout=30)
            result = subprocess.run([str(binary)], check=True, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.stdout.strip(), "C0_APPEND_LOG_PASS")

    def test_existing_archive_is_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);old=root/"C0Harness.xcarchive";old.mkdir();marker=old/"keep.txt";marker.write_text("evidence")
            with patch("build_c0_harness.HARNESS_DIR",root),patch("build_c0_harness.run") as run:
                with self.assertRaisesRegex(RuntimeError,"choose a new --output"):
                    archive(Path("image.dylib"),"Debug",10)
                run.assert_not_called()
            self.assertEqual(marker.read_text(),"evidence")
    def test_existing_export_is_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);old=root/"export";old.mkdir();marker=old/"keep.txt";marker.write_text("evidence")
            with patch("build_c0_harness.HARNESS_DIR",root),patch("build_c0_harness.run") as run:
                with self.assertRaisesRegex(RuntimeError,"choose a new --output"):
                    export_archive(10)
                run.assert_not_called()
            self.assertEqual(marker.read_text(),"evidence")

VTOOL_IOS = """
client-graph.dylib:
Load command 2
      cmd LC_BUILD_VERSION
  cmdsize 32
 platform IOS
    minos 17.0
      sdk 27.2
"""

VTOOL_MACOS = VTOOL_IOS.replace("platform IOS", "platform MACOS")
LIPO_ARM64 = ("Non-fat file: client-graph.dylib is architecture: arm64\n")


class PlatformParsingTests(unittest.TestCase):
    def test_ios_arm64_is_recognised(self):
        facts = parse_platform(VTOOL_IOS, LIPO_ARM64)
        self.assertEqual(facts["platform"], "IOS")
        self.assertEqual(facts["minos"], "17.0")
        self.assertIn("arm64", facts["architectures"])

    def test_macos_object_is_not_ios(self):
        facts = parse_platform(VTOOL_MACOS, LIPO_ARM64)
        self.assertEqual(facts["platform"], "MACOS")
        self.assertNotEqual(facts["platform"], "IOS")

    def test_missing_build_version_yields_none(self):
        facts = parse_platform("", "")
        self.assertIsNone(facts["platform"])
        self.assertIsNone(facts["minos"])
        self.assertIsNone(facts["architectures"])


class ProfileParsingTests(unittest.TestCase):
    PROFILE = """<dict>
        <key>Name</key><string>iOS Team Provisioning Profile: org.example</string>
        <key>ProvisionedDevices</key>
        <array>
            <string>00008160-001A78620A600036</string>
            <string>00008112001218ea0aa3401e</string>
        </array>
    </dict>"""

    def test_devices_are_extracted_in_both_udid_shapes(self):
        self.assertEqual(
            parse_profile_devices(self.PROFILE),
            sorted(["00008112001218ea0aa3401e", "00008160-001A78620A600036"]))

    def test_empty_array_means_no_registered_devices(self):
        text = self.PROFILE.replace(
            "<string>00008160-001A78620A600036</string>\n            "
            "<string>00008112001218ea0aa3401e</string>", "")
        self.assertEqual(parse_profile_devices(text), [])

    def test_missing_key_means_no_registered_devices(self):
        self.assertEqual(parse_profile_devices("<dict><key>Name</key><string>x</string></dict>"), [])

    def test_device_outside_the_array_is_ignored(self):
        text = ("<dict><key>Name</key><string>00008160-001A78620A600036</string>"
                "<key>ProvisionedDevices</key><array></array></dict>")
        self.assertEqual(parse_profile_devices(text), [])

    def test_archive_without_profile_lists_nothing(self):
        self.assertEqual(provisioned_devices(Path("/nonexistent/C0Harness.xcarchive")), [])


class DylibValidationTests(unittest.TestCase):
    def test_missing_dylib_is_refused(self):
        with self.assertRaises(SystemExit):
            validate_dylib(Path("/nonexistent/client-graph.dylib"))

    def test_linked_artifact_is_ios_arm64(self):
        if not DEFAULT_DYLIB.is_file():
            self.skipTest("no linked image present")
        facts = validate_dylib(DEFAULT_DYLIB)
        self.assertEqual(facts["platform"], "IOS")
        self.assertIn("arm64", facts["architectures"])
        self.assertEqual(len(facts["sha256"]), 64)
        self.assertGreater(facts["bytes"], 0)


if __name__ == "__main__":
    unittest.main()
class PackageProfileTests(unittest.TestCase):
    """The harness plist must carry the selected game line, not a stale default."""

    def test_default_line_keeps_26_1_2_and_asset_index_30(self):
        profile = harness.package_profile()
        self.assertEqual(profile, {"minecraftVersion": DEFAULT_VERSION,
                                   "assetIndex": "30", "testVersion": ""})
        settings = harness.package_settings(profile)
        self.assertIn("FAOT_MINECRAFT_VERSION=" + DEFAULT_VERSION, settings)
        self.assertIn("FAOT_ASSET_INDEX=30", settings)
        self.assertIn("FAOT_TEST_VERSION=", settings)

    def test_1_21_1_requires_its_own_asset_index(self):
        with self.assertRaisesRegex(ValueError, "explicit --asset-index"):
            harness.package_profile("1.21.1")
        profile = harness.package_profile("1.21.1", "17", "1211-r1")
        self.assertEqual(profile["assetIndex"], "17")
        self.assertEqual(profile["testVersion"], "1211-r1")
        settings = harness.package_settings(profile)
        self.assertIn("FAOT_MINECRAFT_VERSION=1.21.1", settings)
        self.assertIn("FAOT_ASSET_INDEX=17", settings)
        self.assertIn("FAOT_TEST_VERSION=1211-r1", settings)

    def test_asset_index_and_version_are_validated(self):
        for value in ("", "17=x", "abc", "../17", 17):
            with self.subTest(value=value), self.assertRaises(ValueError):
                harness.package_profile("1.21.1", value)
        with self.assertRaises(ValueError):
            harness.package_profile("1.20.1", "17")
        with self.assertRaises(ValueError):
            harness.package_profile(DEFAULT_VERSION, "30", "../escape")

    def test_packaged_plist_is_read_back_and_mismatches_are_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Path(temp) / "C0Harness.app"
            app.mkdir()
            profile = harness.package_profile("1.21.1", "17", "1211-r1")
            good = {"FAOTMinecraftVersion": "1.21.1", "FAOTAssetIndex": "17",
                    "FAOTTestVersion": "1211-r1"}
            (app / "Info.plist").write_bytes(plistlib.dumps(good))
            self.assertEqual(harness.validate_package_profile(app, profile)["assetIndex"], "17")
            for wrong in ({"FAOTMinecraftVersion": "26.1.2"},
                          {"FAOTAssetIndex": "30"},
                          {"FAOTTestVersion": "1211-r2"}):
                with self.subTest(wrong=wrong):
                    (app / "Info.plist").write_bytes(plistlib.dumps({**good, **wrong}))
                    with self.assertRaisesRegex(ValueError, "differs from the build inputs"):
                        harness.validate_package_profile(app, profile)

    def test_unexpanded_placeholder_is_a_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Path(temp) / "C0Harness.app"
            app.mkdir()
            (app / "Info.plist").write_bytes(plistlib.dumps(
                {"FAOTMinecraftVersion": "$(FAOT_MINECRAFT_VERSION)",
                 "FAOTAssetIndex": "$(FAOT_ASSET_INDEX)", "FAOTTestVersion": ""}))
            with self.assertRaisesRegex(ValueError, "differs from the build inputs"):
                harness.validate_package_profile(app, harness.package_profile())

    def test_missing_plist_is_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError, "no Info.plist"):
                harness.validate_package_profile(Path(temp) / "C0Harness.app",
                                                 harness.package_profile())

    def test_exported_ipa_plist_is_checked(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            ipa = root / "C0Harness.ipa"
            with zipfile.ZipFile(ipa, "w") as bundle:
                bundle.writestr("Payload/C0Harness.app/Info.plist", plistlib.dumps(
                    {"FAOTMinecraftVersion": "1.21.1", "FAOTAssetIndex": "17",
                     "FAOTTestVersion": "1211-r1"}))
            profile = harness.package_profile("1.21.1", "17", "1211-r1")
            self.assertEqual(harness.validate_exported_profile(ipa, profile)["testVersion"], "1211-r1")
            with self.assertRaisesRegex(ValueError, "differs from the build inputs"):
                harness.validate_exported_profile(
                    ipa, harness.package_profile("1.21.1", "17", "1211-r2"))
            empty = root / "empty.ipa"
            with zipfile.ZipFile(empty, "w") as bundle:
                bundle.writestr("Payload/other.txt", b"x")
            with self.assertRaisesRegex(ValueError, "Expected one Payload app Info.plist"):
                harness.validate_exported_profile(empty, profile)

    def test_archive_passes_the_profile_settings_and_checks_the_archived_app(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            profile = harness.package_profile("1.21.1", "17", "1211-r1")
            seen = {}

            def fake_run(argv, timeout=3600):
                seen["argv"] = argv
                archive_path = Path(argv[argv.index("-archivePath") + 1])
                app = archive_path / "Products/Applications/C0Harness.app"
                app.mkdir(parents=True)
                (app / "Info.plist").write_bytes(plistlib.dumps(
                    {"FAOTMinecraftVersion": "1.21.1", "FAOTAssetIndex": "17",
                     "FAOTTestVersion": "1211-r1"}))
                (archive_path / "Info.plist").write_bytes(plistlib.dumps({"ApplicationProperties": {}}))
                return {"argv": argv, "exitCode": 0, "stdout": "", "stderr": ""}

            with patch.object(harness, "HARNESS_DIR", root), \
                    patch.object(harness, "run", side_effect=fake_run):
                outcome = harness.archive(Path("/tmp/client-graph.dylib"), "Debug", 10, None, profile)
            self.assertTrue(outcome["succeeded"])
            self.assertEqual(outcome["profile"]["assetIndex"], "17")
            self.assertIn("FAOT_MINECRAFT_VERSION=1.21.1", seen["argv"])
            self.assertIn("FAOT_ASSET_INDEX=17", seen["argv"])
            self.assertIn("FAOT_TEST_VERSION=1211-r1", seen["argv"])

    def test_archive_with_a_stale_plist_fails_instead_of_reporting_success(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)

            def fake_run(argv, timeout=3600):
                archive_path = Path(argv[argv.index("-archivePath") + 1])
                app = archive_path / "Products/Applications/C0Harness.app"
                app.mkdir(parents=True)
                (app / "Info.plist").write_bytes(plistlib.dumps(
                    {"FAOTMinecraftVersion": "26.1.2", "FAOTAssetIndex": "30",
                     "FAOTTestVersion": ""}))
                (archive_path / "Info.plist").write_bytes(plistlib.dumps({}))
                return {"argv": argv, "exitCode": 0, "stdout": "", "stderr": ""}

            with patch.object(harness, "HARNESS_DIR", root), \
                    patch.object(harness, "run", side_effect=fake_run):
                outcome = harness.archive(Path("/tmp/client-graph.dylib"), "Debug", 10, None,
                                          harness.package_profile("1.21.1", "17", "1211-r1"))
            self.assertFalse(outcome["succeeded"])
            self.assertIn("differs from the build inputs", outcome["profileError"])

    def test_export_renames_the_ipa_to_the_test_label_and_verifies_it(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            profile = harness.package_profile("1.21.1", "17", "1211-r1")

            def fake_run(argv, timeout=3600):
                out = Path(argv[argv.index("-exportPath") + 1])
                out.mkdir(parents=True)
                with zipfile.ZipFile(out / "C0Harness.ipa", "w") as bundle:
                    bundle.writestr("Payload/C0Harness.app/Info.plist", plistlib.dumps(
                        {"FAOTMinecraftVersion": "1.21.1", "FAOTAssetIndex": "17",
                         "FAOTTestVersion": "1211-r1"}))
                return {"argv": argv, "exitCode": 0, "stdout": "", "stderr": ""}

            with patch.object(harness, "HARNESS_DIR", root), \
                    patch.object(harness, "run", side_effect=fake_run):
                outcome = harness.export_archive(10, profile)
            self.assertTrue(outcome["succeeded"])
            self.assertEqual(Path(outcome["ipa"]).name, "NCL-game-1211-r1.ipa")
            self.assertEqual(outcome["profile"]["testVersion"], "1211-r1")

    def test_export_without_a_label_keeps_the_default_ipa_name(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)

            def fake_run(argv, timeout=3600):
                out = Path(argv[argv.index("-exportPath") + 1])
                out.mkdir(parents=True)
                with zipfile.ZipFile(out / "C0Harness.ipa", "w") as bundle:
                    bundle.writestr("Payload/C0Harness.app/Info.plist", plistlib.dumps(
                        {"FAOTMinecraftVersion": DEFAULT_VERSION, "FAOTAssetIndex": "30",
                         "FAOTTestVersion": ""}))
                return {"argv": argv, "exitCode": 0, "stdout": "", "stderr": ""}

            with patch.object(harness, "HARNESS_DIR", root), \
                    patch.object(harness, "run", side_effect=fake_run):
                outcome = harness.export_archive(10)
            self.assertTrue(outcome["succeeded"])
            self.assertEqual(Path(outcome["ipa"]).name, "C0Harness.ipa")
            self.assertEqual(outcome["profile"]["minecraftVersion"], DEFAULT_VERSION)

    def test_export_with_a_wrong_plist_does_not_report_success(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)

            def fake_run(argv, timeout=3600):
                out = Path(argv[argv.index("-exportPath") + 1])
                out.mkdir(parents=True)
                with zipfile.ZipFile(out / "C0Harness.ipa", "w") as bundle:
                    bundle.writestr("Payload/C0Harness.app/Info.plist", plistlib.dumps(
                        {"FAOTMinecraftVersion": "26.1.2", "FAOTAssetIndex": "30",
                         "FAOTTestVersion": ""}))
                return {"argv": argv, "exitCode": 0, "stdout": "", "stderr": ""}

            with patch.object(harness, "HARNESS_DIR", root), \
                    patch.object(harness, "run", side_effect=fake_run):
                outcome = harness.export_archive(10, harness.package_profile("1.21.1", "17", "1211-r1"))
            self.assertFalse(outcome["succeeded"])
            self.assertIn("differs from the build inputs", outcome["profileError"])

    def test_simulator_build_receives_the_profile_settings(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            profile = harness.package_profile("1.21.1", "17", "1211-r1")
            seen = {}

            def fake_run(argv, timeout=3600):
                seen["argv"] = argv
                derived = Path(argv[argv.index("-derivedDataPath") + 1])
                app = derived / "Build/Products/Debug-iphonesimulator/C0Harness.app"
                app.mkdir(parents=True)
                (app / "Info.plist").write_bytes(plistlib.dumps(
                    {"FAOTMinecraftVersion": "1.21.1", "FAOTAssetIndex": "17",
                     "FAOTTestVersion": "1211-r1"}))
                return {"argv": argv, "exitCode": 0, "stdout": "", "stderr": ""}

            with patch.object(harness, "HARNESS_DIR", root), \
                    patch.object(harness, "run", side_effect=fake_run):
                outcome = harness.build_for_simulator(
                    Path("/tmp/client-graph.dylib"), "UDID", "Debug", 10, None, profile)
            self.assertTrue(outcome["succeeded"])
            self.assertIn("FAOT_MINECRAFT_VERSION=1.21.1", seen["argv"])
            self.assertIn("FAOT_ASSET_INDEX=17", seen["argv"])
            self.assertEqual(outcome["profile"]["assetIndex"], "17")

    def test_main_records_the_selected_profile_in_its_result(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "harness"

            def fake_archive(dylib, configuration, timeout, native_dir=None, profile=None):
                return {"argv": [], "exitCode": 0, "succeeded": True,
                        "archivePath": str(output), "profile": dict(profile or {})}

            with patch.object(harness, "validate_dylib", return_value={"sha256": "0" * 64}), \
                    patch.object(harness, "archive", side_effect=fake_archive), \
                    patch.object(harness, "export_archive", return_value={
                        "exitCode": 0, "succeeded": False, "ipa": None, "ipaBytes": None,
                        "ipaSHA256": None, "stderr": "", "stdout": ""}), \
                    patch("builtins.print"):
                code = harness.main(["--output", str(output), "--minecraft-version", "1.21.1",
                                     "--asset-index", "17", "--test-version", "1211-r1"])
            self.assertEqual(code, 1)
            result = json.loads((output / "result.json").read_text())
            self.assertEqual(result["minecraftVersion"], "1.21.1")
            self.assertEqual(result["assetIndex"], "17")
            self.assertEqual(result["testVersion"], "1211-r1")
            self.assertEqual(result["status"], "EXPORT_FAILED")

    def test_main_refuses_a_non_default_line_without_an_asset_index(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(SystemExit), patch("sys.stderr"):
                harness.main(["--output", str(Path(temp) / "harness"),
                              "--minecraft-version", "1.21.1"])


if __name__ == "__main__":
    unittest.main()
