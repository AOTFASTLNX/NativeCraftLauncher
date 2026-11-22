"""Native dependency inventory and transactional rebuilds for the desktop workbench.

Only fixed first-party builders are dispatched. Reports are read-only until a new
timestamped build has passed verification; the selected config is committed once.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import struct
from pathlib import Path
import sys

from bootstrap_toolchain import sha256
from mod_file_policy import is_metadata
from storage_config import cache_path
from capture import ROOT
from game_versions import DEFAULT_VERSION, get_profile


def _prepared_version(prepared) -> str:
    """The Minecraft line a prepared manifest declares; legacy trees are 26.1.2."""
    value = prepared.get('minecraftVersion')
    if value is None:
        return DEFAULT_VERSION
    if not isinstance(value, str) or not value.strip():
        raise ValueError('准备记录中的 Minecraft 版本无效')
    return get_profile(value).version


def client_jar(paths, version=DEFAULT_VERSION, root=None) -> Path:
    """The hash-verified client for the selected Minecraft line.

    The jar must be listed under the selected version's own name in the
    prepared reference libraries. 1.21.1 ships obfuscated classes, so its
    client is remapped into the runtime namespace first; every client-derived
    build (GLFW exports, probes) must see that remapped jar.
    """
    get_profile(version)
    manifest = paths.get('prepared')
    if manifest is None or not (Path(manifest) / 'result.json').is_file():
        if not get_profile(version).requires_remapping:
            return _owned_client_jar(paths, version)
        return _owned_mapped_client(paths, version)
    prepared = read(Path(manifest) / 'result.json')
    if prepared.get('status') != 'PREPARED':
        raise ValueError('客户端准备记录未通过：' + str(paths['prepared']))
    declared = _prepared_version(prepared)
    if declared != version:
        raise ValueError('客户端准备记录属于 Minecraft ' + declared +
                         '，当前选择 ' + version)
    entries = prepared.get('referenceLibraries')
    if not isinstance(entries, list):
        raise ValueError('客户端准备记录缺少 referenceLibraries')
    name = version + '.jar'
    client = next((Path(str(r['path'])) for r in entries
                   if isinstance(r, dict) and Path(str(r.get('path', ''))).name == name), None)
    if client is None or not client.is_file():
        raise ValueError('构建配置缺少已核验的 ' + version + ' 客户端 JAR')
    entry = next(r for r in entries if Path(str(r.get('path', ''))) == client)
    if sha256(client) != entry.get('sha256'):
        raise ValueError('客户端 JAR 哈希与准备记录不一致：' + str(client))
    if not get_profile(version).requires_remapping:
        return client
    # Imported lazily: a packaged source tree may predate the remap helper.
    from prepare_game_version import prepare_client
    relative = ('native-dependencies/game-version/' + version + '-' +
                sha256(client)[:16])
    output = cache_path(relative, root=root or ROOT)
    prepared_client = prepare_client(paths['prepared'], client, output)
    remapped = Path(str(prepared_client.get('client', '')))
    expected = prepared_client.get('clientSHA256')
    if not remapped.is_file() or not isinstance(expected, str) or sha256(remapped) != expected:
        raise ValueError('1.21.1 客户端重映射产物缺失或哈希不一致：' + str(remapped))
    return remapped


def _owned_client_jar(paths, version=DEFAULT_VERSION) -> Path:
    """The verified client jar inside an owned-client tree, no remap needed."""
    owned = paths.get('ownedClient')
    if owned is None:
        raise ValueError('构建配置缺少 prepared 或 ownedClient 输入')
    owned = Path(owned)
    client = owned / 'meta' / 'versions' / version / (version + '.jar')
    if client.is_symlink() or not client.is_file():
        raise ValueError('构建配置缺少已核验的 ' + version + ' 客户端 JAR')
    metadata = owned / (version + '.json')
    if not metadata.is_file():
        metadata = owned / 'versions' / version / (version + '.json')
    descriptor = {}
    if metadata.is_file():
        document = json.loads(metadata.read_text())
        if document.get('id') != version:
            raise ValueError('客户端元数据声明的 Minecraft 版本不是 ' + version)
        descriptor = (document.get('downloads') or {}).get('client') or {}
    if isinstance(descriptor.get('size'), int) and client.stat().st_size != descriptor['size']:
        raise ValueError('客户端 JAR 大小与官方元数据不一致：' + str(client))
    if isinstance(descriptor.get('sha1'), str) and _sha1(client) != descriptor['sha1']:
        raise ValueError('客户端 JAR SHA-1 与官方元数据不一致：' + str(client))
    return client


def _sha1(path: Path) -> str:
    digest = hashlib.sha1()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _owned_mapped_client(paths, version=DEFAULT_VERSION) -> Path:
    """A remapped client recorded by the remap tool, chained to the owned client.

    Bounded helper path for a line whose full prepared manifest does not exist
    yet. The remap output must carry the tool's own result (result.json, or the
    captured stdout.json/stdout.log), its client hash must match, and its
    recorded source SHA-1 must be the owned client jar in this same tree.
    """
    profile = get_profile(version)
    if not profile.requires_remapping:
        raise ValueError('Minecraft ' + version + ' does not need a remapped client')
    directory = paths.get('mappedClient')
    if directory is None:
        raise ValueError('构建配置缺少 mappedClient：' + version + ' 需要重映射后的客户端 JAR')
    directory = Path(directory)
    if directory.is_file():
        directory = directory.parent
    report = None
    # A captured remap run records its result beside the output directory
    # (capture writes stdout.log in the run folder, not in mapped/), so check
    # the directory and then its parent.
    for folder in (directory, directory.parent):
        for name in ('result.json', 'stdout.json'):
            candidate = folder / name
            if candidate.is_file():
                report = read(candidate)
                break
        if report is None and (folder / 'stdout.log').is_file():
            captured = (folder / 'stdout.log').read_text().strip()
            if captured:
                try:
                    report = json.loads(captured)
                except ValueError:
                    report = None
        if isinstance(report, dict):
            break
    if not isinstance(report, dict):
        raise ValueError('重映射目录缺少 remap 结果记录：' + str(directory))
    client = Path(str(report.get('client', directory / 'client.jar')))
    recorded = report.get('clientSHA256')
    if not client.is_file() or not isinstance(recorded, str) or sha256(client) != recorded:
        raise ValueError('重映射客户端缺失或哈希与 remap 记录不一致：' + str(client))
    owned = paths.get('ownedClient')
    if owned is None:
        raise ValueError('构建配置缺少 ownedClient，无法核对重映射来源')
    metadata = Path(owned) / (version + '.json')
    if not metadata.is_file():
        metadata = Path(owned) / 'versions' / version / (version + '.json')
    official = json.loads(metadata.read_text()).get('downloads', {}).get('client', {})
    source = Path(owned) / 'meta' / 'versions' / version / (version + '.jar')
    expected = report.get('sourceClientSHA1')
    if not source.is_file() or not isinstance(expected, str) or _sha1(source) != expected:
        raise ValueError('重映射来源与 owned client 不一致：' + str(source))
    if official.get('sha1') and official['sha1'] != expected:
        raise ValueError('重映射来源与官方 client SHA-1 不一致：' + str(source))
    return client

# Topological order: the OpenGL shim reads OSMesa's exports while generating JNI.
SPECS = (
    ('glfw', 'GLFW-lite', 'build_glfw_lite', 'NATIVE_SUBSET_BUILT', (), ('libglfw.dylib',)),
    ('lwjgl', 'LWJGL-lite', 'build_lwjgl_lite', 'STATIC_JNI_BRIDGE_BUILT', ('glfw',),
     ('liblwjgl.dylib', 'lwjgl-ios.jar', 'lwjgl-glfw-ios.jar', 'lwjgl-stb-ios.jar', 'lwjgl-openal-ios.jar')),
    ('gl41', 'OSMesa / MoltenVK', 'fetch_gl41_runtime', 'GL41_RUNTIME_FETCHED', (), ('libOSMesa.8.dylib', 'libMoltenVK.dylib')),
    ('lwjglOpengl', 'LWJGL OpenGL shim', 'build_lwjgl_opengl_ios', 'LWJGL_OPENGL_SHIM_BUILT', ('gl41',), ('liblwjgl_opengl.dylib',)),
    ('metal', 'Metal 后端', 'build_metal_backend', 'METAL_BOOTSTRAP_BUILT', ('glfw', 'lwjgl'), ('libfaot_metal.dylib', 'metal-backend.jar', 'fabricaot-present.metallib')),
    ('openal', 'OpenAL Soft', 'build_openal_soft', 'OPENAL_SOFT_BUILT', (), ('libopenal.dylib',)),
    ('shaders', '着色器', 'translate_shaders', 'VANILLA_SHADERS_PRECOMPILED', (), ('bundle/index.json',)),
)
BY_KEY = {s[0]: s for s in SPECS}
REQUIRED = ('glfw', 'lwjgl', 'gl41', 'lwjglOpengl', 'openal')
SOURCE_GROUPS = {
    'glfw': ('ios/glfw-lite', 'tools/glfw_runtime_contract.py', 'tools/inventory_client_native_calls.py',
             'tools/prepare_game_version.py'),
    'lwjgl': ('compiler/lwjgl-lite', 'ios/lwjgl-lite', 'compiler/stb.lock.json', 'tools/lwjgl_codegen.py',
              'tools/fetch_stb.py', 'interop/graphics/LwjglProbe.java', 'tools/prepare_game_version.py',
              'compiler/game-versions.lock.json'),
    'gl41': ('tools/verified_download.py', 'tools/build_gl41_mesa.py', 'compiler/gl41/patches'),
    'lwjglOpengl': (),
    'metal': ('compiler/metal-backend', 'ios/metal-bridge', 'interop/graphics/MetalProbe.java',
              'interop/graphics/MetalResourceProbe.java', 'interop/graphics/NativeImageProbe.java',
              'interop/graphics/MetalDrawProbe.java', 'interop/graphics/TextureSpecProbe.java',
              'interop/graphics/texture_transfer_test.c'),
    'openal': ('compiler/openal.lock.json', 'compiler/graphics-toolchain.lock.json', 'tools/prepare_shader_toolchain.py'),
    'shaders': ('compiler/shaders', 'compiler/graphics-toolchain.lock.json', 'tools/collect_shader_catalog.py'),
}
MANIFEST = 'dependency-manifest.json'


def read(path):
    value = json.loads(Path(path).read_text())
    if not isinstance(value, dict):
        raise ValueError('JSON 记录必须是对象：' + str(path))
    return value


def _version_overrides(values, version):
    profiles = values.get('versionProfiles')
    if profiles is None:
        return {}
    if not isinstance(profiles, dict):
        raise ValueError('依赖配置的 versionProfiles 必须是对象')
    override = profiles.get(version)
    if override is None:
        return {}
    if not isinstance(override, dict):
        raise ValueError('依赖配置 versionProfiles[' + version + '] 必须是对象')
    return override


def config_paths(config, version=DEFAULT_VERSION):
    """Resolve dependency paths for one selected Minecraft line.

    A config may carry a versionProfiles object with per-version path
    overrides. The selected version is merged over the root keys so a 1.21.1
    build can point at its own LWJGL/GL41 outputs without rewriting the
    26.1.2 inputs.
    """
    get_profile(version)
    document = read(config)
    values = {k: v for k, v in document.items() if k != 'versionProfiles'}
    values.update(_version_overrides(document, version))
    if any(not isinstance(v, str) or not v.strip() or any(c in v for c in '\0\n\r') for v in values.values()):
        raise ValueError('依赖配置包含无效路径')
    return {k: (config.parent / Path(v).expanduser()).resolve() for k, v in values.items()}


def sources(root, key):
    # game_versions.py selects the Minecraft/LWJGL line, so it is part of the
    # fingerprint for every builder that asks for one.
    paths = [root / ('tools/' + BY_KEY[key][2] + '.py'), root / 'tools/graphics_common.py',
             root / 'tools/game_versions.py']
    for name in SOURCE_GROUPS[key]:
        path = root / name
        paths.extend(path.rglob('*') if path.is_dir() else [path])
    return {p.relative_to(root).as_posix(): sha256(p) for p in sorted(set(paths))
            if p.is_file() and not is_metadata(p) and '__pycache__' not in p.parts
            and p.suffix not in ('.pyc', '.class', '.jar')}


def artifacts(directory, key, report):
    """Require every shipped artifact's recorded hash, including overlays and shaders."""
    hashes = {}
    if key == 'gl41':
        from fetch_gl41_runtime import FILES, APACHE, validate_runtime
        validate_runtime(directory, report)
        pinned = {r['name']: r['sha256'] for r in FILES if r['name'] != "libOSMesa.8.dylib"}
        pinned['LICENSE-Apache-2.0.txt'] = APACHE[1]
        hashes.update(report.get('filesSHA256', {}))
        if any(hashes.get(name) != digest for name, digest in pinned.items()):
            raise ValueError('OSMesa / MoltenVK 记录偏离固定版本哈希')
    elif key == 'shaders':
        hashes['bundle/index.json'] = report.get('indexSHA256')
        for p in report.get('programs', []):
            hashes['bundle/' + p['key'] + '.metallib'] = p.get('metallibSHA256')
        if not report.get('programs'):
            raise ValueError('着色器构建记录缺少程序清单')
    else:
        hashes[BY_KEY[key][5][0]] = report.get('binarySHA256')
        if key == 'lwjgl':
            if not isinstance(report.get('overlaysSHA256'), dict):
                raise ValueError('LWJGL 产物记录缺少有效的 JAR 哈希清单')
            for name in ('lwjgl-ios.jar', 'lwjgl-glfw-ios.jar', 'lwjgl-stb-ios.jar', 'lwjgl-openal-ios.jar'):
                hashes[name] = report.get('overlaysSHA256', {}).get(str(directory / name))
        if key == 'metal':
            hashes.update({'metal-backend.jar': report.get('jarSHA256'),
                           'fabricaot-present.metallib': report.get('metallibSHA256')})
    for name, expected in hashes.items():
        path = (directory / name).resolve()
        if not path.is_relative_to(directory.resolve()):
            raise ValueError('产物记录越过依赖目录：' + name)
        if not path.is_file():
            raise FileNotFoundError('依赖产物缺失：' + name)
        if not expected or sha256(path) != expected:
            raise ValueError('产物哈希校验失败：' + name)
        if path.suffix == '.dylib':
            from fetch_gl41_runtime import macho_platform
            macho_platform(path)
    if key == 'shaders':
        from build_c0_harness import validate_shader_bundle
        validate_shader_bundle(directory / 'bundle', simulator=False)
    return hashes


