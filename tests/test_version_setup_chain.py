"""Version handoff in the GUI setup/config chain.

These tests pin the contract between the desktop workbench, the shared config
loader (mcfao.load_config) and the environment preparer: a config is validated
for the line the GUI selected, a prepared 26.1.2 environment never counts as a
ready 1.21.1 one, and a 1.21.1 preparation writes its own versionProfiles entry.
Nothing here touches a compiler, a network or a device.
"""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import desktop_workbench as w
import mcfao
import prepare_environment


class SetupChainTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = self.root / 'source'
        (self.workspace / 'tools').mkdir(parents=True)
        (self.workspace / 'tools/mcfao.py').write_text('fixture')
        self.jobs = self.root / 'jobs'

    def inputs(self, prefix):
        folder = self.root / prefix
        folder.mkdir(parents=True)
        values = {}
        for key in w.KEYS:
            path = folder / key
            path.write_text('fixture ' + key)
            values[key] = str(path)
        return values

    def config(self, name, values, profiles=None):
        path = self.root / (name + '.json')
        document = dict(values)
        if profiles is not None: document['versionProfiles'] = profiles
        w.write_json(path, document)
        return path

    def storage(self):
        return dict(cache=self.root / 'cache', builds=self.root / 'builds',
                    jobs=self.jobs, rust=self.root / 'rust')

    def test_config_summary_accepts_profiles_and_uses_the_selected_line(self):
        base = self.inputs('base')
        other = self.inputs('other')
        path = self.config('config', base, {'1.21.1': {
            'prepared': other['prepared'], 'assets': other['assets'],
            'versionMetadata': other['versionMetadata'], 'gl41': other['gl41'],
            'lwjglOpengl': other['lwjglOpengl']}})
        _, default = w.config_summary(str(path))
        self.assertEqual(default['prepared'], Path(base['prepared']).resolve())
        _, selected = w.config_summary(str(path), '1.21.1')
        self.assertEqual(selected['prepared'], Path(other['prepared']).resolve())
        self.assertEqual(selected['assets'], Path(other['assets']).resolve())
        # Untouched root inputs still apply to the selected line.
        self.assertEqual(selected['splitter'], Path(base['splitter']).resolve())

    def test_config_summary_refuses_a_line_without_its_own_profile(self):
        path = self.config('config', self.inputs('base'))
        w.config_summary(str(path))
        with self.assertRaisesRegex(ValueError, 'versionProfiles'):
            w.config_summary(str(path), '1.21.1')

    def test_a_ready_26_environment_is_not_ready_for_1_21_1(self):
        base = self.inputs('base')
        other = self.inputs('other')
        folder = self.jobs / 'configs'
        folder.mkdir(parents=True)
        legacy = folder / 'legacy.json'
        modern = folder / 'modern.json'
        w.write_json(legacy, base)
        w.write_json(modern, {**base, 'versionProfiles': {'1.21.1': {
            'prepared': other['prepared'], 'assets': other['assets'],
            'versionMetadata': other['versionMetadata']}}})
        state = dict(state='ready', minecraftVersion='26.1.2', config=str(legacy))
        with patch.object(w, 'storage_paths', return_value=self.storage()), \
             patch('first_run.status', return_value=state):
            result = w.bootstrap(self.workspace, '1.21.1')
        self.assertEqual(result['minecraftVersion'], '1.21.1')
        # The ready 26.1.2 environment is reported as stale, not ready.
        self.assertEqual(result['setup']['state'], 'stale')
        self.assertEqual(result['setup']['preparedVersion'], '26.1.2')
        # Discovery skips the 26.1.2 config and finds the 1.21.1 declaration.
        self.assertEqual(result['config'], str(modern))

    def test_bootstrap_keeps_the_default_line_for_26(self):
        folder = self.jobs / 'configs'
        folder.mkdir(parents=True)
        legacy = folder / 'legacy.json'
        w.write_json(legacy, self.inputs('base'))
        state = dict(state='ready', minecraftVersion='26.1.2', config=str(legacy))
        with patch.object(w, 'storage_paths', return_value=self.storage()), \
             patch('first_run.status', return_value=state):
            result = w.bootstrap(self.workspace)
        self.assertEqual(result['setup']['state'], 'ready')
        self.assertEqual(result['config'], str(legacy))

    def test_save_config_preserves_raw_version_profiles(self):
        values = self.inputs('base')
        profiles = {'1.21.1': {'prepared': '/somewhere/1.21.1/prepared'},
                    '26.1.2': {'assets': 'relative/assets'}}
        with patch.object(w, 'workspace_path', return_value=self.workspace), \
             patch.object(w, 'storage_paths', return_value=self.storage()):
            saved = w.save_config(dict(workspace=str(self.workspace),
                                       minecraftVersion='1.21.1',
                                       fields={**values, 'versionProfiles': profiles}))
        document = w.read_json(saved['path'])
        self.assertEqual(document['versionProfiles'], profiles)
        self.assertEqual(saved['minecraftVersion'], '1.21.1')

    def test_setup_start_passes_the_selected_line_to_first_run(self):
        argv = ['desktop_workbench.py', 'setup-start', '--workspace', str(self.workspace),
                '--request', json.dumps({'workspace': str(self.workspace),
                                         'minecraftVersion': '1.21.1',
                                         'fields': {'owned': True}})]
        seen = {}
        def start(owned, version=None):
            seen.update(owned=owned, version=version)
            return dict(state='queued', minecraftVersion=version)
        with patch.object(sys, 'argv', argv), patch.object(w, 'workspace_path', return_value=self.workspace), \
             patch.object(w, 'storage_paths', return_value=self.storage()), \
             patch.object(w, 'history', return_value=[]), patch.object(w, 'control_lock'), \
             patch('first_run._start', side_effect=start), \
             patch('builtins.print') as output:
            self.assertEqual(w.main(), 0)
        self.assertEqual(seen, {'owned': True, 'version': '1.21.1'})
        self.assertEqual(json.loads(output.call_args.args[0])['minecraftVersion'], '1.21.1')

    def test_setup_status_reports_the_recorded_line_as_stale(self):
        argv = ['desktop_workbench.py', 'setup-status', '--workspace', str(self.workspace),
                '--request', json.dumps({'workspace': str(self.workspace),
                                         'minecraftVersion': '1.21.1'})]
        with patch.object(sys, 'argv', argv), patch.object(w, 'workspace_path', return_value=self.workspace), \
             patch.object(w, 'storage_paths', return_value=self.storage()), \
             patch('first_run.status', return_value=dict(state='ready', minecraftVersion='26.1.2')), \
             patch('builtins.print') as output:
            self.assertEqual(w.main(), 0)
        self.assertEqual(json.loads(output.call_args.args[0])['state'], 'stale')

    def test_dependency_rebuild_refuses_a_line_the_config_does_not_declare(self):
        path = self.config('legacy', self.inputs('base'))
        request = dict(workspace=str(self.workspace), config=str(path),
                       dependencyKeys=['glfw'], minecraftVersion='1.21.1')
        with patch.object(w, 'workspace_path', return_value=self.workspace), \
             patch.object(w, 'storage_paths', return_value=self.storage()), \
             patch.object(w, 'history', return_value=[]), \
             patch.object(w.subprocess, 'Popen') as spawn:
            with self.assertRaisesRegex(ValueError, '版本不一致'):
                w.start_dependencies(request)
        spawn.assert_not_called()

    def test_setup_start_without_a_form_version_uses_the_recorded_selection(self):
        folder = self.jobs / 'setup'
        folder.mkdir(parents=True)
        w.write_json(folder / 'selection.json', {'minecraftVersion': '1.21.1'})
        argv = ['desktop_workbench.py', 'setup-start', '--workspace', str(self.workspace),
                '--request', json.dumps({'workspace': str(self.workspace),
                                         'fields': {'owned': True}})]
        seen = {}
        def start(owned, version=None):
            seen.update(owned=owned, version=version)
            return dict(state='queued', minecraftVersion=version)
        with patch.object(sys, 'argv', argv), patch.object(w, 'workspace_path', return_value=self.workspace), \
             patch.object(w, 'storage_paths', return_value=self.storage()), \
             patch.object(w, 'history', return_value=[]), patch.object(w, 'control_lock'), \
             patch('first_run._start', side_effect=start), patch('builtins.print'):
            self.assertEqual(w.main(), 0)
        self.assertEqual(seen['version'], '1.21.1')


