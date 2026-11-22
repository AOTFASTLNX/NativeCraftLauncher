from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import patch_gl41_client


class GL41ManifestTests(unittest.TestCase):
    def setUp(self):
        prepared = os.environ.get("MCFAO_TEST_GL41_PREPARED")
        toolchain = Path(prepared or os.environ.get("MCFAO_TEST_GL41_TOOLCHAIN", ROOT / "artifacts/compiler/method-splitter-01"))
        report = toolchain / "result.json"
        if not report.is_file():
            self.skipTest("Requires pinned Java 25 / ASM")
        splitter = json.loads(report.read_text())
        self.home = Path(splitter["javaHome"])
        self.asm = [Path(row["path"]) for row in splitter["engineLibraries" if prepared else "asmJars"]
                    if Path(row["path"]).name.startswith("asm-tree-")
                    or (Path(row["path"]).name.startswith("asm-")
                        and Path(row["path"]).name[4:5].isdigit())]
        if (len(self.asm) != 2 or not all(p.is_file() for p in self.asm)
                or not all((self.home / "bin" / name).is_file() for name in ("java", "javac"))):
            self.skipTest("Pinned Java 25 / ASM paths are unavailable")

    def test_main_attributes_survive_and_stale_entry_signatures_are_removed(self):
        asm = os.pathsep.join(map(str, self.asm))
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp); harness = out / "ManifestCheck.java"
            harness.write_text(r'''package fabricaot.gl41;
import java.io.*; import java.util.jar.*;
public class ManifestCheck { public static void main(String[] args) throws Exception {
  String source="Manifest-Version: 1.0\r\nMulti-Release: true\r\nMain-Class: Game\r\n\r\nName: Game.class\r\nSHA-256-Digest: stale\r\n\r\n";
  byte[] clean=PatchDevice.cleanManifest(source.getBytes(java.nio.charset.StandardCharsets.UTF_8));
  Manifest result=new Manifest(new ByteArrayInputStream(clean));
  if(!result.getEntries().isEmpty()) throw new AssertionError("stale entry signatures retained");
  if(!"true".equals(result.getMainAttributes().getValue("Multi-Release"))) throw new AssertionError("MR attribute lost");
  if(!"Game".equals(result.getMainAttributes().getValue("Main-Class"))) throw new AssertionError("main class lost");
  if(!java.util.Arrays.equals(clean,PatchDevice.cleanManifest(clean))) throw new AssertionError("not idempotent");
}}''')
            sources = [ROOT / "compiler/gl41/tools/fabricaot/gl41/PatchDevice.java",
                       ROOT / "compiler/metal-backend/tools/fabricaot/metal/InjectBackend.java", harness]
            subprocess.run([self.home / "bin/javac", "-proc:none", "-cp", asm, "-d", out, *sources],
                           check=True, capture_output=True, timeout=60)
            subprocess.run([self.home / "bin/java", "-cp", str(out) + os.pathsep + asm,
                            "fabricaot.gl41.ManifestCheck"], check=True, capture_output=True, timeout=60)

    def run_patcher(self, client, output, version="26.1.2"):
        with patch.object(patch_gl41_client, "register_tree"), redirect_stdout(io.StringIO()), \
                redirect_stderr(io.StringIO()) as stderr:
            result = patch_gl41_client.main([
                "--version", version, "--client", str(client), "--output", str(output), "--graalvm-home", str(self.home),
                "--asm-jars", *map(str, self.asm),
            ])
        return result, stderr.getvalue()

    def test_missing_patch_targets_report_the_java_cause_and_log_path(self):
        with tempfile.TemporaryDirectory() as temp:
            client = Path(temp) / "incomplete.jar"
            with ZipFile(client, "w") as archive:
                archive.writestr("fixture.txt", "unmodified")
            output = Path(temp) / "output"
            code, error = self.run_patcher(client, output)
            self.assertNotEqual(code, 0)
            self.assertIn("Missing GL41 patch targets:", error)
            self.assertIn("net/minecraft/SystemReport.class", error)
            self.assertIn(str(output / "transform"), error)
            self.assertFalse((output / "result.json").exists())

    def test_full_client_accepts_both_hardware_diagnostic_changes(self):
        client = Path(os.environ.get("MCFAO_TEST_GL41_CLIENT", ROOT / "artifacts/ci/client-split.jar"))
        if not client.is_file():
            self.skipTest("Set MCFAO_TEST_GL41_CLIENT to a local split 26.1.2 client JAR")
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "output"
            code, error = self.run_patcher(client, output)
            self.assertEqual(code, 0, error)
            result = json.loads((output / "result.json").read_text())
            self.assertEqual(result["status"], "GL41_CLIENT_PATCHED")
            self.assertIn("com/mojang/blaze3d/platform/GLX.class", result["changedEntries"])
            self.assertIn("net/minecraft/SystemReport.class", result["changedEntries"])

    def test_1211_patches_real_mapped_client_and_rejects_the_26_profile(self):
        value = os.environ.get("MCFAO_TEST_GL41_1211_CLIENT")
        if not value:
            self.skipTest("Requires locally mapped 1.21.1 client")
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "correct"
            code, error = self.run_patcher(Path(value), output, "1.21.1")
            self.assertEqual(code, 0, error)
            result = json.loads((output / "result.json").read_text())
            self.assertEqual(result["minecraftVersion"], "1.21.1")
            self.assertEqual(len([n for n in result["changedEntries"] if n.endswith(".class")]), 8)
            self.assertIn("net/minecraft/util/profiling/jfr/JvmProfiler.class", result["changedEntries"])
            self.assertIn("NoOpProfiler", result["flightRecorder"])
            self.assertIn("net/minecraft/client/main/Main.class", result["changedEntries"])
            code, error = self.run_patcher(Path(value), Path(temp) / "wrong")
            self.assertNotEqual(code, 0)
            self.assertFalse((Path(temp) / "wrong/result.json").exists())

    def test_1211_preserves_original_failure_before_crash_report_also_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp)
            fixture = out / "StartupFixture.java"
            fixture.write_text('''public class StartupFixture {
  static void crashReport() { throw new NoClassDefFoundError("secondary logging failure"); }
  public static void main(String[] args) {
    try { throw new ExceptionInInitializerError("original logging failure"); }
    catch (Throwable first) { crashReport(); }
    try { throw new IllegalStateException("second stage"); }
    catch (Throwable second) { crashReport(); }
  }
}''')
            harness = out / "StartupPatchCheck.java"
            harness.write_text('''package fabricaot.gl41;
import java.nio.file.*;
public class StartupPatchCheck { public static void main(String[] args) throws Exception {
  Path path = Path.of(args[0]);
  Files.write(path, PatchDevice1211.startupFailures(Files.readAllBytes(path)));
}}''')
            asm = os.pathsep.join(map(str, self.asm))
            subprocess.run([self.home / "bin/javac", "-proc:none", "-cp", asm, "-d", out,
                            ROOT / "compiler/gl41/tools/fabricaot/gl41/PatchDevice.java",
                            ROOT / "compiler/gl41/tools/fabricaot/gl41/PatchDevice1211.java",
                            ROOT / "compiler/metal-backend/tools/fabricaot/metal/InjectBackend.java",
                            fixture, harness], check=True, capture_output=True, timeout=60)
            command = [self.home / "bin/java", "-cp", str(out), "StartupFixture"]
            before = subprocess.run(command, capture_output=True, text=True, timeout=30)
            self.assertNotEqual(before.returncode, 0)
            self.assertNotIn("original logging failure", before.stderr)
            subprocess.run([self.home / "bin/java", "-cp", str(out) + os.pathsep + asm,
                            "fabricaot.gl41.StartupPatchCheck", out / "StartupFixture.class"],
                           check=True, capture_output=True, timeout=30)
            after = subprocess.run(command, capture_output=True, text=True, timeout=30)
            self.assertNotEqual(after.returncode, 0)
            self.assertIn("ExceptionInInitializerError: original logging failure", after.stderr)
            self.assertIn("NoClassDefFoundError: secondary logging failure", after.stderr)
            self.assertLess(after.stderr.index("original logging failure"),
                            after.stderr.index("secondary logging failure"))

    def test_1211_flight_recorder_initializer_selects_noop_after_audited_patch(self):
        """The 1.21.1 initializer must stop probing jdk.jfr and pick NoOpProfiler directly.

        The desktop selector asks the module layer whether jdk.jfr is present and then calls
        JfrProfiler.getInstance(). An iOS image ships jdk.jfr classes without a working recorder,
        so that branch builds a recorder that cannot start. The audited patch replaces the whole
        initializer; everything else about the interface has to survive untouched.
        """
        value = os.environ.get("MCFAO_TEST_GL41_1211_CLIENT")
        if not value:
            self.skipTest("Requires locally mapped 1.21.1 client")
        import hashlib
        entry = "net/minecraft/util/profiling/jfr/JvmProfiler.class"
        with ZipFile(Path(value)) as archive:
            original = archive.read(entry)
        self.assertEqual(hashlib.sha256(original).hexdigest(),
                         "05bc243d0c863f1c6762f18697366f721d7b3eeeecdb58be9d9f8871b0b72da7")
        asm = os.pathsep.join(map(str, self.asm))
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp)
            before, after = out / "before.class", out / "after.class"
            before.write_bytes(original)
            harness = out / "FlightRecorderCheck.java"
            harness.write_text('''package fabricaot.gl41;
import java.nio.file.*;
public class FlightRecorderCheck { public static void main(String[] args) throws Exception {
  Path in = Path.of(args[0]), out = Path.of(args[1]);
  Files.write(out, PatchDevice1211.withoutFlightRecorder(Files.readAllBytes(in)));
}}''')
            sources = [ROOT / "compiler/gl41/tools/fabricaot/gl41/PatchDevice.java",
                       ROOT / "compiler/gl41/tools/fabricaot/gl41/PatchDevice1211.java",
                       ROOT / "compiler/metal-backend/tools/fabricaot/metal/InjectBackend.java",
                       harness]
            subprocess.run([self.home / "bin/javac", "-proc:none", "-cp", asm, "-d", out, *sources],
                           check=True, capture_output=True, timeout=120)
            subprocess.run([self.home / "bin/java", "-cp", str(out) + os.pathsep + asm,
                            "fabricaot.gl41.FlightRecorderCheck", before, after],
                           check=True, capture_output=True, timeout=60)

            def disassemble(path):
                return subprocess.check_output(
                    [self.home / "bin/javap", "-p", "-c", str(path)], text=True)

            patched = disassemble(after)
            self.assertNotIn("JfrProfiler.getInstance", patched)
            for probe in ("findModule", "getModule", "jdk.jfr"):
                self.assertNotIn(probe, patched, "initializer still probes " + probe)
            self.assertIn("JvmProfiler$NoOpProfiler", patched)
            self.assertIn("putstatic", patched)
            self.assertIn("INSTANCE:Lnet/minecraft/util/profiling/jfr/JvmProfiler;", patched)

            # The interface contract is what the rest of the game calls; the patch only owns
            # <clinit>, so every abstract member must survive byte for byte.
            def abstract_members(text):
                return sorted(line.strip() for line in text.splitlines()
                              if line.strip().startswith("public abstract"))

            self.assertEqual(abstract_members(disassemble(before)), abstract_members(patched))

            # A single changed byte must be refused before ASM ever rewrites anything.
            tampered = bytearray(original)
            tampered[-1] ^= 0xFF
            changed = out / "changed.class"
            changed.write_bytes(bytes(tampered))
            refused = subprocess.run(
                [self.home / "bin/java", "-cp", str(out) + os.pathsep + asm,
                 "fabricaot.gl41.FlightRecorderCheck", changed, out / "never.class"],
                capture_output=True, text=True, timeout=60)
            self.assertNotEqual(refused.returncode, 0)
            self.assertIn("audit required", refused.stderr)
            self.assertFalse((out / "never.class").exists())
