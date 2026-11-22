"""Static LWJGL OpenGL adaptation: reject drift, duplicates and signatures.

Only ``lwjgl-opengl-3.3.3.jar`` is adapted, and only ``GL.create()V`` may change. Every other
input, including newer LWJGL releases, has to come back untouched.

The runtime half of this fixture needs the iOS overlay, the host bridge dylib and a first-party GL
shim, so it lives behind ``MCFAO_TEST_OPENGL_PREPARED``. When the variable is unset the runtime
tests skip; the structural tests always run.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from prepare_opengl_aot import ENTRY, prepare_opengl_aot
from bootstrap_toolchain import sha256
from run_b0_reference import execute, require_run, sandbox_policy

FIXTURE = json.loads((ROOT / "tests/fixtures/opengl/audit.json").read_text())
SHIM = """#include <string.h>
const unsigned char *glGetString(unsigned int n) { return n==0x1F02 ? (const unsigned char*)"%s" : 0; }
void *OSMesaGetProcAddress(const char *s) { return strcmp(s,"glGetString")==0 ? (void*)glGetString : 0; }
""" % FIXTURE["shim"]["glGetStringReturns"]


def write_jar(path, entries):
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return path


def prepared_inputs():
    """Return (report, official jar, engine libraries, java home) or skip."""
    prepared = os.environ.get("MCFAO_TEST_OPENGL_PREPARED")
    if not prepared:
        raise unittest.SkipTest("Requires local verified 1.21.1 prepared inputs")
    report = json.loads((Path(prepared) / "result.json").read_text())
    libraries = [Path(row["path"]) for row in report["referenceLibraries"]]
    source = [path for path in libraries if path.name == "lwjgl-opengl-3.3.3.jar"]
    if len(source) != 1:
        raise AssertionError("Expected exactly one lwjgl-opengl-3.3.3.jar, found " + str(len(source)))
    return report, source[0], [Path(row["path"]) for row in report["engineLibraries"]], Path(report["javaHome"])


class OpenGLAotTests(unittest.TestCase):
    def test_fixture_matches_the_documented_audit(self):
        self.assertEqual(FIXTURE["entry"], ENTRY)
        self.assertFalse(FIXTURE["signed"])
        self.assertEqual(FIXTURE["patchedMethod"], "create()V")
        self.assertEqual(FIXTURE["targetMethod"], "create(Ljava/lang/String;)V")
        self.assertEqual(FIXTURE["libraryName"], "liblwjgl_opengl.dylib")
        self.assertEqual(FIXTURE["iosOverlayRequires"]["Platform.get()"], "IOS")

    def test_other_lwjgl_versions_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            libraries = [root / "lwjgl-opengl-3.4.1.jar", root / "lwjgl-3.4.1.jar"]
            actual, result = prepare_opengl_aot(libraries, root / "out", root, [])
            self.assertEqual(actual, libraries)
            self.assertEqual(result["status"], "NOT_REQUIRED")
            self.assertFalse((root / "out").exists())

    def test_unrelated_libraries_do_not_trigger_adaptation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            libraries = [root / "lwjgl-3.3.3.jar", root / "lwjgl-glfw-3.3.3.jar"]
            actual, result = prepare_opengl_aot(libraries, root / "out", root, [])
            self.assertEqual(actual, libraries)
            self.assertEqual(result["status"], "NOT_REQUIRED")
            self.assertFalse((root / "out").exists())

    def test_changed_gl_class_requires_audit_before_any_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            jar = write_jar(root / "lwjgl-opengl-3.3.3.jar",
                            {ENTRY: b"changed upstream bytecode"})
            with self.assertRaisesRegex(ValueError, "audit required"):
                prepare_opengl_aot([jar], root / "out", root, [])
            self.assertFalse((root / "out").exists())

    def test_duplicate_opengl_inputs_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "(?i)ambiguous"):
            prepare_opengl_aot([Path("a/lwjgl-opengl-3.3.3.jar"),
                                Path("b/lwjgl-opengl-3.3.3.jar")],
                               Path("unused"), Path("unused"), [])

    def test_missing_gl_entry_is_rejected(self):
        prepared = os.environ.get("MCFAO_TEST_OPENGL_PREPARED")
        if not prepared:
            self.skipTest("Missing-entry guard needs the audited jar bytes to pass the hash check")
        _, source, _, _ = prepared_inputs()
        with zipfile.ZipFile(source) as jar:
            entries = {name: jar.read(name) for name in jar.namelist() if not name.endswith("/")}
        entries.pop(ENTRY)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            jar = write_jar(root / "lwjgl-opengl-3.3.3.jar", entries)
            with self.assertRaisesRegex(ValueError, "(?i)missing|ambiguous"):
                prepare_opengl_aot([jar], root / "out", root, [])
            self.assertFalse((root / "out").exists())

    def test_signed_opengl_input_is_rejected(self):
        prepared = os.environ.get("MCFAO_TEST_OPENGL_PREPARED")
        if not prepared:
            self.skipTest("Signature guard runs after the hash check, so it needs the audited bytes")
        _, source, _, _ = prepared_inputs()
        with zipfile.ZipFile(source) as jar:
            entries = {name: jar.read(name) for name in jar.namelist() if not name.endswith("/")}
        entries["META-INF/LWJGL.SF"] = b"signature block"
        entries["META-INF/LWJGL.RSA"] = b"signature"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            jar = write_jar(root / "lwjgl-opengl-3.3.3.jar", entries)
            with self.assertRaisesRegex(ValueError, "(?i)sign"):
                prepare_opengl_aot([jar], root / "out", root, [])
            self.assertFalse((root / "out").exists())

    def test_real_official_jar_changes_only_the_gl_entry(self):
        report, source, engine, home = prepared_inputs()
        self.assertEqual(sha256(source), FIXTURE["sourceSHA256"])
        libraries = [Path(row["path"]) for row in report["referenceLibraries"]]
        with tempfile.TemporaryDirectory() as directory:
            actual, result = prepare_opengl_aot(libraries, Path(directory) / "out", home, engine)
            self.assertEqual(result["status"], "LWJGL_OPENGL_STARTUP_PREPARED")
            self.assertEqual(result["classSHA256"], FIXTURE["sha256"])
            self.assertEqual(result["changedEntries"], [ENTRY])
            patched = [path for path in actual if path.name == "lwjgl-opengl-3.3.3.jar"]
            self.assertEqual(len(patched), 1)
            with zipfile.ZipFile(source) as before, zipfile.ZipFile(patched[0]) as after:
                self.assertEqual(before.namelist(), after.namelist())
                self.assertEqual([name for name in before.namelist()
                                  if before.read(name) != after.read(name)], [ENTRY])
            code = subprocess.check_output(
                [home / "bin/javap", "-p", "-c", "-classpath", patched[0],
                 ENTRY[:-6].replace("/", ".")], text=True)
            self.assertIn("liblwjgl_opengl.dylib", code)
            self.assertIn("public static void create();", code)
            self.assertIn("create:(Ljava/lang/String;)V", code)

    def test_probe_reaches_glgetstring_through_the_ios_shim(self):
        report, source, engine, home = prepared_inputs()
        overlay = Path(FIXTURE["iosOverlay"]["path"])
        host = Path(FIXTURE["hostBridge"]["path"])
        for path, digest in ((overlay, FIXTURE["iosOverlay"]["sha256"]),
                             (host, FIXTURE["hostBridge"]["sha256"]),
                             (source, FIXTURE["sourceSHA256"])):
            self.assertTrue(path.is_file(), path)
            self.assertEqual(sha256(path), digest, path)
        libraries = [Path(row["path"]) for row in report["referenceLibraries"]]
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory).resolve()
            patched, _ = prepare_opengl_aot(libraries, work / "out", home, engine)
            patched_jar = [p for p in patched if p.name == "lwjgl-opengl-3.3.3.jar"][0]
            native = work / "native"
            native.mkdir()
            shim = work / "shim.c"
            shim.write_text(SHIM)
            subprocess.run(["xcrun", "clang", "-Wall", "-Wextra", "-Werror", "-dynamiclib",
                            str(shim), "-o", str(native / "liblwjgl_opengl.dylib")],
                           check=True, capture_output=True)
            (native / "liblwjgl.dylib").write_bytes(host.read_bytes())
            classes = work / "classes"
            classes.mkdir()
            classpath = os.pathsep.join(map(str, [classes, overlay, patched_jar]))
            subprocess.run([home / "bin/javac", "-proc:none", "-cp", classpath, "-d", classes,
                            ROOT / "interop/diagnostics/OpenGLStartupProbe.java"],
                           check=True, capture_output=True)
            profile = work / "policy.sb"
            profile.write_text(sandbox_policy(work, home, [overlay, patched_jar, source]))
            result = execute(["/usr/bin/sandbox-exec", "-f", str(profile), str(home / "bin/java"),
                              "--enable-native-access=ALL-UNNAMED", "-XX:+DisableAttachMechanism",
                              "-XX:-UsePerfData", "-Xmx256m",
                              "-Dorg.lwjgl.librarypath=" + str(native),
                              "-cp", classpath, "fabricaot.probe.OpenGLStartupProbe"],
                             work / "run", work)
            require_run(result, work / "run", "OPENGL_STARTUP_PASS")
            stdout = (work / "run/stdout.log").read_text()
            expected = "OPENGL_GL_VERSION pass=%d value=" + FIXTURE["shim"]["glGetStringReturns"]
            for pass_index in (0, 1):
                self.assertIn(expected % pass_index, stdout)

    def test_unpatched_jar_fails_the_same_probe(self):
        """Negative control: the iOS overlay plus the original jar must not reach the shim."""
        _, source, _, home = prepared_inputs()
        overlay = Path(FIXTURE["iosOverlay"]["path"])
        host = Path(FIXTURE["hostBridge"]["path"])
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory).resolve()
            native = work / "native"
            native.mkdir()
            shim = work / "shim.c"
            shim.write_text(SHIM)
            subprocess.run(["xcrun", "clang", "-Wall", "-Wextra", "-Werror", "-dynamiclib",
                            str(shim), "-o", str(native / "liblwjgl_opengl.dylib")],
                           check=True, capture_output=True)
            (native / "liblwjgl.dylib").write_bytes(host.read_bytes())
            classes = work / "classes"
            classes.mkdir()
            classpath = os.pathsep.join(map(str, [classes, overlay, source]))
            subprocess.run([home / "bin/javac", "-proc:none", "-cp", classpath, "-d", classes,
                            ROOT / "interop/diagnostics/OpenGLStartupProbe.java"],
                           check=True, capture_output=True)
            profile = work / "policy.sb"
            profile.write_text(sandbox_policy(work, home, [overlay, source]))
            result = execute(["/usr/bin/sandbox-exec", "-f", str(profile), str(home / "bin/java"),
                              "--enable-native-access=ALL-UNNAMED", "-XX:+DisableAttachMechanism",
                              "-XX:-UsePerfData", "-Xmx256m",
                              "-Dorg.lwjgl.librarypath=" + str(native),
                              "-cp", classpath, "fabricaot.probe.OpenGLStartupProbe"],
                             work / "run", work)
            self.assertNotEqual(result["exitCode"], 0, "unpatched jar unexpectedly passed")
            self.assertNotIn("OPENGL_STARTUP_PASS", (work / "run/stdout.log").read_text())
            self.assertIn("GL.create(GL.java", (work / "run/stderr.log").read_text())


if __name__ == "__main__":
    unittest.main()
