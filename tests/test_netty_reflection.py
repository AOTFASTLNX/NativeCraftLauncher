import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import unittest
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]

# Netty registers these exclusions from their own static initializers; each call
# site is therefore proof that the class must stay reachable in an AOT image.
LEAK_EXCLUSION_CALLERS = {
    "io.netty.buffer.AdvancedLeakAwareByteBuf",
    "io.netty.buffer.AbstractByteBufAllocator",
    "io.netty.util.ReferenceCountUtil",
}


def _javap(java_home, jar, classes):
    # The manifest pins the toolchain used to build the image; another javap
    # could disagree about the bytecode under audit, so fail loudly instead.
    executable = Path(java_home) / "bin/javap"
    return subprocess.check_output(
        [str(executable), "-p", "-c", "-cp", str(jar), *classes],
        text=True, timeout=300)


def _declared_classes(jar):
    with ZipFile(jar) as archive:
        names = [n for n in archive.namelist()
                 if n.endswith(".class") and not n.startswith("META-INF/")
                 and not n.endswith("module-info.class")]
    return sorted(n[:-6].replace("/", ".") for n in names)


def _class_sections(disassembly):
    """Split javap output per class; the class literal audit needs class scope."""
    sections = []
    for chunk in re.split(r"^Compiled from ", disassembly, flags=re.M)[1:]:
        match = re.search(r"^(?:\w+\s+)*(?:class|interface|enum)\s+([\w.$]+)", chunk, re.M)
        if match:
            sections.append((match.group(1), chunk.splitlines()))
    return sections


def _leak_exclusion_call_sites(lines):
    """Yield (enclosing method, class literal, string arguments) per call site."""
    for index, line in enumerate(lines):
        if "ResourceLeakDetector.addExclusions:" not in line or "invokestatic" not in line:
            continue
        code = max((c for c in range(index) if lines[c].strip() == "Code:"), default=0)
        header = code
        while header > 0 and not lines[header - 1].strip():
            header -= 1
        signature = lines[header - 1].strip() if header else ""
        # The class literal is the last non-array operand before the call; skip
        # the "java/lang/String" array element type pushed by anewarray.
        literal_at, literal = None, None
        for cursor in range(index - 1, code, -1):
            found = re.search(r"// class ([\w.$/]+)$", lines[cursor])
            if found and found.group(1) != "java/lang/String":
                literal_at, literal = cursor, found.group(1)
                break
        arguments = []
        for cursor in range(literal_at or code, index):
            strings = re.search(r"// String (\S+)$", lines[cursor])
            if strings:
                arguments.append(strings.group(1))
        yield signature, literal, arguments


