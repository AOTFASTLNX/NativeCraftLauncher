import os
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from capture import capture
from prepare_fabric_aot import (compile_java, load_reference, inspect_archives, inspect_compatibility,
                                remapping_classpath)

PREPARED = Path(os.environ.get("MCFAO_TEST_FABRIC_PREPARED", ROOT / "artifacts/b0/prepared-04"))


class VersionRuntimeMappingTests(unittest.TestCase):
    def test_remapper_uses_only_selected_engine_and_intermediary_client(self):
        selected=Path('/fixture/fabric-loader-selected.jar')
        other=Path('/fixture/fabric-loader-other.jar')
        client=Path('/fixture/client.jar')
        intermediary=Path('/fixture/intermediary.jar')
        gson=Path('/fixture/gson.jar')
        with patch('prepare_fabric_aot.all_engine_libraries', return_value=[
                {'file':selected.name}, {'file':other.name}]):
            result=remapping_classpath([selected], [client,selected,other,gson], {},
                                       client, intermediary)
        self.assertEqual(result, [selected,intermediary,gson])

    def test_intermediary_access_widener_is_accepted_only_for_remapped_version(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            jar = root / "mod.jar"
            with zipfile.ZipFile(jar, "w") as archive:
                archive.writestr("fabric.mod.json", '{"schemaVersion":1,"id":"test"}')
                archive.writestr("test.accesswidener", "accessWidener v2 intermediary\naccessible class net/minecraft/class_1\n")
            inventory = inspect_archives([jar], root)
            parsed = [{"id": "test", "client": True, "entrypoints": {}, "mixins": [],
                       "classTweaker": "test.accesswidener"}]
            report = inspect_compatibility(inventory, parsed, {jar}, namespace="intermediary")
            self.assertEqual(report["status"], "NO_METADATA_BLOCKERS")
            self.assertNotEqual(inspect_compatibility(inventory, parsed, {jar})["status"], "NO_METADATA_BLOCKERS")

    @unittest.skipUnless((PREPARED / "result.json").is_file(), "Requires pinned local Java/Fabric toolchain")
    def test_build_mapping_selectors_and_runtime_resolver(self):
        home, libraries, engine, _ = load_reference(PREPARED)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            classes = output / "classes"
            sources = list((ROOT / "interop/fabric/tool/mcfao/build").glob("*.java"))
            sources += [ROOT / "interop/fabric/tool/mcfao/aot/MappingTable.java",
                        ROOT / "interop/diagnostics/VersionMappingProbe.java"]
            compile_java(home, [*engine, *libraries], sources, classes, output / "compile")
            result = capture([str(home / "bin/java"), "-Xverify:all", "-cp",
                              os.pathsep.join(map(str, [classes, *engine, *libraries])),
                              "mcfao.build.VersionMappingProbe", str(classes)], output / "run", 30)
            self.assertEqual(result["exitCode"], 0, (output / "run/stderr.log").read_text())
            self.assertIn("MCFAO_VERSION_MAPPING_PASS", (output / "run/stdout.log").read_text())


if __name__ == "__main__":
    unittest.main()