def dependency_hashes(paths, key):
    return {dep: {name: sha256(paths[dep] / name) for name in BY_KEY[dep][5]}
            for dep in BY_KEY[key][4]}


def _prepared_references(paths, version=DEFAULT_VERSION):
    """Reference rows a bridge report is bound to.

    The prepared manifest is authoritative when it exists; otherwise the same
    verified owned-client tree that supplied the bounded build inputs is used.
    """
    prepared = paths.get('prepared')
    if prepared is not None and (Path(prepared) / 'result.json').is_file():
        try:
            return read(Path(prepared) / 'result.json').get('referenceLibraries')
        except (OSError, ValueError):
            return None
    owned = paths.get('ownedClient')
    if owned is None:
        return None
    try:
        from graphics_common import owned_client_references
        return owned_client_references(owned, version)
    except (OSError, ValueError):
        return None


def _check_bridge_coupling(paths, key, report, version):
    """Bind a bridge report to the selected Minecraft line.

    Reports written before multi-version support have neither version field.
    They may only be inferred as the default 26.1.2 line, and only when their
    recorded inputs are exactly the pinned LWJGL 3.4.1 jars. A versioned
    report must name the selected line and its exact LWJGL release.
    """
    references = _prepared_references(paths, version)
    if references is None:
        return None
    from graphics_common import bridge_selection, lwjgl_jar, opengl_shim_selection
    # A prepared record with no LWJGL entries at all is not a bridge input
    # (legacy fixtures and non-graphics builds); a partial set is an error.
    if not any(lwjgl_jar(r.get('path', '')) for r in references if isinstance(r, dict)):
        return None
    if key == 'lwjgl':
        selection = bridge_selection(paths[key], references, version)
    elif key == 'lwjglOpengl':
        jar = opengl_shim_selection(paths[key], references, version)
        return {"minecraftVersion": version, "lwjglVersion": report.get("lwjglVersion"),
                "legacy": report.get("minecraftVersion") is None, "jar": jar}
    else:
        return None
    return selection


