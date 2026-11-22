"""Tests for the 1.21.1 client mapping helper and its preparation wrapper."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import prepare_game_version as prepare  # noqa: E402

ENGINE = Path("/Volumes/K/AOTC/cache/owned-client/26.1.2/meta/libraries/engine")
JAVA_HOME = Path("/Volumes/K/AOTC/cache/toolchains/graalvm-25.0.2/graalvm-community-openjdk-25.0.2+10.1/Contents/Home")
LOADER = ENGINE / "fabric-loader-0.19.3.jar"
ASM = [ENGINE / "asm-9.10.1.jar"] + [ENGINE / f"asm-{name}-9.10.1.jar"
                                      for name in ("analysis", "commons", "tree", "util")]
ASM = [path for path in ASM if path.is_file()]

HAVE_TOOLCHAIN = (JAVA_HOME / "bin/javac").is_file() and LOADER.is_file() and bool(ASM)

PROGUARD = """# test fixture
com.example.Foo -> a:
    int count -> x
    void doThing(com.example.Foo) -> b
com.example.Bar -> b:
com.example.Baz -> c:
"""

TINY = """tiny\t2\t0\tofficial\tintermediary
c\ta\tnet/minecraft/class_1
\tf\tI\tx\tfield_1
\tm\t(La;)V\tb\tmethod_1
c\tb\tnet/minecraft/class_2
c\tc\tnet/minecraft/class_3
"""

SOURCE = {
    "a.java": """public class a {
    public int x;
    public void b(a other) { }
}
""",
    "b.java": """public class b extends a { }
