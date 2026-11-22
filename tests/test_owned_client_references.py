"""Bounded owned-client references: rule filtering, containment, and hashes."""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import build_lwjgl_opengl_ios
import graphics_common as g
import native_dependencies as d


def sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class OwnedClient:
    """An owned-client tree shaped like acquire_client output."""

    def __init__(self, root: Path, version: str = '1.21.1', lwjgl: str = '3.3.3'):
        self.root = root
        self.version = version
        self.libraries: list[dict] = []
        self.downloads: dict = {}
        for module in ('lwjgl', 'lwjgl-glfw', 'lwjgl-stb', 'lwjgl-openal', 'lwjgl-opengl'):
            self.add(module + '-' + lwjgl + '.jar', 'org/lwjgl/' + module + '/' + lwjgl)
        # Host-only natives that acquire would keep, plus ones it never would.
        self.add('lwjgl-3.3.3-natives-macos-arm64.jar', 'org/lwjgl/lwjgl/3.3.3')
        self.add('lwjgl-3.3.3-natives-windows.jar', 'org/lwjgl/lwjgl/3.3.3',
                 rules=[dict(action='allow', os=dict(name='windows'))])
        self.add('lwjgl-3.3.3-natives-linux.jar', 'org/lwjgl/lwjgl/3.3.3',
                 rules=[dict(action='allow', os=dict(name='linux'))])
        self.client = b'obfuscated client fixture'
        self.write('meta/versions/' + version + '/' + version + '.jar', self.client)
        self.downloads['client'] = dict(size=len(self.client), sha1=sha1(self.client))
        self.document = dict(id=version, downloads=self.downloads, libraries=self.libraries)

    def add(self, name: str, directory: str, data: bytes | None = None, rules=None):
        data = data if data is not None else ('jar ' + name).encode()
        relative = directory + '/' + name
        self.write('meta/libraries/' + relative, data)
        entry = dict(name='fixture:' + name,
                     downloads=dict(artifact=dict(path=relative, size=len(data), sha1=sha1(data))))
        if rules is not None:
            entry['rules'] = rules
        self.libraries.append(entry)
        return entry

    def write(self, relative: str, data: bytes) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def metadata(self) -> Path:
        return self.write(self.version + '.json', json.dumps(self.document).encode())

    def inventory(self, rows=None) -> Path:
        rows = rows if rows is not None else [
            dict(path=str(self.root / 'meta/libraries/org/lwjgl/lwjgl/3.3.3/lwjgl-3.3.3.jar'),
                 sha256=sha256(b'jar lwjgl-3.3.3.jar'),
                 size=len(b'jar lwjgl-3.3.3.jar'))]
        return self.write('client-inputs.json', json.dumps(dict(
            status='OWNED_CLIENT_INPUTS_VERIFIED', minecraftVersion=self.version,
            jars=rows)).encode())


class OwnedReferenceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.owned = OwnedClient(self.root / 'owned')
        self.owned.metadata()

    def test_unsupported_host_natives_are_skipped_not_required(self):
        rows = g.owned_client_references(self.owned.root, '1.21.1')
        names = {Path(row['path']).name for row in rows}
        self.assertIn('lwjgl-3.3.3-natives-macos-arm64.jar', names)
        self.assertNotIn('lwjgl-3.3.3-natives-windows.jar', names)
        self.assertNotIn('lwjgl-3.3.3-natives-linux.jar', names)
        self.assertIn('1.21.1.jar', names)
        self.assertEqual(g.select_lwjgl_opengl(rows, '1.21.1')['version'], '3.3.3')

    def test_an_absent_supported_library_is_still_an_error(self):
        (self.owned.root / 'meta/libraries/org/lwjgl/lwjgl-stb/3.3.3/lwjgl-stb-3.3.3.jar').unlink()
        with self.assertRaisesRegex(ValueError, 'library missing'):
            g.owned_client_references(self.owned.root, '1.21.1')

    def test_hash_size_and_containment_are_enforced(self):
        stb = self.owned.root / 'meta/libraries/org/lwjgl/lwjgl-stb/3.3.3/lwjgl-stb-3.3.3.jar'
        stb.write_bytes(b'x' * stb.stat().st_size)
        with self.assertRaisesRegex(ValueError, 'SHA-1 changed'):
            g.owned_client_references(self.owned.root, '1.21.1')
        self.setUp()
        self.owned.document['libraries'][0]['downloads']['artifact']['size'] += 1
        self.owned.metadata()
        with self.assertRaisesRegex(ValueError, 'size changed'):
            g.owned_client_references(self.owned.root, '1.21.1')
        self.setUp()
        self.owned.document['libraries'][0]['downloads']['artifact']['path'] = '../../outside.jar'
        self.owned.metadata()
        with self.assertRaisesRegex(ValueError, 'Unsafe library path'):
            g.owned_client_references(self.owned.root, '1.21.1')

    def test_client_jar_hash_is_checked(self):
        client = self.owned.root / 'meta/versions/1.21.1/1.21.1.jar'
        client.write_bytes(b'x' * client.stat().st_size)
        with self.assertRaisesRegex(ValueError, 'client jar SHA-1 changed'):
            g.owned_client_references(self.owned.root, '1.21.1')

    def test_verified_inventory_is_used_and_kept_inside_the_tree(self):
        self.owned.inventory()
        rows = g.owned_client_references(self.owned.root, '1.21.1')
        self.assertEqual(len(rows), 1)
        self.owned.inventory([dict(path=str(self.root / 'outside.jar'), sha256='0' * 64)])
        (self.root / 'outside.jar').write_bytes(b'x')
        with self.assertRaisesRegex(ValueError, 'escapes'):
            g.owned_client_references(self.owned.root, '1.21.1')

    def test_wrong_line_is_rejected_before_any_selection(self):
        # A 1.21.1 tree cannot stand in for 26.1.2; it is rejected before compile.
        with self.assertRaises(ValueError):
            g.owned_client_references(self.owned.root, '26.1.2')
        self.owned.version = '26.1.2'
        self.owned.document['id'] = '26.1.2'
        client = b'26.1.2 client fixture'
        self.owned.write('meta/versions/26.1.2/26.1.2.jar', client)
        self.owned.document['downloads']['client'] = dict(size=len(client), sha1=sha1(client))
        self.owned.metadata()
        with self.assertRaisesRegex(ValueError, '需要 LWJGL 3.4.1'):
            g.owned_client_references(self.owned.root, '26.1.2')

    def test_opengl_shim_cli_rejects_a_jar_from_the_other_line(self):
        osmesa = self.root / 'libOSMesa.8.dylib'
        osmesa.write_bytes(b'osmesa')
        references = []
        for module in ('lwjgl', 'lwjgl-glfw', 'lwjgl-stb', 'lwjgl-openal', 'lwjgl-opengl'):
            path = self.root / (module + '-3.4.1.jar')
            path.write_bytes(b'jar')
            references.append(dict(path=str(path), sha256=d.sha256(path)))
        prepared = self.root / 'prepared'
        prepared.mkdir()
        (prepared / 'result.json').write_text(json.dumps(dict(
            status='PREPARED', minecraftVersion='26.1.2', referenceLibraries=references)))
        jar = self.owned.root / 'meta/libraries/org/lwjgl/lwjgl-opengl/3.3.3/lwjgl-opengl-3.3.3.jar'
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            build_lwjgl_opengl_ios.main([
                '--lwjgl-opengl', str(jar), '--osmesa', str(osmesa),
                '--output', str(self.root / 'out'), '--java-home', str(self.root),
                '--prepared', str(prepared), '--minecraft-version', '26.1.2'])
        self.assertFalse((self.root / 'out').exists())


class ShimCommandTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()

    def test_command_passes_the_selected_opengl_jar_and_version(self):
        references = []
        for module in ('lwjgl', 'lwjgl-glfw', 'lwjgl-stb', 'lwjgl-openal', 'lwjgl-opengl'):
            path = self.root / (module + '-3.3.3.jar')
            path.write_bytes(b'jar')
            references.append(dict(path=str(path), sha256=d.sha256(path)))
        prepared = self.root / 'prepared'
        prepared.mkdir()
        (prepared / 'result.json').write_text(json.dumps(dict(
            status='PREPARED', minecraftVersion='1.21.1', javaHome='/java',
            referenceLibraries=references)))
        paths = dict(prepared=prepared, gl41=self.root / 'gl41')
        argv = d.command(self.root, paths, 'lwjglOpengl', self.root / 'out', '1.21.1')
        self.assertIn('lwjgl-opengl-3.3.3.jar', ' '.join(argv))
        self.assertEqual(argv[argv.index('--minecraft-version') + 1], '1.21.1')
        self.assertEqual(argv[argv.index('--prepared') + 1], str(prepared))
        with self.assertRaisesRegex(ValueError, '需要 LWJGL 3.4.1'):
            d.command(self.root, paths, 'lwjglOpengl', self.root / 'out', '26.1.2')


if __name__ == '__main__':
    unittest.main()

