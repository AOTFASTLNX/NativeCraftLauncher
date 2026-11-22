"""Versioned client acquisition, B0 preparation, and first-run wiring.

Every download in these tests is served from in-process fixtures; no official
origin is contacted and no downloaded code is executed.
"""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import acquire_client
import first_run
import prepare_b0
import stage_game_assets


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


class FakeResponse(io.BytesIO):
    def __init__(self, content, url):
        super().__init__(content)
        self.url = url
        self.status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


class FakeNetwork:
    """Serves pinned bytes for exact URLs and records every request."""

    def __init__(self, files):
        self.files = files
        self.requests = []

    def open(self, url, timeout=None):
        self.requests.append(url)
        if url not in self.files:
            raise AssertionError("unexpected download: " + url)
        return FakeResponse(self.files[url], url)


class ClientFixture(unittest.TestCase):
    version = "1.21.1"
    java_version = 21

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.output = self.root / "owned-client"
        self.client_jar = b"obfuscated client fixture"
        self.libraries = {name: name.encode() for name in
                          ("authlib.jar", "brigadier.jar", "datafixerupper.jar")}
        self.objects = {"sound.ogg": b"asset object fixture"}
        self.index = json.dumps({"objects": {name: {"hash": sha1(data), "size": len(data)}
                                             for name, data in self.objects.items()}}).encode()
        self.mappings = b"official client mappings fixture\n"
        self.files = {}
        self.metadata = self.version_metadata()
        metadata_bytes = json.dumps(self.metadata).encode()
        self.metadata_url = "https://piston-meta.mojang.com/versions/" + self.version + ".json"
        self.files[self.metadata_url] = metadata_bytes
        self.files[self.metadata["downloads"]["client"]["url"]] = self.client_jar
        for library in self.metadata["libraries"]:
            artifact = library["downloads"]["artifact"]
            self.files[artifact["url"]] = self.libraries[Path(artifact["path"]).name]
        self.files[self.metadata["assetIndex"]["url"]] = self.index
        for data in self.objects.values():
            digest = sha1(data)
            self.files["https://resources.download.minecraft.net/" + digest[:2] + "/" + digest] = data
        if "client_mappings" in self.metadata["downloads"]:
            self.files[self.metadata["downloads"]["client_mappings"]["url"]] = self.mappings
        self.manifest = {"versions": [{"id": self.version, "type": "release",
                                       "url": self.metadata_url,
                                       "sha1": sha1(metadata_bytes)}]}
        self.network = FakeNetwork(self.files)

    def version_metadata(self):
        downloads = {"client": {"url": "https://piston-data.mojang.com/client.jar",
                                "size": len(self.client_jar), "sha1": sha1(self.client_jar)}}
        if self.version == "1.21.1":
            downloads["client_mappings"] = {"url": "https://piston-data.mojang.com/mappings.txt",
                                            "size": len(self.mappings), "sha1": sha1(self.mappings)}
        libraries = [{"name": "fixture:" + name, "downloads": {"artifact": {
            "path": "fixture/" + name, "url": "https://libraries.minecraft.net/" + name,
            "size": len(data), "sha1": sha1(data)}}} for name, data in self.libraries.items()]
        digest = sha1(self.index)
        return {"id": self.version, "javaVersion": {"majorVersion": self.java_version},
                "assetIndex": {"id": "17", "size": len(self.index), "sha1": digest,
                               "url": "https://piston-meta.mojang.com/v1/packages/" + digest + "/17.json"},
                "downloads": downloads, "libraries": libraries}

    def acquire(self, **kwargs):
        engine = self.root / "engine.jar"
        engine.write_bytes(b"ldr")
        lock = {"engineLibraries": [{"file": "fabric-loader-0.19.3.jar", "bytes": 3,
                                     "sha256": sha256(b"ldr")}]}
        (self.root / "source/compiler").mkdir(parents=True, exist_ok=True)
        self.lock = self.root / "source/compiler/fabric.lock.json"
        self.lock.write_text(json.dumps(lock))
        with patch.object(acquire_client, "metadata", return_value=self.manifest), \
                patch.object(acquire_client, "ROOT", self.root / "source"), \
                patch.object(acquire_client, "fetch", side_effect=self.fetch), \
                patch("stage_game_assets.verified_assets", side_effect=self.stage_assets), \
                patch("verified_download.opener", return_value=self.network), \
                patch.object(acquire_client, "engine_url", return_value="https://maven.fabricmc.net/loader.jar"), \
                patch("loader_selection.all_engine_libraries", return_value=lock["engineLibraries"]):
            self.files["https://maven.fabricmc.net/loader.jar"] = b"ldr"
            kwargs.setdefault("version", self.version)
            return acquire_client.acquire(self.output, True, progress=lambda message: None, **kwargs)

    def fetch(self, spec, target, hosts=None):
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(self.files[spec["url"]])
        return target

    def stage_assets(self, assets, data, download=None):
        for blob in self.objects.values():
            path = Path(assets) / "objects" / sha1(blob)[:2] / sha1(blob)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(blob)
        index = Path(assets) / "indexes" / (data["assetIndex"]["id"] + ".json")
        index.parent.mkdir(parents=True, exist_ok=True)
        index.write_bytes(self.index)