def inspect(root, paths, key, version=DEFAULT_VERSION):
    get_profile(version)
    spec = BY_KEY[key]
    directory = paths.get(key)
    row = dict(key=key, name=spec[1], path=str(directory) if directory else '',
               builtAt=None, target='未知', state='missing', changedFiles=[], reasons=[],
               dependsOn=list(spec[4]), required=key in REQUIRED,
               mode='fetch' if key == 'gl41' else 'build')
    if not directory or not directory.is_dir() or not (directory / 'result.json').is_file():
        row['reasons'] = ['未配置路径或缺少构建记录']
        return row
    try:
        report = read(directory / 'result.json')
        row['builtAt'] = datetime.fromtimestamp((directory / 'result.json').stat().st_mtime, timezone.utc).isoformat()
        row['timeBasis'] = 'report-mtime'
        row['target'] = report.get('target') or ('arm64-apple-ios17.0' if key == 'gl41' or report.get('sdk') == 'iphoneos' else '未知')
        if row['target'] != 'arm64-apple-ios17.0':
            raise ValueError('目标架构不匹配：需要 iPhoneOS arm64，当前为 ' + row['target'])
        if report.get('status') != spec[3]:
            raise ValueError(report.get('error') or '构建记录未成功：' + str(report.get('status')))
        if key in ('lwjgl', 'lwjglOpengl'):
            selection = _check_bridge_coupling(paths, key, report, version)
            if selection is not None:
                row['minecraftVersion'] = selection['minecraftVersion']
                if selection.get('lwjglVersion'):
                    row['lwjglVersion'] = selection['lwjglVersion']
        verified = artifacts(directory, key, report)
        manifest = read(directory / MANIFEST) if (directory / MANIFEST).exists() else None
        if manifest:
            if manifest.get('key') != key or manifest.get('artifacts') != verified:
                raise ValueError('依赖清单与构建记录不一致')
            recorded = manifest['sources']
            if not isinstance(recorded, dict):
                raise ValueError('依赖清单中的源码指纹格式无效')
            current = sources(root, key)
            row.update(builtAt=manifest['builtAt'], timeBasis='build', target=manifest['target'])
            row['changedFiles'] = sorted(n for n in recorded.keys() | current.keys() if recorded.get(n) != current.get(n))
        else:
            recorded = report.get('sourcesSHA256', {})
            if not isinstance(recorded, dict):
                raise ValueError('历史构建记录中的源码指纹格式无效')
            # Legacy reports did not all fingerprint the builder itself. Compare
            # the sources actually recorded, and directory membership where known.
            if not recorded and key != 'gl41':
                raise ValueError('历史构建缺少源码指纹，请重建以建立清单')
            for name, expected in recorded.items():
                path = (root / name).resolve()
                if not path.is_relative_to(root.resolve()):
                    raise ValueError('源码记录越过项目目录：' + name)
                if not path.is_file() or sha256(path) != expected:
                    row['changedFiles'].append(name)
            for group in SOURCE_GROUPS[key]:
                if any(n.startswith(group + '/') for n in recorded):
                    for name in sources(root, key):
                        if name.startswith(group + '/') and name not in recorded:
                            row['changedFiles'].append(name)
            row['changedFiles'] = sorted(set(row['changedFiles']))
        if row['target'] != 'arm64-apple-ios17.0':
            raise ValueError('目标架构不匹配：需要 iPhoneOS arm64，当前为 ' + row['target'])
        row['state'] = 'stale' if row['changedFiles'] else 'current'
        if row['changedFiles']:
            row['reasons'].append('源码已变更')
        try:
            deps = dependency_hashes(paths, key)
            legacy_hash = {'lwjgl': ('glfwLibrarySHA256', 'glfw', 'libglfw.dylib'),
                           'lwjglOpengl': ('osmesaSHA256', 'gl41', 'libOSMesa.8.dylib'),
                           'metal': ('glfwSHA256', 'glfw', 'libglfw.dylib')}.get(key)
            if manifest and manifest.get('dependencies') != deps:
                row['state'] = 'stale'
                row['reasons'].append('依赖库哈希已变更，待重建')
            if legacy_hash:
                field, dep, filename = legacy_hash
                if report.get(field) != deps[dep][filename]:
                    row['state'] = 'stale'
                    row['reasons'].append(BY_KEY[dep][1] + ' 哈希已变更，待重建')
            # GLFW exports are derived from the client's constant pools, so a
            # different client can require different entry points.
            if key == 'glfw':
                try:
                    current_client = sha256(client_jar(paths, version, root))
                except (OSError, ValueError, KeyError) as exc:
                    row['state'] = 'stale'
                    row['reasons'].append('客户端输入不可用，待重建：' + str(exc))
                else:
                    if report.get('clientSHA256') != current_client:
                        row['state'] = 'stale'
                        row['reasons'].append('客户端已变更，待重建')
        except (KeyError, OSError):
            row['state'] = 'stale'
            row['reasons'].append('前置依赖缺失，待重建')
    except FileNotFoundError as exc:
        row.update(state='missing', reasons=[str(exc)])
    except (OSError, ValueError, KeyError, TypeError, struct.error) as exc:
        row.update(state='invalid', reasons=[str(exc)])
    return row


