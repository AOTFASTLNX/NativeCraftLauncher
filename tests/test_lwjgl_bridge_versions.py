"""Versioned LWJGL bridge selection and native dependency version flow.

Every fixture is local: no GraalVM, Xcode or official download is contacted.
"""
from __future__ import annotations

import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import graphics_common as g
import native_dependencies as d
import desktop_workbench as w
from game_versions import DEFAULT_VERSION


def macho(payload=b'first-party fixture', platform=2):
    return (struct.pack('<IiiIIIII', 0xFEEDFACF, 0x0100000C, 0, 6, 1, 24, 0, 0)
            + struct.pack('<IIIIII', 0x32, 24, platform, 0x110000, 0x110000, 0)
            + payload)


def write(path: Path, value) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value if isinstance(value, str) else json.dumps(value))
    return path


class References:
    """A prepared referenceLibraries fixture with real jars on disk."""

    def __init__(self, root: Path):
        self.root = root
        self.rows = []

    def add(self, name: str, data: bytes = b'first-party jar') -> dict:
        path = self.root / 'libraries' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        row = dict(path=str(path), sha256=d.sha256(path), size=path.stat().st_size)
        self.rows.append(row)
        return row

    def lwjgl(self, version: str = '3.4.1') -> list[dict]:
        for module in ('lwjgl', 'lwjgl-glfw', 'lwjgl-stb', 'lwjgl-openal', 'lwjgl-opengl'):
            self.add(module + '-' + version + '.jar')
            # Host-only natives must never be selected.
            self.add(module + '-' + version + '-natives-macos-arm64.jar', b'host natives')
        return self.rows


def bridge_inputs(rows):
    return {row['path']: row['sha256'] for row in rows
            if 'natives' not in Path(row['path']).name and 'opengl' not in Path(row['path']).name}


class ModuleSelectionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()

    def test_default_selection_keeps_the_26_1_2_contract(self):
        rows = References(self.root).lwjgl('3.4.1')
        modules = g.select_lwjgl_modules(rows)
        self.assertEqual(list(modules), list(g.BRIDGE_MODULES))
        self.assertTrue(all(row['version'] == '3.4.1' for row in modules.values()))
        self.assertTrue(all('natives' not in Path(row['path']).name for row in modules.values()))

    def test_1_21_1_selects_3_3_3_and_rejects_the_other_line(self):
        rows = References(self.root).lwjgl('3.3.3')
        self.assertEqual(g.expected_lwjgl_version('1.21.1'), '3.3.3')
        modules = g.select_lwjgl_modules(rows, '1.21.1')
        self.assertTrue(all(row['version'] == '3.3.3' for row in modules.values()))
        with self.assertRaisesRegex(ValueError, '需要 LWJGL 3.4.1'):
            g.select_lwjgl_modules(rows, '26.1.2')
        with self.assertRaisesRegex(ValueError, '需要 LWJGL 3.3.3'):
            g.select_lwjgl_modules(References(self.root / 'other').lwjgl('3.4.1'), '1.21.1')

    def test_missing_duplicate_and_mixed_modules_fail_closed(self):
        rows = References(self.root).lwjgl('3.4.1')
        with self.assertRaisesRegex(ValueError, '缺少 LWJGL 模块'):
            g.select_lwjgl_modules([r for r in rows if 'lwjgl-stb-3' not in r['path']])
        duplicate = [*rows, dict(rows[0])]
        with self.assertRaisesRegex(ValueError, '重复的 LWJGL 模块'):
            g.select_lwjgl_modules(duplicate)
        mixed = References(self.root / 'mixed')
        for module in ('lwjgl', 'lwjgl-glfw', 'lwjgl-stb'):
            mixed.add(module + '-3.4.1.jar')
        mixed.add('lwjgl-openal-3.3.3.jar')
        with self.assertRaisesRegex(ValueError, '版本不一致'):
            g.select_lwjgl_modules(mixed.rows)

    def test_opengl_module_is_pinned_to_the_same_line(self):
        rows = References(self.root).lwjgl('3.3.3')
        entry = g.select_lwjgl_opengl(rows, '1.21.1')
        self.assertEqual(entry['version'], '3.3.3')
        self.assertEqual(Path(entry['path']).name, 'lwjgl-opengl-3.3.3.jar')
        with self.assertRaisesRegex(ValueError, '需要 LWJGL 3.4.1'):
            g.select_lwjgl_opengl(rows, '26.1.2')


class BridgeReportTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.rows = References(self.root).lwjgl('3.4.1')
        self.report = dict(status='STATIC_JNI_BRIDGE_BUILT', target='arm64-apple-ios17.0',
                           inputsSHA256=bridge_inputs(self.rows))

    def bridge(self, name='bridge', **fields):
        directory = self.root / name
        directory.mkdir()
        write(directory / 'result.json', {**self.report, **fields})
        return directory

    def test_legacy_report_is_inferred_as_default_only_with_exact_pinned_inputs(self):
        directory = self.bridge()
        selection = g.bridge_selection(directory, self.rows)
        self.assertTrue(selection['legacy'])
        self.assertEqual(selection['minecraftVersion'], '26.1.2')
        self.assertEqual(g.validate_bridge_libraries(directory, self.rows), selection['paths'])
        with self.assertRaisesRegex(g.BridgeVersionMismatch, '只能用于 26.1.2'):
            g.validate_bridge_libraries(directory, self.rows, '1.21.1')

    def test_legacy_report_with_other_inputs_is_not_guessed(self):
        rows = References(self.root / 'other').lwjgl('3.3.3')
        directory = self.bridge()
        # The 3.3.3 references cannot stand in for the default 3.4.1 inputs.
        with self.assertRaisesRegex(ValueError, '需要 LWJGL 3.4.1'):
            g.bridge_selection(directory, rows)

    def test_versioned_report_must_match_line_release_and_hashes(self):
        rows = References(self.root / 'other').lwjgl('3.3.3')
        pinned = bridge_inputs(rows)
        directory = self.bridge(minecraftVersion='1.21.1', lwjglVersion='3.3.3', inputsSHA256=pinned)
        self.assertEqual(g.bridge_selection(directory, rows, '1.21.1')['lwjglVersion'], '3.3.3')
        with self.assertRaisesRegex(g.BridgeVersionMismatch, '属于 Minecraft'):
            g.bridge_selection(directory, rows, '26.1.2')
        wrong = self.bridge('wrong', minecraftVersion='1.21.1', lwjglVersion='3.4.1', inputsSHA256=pinned)
        with self.assertRaisesRegex(g.BridgeVersionMismatch, '不一致'):
            g.bridge_selection(wrong, rows, '1.21.1')
        partial = self.bridge('partial', minecraftVersion='1.21.1', inputsSHA256=pinned)
        with self.assertRaisesRegex(ValueError, '版本字段不完整'):
            g.bridge_selection(partial, rows, '1.21.1')
        stale = self.bridge('stale', minecraftVersion='1.21.1', lwjglVersion='3.3.3',
                            inputsSHA256={**pinned, '/tmp/extra.jar': '0' * 64})
        with self.assertRaisesRegex(ValueError, '输入集合'):
            g.bridge_selection(stale, rows, '1.21.1')

    def test_opengl_shim_report_is_coupled_to_the_selected_jar(self):
        rows = References(self.root / 'other').lwjgl('3.4.1')
        jar = next(row for row in rows if Path(row['path']).name == 'lwjgl-opengl-3.4.1.jar')
        directory = self.root / 'shim'
        directory.mkdir()
        base = dict(status='LWJGL_OPENGL_SHIM_BUILT', target='arm64-apple-ios17.0',
                    lwjglOpenGLJar=jar['path'], lwjglOpenGLJarSHA256=jar['sha256'])
        write(directory / 'result.json', base)
        self.assertEqual(g.opengl_shim_selection(directory, rows), Path(jar['path']))
        # A versionless report is the default line only; it cannot serve 1.21.1.
        with self.assertRaisesRegex(g.BridgeVersionMismatch, '只能用于 26.1.2'):
            g.opengl_shim_selection(directory, rows, '1.21.1')
        rows = References(self.root / '1211').lwjgl('3.3.3')
        jar = next(row for row in rows if Path(row['path']).name == 'lwjgl-opengl-3.3.3.jar')
        versioned = dict(status='LWJGL_OPENGL_SHIM_BUILT', target='arm64-apple-ios17.0',
                         minecraftVersion='1.21.1', lwjglVersion='3.3.3',
                         lwjglOpenGLJar=jar['path'], lwjglOpenGLJarSHA256=jar['sha256'])
        write(directory / 'result.json', versioned)
        self.assertEqual(g.opengl_shim_selection(directory, rows, '1.21.1'), Path(jar['path']))
        write(directory / 'result.json', {**versioned, 'lwjglOpenGLJarSHA256': '0' * 64})
        with self.assertRaisesRegex(ValueError, '哈希'):
            g.opengl_shim_selection(directory, rows, '1.21.1')


class ConfigVersionFlowTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.config = self.root / 'config.json'
        self.paths = {}
        w.CANCELLED = False
        for key, _, builder, *_ in d.SPECS:
            write(self.root / ('tools/' + builder + '.py'), 'first-party ' + key)
        write(self.root / 'tools/game_versions.py', 'first-party versions')
        write(self.root / 'ios/glfw-lite/window.c', 'first-party source')
        prepared = self.root / 'prepared'
        prepared.mkdir()
        self.client = prepared / '26.1.2.jar'
        self.client.write_bytes(b'first-party client fixture')
        write(prepared / 'result.json', dict(status='PREPARED', minecraftVersion='26.1.2',
                                             referenceLibraries=[
                                                 dict(path=str(self.client), sha256=d.sha256(self.client))]))
        self.paths['prepared'] = prepared
        for key in ('glfw', 'lwjgl'):
            directory = self.root / ('old-' + key)
            self.emit(directory, key, self.paths)
            self.paths[key] = directory
        (self.root / 'old-1211-glfw').mkdir()
        (self.root / 'old-1211-lwjgl').mkdir()
        self.document = {k: str(v) for k, v in self.paths.items()}
        self.document['versionProfiles'] = {
            '1.21.1': {'glfw': 'old-1211-glfw', 'lwjgl': 'old-1211-lwjgl'},
            '26.1.2': {'glfw': 'old-26-glfw'},
        }
        write(self.config, self.document)
        registration = patch('graphics_common.register_tree')
        registration.start()
        self.addCleanup(registration.stop)

    def emit(self, directory, key, paths, payload=b'fixture'):
        directory.mkdir()
        for name in d.BY_KEY[key][5]:
            (directory / name).write_bytes(macho(payload))
        report = dict(status=d.BY_KEY[key][3], target='arm64-apple-ios17.0',
                      sourcesSHA256=d.sources(self.root, key),
                      binarySHA256=d.sha256(directory / d.BY_KEY[key][5][0]))
        if key == 'glfw':
            report['clientSHA256'] = d.sha256(self.client)
        if key == 'lwjgl':
            report['glfwLibrarySHA256'] = d.sha256(paths['glfw'] / 'libglfw.dylib')
            report['overlaysSHA256'] = {}
            for name in ('lwjgl-ios.jar', 'lwjgl-glfw-ios.jar', 'lwjgl-stb-ios.jar', 'lwjgl-openal-ios.jar'):
                path = directory / name
                path.write_bytes(b'first-party jar fixture')
                report['overlaysSHA256'][str(path)] = d.sha256(path)
        write(directory / 'result.json', report)

    def execute(self, argv, key):
        directory = Path(argv[argv.index('--output') + 1])
        self.emit(directory, key, {**self.paths, 'glfw': directory})

    def test_config_paths_merges_only_the_selected_profile(self):
        default = d.config_paths(self.config)
        # A declared default-profile override is applied to the default line too.
        self.assertEqual(default['glfw'], self.root / 'old-26-glfw')
        self.assertEqual(default['lwjgl'], self.paths['lwjgl'])
        self.assertNotIn('versionProfiles', default)
        selected = d.config_paths(self.config, '1.21.1')
        self.assertEqual(selected['glfw'], self.root / 'old-1211-glfw')
        self.assertEqual(selected['prepared'], self.paths['prepared'])
        with self.assertRaises(ValueError):
            d.config_paths(self.config, '1.20.1')
        write(self.config, {'glfw': 3})
        with self.assertRaisesRegex(ValueError, '无效路径'):
            d.config_paths(self.config)

    def test_inventory_and_preflight_use_the_selected_line(self):
        self.assertEqual(d.inspect(self.root, self.paths, 'glfw')['state'], 'current')
        inventory = d.inventory(self.root, self.config)
        self.assertEqual(inventory['minecraftVersion'], '26.1.2')
        with self.assertRaises(ValueError):
            d.preflight(self.root, self.config, '1.21.1')

    def test_legacy_bridge_report_stays_current_and_a_versioned_one_is_coupled(self):
        rows = References(self.root / 'lwjgl').lwjgl('3.4.1')
        write(self.paths['prepared'] / 'result.json', dict(
            status='PREPARED', minecraftVersion='26.1.2',
            referenceLibraries=[dict(path=str(self.client), sha256=d.sha256(self.client)), *rows]))
        report = d.read(self.paths['lwjgl'] / 'result.json')
        report['inputsSHA256'] = bridge_inputs(rows)
        write(self.paths['lwjgl'] / 'result.json', report)
        row = d.inspect(self.root, self.paths, 'lwjgl')
        self.assertEqual(row['state'], 'current')
        # The inferred default is reported, but the report itself is untouched.
        self.assertEqual(row['minecraftVersion'], '26.1.2')
        self.assertNotIn('minecraftVersion', d.read(self.paths['lwjgl'] / 'result.json'))
        write(self.paths['lwjgl'] / 'result.json',
              {**report, 'minecraftVersion': '1.21.1', 'lwjglVersion': '3.3.3'})
        row = d.inspect(self.root, self.paths, 'lwjgl')
        self.assertEqual(row['state'], 'invalid')
        self.assertIn('属于 Minecraft', ' '.join(row['reasons']))

    def test_client_jar_selects_the_version_and_delegates_to_the_remap_owner(self):
        self.assertEqual(d.client_jar(self.paths), self.client)
        with self.assertRaisesRegex(ValueError, '属于 Minecraft 26.1.2'):
            d.client_jar(self.paths, '1.21.1')
        client = self.paths['prepared'] / '1.21.1.jar'
        client.write_bytes(b'obfuscated fixture')
        write(self.paths['prepared'] / 'result.json', dict(
            status='PREPARED', minecraftVersion='1.21.1',
            referenceLibraries=[dict(path=str(client), sha256=d.sha256(client))]))
        remapped = self.root / 'remapped/client.jar'
        remapped.parent.mkdir()
        remapped.write_bytes(b'remapped')
        with patch('prepare_game_version.prepare_client',
                   return_value={'client': str(remapped), 'clientSHA256': d.sha256(remapped)}) as prepare:
            self.assertEqual(d.client_jar(self.paths, '1.21.1'), remapped)
        prepare.assert_called_once()
        self.assertEqual(Path(prepare.call_args.args[1]), client)

    def test_rebuild_for_1_21_1_writes_into_version_profiles_only(self):
        updates = []
        with patch.object(d, 'client_jar', return_value=self.client):
            d.rebuild(self.root, self.config, ['glfw'], self.root / 'new-output', self.execute,
                      lambda: False, lambda **value: updates.append(value), w.write_json,
                      version='1.21.1')
        document = d.read(self.config)
        self.assertEqual(document['glfw'], str(self.paths['glfw']))
        self.assertEqual(document['versionProfiles']['1.21.1']['glfw'],
                         str(self.root / 'new-output/glfw'))
        self.assertEqual(document['versionProfiles']['1.21.1']['lwjgl'], 'old-1211-lwjgl')
        self.assertEqual(document['versionProfiles']['26.1.2'], {'glfw': 'old-26-glfw'})
        self.assertEqual(d.config_paths(self.config)['glfw'], self.root / 'old-26-glfw')
        self.assertEqual(d.config_paths(self.config, '1.21.1')['glfw'], self.root / 'new-output/glfw')

    def test_rebuild_for_1_21_1_without_a_profile_keeps_the_config(self):
        write(self.config, {k: v for k, v in self.document.items() if k != 'versionProfiles'})
        original = self.config.read_bytes()
        with patch.object(d, 'client_jar', return_value=self.client):
            with self.assertRaisesRegex(ValueError, 'versionProfiles'):
                d.rebuild(self.root, self.config, ['glfw'], self.root / 'new-output', self.execute,
                          lambda: False, lambda **value: None, w.write_json, version='1.21.1')
        self.assertEqual(self.config.read_bytes(), original)

    def test_default_rebuild_also_updates_a_declared_default_profile(self):
        d.rebuild(self.root, self.config, ['glfw'], self.root / 'new-output', self.execute,
                  lambda: False, lambda **value: None, w.write_json)
        document = d.read(self.config)
        self.assertEqual(document['glfw'], str(self.root / 'new-output/glfw'))
        self.assertEqual(document['versionProfiles'][DEFAULT_VERSION]['glfw'],
                         str(self.root / 'new-output/glfw'))
        self.assertEqual(document['versionProfiles']['1.21.1']['glfw'], 'old-1211-glfw')
        self.assertEqual(d.config_paths(self.config)['glfw'], self.root / 'new-output/glfw')

    def test_metal_needs_a_prepared_manifest(self):
        paths = dict(ownedClient=self.paths['prepared'], glfw=self.paths['glfw'],
                     lwjgl=self.paths['lwjgl'], prepared=self.root / 'absent')
        with self.assertRaisesRegex(ValueError, 'prepared'):
            d.command(self.root, paths, 'metal', self.root / 'out')

    def test_lwjgl_command_uses_the_bounded_owned_client_inputs(self):
        paths = dict(ownedClient=self.paths['prepared'], glfw=self.paths['glfw'],
                     lwjgl=self.paths['lwjgl'], prepared=self.root / 'absent')
        argv = d.command(self.root, paths, 'lwjgl', self.root / 'out')
        self.assertEqual(argv[argv.index('--minecraft-version') + 1], DEFAULT_VERSION)
        self.assertEqual(argv[argv.index('--owned-client') + 1], str(self.paths['prepared']))
        self.assertNotIn('--prepared', argv)
        self.assertNotIn('--mapped-client', argv)

    def test_prepared_manifest_still_wins_over_the_owned_client(self):
        prepared = self.root / 'prepared-1211'
        prepared.mkdir()
        write(prepared / 'result.json', dict(status='PREPARED', minecraftVersion='1.21.1',
                                             referenceLibraries=[]))
        paths = dict(ownedClient=self.root / 'owned', glfw=self.paths['glfw'],
                     lwjgl=self.paths['lwjgl'], prepared=prepared)
        argv = d.command(self.root, paths, 'lwjgl', self.root / 'out', '1.21.1')
        self.assertEqual(argv[argv.index('--prepared') + 1], str(prepared))
        self.assertNotIn('--owned-client', argv)


if __name__ == '__main__':
    unittest.main()

