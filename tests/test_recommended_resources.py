"""Recommended resources: pinned offers, requirement expansion, and the kotlin-reflect rule."""
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))

import desktop_workbench
import prepare_fabric_aot


class RecommendedResourceTests(unittest.TestCase):
    def test_touchcontroller_is_offered_with_its_kotlin_runtime(self):
        rows = desktop_workbench.recommended_resources(ROOT, '26.1.2')
        self.assertEqual([row['id'] for row in rows], ['touchcontroller'])
        entry = rows[0]
        self.assertEqual(entry['version'], '0.3.1-alpha17')
        self.assertEqual([row['id'] for row in entry['requires']], ['fabric-language-kotlin'])
        self.assertEqual(entry['loader'], ['0.19.3', '0.19.5'])

    def test_another_minecraft_line_offers_nothing(self):
        self.assertEqual(desktop_workbench.recommended_resources(ROOT, '1.21.1'), [])

    def test_a_tick_expands_into_the_pinned_file_set(self):
        rows = desktop_workbench.recommended_entries(ROOT, '26.1.2', ['touchcontroller'])
        self.assertEqual([row['file'] for row in rows],
                         ['TouchController-0.3.1-alpha17+fabric.jar',
                          'fabric-language-kotlin-1.13.11+kotlin.2.3.21.jar'])
        for row in rows:
            self.assertTrue(row['url'].startswith('https://cdn.modrinth.com/'))
            self.assertGreater(row['size'], 0)
            self.assertRegex(row['sha512'], r'^[0-9a-f]{128}$')

    def test_unknown_ids_are_rejected_before_a_job_is_queued(self):
        with self.assertRaisesRegex(ValueError, '未知的推荐资源'):
            desktop_workbench.recommended_entries(ROOT, '26.1.2', ['not-a-mod'])

    def test_selection_shape_is_validated(self):
        base = dict(workspace=str(ROOT), minecraftVersion='26.1.2', loader='fabric',
                    output='/tmp/ncl-recommended-out')
        for bad in ('touchcontroller', [''], ['touchcontroller', 'touchcontroller'], [1]):
            with self.assertRaises(ValueError):
                desktop_workbench.validate_request({**base, 'recommendedResources': bad})


class BuildTimeNestedLibraryTests(unittest.TestCase):
    def test_kotlin_reflect_is_named_across_versions(self):
        name = prepare_fabric_aot.nested_library_name
        self.assertEqual(name('META-INF/jars/kotlin-reflect-2.3.21.jar'), 'kotlin-reflect')
        self.assertEqual(name('kotlin-reflect-2.4.0.jar'), 'kotlin-reflect')
        self.assertIn('kotlin-reflect', prepare_fabric_aot.BUILD_TIME_NESTED_LIBRARIES)

    def test_the_stdlib_is_never_skipped(self):
        name = prepare_fabric_aot.nested_library_name
        for kept in ('kotlin-stdlib-2.3.21.jar', 'kotlinx-coroutines-core-jvm-1.10.2.jar',
                     'kotlinx-serialization-json-jvm-1.11.0.jar'):
            self.assertNotIn(name(kept), prepare_fabric_aot.BUILD_TIME_NESTED_LIBRARIES)

    def test_a_skipped_library_leaves_no_classes_and_is_reported(self):
        """The parent still declares kotlin-reflect; only its classes must disappear."""
        work = Path(tempfile.mkdtemp(prefix='ncl-nested-'))
        parent = work / 'flk.jar'
        with zipfile.ZipFile(parent, 'w') as archive:
            archive.writestr('fabric.mod.json', json.dumps({
                'id': 'fabric-language-kotlin', 'version': '1.0',
                'schemaVersion': 1,
                'jars': [{'file': 'META-INF/jars/kotlin-reflect-2.3.21.jar'},
                         {'file': 'META-INF/jars/kotlin-stdlib-2.3.21.jar'}],
            }))
            for nested in ('kotlin-reflect-2.3.21', 'kotlin-stdlib-2.3.21'):
                inner = work / f'{nested}.jar'
                with zipfile.ZipFile(inner, 'w') as child:
                    child.writestr('fabric.mod.json', json.dumps({
                        'id': nested, 'version': '1.0', 'schemaVersion': 1}))
                    child.writestr('sample/Type.class', b'\xca\xfe\xba\xbe')
                archive.write(inner, f'META-INF/jars/{nested}.jar')
                inner.unlink()
        inventory = prepare_fabric_aot.inspect_archives([parent], work / 'scan')
        origins = [origin for _, origin, _, _ in inventory]
        self.assertFalse(any('kotlin-reflect' in origin for origin in origins), origins)
        self.assertTrue(any('kotlin-stdlib' in origin for origin in origins), origins)
        record = prepare_fabric_aot.build_time_library_record(work / 'scan')
        self.assertEqual([row['library'] for row in record], ['kotlin-reflect'])
        self.assertEqual(record[0]['archive'], 'META-INF/jars/kotlin-reflect-2.3.21.jar')


if __name__ == '__main__':
    unittest.main()