def inventory(root, config, version=DEFAULT_VERSION):
    get_profile(version)
    paths = config_paths(config, version)
    rows = [inspect(root, paths, s[0], version) for s in SPECS]
    by_key = {r['key']: r for r in rows}
    for row in rows:
        if row['state'] == 'current':
            blocked = [BY_KEY[k][1] for k in row['dependsOn'] if by_key[k]['state'] != 'current']
            if blocked:
                row.update(state='stale', reasons=['前置依赖需要重建：' + '、'.join(blocked)])
    return dict(ready=all(r['state'] == 'current' for r in rows if r['required']), libraries=rows,
                minecraftVersion=version, checkedAt=datetime.now(timezone.utc).isoformat())


def preflight(root, config, version=DEFAULT_VERSION):
    result = inventory(root, config, version)
    if not result['ready']:
        bad = [r for r in result['libraries'] if r['required'] and r['state'] != 'current']
        raise ValueError('原生依赖需要重建，请到「环境与资源」一键重建：\n' + '\n'.join(
            r['name'] + '：' + '；'.join(r['reasons'] + r['changedFiles']) for r in bad))
    return result


def plan(keys):
    if not isinstance(keys, list) or not keys or any(not isinstance(k, str) or k not in BY_KEY for k in keys):
        raise ValueError('不支持的依赖库动作')
    if len(set(keys)) != len(keys):
        raise ValueError('依赖库选择重复')
    return [s[0] for s in SPECS if s[0] in keys]


