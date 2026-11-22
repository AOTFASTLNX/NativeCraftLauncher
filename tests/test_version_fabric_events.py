"""Real-bytecode regression for the audited Fabric API 1.21.1 event factory lowering.

The probe opens the actual fabric-api-base.jar from the 1.21.1 build, parses
EventFactoryImpl with ASM, and runs RuntimeLowering.removeUnusedEventProxy on the
parsed tree. No class from the Mod JAR is defined or initialized, no Mod entrypoint
runs, and no Minecraft class is loaded: only bytes are read and rewritten.

Set MCFAO_TEST_FABRIC_API_1211_BASE to the JAR to run; otherwise the suite skips.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from bootstrap_toolchain import sha256
from capture import capture
from prepare_fabric_aot import load_reference
from storage_config import cache_path

JAR_ENV = "MCFAO_TEST_FABRIC_API_1211_BASE"
PREPARED = Path(os.environ.get("MCFAO_TEST_FABRIC_PREPARED", ROOT / "artifacts/b0/prepared-04"))
TARGET_SHA256 = "6d468bd557975d377f7f925b60289e7853bb4a10f585e4a5e2a03e24478bcc7f"
PASS_MARKER = "VERSION_FABRIC_EVENTS_PASS"

SOURCES = (
    "interop/fabric/tool/mcfao/build/RuntimeLowering.java",
    "interop/fabric/tool/mcfao/build/AdaptationRuleEngine.java",
    "interop/diagnostics/VersionFabricEventProbe.java",
)


class VersionFabricEventsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.jar = Path(os.environ.get(JAR_ENV, "")).expanduser() if os.environ.get(JAR_ENV) else None
        if cls.jar is None:
            raise unittest.SkipTest(f"Set {JAR_ENV} to the audited 1.21.1 fabric-api-base.jar")
        if not cls.jar.is_file():
            raise unittest.SkipTest(f"{JAR_ENV} does not exist: {cls.jar}")
        if not (PREPARED / "result.json").is_file():
            raise unittest.SkipTest("Requires the pinned 26.1.2 Java/ASM prepared toolchain")
        try:
            cls.home, _, cls.engine, _ = load_reference(PREPARED)
        except (OSError, ValueError) as exc:
            cls.home = cls.relocated_home()
            if cls.home is None:
                raise unittest.SkipTest("Prepared toolchain is not usable: " + str(exc)) from exc
            cls.engine = [Path(row["path"]) for row in
                          json.loads((PREPARED / "result.json").read_text())["engineLibraries"]]
        temporary = tempfile.TemporaryDirectory(prefix="ncl-fabric-events-")
        cls.addClassCleanup(temporary.cleanup)
        cls.output = Path(temporary.name)
        cls.classes = cls.output / "classes"
        cls.classes.mkdir()
        cls.compile(cls.classes)
        cls.probe = cls.command("probe", cls.probe_argv(cls.classes))

    @classmethod
    def relocated_home(cls):
        """The same pinned GraalVM when the prepared record points at a moved copy.

        The record stores an absolute javaHome, which breaks when the project is
        copied. The versioned cache copy is accepted only when bin/java matches
        the recorded SHA-256, so a different JDK is never substituted.
        """
        report = json.loads((PREPARED / "result.json").read_text())
        expected = report.get("javaSHA256")
        candidate = cache_path("toolchains/graalvm-25.0.2/"
                               "graalvm-community-openjdk-25.0.2+10.1/Contents/Home")
        if not isinstance(expected, str) or not (candidate / "bin/java").is_file():
            return None
        if sha256(candidate / "bin/java") != expected:
            return None
        for row in report["engineLibraries"]:
            path = Path(row["path"])
            if not path.is_file() or sha256(path) != row.get("sha256"):
                return None
        return candidate

    @classmethod
    def compile(cls, classes):
        argv = [str(cls.home / "bin/javac"), "--release", "25", "-proc:none", "-Xlint:all",
                "-cp", os.pathsep.join(map(str, cls.engine)), "-d", str(classes),
                *(str(ROOT / name) for name in SOURCES)]
        report = capture(argv, cls.output / "compile", 300)
        if report["exitCode"] != 0 or report["timedOut"]:
            raise AssertionError("compile failed:\n"
                                 + (cls.output / "compile/stderr.log").read_text()[-4000:])

    @classmethod
    def probe_argv(cls, classes):
        return [str(cls.home / "bin/java"), "-Xverify:all", "-cp",
                os.pathsep.join(map(str, [classes, *cls.engine])),
                "mcfao.build.VersionFabricEventProbe", str(cls.jar)]

    @classmethod
    def command(cls, name, argv):
        report = capture(argv, cls.output / name, 300)
        stdout = (cls.output / name / "stdout.log").read_text()
        stderr = (cls.output / name / "stderr.log").read_text()
        return {"exitCode": report["exitCode"], "timedOut": report["timedOut"],
                "stdout": stdout, "stderr": stderr, "path": cls.output / name}

    def test_audited_jar_hash_is_the_one_the_lowering_pins(self):
        import hashlib
        import zipfile
        with zipfile.ZipFile(self.jar) as archive:
            data = archive.read("net/fabricmc/fabric/impl/base/event/EventFactoryImpl.class")
        self.assertEqual(hashlib.sha256(data).hexdigest(), TARGET_SHA256)

    def test_lowering_removes_only_the_two_unused_private_helpers(self):
        self.assertEqual(self.probe["exitCode"], 0, self.probe["stderr"][-4000:])
        self.assertIn(PASS_MARKER, self.probe["stdout"])
        self.assertIn(f"sha256={TARGET_SHA256}", self.probe["stdout"])
        # 8 methods before, 2 helpers removed, 6 left; the other 6 stay byte-identical.
        self.assertIn("removed=2", self.probe["stdout"])
        self.assertIn("methods=6", self.probe["stdout"])
        self.assertIn("unchanged=8", self.probe["stdout"])

    def test_probe_does_not_load_the_mod_jar(self):
        # The classpath holds only the probe and the pinned engine; the Mod JAR is
        # passed as a file argument, so a Mod class could not be loaded even by name.
        argv = self.probe_argv(self.classes)
        classpath = argv[argv.index("-cp") + 1].split(os.pathsep)
        self.assertNotIn(str(self.jar), classpath)
        self.assertIn(str(self.classes), classpath)

    def test_probe_fails_on_an_unknown_jar(self):
        # A same-named jar with different bytes must be refused by the pinned hash.
        with tempfile.TemporaryDirectory() as temp:
            forged = Path(temp) / self.jar.name
            import zipfile
            with zipfile.ZipFile(forged, "w") as archive:
                # Same class names, so only the pinned hash can tell them apart.
                archive.writestr("net/fabricmc/fabric/impl/base/event/EventFactoryImpl.class",
                                 b"not the audited class")
                archive.writestr("net/fabricmc/fabric/impl/base/event/ArrayBackedEvent.class",
                                 b"not the audited class either")
            result = self.command("forged", self.probe_argv(self.classes)[:-1] + [str(forged)])
        self.assertNotEqual(result["exitCode"], 0)
        self.assertIn("unaudited EventFactoryImpl", result["stderr"])


if __name__ == "__main__":
    unittest.main()

