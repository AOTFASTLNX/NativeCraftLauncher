"""Fabric Loader selection: verified versions only, and automatic rounds upward."""
import json
import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))

import loader_selection


class LoaderSelectionTests(unittest.TestCase):
    def setUp(self):
        self.lock = json.loads((ROOT / 'compiler/fabric.lock.json').read_text())

    def test_lock_lists_two_verified_loaders_with_their_own_engine_sets(self):
        self.assertEqual(loader_selection.verified(self.lock), ['0.19.3', '0.19.5'])
        for version in ('0.19.3', '0.19.5'):
            files = [row['file'] for row in loader_selection.engine_libraries(self.lock, version)]
            self.assertIn(f'fabric-loader-{version}.jar', files)
            self.assertTrue(any(name.startswith('sponge-mixin-') for name in files))

    def test_automatic_selection_rounds_up_to_a_verified_version(self):
        for declared, expected in (('0.19.2', '0.19.3'), ('0.19.3', '0.19.3'),
                                   ('0.19.4', '0.19.5'), ('0.19.5', '0.19.5'),
                                   ('0.19.9', '0.19.5'), ('0.18.0', '0.19.3')):
            chosen = loader_selection.select(self.lock, declared)
            self.assertEqual(chosen['version'], expected, declared)
            self.assertTrue(chosen['automatic'])
            self.assertEqual(chosen['declared'], declared)

    def test_unknown_source_uses_the_default_and_explicit_picks_must_be_verified(self):
        self.assertEqual(loader_selection.select(self.lock, None)['version'], '0.19.3')
        chosen = loader_selection.select(self.lock, '0.19.2', '0.19.5')
        self.assertEqual((chosen['version'], chosen['automatic']), ('0.19.5', False))
        with self.assertRaisesRegex(ValueError, '未经验证'):
            loader_selection.select(self.lock, '0.19.2', '0.19.4')

    def test_a_prepared_engine_set_cannot_swap_a_pinned_jar(self):
        rows = loader_selection.engine_libraries(self.lock, '0.19.5')
        tampered = [dict(row) for row in rows]
        tampered[0]['sha256'] = '0' * 64
        expected = {row['file']: row for row in rows}
        self.assertNotEqual(tampered[0]['sha256'], expected[tampered[0]['file']]['sha256'])

    def test_prepared_record_keeps_one_driver_and_engine_set_per_loader(self):
        """A prepared environment must expose both Loaders, each with its own driver."""
        sys.path.insert(0, str(ROOT / 'tools'))
        import prepare_b0
        lock = self.lock
        rows = {version: prepare_b0.loader_engines(lock, version)
                for version in prepare_b0.verified_loaders(lock)}
        self.assertEqual(set(rows), {'0.19.3', '0.19.5'})
        for version, engine in rows.items():
            names = [row['file'] for row in engine]
            self.assertIn(f'fabric-loader-{version}.jar', names)
        self.assertNotEqual([r['file'] for r in rows['0.19.3']],
                            [r['file'] for r in rows['0.19.5']])

    def test_all_engine_libraries_deduplicates_shared_asm_jars(self):
        rows = loader_selection.all_engine_libraries(self.lock)
        names = [row['file'] for row in rows]
        self.assertEqual(len(names), len(set(names)))
        # ASM is shared by both Loaders; Mixin and Loader jars are not.
        self.assertEqual(sum(name.startswith('asm-') for name in names), 5)
        self.assertEqual(sum(name.startswith('fabric-loader-') for name in names), 2)
        self.assertEqual(sum(name.startswith('sponge-mixin-') for name in names), 2)

    def test_prepared_size_is_accepted_as_the_lock_byte_count(self):
        """Preparation records "size"; the lock file calls the same number "bytes"."""
        import prepare_fabric_aot
        self.assertEqual(prepare_fabric_aot.row_bytes({'size': 1984980}), 1984980)
        self.assertEqual(prepare_fabric_aot.row_bytes({'bytes': 1984980}), 1984980)
        self.assertIsNone(prepare_fabric_aot.row_bytes({'sha256': '0' * 64}))

    def test_knot_classpath_never_carries_two_fabric_loaders(self):
        """Knot refuses a classpath with two Loader jars; the reference holds both."""
        import prepare_fabric_aot
        engine_dir = Path('/cache/engine')
        selected = [engine_dir / 'fabric-loader-0.19.5.jar',
                    engine_dir / 'sponge-mixin-0.17.4+mixin.0.8.7.jar']
        libraries = [Path('/cache/versions/26.1.2/26.1.2.jar'),
                     engine_dir / 'fabric-loader-0.19.3.jar',
                     engine_dir / 'sponge-mixin-0.17.3+mixin.0.8.7.jar',
                     *selected]
        classpath = prepare_fabric_aot.knot_classpath(selected, libraries, self.lock)
        names = [path.name for path in classpath]
        self.assertEqual(names.count('fabric-loader-0.19.5.jar'), 1)
        self.assertNotIn('fabric-loader-0.19.3.jar', names)
        self.assertNotIn('sponge-mixin-0.17.3+mixin.0.8.7.jar', names)
        self.assertIn('26.1.2.jar', names)


if __name__ == '__main__':
    unittest.main()