class Acquire1211Tests(ClientFixture):
    version = "1.21.1"

    def test_records_version_java_and_official_mappings_descriptor(self):
        result = self.acquire()
        self.assertEqual(result["status"], "OWNED_CLIENT_INPUTS_VERIFIED")
        self.assertEqual(result["minecraftVersion"], "1.21.1")
        self.assertEqual(result["javaVersion"], 21)
        self.assertEqual(result["runtimeNamespace"], "official")
        self.assertTrue(result["requiresRemapping"])
        mappings = result["mappings"]
        self.assertEqual(Path(mappings["path"]), self.output / "meta/mappings/1.21.1-client.txt")
        self.assertEqual(mappings["sha1"], sha1(self.mappings))
        self.assertEqual(mappings["sha256"], sha256(self.mappings))
        self.assertEqual(mappings["size"], len(self.mappings))
        self.assertEqual(set(mappings), {"path", "sha256", "sha1", "size"})
        self.assertNotIn(str(mappings["path"]), [row["path"] for row in result["jars"]])

    def test_client_jar_is_stored_unchanged_for_the_remap_owner(self):
        result = self.acquire()
        client = self.output / "meta/versions/1.21.1/1.21.1.jar"
        self.assertEqual(client.read_bytes(), self.client_jar)
        self.assertIn(str(client), [row["path"] for row in result["jars"]])
        self.assertEqual(json.loads(Path(result["versionMetadata"]).read_text())["id"], "1.21.1")
        self.assertEqual(result["metadataSHA256"], sha256(Path(result["versionMetadata"]).read_bytes()))

    def test_missing_mappings_metadata_fails_closed(self):
        self.metadata = self.version_metadata()
        self.metadata["downloads"].pop("client_mappings")
        raw = json.dumps(self.metadata).encode()
        self.files[self.metadata_url] = raw
        self.manifest["versions"][0]["sha1"] = sha1(raw)
        with self.assertRaisesRegex(ValueError, "client mappings"):
            self.acquire()

    def test_unknown_version_is_rejected_before_any_request(self):
        with self.assertRaises(ValueError):
            self.acquire(version="1.20.1")
        self.assertEqual(self.network.requests, [])


class AcquireDefaultVersionTests(ClientFixture):
    version = "26.1.2"
    java_version = 25

    def test_default_call_keeps_the_legacy_behaviour(self):
        result = self.acquire()
        self.assertEqual(result["minecraftVersion"], "26.1.2")
        self.assertEqual(result["javaVersion"], 25)
        self.assertFalse(result["requiresRemapping"])
        self.assertNotIn("mappings", result)
        self.assertTrue((self.output / "26.1.2.json").is_file())

    def test_ownership_still_precedes_any_request(self):
        with patch.object(acquire_client, "metadata") as metadata:
            with self.assertRaisesRegex(ValueError, "ownership"):
                acquire_client.acquire(self.output, False, version="1.21.1")
        metadata.assert_not_called()


