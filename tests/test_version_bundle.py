"""Packaged desktop resources must resolve every supported Minecraft line."""
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
TAURI = ROOT / 'desktop/src-tauri/tauri.conf.json'
sys.path.insert(0, str(ROOT / 'tools'))
import bundle_source


def resource_files():
    """Resolve the Tauri file resources the packaged desktop app installs."""
    config = json.loads(TAURI.read_text())
    for source, destination in config['bundle']['resources'].items():
        if source.endswith('/'):
            continue  # Generated source trees are staged separately.
        yield (ROOT / 'desktop/src-tauri' / source).resolve(), destination


def packaged_report(tools, pack):
    """Run the copied preflight module exactly as the packaged app resolves it."""
    # -I ignores PYTHONPATH and the current directory, so only the copied
    # resource directory can satisfy the module imports. -I also omits the
    # script directory, so the launcher adds it explicitly like a frozen app.
    launcher = (
        'import sys; tools=sys.argv[1]; '
        'sys.argv=[tools+"/preflight.py", sys.argv[2]]; '
        'sys.path.insert(0, tools); '
        'exec(compile(open(sys.argv[0], encoding="utf-8").read(), sys.argv[0], "exec"), '
        '{"__name__":"__main__", "__file__":sys.argv[0]})'
    )
    env = {key: value for key, value in os.environ.items() if key != 'PYTHONPATH'}
    result = subprocess.run(
        [sys.executable, '-I', '-B', '-X', 'utf8', '-c', launcher, str(tools), str(pack)],
        cwd=tempfile.gettempdir(), env=env, capture_output=True, text=True)
    return result, json.loads(result.stdout) if result.stdout.strip() else None


class PackagedVersionResourcesTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.resources = self.root / 'Resources'
        for source, destination in resource_files():
            target = self.resources / destination
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        self.tools = self.resources / 'tools'

    def pack(self, version):
        path = self.root / (version.replace('/', '_') + '.mrpack')
        manifest = dict(formatVersion=1, game='minecraft', name='version fixture', versionId='1',
                        dependencies={'minecraft': version, 'fabric-loader': '0.19.3'}, files=[])
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('modrinth.index.json', json.dumps(manifest))
        return path

    def test_packaged_preflight_recognizes_each_supported_line(self):
        for version in ('1.21.1', '26.1.2'):
            with self.subTest(version=version):
                result, report = packaged_report(self.tools, self.pack(version))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(report['target_minecraft'], version)
                self.assertEqual(report['minecraft_version'], version)
                self.assertNotIn('WRONG_MC_VERSION', [row['code'] for row in report['findings']])

    def test_packaged_preflight_rejects_an_unknown_line(self):
        result, report = packaged_report(self.tools, self.pack('1.20.1'))
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(report['target_minecraft'], '')
        self.assertIn('WRONG_MC_VERSION', [row['code'] for row in report['findings']])


class SourceBundleVersionTests(unittest.TestCase):
    def test_game_versions_is_a_required_bundled_module(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / 'source'
            manifest = bundle_source.stage(output)
        self.assertIn('tools/game_versions.py', manifest['filesSHA256'])
        self.assertTrue(bundle_source.allowed('tools/game_versions.py'))


if __name__ == '__main__':
    unittest.main()
