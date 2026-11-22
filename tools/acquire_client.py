"""Acquire owned client inputs from official origins; never authenticate or execute game code."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path, PurePosixPath
import re

from bootstrap_toolchain import sha256
from verified_download import fetch, metadata, GAME_HOSTS, TOOL_HOSTS
from game_versions import DEFAULT_VERSION, get_profile

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = 'https://piston-meta.mojang.com/mc/game/version_manifest_v2.json'
METADATA_BUDGET = 4 * 1024**2


def library_path(name):
    p = PurePosixPath(name)
    if not name or p.is_absolute() or '..' in p.parts or '\\' in name or ':' in name:
        raise ValueError('Unsafe library path')
    return Path(*p.parts)


def engine_url(name, spec=None):
    """Resolve an engine jar URL, preferring the explicit URL pinned in the lock file."""
    if isinstance(spec, dict) and isinstance(spec.get('url'), str):
        return spec['url']
    if name.startswith('asm-'):
        artifact = name.removesuffix('-9.10.1.jar')
        return f'https://repo.maven.apache.org/maven2/org/ow2/asm/{artifact}/9.10.1/{name}'
    if name.startswith('fabric-loader-'):
        version = name.removesuffix('.jar').removeprefix('fabric-loader-')
        return f'https://maven.fabricmc.net/net/fabricmc/fabric-loader/{version}/' + name
    version = name.removesuffix('.jar').removeprefix('sponge-mixin-')
    return f'https://maven.fabricmc.net/net/fabricmc/sponge-mixin/{version}/' + name


def _official_metadata(entry, version):
    """Fetch one version manifest entry and verify it against the official SHA-1."""
    from verified_download import opener, check_url
    check_url(entry['url'], GAME_HOSTS)
    with opener(GAME_HOSTS).open(entry['url'], timeout=45) as response:
        check_url(response.url, GAME_HOSTS)
        raw = response.read(METADATA_BUDGET + 1)
    if len(raw) > METADATA_BUDGET or hashlib.sha1(raw).hexdigest() != entry['sha1']:
        raise ValueError('Official version metadata fingerprint mismatch')
    data = json.loads(raw)
    if data.get('id') != version:
        raise ValueError('Wrong Minecraft version')
    if data.get('javaVersion', {}).get('majorVersion') != get_profile(version).java_version:
        raise ValueError('Version metadata Java requirement differs from the supported profile')
    return raw, data


def _stage_assets(assets, data, version):
    """Stage official assets with the same verifier every other caller uses."""
    from stage_game_assets import verified_assets
    verified_assets(assets, data, assets)


def _mappings(version, data, output, progress=print):
    """Fetch the official 1.21.1 client mappings; the remap itself belongs to the caller."""
    spec = data.get('downloads', {}).get('client_mappings')
    if not spec:
        raise ValueError('Official client mappings are missing for ' + version)
    if not isinstance(spec.get('sha1'), str) or not re.fullmatch(r'[0-9a-f]{40}', spec['sha1']):
        raise ValueError('Invalid client mappings fingerprint')
    if not isinstance(spec.get('size'), int) or isinstance(spec['size'], bool) or not 0 < spec['size'] <= 64 * 1024**2:
        raise ValueError('Invalid client mappings size')
    target = output / 'meta/mappings' / (version + '-client.txt')
    progress('校验官方映射：' + target.name)
    fetch(dict(url=spec['url'], size=spec['size'], sha1=spec['sha1']), target, GAME_HOSTS)
    # Contract shared with tools/prepare_b0.py: exactly path/sha256/sha1/size.
    return dict(path=str(target), sha256=sha256(target), sha1=spec['sha1'],
                size=target.stat().st_size)


def acquire(output, owned, progress=print, version=DEFAULT_VERSION):
    if owned is not True:
        raise ValueError('Confirm ownership before downloading Minecraft')
    profile = get_profile(version)
    version = profile.version
    output = Path(output).resolve()
    if output.is_relative_to(ROOT.resolve()):
        raise ValueError('Owned game content must be stored outside the source repository')
    output.mkdir(parents=True, exist_ok=True)
    versions = metadata(MANIFEST)['versions']
    entry = next((v for v in versions if v['id'] == version and v['type'] == 'release'), None)
    if entry is None:
        raise ValueError('Official manifest has no release for ' + version)
    # Official version manifest supplies SHA-1; obtain bounded bytes and verify before use.
    raw, data = _official_metadata(entry, version)
    meta_path = output / (version + '.json')
    if meta_path.exists() and meta_path.read_bytes() != raw: raise ValueError('Existing version metadata changed')
    if not meta_path.exists(): meta_path.write_bytes(raw)
    jars = []
    def add(spec, path, hosts=GAME_HOSTS):
        progress('校验游戏输入：' + path.name)
        fetch(spec, path, hosts)
        jars.append(dict(path=str(path), sha256=sha256(path), size=path.stat().st_size))
    add(data['downloads']['client'], output / 'meta/versions' / version / (version + '.jar'))
    total = data['downloads']['client']['size']
    for library in data['libraries']:
        rules = library.get('rules', [])
        allowed = not rules
        for rule in rules:
            os_rule = rule.get('os', {})
            if os_rule.get('name', 'osx') == 'osx' and os_rule.get('arch', 'arm64') in ('arm64', 'aarch64') and not rule.get('features'):
                allowed = rule['action'] == 'allow'
        if not allowed: continue
        artifact = library.get('downloads', {}).get('artifact')
        if not artifact: continue
        total += artifact['size']
        if total > 1024**3 or len(jars) > 1000: raise ValueError('Client library budget exceeded')
        add(artifact, output / 'meta/libraries' / library_path(artifact['path']))
    from loader_selection import all_engine_libraries
    for spec in all_engine_libraries(json.loads((ROOT / 'compiler/fabric.lock.json').read_text())):
        add(dict(url=engine_url(spec['file'], spec), size=spec['bytes'], sha256=spec['sha256']),
            output / 'meta/libraries/engine' / spec['file'], TOOL_HOSTS)
    assets = output / 'assets'
    assets.mkdir(exist_ok=True)
    progress('下载并校验官方资源索引与对象')
    _stage_assets(assets, data, version)
    # The mappings descriptor is provenance for the remap step, not a runtime jar.
    mappings = _mappings(version, data, output, progress) if version == '1.21.1' else None
    result = dict(status='OWNED_CLIENT_INPUTS_VERIFIED', gateA0='BLOCKED',
                  scope='download/hash verification only; no desktop game execution or ownership authentication',
                  ownershipConfirmed=True, minecraftVersion=version, javaVersion=profile.java_version,
                  runtimeNamespace=profile.runtime_namespace, requiresRemapping=profile.requires_remapping,
                  jars=jars, versionMetadata=str(meta_path), metadataSHA256=sha256(meta_path),
                  assets=str(assets))
    if mappings is not None:
        result['mappings'] = mappings
    temporary = output / 'client-inputs.pending.json'
    temporary.write_text(json.dumps(result, indent=2) + '\n')
    temporary.replace(output / 'client-inputs.json')
    return result