""",
}


def jar_bytes(entries: dict[str, bytes]) -> bytes:
    import io
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return stream.getvalue()


class ManifestTests(unittest.TestCase):
    def test_missing_version_defaults_to_legacy(self):
        self.assertEqual(prepare.game_version({}), prepare.LEGACY_VERSION)

    def test_version_is_returned_verbatim(self):
        self.assertEqual(prepare.game_version({"minecraftVersion": "1.21.1"}), "1.21.1")

    def test_version_rejects_surrounding_whitespace(self):
        with self.assertRaises(prepare.PreparationError):
            prepare.game_version({"minecraftVersion": " 1.21.1 "})

    def test_version_rejects_empty(self):
        with self.assertRaises(prepare.PreparationError):
            prepare.game_version({"minecraftVersion": ""})

    def test_version_accepts_nested_minecraft_object(self):
        self.assertEqual(prepare.game_version({"minecraft": {"version": "1.21.1"}}), "1.21.1")

    def test_legacy_client_is_returned_without_output(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            client = root / "26.1.2.jar"
            client.write_bytes(b"not read")
            (root / "result.json").write_text(json.dumps({"minecraftVersion": "26.1.2"}))
            output = root / "out"
            result = prepare.prepare_client(root, client, output)
            self.assertEqual(Path(result["client"]), client)
            self.assertFalse(result["remapped"])
            self.assertIsNone(result["intermediaryClient"])
            self.assertFalse(output.exists())


class ValidationTests(unittest.TestCase):
    def test_metadata_requires_id_match(self):
        with self.assertRaises(prepare.PreparationError):
            prepare._official_descriptors({"id": "1.20.1", "downloads": {}}, "1.21.1")

    def test_metadata_requires_sha1_and_size(self):
        with self.assertRaises(prepare.PreparationError):
            prepare._official_descriptors({"id": "1.21.1", "downloads": {"client": {"sha1": "a"}}}, "1.21.1")

    def test_intermediary_pin_rejects_plain_http(self):
        lock = {"versions": {"1.21.1": {"intermediary": {
            "url": "http://maven.fabricmc.net/x.jar", "sha256": "a" * 64, "size": 1}}}}
        with self.assertRaises(prepare.PreparationError):
            prepare.intermediary_pin(lock, "1.21.1")

    def test_intermediary_pin_rejects_foreign_host(self):
        lock = {"versions": {"1.21.1": {"intermediary": {
            "url": "https://example.com/x.jar", "sha256": "a" * 64, "size": 1}}}}
        with self.assertRaises(prepare.PreparationError):
            prepare.intermediary_pin(lock, "1.21.1")

    def test_intermediary_pin_requires_size_and_sha(self):
        lock = {"versions": {"1.21.1": {"intermediary": {
            "url": "https://maven.fabricmc.net/x.jar", "sha256": "short", "size": 0}}}}
        with self.assertRaises(prepare.PreparationError):
            prepare.intermediary_pin(lock, "1.21.1")

    def test_lock_pins_the_real_intermediary(self):
        pin = prepare.intermediary_pin(prepare.load_lock(), "1.21.1")
        self.assertEqual(pin["sha256"], "e8bd6a4c39e235ee2dc73063558610775c72a2572fa0c6b32915b149074012a6")
        self.assertEqual(pin["size"], 582921)
        self.assertTrue(pin["url"].startswith("https://maven.fabricmc.net/"))

    def test_lock_has_no_proprietary_game_artifacts(self):
        text = (ROOT / "compiler/game-versions.lock.json").read_text()
        for forbidden in ("client.jar", "client.txt", "piston-data.mojang.com", "libraries.minecraft.net"):
            self.assertNotIn(forbidden, text)
        lock = prepare.load_lock()
        self.assertEqual(set(lock["versions"]["1.21.1"]) - {"intermediary"},
                         {"java", "runtimeNamespace", "mappingNamespaces"})

    def test_helper_rejects_symlinked_inputs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            real = root / "real.jar"
            real.write_bytes(b"x")
            link = root / "link.jar"
            link.symlink_to(real)
            with self.assertRaises(prepare.PreparationError):
                prepare._require_regular(link, "client jar")

    def test_helper_ignores_apple_double_sidecars(self):
        from mod_file_policy import clean_staged_metadata, content_paths
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "client.jar").write_bytes(b"jar")
            (root / "._client.jar").write_bytes(b"sidecar")
            self.assertEqual([p.name for p in content_paths(root)], ["client.jar"])
            clean_staged_metadata(root)
            self.assertEqual(sorted(p.name for p in root.iterdir()), ["client.jar"])

    def test_reference_library_requires_a_listed_hash(self):
        with tempfile.TemporaryDirectory() as temp:
            client = Path(temp) / "1.21.1.jar"
            client.write_bytes(b"x")
            with self.assertRaises(prepare.PreparationError):
                prepare._reference_library({"referenceLibraries": []}, client)
            with self.assertRaises(prepare.PreparationError):
                prepare._reference_library({"referenceLibraries": [
                    {"path": str(client), "sha256": "0" * 64, "size": 1}]}, client)

    def test_cached_result_requires_matching_inputs_and_rehashed_outputs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            outputs = {name: root / name for name in ("client.jar", "intermediary-client.jar", "mappings.tiny")}
            for path in outputs.values():
                path.write_bytes(path.name.encode())
            inputs = {"helper": "a" * 64}
            cached = {"sourceClientSHA1": "c", "sourceMappingsSHA1": "m", "inputsSHA256": inputs,
                      "minecraftVersion": "1.21.1", "remapped": True,
                      "client": str(outputs["client.jar"]), "clientSHA256": prepare.sha256(outputs["client.jar"]),
                      "intermediaryClient": str(outputs["intermediary-client.jar"]),
                      "intermediaryClientSHA256": prepare.sha256(outputs["intermediary-client.jar"]),
                      "mappings": str(outputs["mappings.tiny"]),
                      "mappingsSHA256": prepare.sha256(outputs["mappings.tiny"])}
            (root / "result.json").write_text(json.dumps(cached))
            self.assertIsNotNone(prepare._cached_result(root, inputs, "c", "m", "1.21.1"))
            self.assertIsNone(prepare._cached_result(root, inputs, "other", "m", "1.21.1"))
            self.assertIsNone(prepare._cached_result(root, {"helper": "b" * 64}, "c", "m", "1.21.1"))
            self.assertIsNone(prepare._cached_result(root, inputs, "c", "m", "26.1.2"))
            outputs["client.jar"].write_bytes(b"tampered")
            self.assertIsNone(prepare._cached_result(root, inputs, "c", "m", "1.21.1"))

    def test_cached_result_rejects_paths_outside_the_output(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            output = root / "out"
            output.mkdir()
            outside = root / "client.jar"
            outside.write_bytes(b"x")
            cached = {"sourceClientSHA1": "c", "sourceMappingsSHA1": "m", "inputsSHA256": {},
                      "minecraftVersion": "1.21.1", "remapped": True,
                      "client": str(outside), "clientSHA256": prepare.sha256(outside),
                      "intermediaryClient": str(outside), "intermediaryClientSHA256": prepare.sha256(outside),
                      "mappings": str(outside), "mappingsSHA256": prepare.sha256(outside)}
            (output / "result.json").write_text(json.dumps(cached))
            self.assertIsNone(prepare._cached_result(output, {}, "c", "m", "1.21.1"))

    def test_cached_result_accepts_the_shared_artifact_cache(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            output = root / "out"
            shared = root / "cache/remapped/key"
            output.mkdir()
            shared.mkdir(parents=True)
            jar = shared / "client.jar"
            jar.write_bytes(b"jar")
            digest = prepare.sha256(jar)
            cached = {"sourceClientSHA1": "c", "sourceMappingsSHA1": "m", "inputsSHA256": {},
                      "minecraftVersion": "1.21.1", "remapped": True,
                      "client": str(jar), "clientSHA256": digest,
                      "intermediaryClient": str(jar), "intermediaryClientSHA256": digest,
                      "mappings": str(jar), "mappingsSHA256": digest}
            (output / "result.json").write_text(json.dumps(cached))
            self.assertIsNone(prepare._cached_result(output, {}, "c", "m", "1.21.1"))
            self.assertIsNotNone(prepare._cached_result(output, {}, "c", "m", "1.21.1", artifact_root=shared))


@unittest.skipUnless(HAVE_TOOLCHAIN, "requires the Java 25 toolchain and Fabric Loader engine jars")
class RemapperTests(unittest.TestCase):
    def test_proguard_and_intermediary_join_remaps_a_class(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "src"
            for name, body in SOURCE.items():
                path = source / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(body)
            classes = root / "classes"
            classes.mkdir()
            subprocess.run([str(JAVA_HOME / "bin/javac"), "-proc:none", "-d", str(classes),
                            *map(str, sorted(source.rglob("*.java")))], check=True,
                           capture_output=True, timeout=120)
            client = root / "client.jar"
            client.write_bytes(jar_bytes({str(path.relative_to(classes)): path.read_bytes()
                                          for path in classes.rglob("*.class")}))
            proguard = root / "client.txt"
            proguard.write_text(PROGUARD)
            tiny = root / "intermediary.tiny"
            tiny.write_text(TINY)

            classpath = os.pathsep.join(map(str, [LOADER, *ASM]))
            helper = root / "helper"
            helper.mkdir()
            subprocess.run([str(JAVA_HOME / "bin/javac"), "--release", "25", "-proc:none",
                            "-cp", classpath, "-d", str(helper),
                            str(ROOT / "compiler/version-tools/src/fabricaot/version/GameRemapper.java")],
                           check=True, capture_output=True, timeout=180)
            def remap_into(directory):
                return subprocess.run([str(JAVA_HOME / "bin/java"), "-cp",
                                       os.pathsep.join([str(helper), classpath]),
                                       "fabricaot.version.GameRemapper",
                                       "--client", str(client), "--output", str(directory),
                                       "--proguard", str(proguard), "--intermediary", str(tiny),
                                       "--expect-client-sha1", prepare._digest(client, "sha1"),
                                       "--expect-client-size", str(client.stat().st_size),
                                       "--expect-mappings-sha1", prepare._digest(proguard, "sha1"),
                                       "--expect-mappings-size", str(proguard.stat().st_size)],
                                      capture_output=True, text=True, timeout=300)

            output = root / "out"
            run = remap_into(output)
            self.assertEqual(run.returncode, 0, run.stderr)
            result = json.loads(run.stdout)
            self.assertEqual(result["namespaces"], ["obfuscated", "intermediary", "official"])

            # Same inputs must produce byte-identical jars, so recorded hashes stay meaningful.
            repeat = remap_into(root / "out-again")
            self.assertEqual(repeat.returncode, 0, repeat.stderr)
            self.assertEqual(result["clientSHA256"], json.loads(repeat.stdout)["clientSHA256"])
            self.assertEqual(prepare.sha256(output / "client.jar"),
                             prepare.sha256(root / "out-again" / "client.jar"))
            with zipfile.ZipFile(output / "client.jar") as archive:
                names = set(archive.namelist())
                self.assertIn("com/example/Foo.class", names)
                self.assertNotIn("a.class", names)
                self.assertIn("com/example/Bar.class", names)
            with zipfile.ZipFile(output / "intermediary-client.jar") as archive:
                self.assertIn("net/minecraft/class_1.class", set(archive.namelist()))
            lines = (output / "mappings.tiny").read_text().splitlines()
            self.assertEqual(lines[0], "tiny\t2\t0\tobfuscated\tintermediary\tofficial")
            member = next(line for line in lines if line.startswith("\tm\t"))
            columns = member.split("\t")
            # Leading empty column, kind, source descriptor, then exactly three names.
            self.assertEqual(len(columns), 6, f"member line must be kind, desc, 3 names: {member}")
            self.assertEqual(columns[0], "")
            self.assertEqual(columns[1], "m")
            self.assertEqual(columns[2], "(La;)V")
            for name in columns[3:]:
                self.assertNotIn("(", name)
            self.assertEqual(columns[5], "doThing")


if __name__ == "__main__":
    unittest.main()
