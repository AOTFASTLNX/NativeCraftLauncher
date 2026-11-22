import json
from pathlib import Path
import plistlib
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import mcfao
from renderer_profile import (BUILD_PROVENANCE, libraries, config_names, validate_runtime,
                             packaged_asset_index)
from glfw_runtime_contract import CONTRACT, GL41_EXPORTS, validate_bytes, validate_glfw_renderer


def app_with_runtime(app, index=None, version=None):
    """A minimal app whose runtime checks pass and whose asset tree is real."""
    frameworks = app / "Frameworks"
    frameworks.mkdir()
    for name in libraries("gl41"):
        (frameworks / name).touch()
    (frameworks / 'libglfw.dylib').write_bytes(CONTRACT + b'\0')
    info = {}
    if version is not None:
        info["FAOTMinecraftVersion"] = version
    if index is not None:
        info["FAOTAssetIndex"] = index
    (app / "Info.plist").write_bytes(plistlib.dumps(info))
    return frameworks


def asset_tree(app, index):
    (app / "assets/indexes").mkdir(parents=True)
    (app / ("assets/indexes/" + index + ".json")).write_text("{}")
    (app / "assets/objects/aa").mkdir(parents=True)
    (app / "assets/objects/aa/object").write_bytes(b"fixture")

class RendererProfileTests(unittest.TestCase):
    def test_gl41_config_does_not_require_native_metal_inputs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            names = set(mcfao.CONFIG_PATHS) - {"metal", "shaders"}
            names.update(config_names("gl41"))
            config = root / "config.json"
            config.write_text(json.dumps({**{key: str(root) for key in names},
                                          "metal": str(root / "retired-metal")}))
            self.assertNotIn("metal", mcfao.load_config(config, "gl41"))
            with self.assertRaises(ValueError):
                mcfao.load_config(config, "metal")

    def test_provenance_is_bound_to_renderer_build_and_each_dylib(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Path(temp)
            path = app / BUILD_PROVENANCE
            uuids = {name: f"uuid-for-{name}" for name in libraries("gl41")}
            record = dict(schemaVersion=1, buildKey="build", sourceCommit="commit",
                          renderer="gl41", dylibUUIDs=uuids)
            path.write_text(json.dumps(record))
            with patch("mcfao.dylib_uuid", side_effect=lambda image: f"uuid-for-{image.name}"):
                self.assertEqual(mcfao.validate_build_provenance(app, "build", "commit", "gl41"), record)
                with self.assertRaises(ValueError):
                    mcfao.validate_build_provenance(app, "stale", "commit", "gl41")
                uuids["client-graph.dylib"] = "older-client"
                path.write_text(json.dumps(record))
                with self.assertRaises(ValueError):
                    mcfao.validate_build_provenance(app, "build", "commit", "gl41")

    def test_gl41_contract_rejects_missing_libraries_and_does_not_read_metal_shaders(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Path(temp)
            frameworks = app_with_runtime(app, index="30", version="26.1.2")
            asset_tree(app, "30")
            with patch("build_c0_harness.validate_dylib") as validate, patch('glfw_runtime_contract.subprocess.check_output', return_value='\n'.join('_' + name for name in GL41_EXPORTS)):
                validate_runtime(app, "gl41")
                self.assertEqual(validate.call_count, 7)
                # exFAT volumes add `._name` sidecars next to every file; they are
                # metadata and must not read as unexpected runtime libraries.
                for name in libraries("gl41"):
                    (frameworks / ("._" + name)).write_bytes(b"\x00\x05\x16\x07Mac OS X")
                validate_runtime(app, "gl41")
                self.assertEqual(validate.call_count, 14)
                for name in libraries("gl41"):
                    (frameworks / ("._" + name)).unlink()
                (frameworks / "libOSMesa.8.dylib").unlink()
                with self.assertRaisesRegex(ValueError, "libOSMesa"):
                    validate_runtime(app, "gl41")

    def test_placeholder_and_host_libraries_cannot_pass_integrity(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Path(temp); directory = app / "Frameworks"; directory.mkdir()
            for name in libraries("gl41"):
                (directory / name).write_bytes(b"not a dylib")
            with self.assertRaises((ValueError, OSError, SystemExit)):
                validate_runtime(app, "gl41")

    def test_packaged_1211_app_validates_against_its_own_asset_index(self):
        # vanilla-r2 built the app and then failed because the validator demanded
        # 26.1.2's 30.json. A 1.21.1 app carries index 17 and must be accepted.
        with tempfile.TemporaryDirectory() as temp:
            app = Path(temp)
            app_with_runtime(app, index="17", version="1.21.1")
            asset_tree(app, "17")
            with patch("build_c0_harness.validate_dylib"), \
                    patch('glfw_runtime_contract.subprocess.check_output',
                          return_value='\n'.join('_' + name for name in GL41_EXPORTS)):
                validate_runtime(app, "gl41")
            self.assertEqual(packaged_asset_index(app), "17")

    def test_missing_index_file_for_the_declared_version_is_reported(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Path(temp)
            app_with_runtime(app, index="17", version="1.21.1")
            asset_tree(app, "30")
            with patch("build_c0_harness.validate_dylib"), \
                    patch('glfw_runtime_contract.subprocess.check_output',
                          return_value='\n'.join('_' + name for name in GL41_EXPORTS)):
                with self.assertRaisesRegex(ValueError, "asset index 17"):
                    validate_runtime(app, "gl41")

    def test_nondefault_version_without_its_own_index_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Path(temp)
            app_with_runtime(app, version="1.21.1")
            asset_tree(app, "30")
            with patch("build_c0_harness.validate_dylib"), \
                    patch('glfw_runtime_contract.subprocess.check_output',
                          return_value='\n'.join('_' + name for name in GL41_EXPORTS)):
                with self.assertRaisesRegex(ValueError, "must declare its own FAOTAssetIndex"):
                    validate_runtime(app, "gl41")

    def test_default_line_keeps_its_historical_index_when_the_key_is_absent(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Path(temp)
            app_with_runtime(app)
            asset_tree(app, "30")
            with patch("build_c0_harness.validate_dylib"), \
                    patch('glfw_runtime_contract.subprocess.check_output',
                          return_value='\n'.join('_' + name for name in GL41_EXPORTS)):
                validate_runtime(app, "gl41")
            self.assertEqual(packaged_asset_index(app), "30")

    def test_unexpanded_placeholder_is_not_accepted_as_an_index(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Path(temp)
            app_with_runtime(app, index="$(FAOT_ASSET_INDEX)", version="1.21.1")
            with self.assertRaisesRegex(ValueError, "must declare its own FAOTAssetIndex"):
                packaged_asset_index(app)

    def test_index_that_does_not_belong_to_the_version_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Path(temp)
            app_with_runtime(app, index="30", version="1.21.1")
            with self.assertRaisesRegex(ValueError, "does not belong to Minecraft 1.21.1"):
                packaged_asset_index(app)

    def test_unknown_version_and_traversal_index_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Path(temp)
            app_with_runtime(app, index="17", version="1.20.4")
            with self.assertRaisesRegex(ValueError, "not supported"):
                packaged_asset_index(app)
            other = Path(temp) / "other"
            other.mkdir()
            app_with_runtime(other, index="../../escape", version="1.21.1")
            with self.assertRaisesRegex(ValueError, "Invalid packaged asset index"):
                packaged_asset_index(other)
            self.assertFalse((Path(temp) / "escape.json").exists())

    def test_symlinked_index_file_is_not_followed_out_of_the_bundle(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Path(temp) / "Game.app"
            app.mkdir()
            app_with_runtime(app, index="17", version="1.21.1")
            outside = Path(temp) / "outside.json"
            outside.write_text("{}")
            (app / "assets/indexes").mkdir(parents=True)
            (app / "assets/indexes/17.json").symlink_to(outside)
            (app / "assets/objects/aa").mkdir(parents=True)
            (app / "assets/objects/aa/object").write_bytes(b"fixture")
            with patch("build_c0_harness.validate_dylib"), \
                    patch('glfw_runtime_contract.subprocess.check_output',
                          return_value='\n'.join('_' + name for name in GL41_EXPORTS)):
                with self.assertRaisesRegex(ValueError, "Missing Minecraft asset index 17"):
                    validate_runtime(app, "gl41")

    def test_historical_metal_only_glfw_is_rejected_even_with_valid_input_hashes(self):
        with self.assertRaisesRegex(ValueError, 'OpenGL/OSMesa GLFW'):
            validate_bytes(b'GLFW-lite requires GLFW_NO_API; select the Metal backend\0', set(GL41_EXPORTS), 'gl41')
        with self.assertRaisesRegex(ValueError, 'Missing exports'):
            validate_bytes(CONTRACT + b'\0', set(), 'gl41')
        self.assertEqual(validate_bytes(CONTRACT + b'\0', set(GL41_EXPORTS), 'gl41')['status'], 'GLFW_RENDERER_ABI_VERIFIED')
        self.assertEqual(validate_bytes(b'legacy NO_API bridge', set(), 'metal')['renderer'], 'metal')

    def test_actual_binary_contract_is_required_instead_of_a_report_claim(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'libglfw.dylib'
            path.write_bytes(b'old library with a previously valid checksum')
            symbols = '\n'.join('_' + name for name in GL41_EXPORTS)
            with patch('glfw_runtime_contract.subprocess.check_output', return_value=symbols):
                with self.assertRaises(ValueError): validate_glfw_renderer(path, 'gl41')

    def test_unsigned_gl41_xcode_argv_is_literal_and_uses_distinct_bundle(self):
        argv = mcfao.device_xcode(Path("/tmp/stage with spaces"), Path("/tmp/assets"),
                                  renderer="gl41", unsigned=True, bundle_id="org.fabricaot.integration.gl41")
        self.assertIn("CODE_SIGNING_ALLOWED=NO", argv)
        self.assertIn("FAOT_RENDERER=gl41", argv)
        self.assertIn("PRODUCT_BUNDLE_IDENTIFIER=org.fabricaot.integration.gl41", argv)
        self.assertIn("FAOT_GRAPHICS_NATIVE_DIR=/tmp/stage with spaces", argv)

    def test_gl41_patch_preserves_split_chain_and_rejects_tampering(self):
        from bootstrap_toolchain import sha256
        from probe_client_graph import verify_split_runtime_input
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); split = root / "split"; split.mkdir(); patched = root / "gl41"; patched.mkdir()
            original = split / "client.jar"; original.write_bytes(b"split fixture")
            (split / "result.json").write_text(json.dumps({"status": "SPLIT", "outputJarSHA256": sha256(original),
                "classes": [{"methodsSplit": 1, "finalFieldsUnfinalised": 2}]}))
            jar = patched / "client.jar"; jar.write_bytes(b"patched fixture")
            (patched / "result.json").write_text(json.dumps({"status": "GL41_CLIENT_PATCHED",
                "outputJarSHA256": sha256(jar), "client": str(original), "clientSHA256": sha256(original)}))
            self.assertEqual(verify_split_runtime_input(jar)["split"]["finalFieldsUnfinalised"], 2)
            original.write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "fingerprint"):
                verify_split_runtime_input(jar)
