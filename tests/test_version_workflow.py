"""Backend workflow coverage for the added Minecraft 1.21.1 build line.

These tests exercise the version handoff in tools/desktop_workbench.py without
touching a real compiler, network or device: the source declaration must win
over a mismatched request, a staged 1.21.1 pack must record that line in its
metadata, retries must replay the recorded line, and legacy 26.1.2 jobs must
keep their implicit version.
"""
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import desktop_workbench as w


def mod_jar(mod_id='version_fixture'):
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w') as archive:
        archive.writestr('fabric.mod.json', json.dumps({
            'schemaVersion': 1, 'id': mod_id, 'name': mod_id, 'version': '1.0.0',
            'environment': 'client', 'depends': {},
        }))
    return data.getvalue()


class VersionWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        cache = patch('mod_download_cache.cache_path', return_value=self.root / 'compiler-cache')
        cache.start()
        self.addCleanup(cache.stop)
        w.CANCELLED = False

    def prism(self, version='1.21.1'):
        source = self.root / ('Prism 1.21.1' if version == '1.21.1' else 'Prism instance')
        (source / '.minecraft/mods').mkdir(parents=True)
        (source / '.minecraft/config').mkdir()
        (source / '.minecraft/config/fixture.json').write_text('{"enabled":true}')
        (source / '.minecraft/mods/fixture.jar').write_bytes(mod_jar())
        w.write_json(source / 'mmc-pack.json', {'components': [
            {'uid': 'net.minecraft', 'version': version},
            {'uid': 'net.fabricmc.fabric-loader', 'version': '0.19.3'},
        ]})
        return source

    def test_source_version_rejects_a_request_that_disagrees_with_the_pack(self):
        source = self.prism('1.21.1')
        report = w.scan_pack(source)
        self.assertEqual(report['target_minecraft'], '1.21.1')
        self.assertEqual(w.source_version(report, '1.21.1'), '1.21.1')
        self.assertEqual(w.source_version(report, None), '1.21.1')
        with self.assertRaisesRegex(ValueError, '1.21.1.*26.1.2'):
            w.source_version(report, '26.1.2')
        # The reverse direction fails the same way: a 26.1.2 pack cannot be
        # built as 1.21.1 just because the request asked for it.
        legacy = w.scan_pack(self.prism('26.1.2'))
        with self.assertRaisesRegex(ValueError, '26.1.2.*1.21.1'):
            w.source_version(legacy, '1.21.1')
        with self.assertRaises(ValueError):
            w.source_version(report, '1.20.1')

    def test_source_version_rejects_a_pack_whose_line_cannot_be_recognized(self):
        source = self.prism('1.21.1')
        w.write_json(source / 'mmc-pack.json', {'components': [
            {'uid': 'net.minecraft', 'version': '1.20.4'},
            {'uid': 'net.fabricmc.fabric-loader', 'version': '0.19.3'},
        ]})
        report = w.scan_pack(source)
        self.assertTrue(report['fatal'])
        self.assertEqual(report['target_minecraft'], '')
        # An unrecognized declaration is a hard error: it must never be built as
        # the requested line, and passing None does not fall back to 26.1.2.
        with self.assertRaisesRegex(ValueError, "unsupported Minecraft '1.20.4'"):
            w.source_version(report, '1.21.1')
        with self.assertRaisesRegex(ValueError, "unsupported Minecraft '1.20.4'"):
            w.source_version(report, None)

    def test_prism_1_21_1_staging_records_the_staged_line_in_metadata(self):
        source = self.prism('1.21.1')
        mods, game = w.stage_source(source, self.root / 'stage', version='1.21.1')
        self.assertTrue((mods / 'fixture.jar').is_file())
        staged = w.read_json(self.root / 'stage/mmc-pack.json')
        components = {row['uid']: row['version'] for row in staged['components']}
        self.assertEqual(components['net.minecraft'], '1.21.1')
        self.assertEqual(components['net.fabricmc.fabric-loader'], '0.19.3')
        self.assertEqual(w.read_json(game / 'import.json')['minecraftVersion'], '1.21.1')
        record = w.read_json(self.root / 'stage/source.json')
        self.assertEqual(record['minecraftVersion'], '1.21.1')
        self.assertEqual(record['sourceReport']['target_minecraft'], '1.21.1')
        # The staged metadata is what a later re-scan trusts, so it must agree.
        self.assertEqual(w.scan_pack(self.root / 'stage')['target_minecraft'], '1.21.1')

    def test_staging_refuses_a_version_the_pack_does_not_declare(self):
        source = self.prism('1.21.1')
        with self.assertRaisesRegex(ValueError, '不一致'):
            w.stage_source(source, self.root / 'stage', version='26.1.2')
        self.assertFalse((self.root / 'stage').exists())

    def test_retry_keeps_the_recorded_version_and_leaves_legacy_jobs_untouched(self):
        workspace = self.root / 'workspace'
        workspace.mkdir()
        modern = {'request': {'source': '/saved/pack.mrpack', 'minecraftVersion': '1.21.1'}}
        self.assertEqual(w.retry_request(modern, workspace)['minecraftVersion'], '1.21.1')
        self.assertEqual(modern['request']['minecraftVersion'], '1.21.1')
        # A job that only recorded the line on the job document replays it.
        job_level = {'request': {'source': '/saved/pack.mrpack'}, 'minecraftVersion': '1.21.1'}
        self.assertEqual(w.retry_request(job_level, workspace)['minecraftVersion'], '1.21.1')
        # Jobs from before multi-version support have no field at all and must
        # stay byte-identical so their 26.1.2 meaning is unchanged.
        legacy_request = {'source': '/saved/pack.mrpack', 'loader': 'fabric'}
        legacy = {'request': legacy_request}
        self.assertEqual(w.retry_request(legacy, workspace), legacy_request)
        self.assertNotIn('minecraftVersion', w.retry_request(legacy, workspace))
        self.assertEqual(w.minecraft_version(None), w.DEFAULT_VERSION)

    def test_recorded_version_reads_build_records_and_defaults_old_builds_to_26(self):
        build = self.root / 'build'
        build.mkdir()
        # No records at all: a pre-1.21.1 build was 26.1.2 by definition.
        self.assertEqual(w.recorded_version(build), '26.1.2')
        w.write_json(build / 'inputs.json', {'version': '1.21.1'})
        self.assertEqual(w.recorded_version(build), '1.21.1')
        # result.json is the finished build's own record and outranks inputs.
        w.write_json(build / 'result.json', {'status': 'SIGNED_DEVICE_APP', 'minecraftVersion': '26.1.2'})
        self.assertEqual(w.recorded_version(build), '26.1.2')
        w.write_json(build / 'result.json', {'status': 'SIGNED_DEVICE_APP', 'minecraftVersion': '1.21.1'})
        self.assertEqual(w.recorded_version(build), '1.21.1')
        # A corrupt record must not be silently treated as the requested line.
        (build / 'result.json').write_text('{not json')
        self.assertEqual(w.recorded_version(build), '1.21.1')

    def test_compile_build_passes_the_requested_line_to_the_compiler(self):
        folder = self.root / 'job'
        folder.mkdir()
        request = {'loader': 'vanilla', 'minecraftVersion': '1.21.1',
                   'config': str(self.root / 'config.json'), 'output': str(self.root / 'output')}
        seen = []
        def execute(argv, folder, name, update, stage, env):
            seen.append(list(argv))
        with patch.object(w, 'execute', side_effect=execute):
            error, build = w.compile_build(['python', 'mcfao.py'], folder, request,
                                           self.root / 'build', None, None, lambda **_: None, {})
        self.assertIsNone(error)
        argv = seen[0]
        self.assertEqual(argv[argv.index('--version') + 1], '1.21.1')
        self.assertEqual(argv[argv.index('--target') + 1], 'ios-arm64')
        # A request without a line keeps the 26.1.2 default for legacy callers.
        with patch.object(w, 'execute', side_effect=execute):
            w.compile_build(['python', 'mcfao.py'], folder, {**request, 'minecraftVersion': None},
                            self.root / 'build', None, None, lambda **_: None, {})
        self.assertEqual(seen[1][seen[1].index('--version') + 1], '26.1.2')
        with self.assertRaises(ValueError):
            w.compile_build(['python', 'mcfao.py'], folder, {**request, 'minecraftVersion': '1.20.1'},
                            self.root / 'build', None, None, lambda **_: None, {})


if __name__ == '__main__':
    unittest.main()
