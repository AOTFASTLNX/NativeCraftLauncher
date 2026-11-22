"""Exercise Xcode's resource gate with version-specific asset indexes."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class VersionAssetEmbedTests(unittest.TestCase):
    def run_embed(self, version, index, available):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            assets = root / 'assets'
            (assets / 'indexes').mkdir(parents=True)
            (assets / 'objects').mkdir()
            for item in available:
                (assets / 'indexes' / (item + '.json')).write_text('{}')
            native = root / 'native'
            native.mkdir()
            for name in ('libglfw.dylib', 'liblwjgl.dylib', 'libopenal.dylib',
                         'liblwjgl_opengl.dylib', 'libOSMesa.8.dylib', 'libMoltenVK.dylib'):
                (native / name).write_bytes(b'first-party fixture')
            env = {key: value for key, value in os.environ.items() if not key.startswith('FAOT_')}
            env.update(FAOT_GAME_ASSETS_DIR=str(assets), FAOT_GRAPHICS_NATIVE_DIR=str(native),
                       FAOT_MINECRAFT_VERSION=version, PLATFORM_NAME='iphoneos',
                       TARGET_BUILD_DIR=str(root / 'build'),
                       UNLOCALIZED_RESOURCES_FOLDER_PATH='Test.app',
                       FRAMEWORKS_FOLDER_PATH='Test.app/Frameworks', CODE_SIGNING_ALLOWED='NO')
            if index is not None:
                env['FAOT_ASSET_INDEX'] = index
            result = subprocess.run(['/bin/sh', str(ROOT / 'ios/C0Harness/embed_graphics_libraries.sh')],
                                    env=env, capture_output=True, text=True, timeout=15)
            copied = root / 'build/Test.app/assets/indexes'
            return result, sorted(path.name for path in copied.glob('*.json'))

    def test_selected_1211_index_is_embedded(self):
        result, copied = self.run_embed('1.21.1', '17', ['17'])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(copied, ['17.json'])

    def test_default_version_retains_index_30(self):
        result, copied = self.run_embed('26.1.2', None, ['30'])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(copied, ['30.json'])

    def test_selected_version_cannot_fall_back_to_other_index(self):
        for index in (None, '17', '../30'):
            with self.subTest(index=index):
                result, copied = self.run_embed('1.21.1', index, ['30'])
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(copied, [])


if __name__ == '__main__':
    unittest.main()