def prerequisites(root, paths, key, version=DEFAULT_VERSION):
    for dep in BY_KEY[key][4]:
        row = inspect(root, paths, dep, version)
        if row['state'] != 'current':
            raise ValueError('请先重建 ' + row['name'] + '：' + '；'.join(row['reasons'] + row['changedFiles']))


def command(root, paths, key, output, version=DEFAULT_VERSION):
    get_profile(version)
    prepared = paths.get('prepared')
    prepared_ready = prepared is not None and (Path(prepared) / 'result.json').is_file()
    argv = [sys.executable, '-B', root / ('tools/' + BY_KEY[key][2] + '.py'), '--output', output]
    if key != 'gl41':
        argv += ['--sdk', 'iphoneos']
    # A packaged app omits artifacts/, so client-derived inputs come from the
    # selected build configuration instead of the source tree.
    if key == 'glfw':
        argv += ['--client', client_jar(paths, version, root)]
    if key in ('lwjgl', 'metal'):
        if prepared_ready:
            argv += ['--prepared', prepared]
        elif key == 'metal':
            raise ValueError('Metal 后端需要 prepared 输入')
        else:
            # Bounded scope: generate the bridge straight from the verified
            # owned-client jars when the full B0 manifest does not exist yet.
            if paths.get('ownedClient') is None:
                raise ValueError('LWJGL 桥需要 prepared 或 ownedClient 输入')
            argv += ['--owned-client', paths['ownedClient']]
        argv += ['--glfw-library', paths['glfw'] / 'libglfw.dylib']
    if key == 'lwjgl':
        argv += ['--minecraft-version', version]
        if paths.get('mappedClient') is not None and get_profile(version).requires_remapping:
            argv += ['--mapped-client', client_jar(paths, version, root)]
    if key == 'metal':
        argv += ['--graphics-bridge', paths['lwjgl']]
    if key == 'lwjglOpengl':
        from graphics_common import select_lwjgl_opengl
        if prepared_ready:
            manifest = read(Path(prepared) / 'result.json')
            references = manifest.get('referenceLibraries')
            java_home = manifest['javaHome']
            inputs = ['--prepared', prepared]
        else:
            from graphics_common import owned_client_references
            if paths.get('ownedClient') is None:
                raise ValueError('LWJGL OpenGL 桥需要 prepared 或 ownedClient 输入')
            references = owned_client_references(paths['ownedClient'], version)
            java_home = cache_path('toolchains/graalvm-25.0.2/'
                                   'graalvm-community-openjdk-25.0.2+10.1/Contents/Home', root=root)
            inputs = ['--owned-client', paths['ownedClient']]
        entry = select_lwjgl_opengl(references, version)
        jar = Path(entry['path'])
        if sha256(jar) != entry['sha256']:
            raise ValueError('LWJGL OpenGL JAR 哈希校验失败')
        argv += ['--java-home', java_home, '--lwjgl-opengl', jar,
                 *inputs, '--minecraft-version', version,
                 '--osmesa', paths['gl41'] / 'libOSMesa.8.dylib']
    if key == 'shaders':
        # Optional Metal route: the catalog and host tools are separate builds,
        # not part of a packaged source tree.
        catalog = root / 'artifacts/graphics/shader-catalog-03'
        toolchain = root / 'artifacts/graphics/shader-toolchain-03'
        if not (catalog / 'result.json').is_file() or not (toolchain / 'result.json').is_file():
            raise ValueError('着色器重建需要先准备 shader-catalog-03 与 shader-toolchain-03；'
                             '分发应用不含 artifacts/，请从源码目录生成，或继续使用已有着色器产物')
        argv += ['--catalog', catalog, '--toolchain', toolchain]
    return list(map(str, argv))


