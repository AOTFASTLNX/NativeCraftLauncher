"""Regression tests for the 1.21.1 secure-chat key wait guard.

The fixture reproduces the vanilla 1.21.1 ClientPacketListener shape: the predicate is
``canValidateProfileKeys() && serverEnforcesSecureChat`` and the initializer fetches
``Minecraft.getProfileKeySignatureValidator()`` before reading ``Entry.chatSession()``.
An unfinished services-key future blocks the predicate, so the negative case can only pass
if the patch really avoids that wait.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/chat1211"
PATCHER_SOURCES = [
    ROOT / "compiler/gl41/tools/fabricaot/gl41/PatchDevice.java",
    ROOT / "compiler/gl41/tools/fabricaot/gl41/PatchDevice1211.java",
    ROOT / "compiler/metal-backend/tools/fabricaot/metal/InjectBackend.java",
]
LISTENER = "net/minecraft/client/multiplayer/ClientPacketListener.class"


def prepared_report():
    value = os.environ.get("MCFAO_TEST_1211_PREPARED")
    if not value:
        return None
    report = Path(value) / "result.json"
    return json.loads(report.read_text()) if report.is_file() else None


def toolchain():
    """Pinned Java 25 plus ASM, resolved from env, prepared output, or storage config."""
    home = os.environ.get("MCFAO_TEST_JAVA_25")
    asm = []
    report = prepared_report()
    if report:
        home = home or report.get("javaHome")
        asm = [Path(row["path"]) for row in report.get("engineLibraries", [])
               if Path(row["path"]).name.startswith(("asm-9.", "asm-tree-9."))]
    home = Path(home) if home else repo_java_home()
    if not asm:
        asm = default_asm()
    asm = [path for path in asm if path.is_file()]
    if (home is None or len(asm) != 2
            or not (home / "bin/javac").is_file() or not (home / "bin/java").is_file()):
        return None
    return home, asm


def storage_paths():
    sys.path.insert(0, str(ROOT / "tools"))
    try:
        import storage_config
        return storage_config
    except Exception:  # a self-contained fixture run does not need storage config
        return None


def repo_java_home():
    """Reuse the repository's Java 25 resolver; fall back to the storage cache."""
    sys.path.insert(0, str(ROOT / "tools"))
    try:
        from build_method_splitter import java_home
        return java_home()
    except (SystemExit, OSError, KeyError, ValueError):
        pass
    storage = storage_paths()
    if storage is not None:
        for candidate in sorted(storage.cache_path("toolchains").glob(
                "graalvm-25.0.2/*/Contents/Home")):
            if (candidate / "bin/javac").is_file():
                return candidate
    return None


def default_asm():
    storage = storage_paths()
    if storage is not None:
        libs = storage.cache_path("libs/asm-9.9")
        asm = [libs / "asm-9.9.jar", libs / "asm-tree-9.9.jar"]
        if all(path.is_file() for path in asm):
            return asm
    record = ROOT / "artifacts/b0/prepared-04/result.json"
    if record.is_file():
        return [Path(row["path"]) for row in json.loads(record.read_text())["engineLibraries"]
                if Path(row["path"]).name.startswith(("asm-9.", "asm-tree-9."))]
    return []