class PrepareB0VersionTests(ClientFixture):
    version = "1.21.1"

    def prepared_client(self):
        result = self.acquire()
        (self.output / "client-inputs.json").write_text(json.dumps(result))
        return result

    def test_preparation_inputs_accept_1211_with_mappings(self):
        self.prepared_client()
        inventory, shared_meta = prepare_b0.preparation_inputs(self.output)
        self.assertEqual(inventory["minecraftVersion"], "1.21.1")
        self.assertEqual(shared_meta, self.output / "meta")

    def test_preparation_rejects_a_mixed_version_client_directory(self):
        self.prepared_client()
        stray = self.output / "meta/versions/26.1.2"
        stray.mkdir(parents=True)
        (stray / "26.1.2.jar").write_bytes(b"other version")
        with self.assertRaisesRegex(ValueError, "exactly one Minecraft version"):
            prepare_b0.preparation_inputs(self.output)

    def test_preparation_rejects_a_jar_of_another_version(self):
        self.prepared_client()
        client = self.output / "meta/versions/1.21.1/1.21.1.jar"
        client.rename(client.with_name("1.21.0.jar"))
        with self.assertRaises(ValueError):
            prepare_b0.preparation_inputs(self.output)

    def test_tampered_mappings_or_metadata_are_rejected(self):
        inventory = self.prepared_client()
        mappings = Path(inventory["mappings"]["path"])
        mappings.write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "mappings"):
            prepare_b0.preparation_inputs(self.output)
        mappings.write_bytes(self.mappings)
        prepare_b0.preparation_inputs(self.output)
        Path(inventory["versionMetadata"]).write_text(json.dumps({"id": "1.21.1", "x": 1}))
        with self.assertRaisesRegex(ValueError, "metadata"):
            prepare_b0.preparation_inputs(self.output)

    def test_unsupported_declared_version_is_rejected(self):
        inventory = self.prepared_client()
        inventory["minecraftVersion"] = "1.20.1"
        (self.output / "client-inputs.json").write_text(json.dumps(inventory))
        with self.assertRaises(ValueError):
            prepare_b0.preparation_inputs(self.output)

    def test_manifest_propagates_version_and_mapping_descriptors(self):
        inventory = self.prepared_client()
        fields = prepare_b0.version_manifest_fields(inventory, self.output / "meta", "1.21.1")
        self.assertEqual(fields["minecraftVersion"], "1.21.1")
        self.assertEqual(fields["javaVersion"], 21)
        self.assertEqual(fields["clientDirectory"], str(self.output))
        self.assertEqual(fields["versionMetadata"], inventory["versionMetadata"])
        self.assertEqual(fields["metadataSHA256"], sha256(Path(inventory["versionMetadata"]).read_bytes()))
        self.assertEqual(fields["mappings"], inventory["mappings"])
        self.assertEqual(fields["clientInputs"], str(self.output / "client-inputs.json"))
        self.assertEqual(fields["clientInputsSHA256"],
                         sha256((self.output / "client-inputs.json").read_bytes()))


class FirstRunVersionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.state = self.root / "setup/status.json"
        self.state.parent.mkdir(parents=True)
        self.paths = {"cache": self.root / "cache", "jobs": self.root / "jobs"}
        for path in self.paths.values():
            path.mkdir()

    def test_ready_state_is_per_version(self):
        self.state.write_text(json.dumps({"state": "ready", "cache": str(self.paths["cache"].resolve()),
                                          "minecraftVersion": "26.1.2"}))
        with patch.object(first_run, "storage_paths", return_value=self.paths), \
                patch.object(first_run, "require_mount"), patch.object(first_run, "require_space"), \
                patch.object(first_run, "state_path", return_value=self.state), \
                patch.object(first_run, "download_specs", return_value=[]), \
                patch.object(first_run.subprocess, "Popen") as popen:
            popen.return_value.pid = 4242
            result = first_run._start(False, "1.21.1")
        self.assertEqual(result["state"], "queued")
        self.assertEqual(result["minecraftVersion"], "1.21.1")
        argv = popen.call_args.args[0]
        self.assertEqual(argv[argv.index("--version") + 1], "1.21.1")

    def test_matching_ready_state_is_reused(self):
        self.state.write_text(json.dumps({"state": "ready", "cache": str(self.paths["cache"].resolve()),
                                          "minecraftVersion": "1.21.1"}))
        with patch.object(first_run, "storage_paths", return_value=self.paths), \
                patch.object(first_run, "require_mount"), patch.object(first_run, "require_space"), \
                patch.object(first_run, "state_path", return_value=self.state), \
                patch.object(first_run.subprocess, "Popen") as popen:
            result = first_run._start(False, "1.21.1")
        self.assertEqual(result["state"], "ready")
        popen.assert_not_called()

    def test_active_setup_for_another_version_is_rejected_without_replacing_it(self):
        active = {"state": "running", "pid": 1234, "minecraftVersion": "26.1.2"}
        self.state.write_text(json.dumps(active))
        with patch.object(first_run, "require_mount"), patch.object(first_run, "require_space"), \
                patch.object(first_run, "state_path", return_value=self.state), \
                patch.object(first_run.subprocess, "check_output", return_value="python first_run.py"), \
                patch.object(first_run.subprocess, "Popen") as popen:
            with self.assertRaisesRegex(ValueError, "26.1.2.*1.21.1"):
                first_run._start(False, "1.21.1")
        self.assertEqual(json.loads(self.state.read_text()), active)
        popen.assert_not_called()

    def test_unsupported_version_is_rejected_before_launch(self):
        with patch.object(first_run, "require_mount") as mount, \
                patch.object(first_run.subprocess, "Popen") as popen:
            with self.assertRaises(ValueError):
                first_run._start(False, "1.20.1")
        mount.assert_not_called()
        popen.assert_not_called()

    def test_worker_passes_the_requested_version_to_acquire(self):
        calls = {}

        def fake_acquire(client, owned, progress, version):
            calls["version"] = version
            calls["client"] = client
            raise ValueError("stop after acquisition")

        source = self.root / "source"
        (source / "compiler").mkdir(parents=True)
        (source / "compiler/toolchains.lock.json").write_text(json.dumps({"graalvm": {"file": "graal.tar.gz"}}))
        (source / "compiler/upstream.lock.json").write_text("{}")
        home = self.paths["cache"] / "toolchains/graalvm-25.0.2/graal/Contents/Home"
        (home / "bin").mkdir(parents=True)
        (home / "bin/native-image").write_text("fixture")
        self.state.write_text(json.dumps({"state": "queued", "minecraftVersion": "1.21.1"}))
        with patch.object(first_run, "ROOT", source),                 patch.object(first_run, "storage_paths", return_value=self.paths),                 patch.object(first_run, "require_mount"), patch.object(first_run, "require_space"),                 patch.object(first_run, "download_specs", return_value=[]),                 patch("prepare_shader_toolchain.extract"),                 patch("prepare_environment.check_host", return_value=b"Xcode fixture"),                 patch("prepare_environment.prepare", return_value=self.root / "config.json"),                 patch.object(acquire_client, "acquire", side_effect=fake_acquire):
            code = first_run.run(self.state, True, "1.21.1")
        self.assertEqual(code, 2)
        self.assertEqual(calls["version"], "1.21.1")
        self.assertEqual(Path(calls["client"]).name, "1.21.1")
        self.assertIn("stop after acquisition", json.loads(self.state.read_text())["error"])


class VerifiedAssetsVersionTests(unittest.TestCase):
    def stage(self, version, objects):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name).resolve()
        index = json.dumps({"objects": {name: {"hash": sha1(data), "size": len(data)}
                                        for name, data in objects.items()}}).encode()
        (root / "indexes").mkdir()
        (root / "indexes/17.json").write_bytes(index)
        for data in objects.values():
            path = root / "objects" / sha1(data)[:2] / sha1(data)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        metadata = {"id": version,
                    "assetIndex": {"id": "17", "size": len(index), "sha1": sha1(index),
                                   "url": "https://piston-meta.mojang.com/v1/packages/" + sha1(index) + "/17.json"}}
        return root, metadata

    def test_both_supported_versions_stage_with_one_verifier(self):
        for version in ("26.1.2", "1.21.1"):
            with self.subTest(version=version):
                root, metadata = self.stage(version, {"pack.mcmeta": b"asset fixture"})
                index_id, index, files = stage_game_assets.verified_assets(root, metadata)
                self.assertEqual(index_id, "17")
                self.assertEqual(len(files), 1)

    def test_unsupported_or_missing_id_is_rejected(self):
        for value in ("1.20.1", None, " 1.21.1"):
            with self.subTest(value=value):
                root, metadata = self.stage("1.21.1", {"pack.mcmeta": b"asset fixture"})
                metadata["id"] = value
                with self.assertRaises(ValueError):
                    stage_game_assets.verified_assets(root, metadata)

    def test_tampered_objects_still_fail_for_the_added_version(self):
        root, metadata = self.stage("1.21.1", {"pack.mcmeta": b"asset fixture"})
        path = next((root / "objects").glob("*/*"))
        path.write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "invalid asset"):
            stage_game_assets.verified_assets(root, metadata)


if __name__ == "__main__":
    unittest.main()

