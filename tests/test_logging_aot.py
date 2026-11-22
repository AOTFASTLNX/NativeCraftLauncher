"""Static Log4j adaptation must reject drift and preserve unrelated JAR entries."""
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
from prepare_logging_aot import ENTRY, prepare_logging_aot
from bootstrap_toolchain import sha256
from run_b0_reference import execute, require_run, sandbox_policy


class LoggingAotTests(unittest.TestCase):
    def test_official_logging_disables_jmx_without_losing_core_or_formatting(self):
        prepared = os.environ.get("MCFAO_TEST_LOGGING_PREPARED")
        if not prepared:
            self.skipTest("Requires local verified official 1.21.1 logging inputs")
        report = json.loads((Path(prepared) / "result.json").read_text())
        libraries = []
        for row in report["referenceLibraries"]:
            path = Path(row["path"])
            if any(part in path.as_posix() for part in
                   ("/com/mojang/logging/", "/org/slf4j/", "/org/apache/logging/log4j/")):
                self.assertEqual(sha256(path), row["sha256"])
                libraries.append(path)
        self.assertEqual(len(libraries), 5)
        home = Path(report["javaHome"])
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory).resolve()
            classes = work / "classes"
            classes.mkdir()
            source = ROOT / "interop/diagnostics/LoggingStartupProbe.java"
            subprocess.run([home / "bin/javac", "-proc:none", "-cp", os.pathsep.join(map(str, libraries)),
                            "-d", classes, source], check=True, capture_output=True)
            profile = work / "policy.sb"
            profile.write_text(sandbox_policy(work, home, libraries))
            result = execute(["/usr/bin/sandbox-exec", "-f", str(profile), str(home / "bin/java"),
                              "-XX:+DisableAttachMechanism", "-XX:-UsePerfData", "-Xmx256m",
                              "-Dlog4j2.disable.jmx=true", "-cp",
                              os.pathsep.join(map(str, [classes, *libraries])),
                              "fabricaot.probe.LoggingStartupProbe"], work / "run", work)
            require_run(result, work / "run", "LOGGING_JMX_DISABLED_PASS")
            self.assertIn("LOGGING_PROBE_PASS logger=", (work / "run/stdout.log").read_text())
            self.assertNotIn("StatusLogger", (work / "run/stderr.log").read_text())

    def test_newer_log4j_classpath_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            libraries = [root / "log4j-api-2.25.2.jar"]
            actual, result = prepare_logging_aot(libraries, root / "out", root, [])
            self.assertEqual(actual, libraries)
            self.assertEqual(result["status"], "NOT_REQUIRED")
            self.assertFalse((root / "out").exists())

    def test_changed_logging_class_requires_audit_before_any_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            jar = root / "log4j-api-2.22.1.jar"
            with zipfile.ZipFile(jar, "w") as archive:
                archive.writestr(ENTRY, b"changed upstream bytecode")
            with self.assertRaisesRegex(ValueError, "audit required"):
                prepare_logging_aot([jar], root / "out", root, [])
            self.assertFalse((root / "out").exists())

    def test_ambiguous_logging_inputs_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "Ambiguous"):
            prepare_logging_aot([Path("a/log4j-api-2.22.1.jar"),
                                 Path("b/log4j-api-2.22.1.jar")], Path("unused"), Path("unused"), [])

    def test_real_official_jar_changes_only_audited_service_method(self):
        prepared = os.environ.get("MCFAO_TEST_LOGGING_PREPARED")
        if not prepared:
            self.skipTest("Requires local verified official 1.21.1 logging inputs")
        report = json.loads((Path(prepared) / "result.json").read_text())
        libraries = [Path(row["path"]) for row in report["referenceLibraries"]
                     if Path(row["path"]).name == "log4j-api-2.22.1.jar"]
        engine = [Path(row["path"]) for row in report["engineLibraries"]]
        home = Path(report["javaHome"])
        with tempfile.TemporaryDirectory() as directory:
            actual, result = prepare_logging_aot(libraries, Path(directory) / "out", home, engine)
            self.assertEqual(result["status"], "LOG4J_SERVICE_LOOKUP_PREPARED")
            with zipfile.ZipFile(libraries[0]) as before, zipfile.ZipFile(actual[0]) as after:
                self.assertEqual(before.namelist(), after.namelist())
                self.assertEqual([name for name in before.namelist()
                                  if before.read(name) != after.read(name)], [ENTRY])
            code = subprocess.check_output([home / "bin/javap", "-p", "-c", "-classpath", actual[0],
                                            ENTRY[:-6].replace("/", ".")], text=True)
            self.assertIn("java/util/ServiceLoader.load:", code)
            self.assertNotIn("LambdaMetafactory.metafactory", code)
            self.assertIn("Unable to load services for service {}", code)
            self.assertIn("java/util/Collections.emptyList", code)


if __name__ == "__main__":
    unittest.main()