class ChatKeys1211Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        resolved = toolchain()
        if resolved is None:
            raise unittest.SkipTest("Requires pinned Java 25 and ASM")
        cls.home, cls.asm = resolved
        cls.classpath = os.pathsep.join(map(str, cls.asm))
        cls.temporary = tempfile.TemporaryDirectory()
        cls.build = Path(cls.temporary.name) / "build"
        cls.build.mkdir()
        sources = sorted((FIXTURE / "src").rglob("*.java"))
        sources += [FIXTURE / "fabricaot/gl41/ChatKeyCheck.java",
                    FIXTURE / "fabricaot/gl41/FailClosed.java",
                    FIXTURE / "RealClientPatch.java", *PATCHER_SOURCES]
        subprocess.run([str(cls.home / "bin/javac"), "--release", "25", "-proc:none",
                        "-cp", cls.classpath, "-d", str(cls.build), *map(str, sources)],
                       check=True, capture_output=True, text=True, timeout=180)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def java(self, *args, timeout=120):
        return subprocess.run([str(self.home / "bin/java"), "-Xverify:all", "-cp",
                               str(self.build) + os.pathsep + self.classpath, *map(str, args)],
                              capture_output=True, text=True, timeout=timeout)

    def patched_classpath(self, patched):
        return str(patched) + os.pathsep + str(self.build) + os.pathsep + self.classpath

    def test_false_flag_and_null_session_return_without_waiting(self):
        """The plain fixture blocks on the key future; the patch must not."""
        result = self.java("fabricaot.gl41.ChatKeyCheck", "vanilla", timeout=180)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("CHAT_KEYS_1211_VANILLA_BLOCKS", result.stdout)

        with tempfile.TemporaryDirectory() as temp:
            patched = Path(temp)
            subprocess.run([str(self.home / "bin/java"), "-cp",
                            str(self.build) + os.pathsep + self.classpath,
                            "fabricaot.gl41.RealClientPatch", self.build / LISTENER, patched / LISTENER],
                           check=True, capture_output=True, text=True, timeout=60)
            result = subprocess.run(
                [str(self.home / "bin/java"), "-Xverify:all", "-cp", self.patched_classpath(patched),
                 "fabricaot.gl41.ChatKeyCheck", "patched"],
                capture_output=True, text=True, timeout=180)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("CHAT_KEYS_1211_PASS", result.stdout)

    def test_unexpected_secure_chat_structures_fail_closed(self):
        predicate = (FIXTURE / "src/net/minecraft/client/multiplayer/ClientPacketListener.java")
        variants = {
            "predicate ignores the server flag": (
                predicate, "return this.minecraft.canValidateProfileKeys() && this.serverEnforcesSecureChat;",
                "return this.minecraft.canValidateProfileKeys();"),
            "initializer reads the session twice": (
                predicate, "RemoteChatSession.Data session = entry.chatSession();",
                "RemoteChatSession.Data session = entry.chatSession();\n        entry.chatSession();"),
            "initializer fetches the validator twice": (
                predicate, "SignatureValidator validator = this.minecraft.getProfileKeySignatureValidator();",
                "SignatureValidator validator = this.minecraft.getProfileKeySignatureValidator();\n"
                "        this.minecraft.getProfileKeySignatureValidator();"),
        }
        for label, (source, original, replacement) in variants.items():
            with self.subTest(label), tempfile.TemporaryDirectory() as temp:
                tree = Path(temp) / "src"
                shutil.copytree(FIXTURE / "src", tree)
                variant = tree / source.relative_to(FIXTURE / "src")
                text = variant.read_text()
                self.assertIn(original, text)
                variant.write_text(text.replace(original, replacement, 1))
                classes = Path(temp) / "classes"
                classes.mkdir()
                subprocess.run([str(self.home / "bin/javac"), "--release", "25", "-proc:none",
                                "-cp", str(self.build) + os.pathsep + self.classpath,
                                "-d", str(classes), *map(str, sorted(tree.rglob("*.java")))],
                               check=True, capture_output=True, text=True, timeout=120)
                result = subprocess.run(
                    [str(self.home / "bin/java"), "-cp",
                     str(classes) + os.pathsep + str(self.build) + os.pathsep + self.classpath,
                     "fabricaot.gl41.FailClosed", classes / LISTENER],
                    capture_output=True, text=True, timeout=60)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("FAIL_CLOSED", result.stdout)

    def test_own_fixture_class_is_not_the_audited_client(self):
        """A same-name class outside 1.21.1 is rejected rather than patched by name alone."""
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp)
            source = out / "Other.java"
            source.write_text("package fixture; public class Other {}\n")
            subprocess.run([str(self.home / "bin/javac"), "--release", "25", "-proc:none",
                            "-d", str(out), str(source)], check=True, capture_output=True, timeout=60)
            result = self.java("fabricaot.gl41.FailClosed", out / "fixture/Other.class", timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("FAIL_CLOSED", result.stdout)

    def test_real_1211_listener_keeps_vanilla_structure_after_guard(self):
        """Structural check only; the proprietary client is never executed."""
        value = os.environ.get("MCFAO_TEST_1211_CLIENT")
        if not value or not Path(value).is_file():
            self.skipTest("Requires locally mapped 1.21.1 client (MCFAO_TEST_1211_CLIENT)")
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp)
            with zipfile.ZipFile(value) as archive:
                original = out / "original.class"
                original.write_bytes(archive.read(LISTENER))
            patched = out / "patched.class"
            subprocess.run([str(self.home / "bin/java"), "-cp",
                            str(self.build) + os.pathsep + self.classpath,
                            "fabricaot.gl41.RealClientPatch", original, patched],
                           check=True, capture_output=True, text=True, timeout=60)
            before = subprocess.check_output([self.home / "bin/javap", "-p", "-c", original], text=True)
            after = subprocess.check_output([self.home / "bin/javap", "-p", "-c", patched], text=True)

        def body(disassembly, signature):
            start = disassembly.index(signature)
            end = disassembly.index("\n\n", start)
            return disassembly[start:end]

        predicate = "private boolean enforcesSecureChat();"
        initializer = ("private void initializeChatSession(net.minecraft.network.protocol.game."
                       "ClientboundPlayerInfoUpdatePacket$Entry, net.minecraft.client.multiplayer.PlayerInfo);")
        guarded = body(after, predicate)
        self.assertIn("serverEnforcesSecureChat:Z", guarded)
        self.assertLess(guarded.index("serverEnforcesSecureChat:Z"),
                        guarded.index("Minecraft.canValidateProfileKeys"))
        self.assertIn("ireturn", guarded)
        self.assertEqual(body(after, predicate).count("Minecraft.canValidateProfileKeys"), 1)

        original_initializer = body(before, initializer)
        patched_initializer = body(after, initializer)
        self.assertEqual(patched_initializer.count("Minecraft.getProfileKeySignatureValidator"), 1)
        # One read in the new guard, one left untouched in the vanilla body.
        self.assertEqual(original_initializer.count("Entry.chatSession"), 1)
        self.assertEqual(patched_initializer.count("Entry.chatSession"), 2)
        self.assertLess(patched_initializer.index("Entry.chatSession"),
                        patched_initializer.index("Minecraft.getProfileKeySignatureValidator"))
        for preserved in ("RemoteChatSession$Data.validate", "PlayerInfo.setChatSession",
                          "ProfilePublicKey$ValidationException"):
            self.assertIn(preserved, patched_initializer)
            self.assertIn(preserved, original_initializer)
        self.assertIn("PlayerInfo.clearChatSession", patched_initializer)


if __name__ == "__main__":
    unittest.main()