class NettyReflectionTests(unittest.TestCase):
    def test_actual_jctools_field_offsets_have_unsafe_reflection_metadata(self):
        prepared = os.environ.get("MCFAO_TEST_NETTY_PREPARED")
        if not prepared:
            self.skipTest("Requires locally verified PREPARED Netty libraries")
        manifest = json.loads((Path(prepared) / "result.json").read_text())
        library = next(r for r in manifest["referenceLibraries"]
                       if "/netty-common/" in r["path"])
        jar = Path(library["path"])
        self.assertEqual(hashlib.sha256(jar.read_bytes()).hexdigest(), library["sha256"])
        with ZipFile(jar) as archive:
            classes = [n[:-6].replace("/", ".") for n in archive.namelist()
                       if n.startswith("io/netty/util/internal/shaded/org/jctools/queues/")
                       and n.endswith(".class")]
        self.assertTrue(classes)
        bytecode = subprocess.check_output(
            [str(Path(manifest["javaHome"]) / "bin/javap"), "-p", "-c",
             "-cp", str(jar), *classes], text=True, timeout=60)
        # Read the actual class/field literals passed to UnsafeAccess.fieldOffset;
        # missing any one can crash a queue initializer in an AOT runtime.
        offsets = re.findall(
            r"// class ([\w/$]+)\s+\d+: ldc\s+#\d+\s+// String (\w+)\s+"
            r"\d+: invokestatic[^\n]+UnsafeAccess.fieldOffset:", bytecode)
        calls = bytecode.count("UnsafeAccess.fieldOffset:")
        self.assertGreater(calls, 0)
        self.assertEqual(len(offsets), calls, "Unrecognized offset lookup; audit required")
        entries = {e["name"]: e for e in json.loads(
            (ROOT / "compiler/reflection-config.json").read_text())}
        for owner, field in offsets:
            name = owner.replace("/", ".")
            with self.subTest(owner=name, field=field):
                self.assertIn(name, entries)
                entry = entries[name]
                self.assertEqual(entry["condition"], {"typeReachable": name})
                registered = {f["name"]: f for f in entry["fields"]}
                self.assertIn(field, registered)
                self.assertTrue(registered[field].get("allowUnsafeAccess"))

    def test_leak_detector_exclusions_are_reachable_and_reflectively_queryable(self):
        prepared = os.environ.get("MCFAO_TEST_NETTY_PREPARED")
        if not prepared:
            self.skipTest("Requires locally verified PREPARED Netty libraries")
        manifest = json.loads((Path(prepared) / "result.json").read_text())
        jars = {}
        for artifact in ("netty-common", "netty-buffer"):
            library = next(r for r in manifest["referenceLibraries"]
                           if f"/{artifact}/" in r["path"])
            jar = Path(library["path"])
            with self.subTest(artifact=artifact):
                self.assertEqual(hashlib.sha256(jar.read_bytes()).hexdigest(),
                                 library["sha256"])
            jars[artifact] = jar
        java_home = manifest.get("javaHome", "")

        # Audit callers statically: the jars must never be executed here.
        callers = {}
        for artifact, jar in jars.items():
            disassembly = _javap(java_home, jar, _declared_classes(jar))
            for owner, lines in _class_sections(disassembly):
                for method, literal, arguments in _leak_exclusion_call_sites(lines):
                    callers.setdefault(owner, []).append((artifact, method, literal, arguments))
        self.assertEqual(set(callers), LEAK_EXCLUSION_CALLERS,
                         "Unrecognized addExclusions caller; audit required")
        for owner, sites in callers.items():
            with self.subTest(caller=owner):
                self.assertEqual(len(sites), 1)
                _, method, literal, arguments = sites[0]
                self.assertTrue(method.startswith("static {}"),
                                f"{owner} must register exclusions from its static initializer")
                self.assertEqual(literal, owner.replace(".", "/"),
                                 "addExclusions must receive the caller's own class literal")
                self.assertTrue(arguments, "addExclusions must pass excluded method names")

        # AOT needs each caller reachable (so the static initializer runs) and
        # reflectively queryable (so GraalVM keeps the excluded method metadata).
        entries = {e["name"]: e for e in json.loads(
            (ROOT / "compiler/reflection-config.json").read_text())}
        for name in sorted(LEAK_EXCLUSION_CALLERS):
            with self.subTest(name=name):
                self.assertIn(name, entries)
                entry = entries[name]
                self.assertEqual(entry["condition"], {"typeReachable": name})
                self.assertTrue(entry.get("queryAllDeclaredMethods"))

    def test_merged_metadata_keeps_unsafe_access_and_query_entries(self):
        sys.path.insert(0, str(ROOT / "tools"))
        from mdaf import build_native_image_config

        maintained = json.loads((ROOT / "compiler/reflection-config.json").read_text())
        documents, conflicts = build_native_image_config(maintained, [], {})
        self.assertFalse(conflicts)
        merged = documents["reflect-config.json"]

        queues = "io.netty.util.internal.shaded.org.jctools.queues."
        unsafe = [(e["name"], f["name"]) for e in merged
                  if e["name"].startswith(queues)
                  for f in e.get("fields", []) if f.get("allowUnsafeAccess")]
        self.assertEqual(len(unsafe), 9)
        self.assertEqual({name for name, _ in unsafe}, {
            "io.netty.util.internal.shaded.org.jctools.queues.BaseLinkedQueueConsumerNodeRef",
            "io.netty.util.internal.shaded.org.jctools.queues.BaseLinkedQueueProducerNodeRef",
            "io.netty.util.internal.shaded.org.jctools.queues.BaseMpscLinkedArrayQueueColdProducerFields",
            "io.netty.util.internal.shaded.org.jctools.queues.BaseMpscLinkedArrayQueueConsumerFields",
            "io.netty.util.internal.shaded.org.jctools.queues.BaseMpscLinkedArrayQueueProducerFields",
            "io.netty.util.internal.shaded.org.jctools.queues.LinkedQueueNode",
            "io.netty.util.internal.shaded.org.jctools.queues.MpscArrayQueueConsumerIndexField",
            "io.netty.util.internal.shaded.org.jctools.queues.MpscArrayQueueProducerIndexField",
            "io.netty.util.internal.shaded.org.jctools.queues.MpscArrayQueueProducerLimitField",
        })

        queryable = {e["name"] for e in merged if e.get("queryAllDeclaredMethods")}
        queryable &= LEAK_EXCLUSION_CALLERS
        self.assertEqual(queryable, LEAK_EXCLUSION_CALLERS)
        by_name = {e["name"]: e for e in merged}
        for name in sorted(LEAK_EXCLUSION_CALLERS):
            with self.subTest(name=name):
                self.assertEqual(by_name[name]["condition"], {"typeReachable": name})


if __name__ == "__main__":
    unittest.main()