class PrepareEnvironmentVersionTests(unittest.TestCase):
    def test_verify_config_forwards_the_line_to_preflight_and_the_loader(self):
        with patch.object(prepare_environment.native_dependencies, 'preflight') as preflight, \
             patch.object(mcfao, 'load_config', return_value={}) as load, \
             patch.object(mcfao, 'inputs') as inputs:
            prepare_environment.verify_config(Path('/source'), Path('/config.json'), '1.21.1')
        preflight.assert_called_once_with(Path('/source'), Path('/config.json'), '1.21.1')
        load.assert_called_once_with(Path('/config.json'), 'gl41', '1.21.1')
        self.assertEqual(inputs.call_args.kwargs, {'version': '1.21.1'})

    def test_verify_config_defaults_to_26_for_legacy_callers(self):
        with patch.object(prepare_environment.native_dependencies, 'preflight') as preflight, \
             patch.object(mcfao, 'load_config', return_value={}), \
             patch.object(mcfao, 'inputs'):
            prepare_environment.verify_config(Path('/source'), Path('/config.json'))
        self.assertEqual(preflight.call_args.args[2], '26.1.2')

    def test_1_21_1_configuration_records_its_own_profiles_entry(self):
        fields = {key: '/prepared/' + key for key in w.KEYS}
        fields['versionProfiles'] = {'26.1.2': {'assets': '/other/assets'}}
        document = prepare_environment.config_document(fields, '1.21.1')
        self.assertEqual(document['prepared'], '/prepared/prepared')
        self.assertEqual(document['versionProfiles']['26.1.2'], {'assets': '/other/assets'})
        entry = document['versionProfiles']['1.21.1']
        for key in ('prepared', 'versionMetadata', 'assets', 'glfw', 'lwjgl', 'gl41', 'lwjglOpengl'):
            self.assertEqual(entry[key], '/prepared/' + key)

    def test_default_configuration_keeps_the_plain_root_keys(self):
        fields = {key: '/prepared/' + key for key in w.KEYS}
        document = prepare_environment.config_document(fields, '26.1.2')
        self.assertNotIn('versionProfiles', document)
        self.assertEqual(document['prepared'], '/prepared/prepared')


if __name__ == '__main__':
    unittest.main()