def build_one(root, paths, key, destination, execute, write_json, version=DEFAULT_VERSION):
    get_profile(version)
    prerequisites(root, paths, key, version)
    before = sources(root, key)
    before_deps = dependency_hashes(paths, key)
    argv = command(root, paths, key, destination, version)
    execute(argv, key)
    report = read(destination / 'result.json')
    if report.get('status') != BY_KEY[key][3]:
        raise ValueError(report.get('error') or key + ' 构建未通过')
    hashes = artifacts(destination, key, report)
    if before != sources(root, key) or before_deps != dependency_hashes(paths, key):
        raise ValueError('构建过程中源码或依赖库发生变化：' + key)
    manifest = dict(schema=1, key=key, builtAt=datetime.now(timezone.utc).isoformat(),
                    target='arm64-apple-ios17.0', sources=before, artifacts=hashes, dependencies=before_deps)
    write_json(destination / MANIFEST, manifest)
    row = inspect(root, {**paths, key: destination}, key, version)
    if row['state'] != 'current':
        raise ValueError('新产物验证失败：' + '；'.join(row['reasons'] + row['changedFiles']))
    from graphics_common import register_tree
    register_tree(destination / MANIFEST, argv, recursive=False)


def rebuild(root, config, keys, output, execute, cancelled, update, write_json,
            version=DEFAULT_VERSION):
    """Build in dependency order; atomically switch paths only after all succeed."""
    get_profile(version)
    keys = plan(keys)
    original_hash = sha256(config)
    original = read(config)
    paths = config_paths(config, version)
    # Validate every selected input before compiling anything. A packaged app
    # omits artifacts/, so an optional Metal shader rebuild must fail now rather
    # than after the required libraries have already been compiled.
    problems = []
    for key in keys:
        try:
            command(root, paths, key, output / key, version)
        except (OSError, ValueError, KeyError) as exc:
            problems.append(BY_KEY[key][1] + '：' + str(exc))
    if problems:
        raise ValueError('依赖重建所需输入不完整：\n' + '\n'.join(problems))
    output.mkdir(parents=True, exist_ok=False)
    outputs = {}
    for index, key in enumerate(keys):
        if cancelled():
            raise InterruptedError('依赖库重建已取消')
        destination = output / key
        update(stage='dependency-' + key, dependencyProgress=dict(completed=index, total=len(keys), key=key))
        def execute_step(argv, name):
            execute(argv, name)
            if cancelled():
                raise InterruptedError('依赖库重建已取消')
        build_one(root, paths, key, destination, execute_step, write_json, version)
        paths[key] = destination
        outputs[key] = str(destination)
    # Verify all selected inputs again immediately before committing the config.
    for key in keys:
        row = inspect(root, paths, key, version)
        if row['state'] != 'current':
            raise ValueError('提交配置前依赖已变更：' + key)
    if cancelled():
        raise InterruptedError('依赖库重建已取消')
    if sha256(config) != original_hash:
        raise ValueError('构建配置已被修改，保留新产物；请刷新配置后重试')
    # versionProfiles is preserved untouched so other lines keep their inputs.
    profiles = original.get('versionProfiles')
    if version == DEFAULT_VERSION:
        document = {**original, **outputs}
        # A declared default profile would otherwise shadow the new root keys.
        if isinstance(profiles, dict) and DEFAULT_VERSION in profiles:
            document['versionProfiles'] = {
                **profiles, DEFAULT_VERSION: {**profiles[DEFAULT_VERSION], **outputs}}
    else:
        if not isinstance(profiles, dict):
            raise ValueError('构建配置缺少 versionProfiles，无法写回 Minecraft ' + version)
        current = profiles.get(version)
        if not isinstance(current, dict):
            raise ValueError('构建配置缺少 versionProfiles[' + version + ']')
        document = {**original, 'versionProfiles': {**profiles, version: {**current, **outputs}}}
    write_json(config, document)
    update(configFields=read(config), dependencyProgress=dict(completed=len(keys), total=len(keys)))
    return outputs
