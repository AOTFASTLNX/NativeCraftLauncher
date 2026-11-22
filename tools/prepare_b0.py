#!/usr/bin/env python3
"""Compile the audited first-party B0 fixture with annotation processing disabled; never run it."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import zipfile
from pathlib import Path

from bootstrap_toolchain import sha256
from capture import ROOT, capture
from mod_file_policy import content_paths, is_metadata
from loader_selection import engine_libraries, verified

from game_versions import DEFAULT_VERSION, get_profile


def jar_bytes(files: dict[str, bytes]) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(files.items()):
            if name.startswith("/") or ".." in Path(name).parts or "\\" in name:
                raise ValueError("unsafe JAR entry")
            if is_metadata(name):
                continue
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data)
    return stream.getvalue()


def loader_engines(lock: dict, loader: str | None = None) -> list[dict]:
    """Engine jars for one verified Loader, or the legacy single-version list."""
    if loader is not None:
        return engine_libraries(lock, loader)
    return lock["engineLibraries"]


def verified_loaders(lock: dict) -> list[str]:
    return verified(lock)


def select_libraries(inventory: dict, lock: dict, loader: str | None = None) -> list[dict]:
    selected = []
    for expected in loader_engines(lock, loader):
        matches = [entry for entry in inventory["jars"] if Path(entry["path"]).name == expected["file"]]
        if len(matches) != 1:
            raise ValueError("ambiguous/missing pinned engine library: " + expected["file"])
        entry = matches[0]
        path = Path(entry["path"])
        if path.is_symlink() or path.stat().st_size != expected["bytes"] or sha256(path) != expected["sha256"]:
            raise ValueError("engine hash mismatch: " + str(path))
        selected.append(entry)
    return selected


def preparation_inputs(client_dir=None, baseline=None):
    baseline = Path(baseline or os.environ.get('MCFAO_A0') or ROOT / 'artifacts/evidence/a0-desktop-reference')
    if client_dir:
        client_dir = Path(client_dir).resolve(strict=True)
        inventory = json.loads((client_dir / 'client-inputs.json').read_text())
        if inventory.get('status') != 'OWNED_CLIENT_INPUTS_VERIFIED':
            raise ValueError('Expected a verified external client directory')
        version = inventory.get('minecraftVersion', DEFAULT_VERSION)
        get_profile(version)  # unsupported versions fail before any compile
        shared_meta = client_dir / 'meta'
        for row in inventory['jars']:
            path = Path(row['path'])
            if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(shared_meta):
                raise ValueError('External client library changed or escaped its directory')
            if sha256(path) != row['sha256']:
                raise ValueError('External client library changed or escaped its directory')
        verify_client_version(shared_meta, version)
        # Inventories written before versioning carry neither descriptor; they are
        # treated as legacy 26.1.2 records and keep their previous checks.
        if 'minecraftVersion' in inventory or 'mappings' in inventory:
            verify_client_provenance(inventory, version)
        return inventory, shared_meta
    inventory = json.loads((baseline / 'dependencies.json').read_text())
    result = json.loads((baseline / 'result.json').read_text())
    if result['gateA0'] != 'PASS': raise ValueError('A0 is not passed')
    version = inventory.get('minecraftVersion', result.get('minecraft', DEFAULT_VERSION))
    get_profile(version)
    if result.get('minecraft', version) != version:
        raise ValueError('A0 evidence and client inventory disagree on the Minecraft version')
    return inventory, Path(result['profile']).parent.parent / 'meta'


def verify_client_version(shared_meta: Path, version: str) -> Path:
    """Require exactly one client jar matching the declared version.

    exFAT volumes add AppleDouble companions next to real files; those are
    metadata, never client inputs.
    """
    versions_dir = Path(shared_meta) / 'versions'
    jars = sorted(path for path in versions_dir.glob('*/*.jar')
                  if path.is_file() and not is_metadata(path.name))
    if len(jars) != 1:
        raise ValueError('Client directory must hold exactly one Minecraft version jar')
    client = jars[0]
    if client.parent.name != version or client.name != version + '.jar':
        raise ValueError('Client inputs do not match the declared Minecraft version: ' + version)
    return client


def verify_client_provenance(inventory: dict, version: str) -> dict:
    """Check the metadata and (for remapped versions) official mappings descriptors."""
    metadata = inventory.get('versionMetadata')
    if not isinstance(metadata, str):
        raise ValueError('Client inventory lacks its version metadata path')
    meta_path = Path(metadata)
    if meta_path.is_symlink() or not meta_path.is_file() or meta_path.name != version + '.json':
        raise ValueError('Client version metadata does not match the declared version: ' + version)
    recorded = inventory.get('metadataSHA256')
    if recorded is not None and sha256(meta_path) != recorded:
        raise ValueError('Client version metadata changed after acquisition')
    if json.loads(meta_path.read_text()).get('id') != version:
        raise ValueError('Client version metadata declares another Minecraft version')
    mappings = inventory.get('mappings')
    if get_profile(version).requires_remapping:
        if not isinstance(mappings, dict):
            raise ValueError('Client inventory lacks the official client mappings for ' + version)
        path = Path(str(mappings.get('path')))
        if path.is_symlink() or not path.is_file():
            raise ValueError('Client mappings file is missing: ' + str(path))
        if path.stat().st_size != mappings.get('size'):
            raise ValueError('Client mappings size changed: ' + str(path))
        if sha256(path) != mappings.get('sha256'):
            raise ValueError('Client mappings sha256 changed: ' + str(path))
        with path.open('rb') as stream:
            if hashlib.sha1(stream.read()).hexdigest() != mappings.get('sha1'):
                raise ValueError('Client mappings sha1 changed: ' + str(path))
    elif mappings is not None:
        raise ValueError('Official mappings are only expected for remapped versions')
    return mappings


def client_descriptors(inventory: dict, shared_meta: Path, version: str) -> dict:
    """Locate the version metadata, optional client-inputs record, and mappings.

    Legacy A0 baselines predate the versioned layout; their metadata is simply
    absent and stays absent in the manifest.
    """
    root = Path(shared_meta).parent
    declared = inventory.get('versionMetadata')
    candidates = [Path(declared)] if declared else [
        Path(shared_meta) / 'versions' / version / (version + '.json'),
        root / (version + '.json'),
    ]
    metadata = next((path for path in candidates if path.is_file() and not path.is_symlink()), None)
    if metadata is None:
        if declared:
            raise ValueError('Client version metadata is missing: ' + str(declared))
        metadata = None
    elif json.loads(metadata.read_text()).get('id') != version:
        raise ValueError('Client version metadata declares another Minecraft version')
    recorded = inventory.get('metadataSHA256')
    if metadata is not None and recorded is not None and sha256(metadata) != recorded:
        raise ValueError('Client version metadata changed after acquisition')
    record = root / 'client-inputs.json'
    return dict(versionMetadata=metadata, clientInputs=record if record.is_file() else None,
                mappings=inventory.get('mappings'))


def version_manifest_fields(inventory: dict, shared_meta: Path, version: str) -> dict:
    """Manifest fields that let the remap step find the client, metadata, and mappings."""
    descriptors = client_descriptors(inventory, shared_meta, version)
    fields = {"minecraftVersion": version,
              "javaVersion": get_profile(version).java_version,
              "clientDirectory": str(Path(shared_meta).parent)}
    if descriptors["versionMetadata"] is not None:
        fields["versionMetadata"] = str(descriptors["versionMetadata"])
        fields["metadataSHA256"] = sha256(descriptors["versionMetadata"])
    if descriptors["clientInputs"] is not None:
        fields["clientInputs"] = str(descriptors["clientInputs"])
        fields["clientInputsSHA256"] = sha256(descriptors["clientInputs"])
    if descriptors["mappings"] is not None:
        # Descriptor only: the remap itself belongs to the mappings owner.
        fields["mappings"] = dict(descriptors["mappings"])
    return fields


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--graalvm-home", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument('--client-dir', type=Path, help='External hash-verified client input directory; does not assert A0 PASS')
    parser.add_argument('--baseline', type=Path, help='Existing real A0 evidence directory (or MCFAO_A0)')
    args = parser.parse_args(argv)
    home = args.graalvm_home.resolve(strict=True)
    lock = json.loads((ROOT / "compiler/fabric.lock.json").read_text())
    inventory, shared_meta = preparation_inputs(args.client_dir, args.baseline)
    output = args.output.absolute()
    output.mkdir(parents=True, exist_ok=False)
    classes = output / "classes"
    classes.mkdir()
    fixture = ROOT / "interop/b0"
    sources = sorted(p for p in content_paths(fixture) if p.suffix == ".java"
                     and p.relative_to(fixture).parts[0] in ("src", "bootstrap"))
    versions = verified_loaders(lock)
    drivers = {}
    for version in versions:
        engine = select_libraries(inventory, lock, version)
        version_dir = output / "loaders" / version
        version_classes = version_dir / "classes"
        version_classes.mkdir(parents=True)
        command = [str(home / "bin/javac"), "--release", "25", "-proc:none", "-cp",
                   os.pathsep.join(entry["path"] for entry in engine), "-d", str(version_classes),
                   *map(str, sources)]
        result = capture(command, version_dir / "compile", 180)
        if result["exitCode"] or result["timedOut"]:
            print(json.dumps(result, indent=2))
            return 1
        compiled = {str(path.relative_to(version_classes)): path.read_bytes()
                    for path in content_paths(version_classes) if path.suffix == ".class"}
        bootstrap = {name: data for name, data in compiled.items()
                     if name.startswith("fabricaot/b0/bootstrap/")}
        bootstrap["META-INF/MANIFEST.MF"] = b"Manifest-Version: 1.0\r\nPremain-Class: fabricaot.b0.bootstrap.CaptureAgent\r\n\r\n"
        (version_dir / "driver.jar").write_bytes(jar_bytes(bootstrap))
        drivers[version] = {"engineLibraries": [
                                {**row, "file": Path(row["path"]).name} for row in engine],
                            "driver": {"bytes": (version_dir / "driver.jar").stat().st_size,
                                       "sha256": sha256(version_dir / "driver.jar")}}
    # The shared fixture is byte-identical across Loaders; compile it once against the default
    # engine so the audited B0 evidence keeps referring to one fixture.
    engine = select_libraries(inventory, lock)
    # The legacy engine list carries file names so older readers keep working.
    engine = [{**row, "file": Path(row["path"]).name} for row in engine]
    command = [str(home / "bin/javac"), "--release", "25", "-proc:none", "-cp",
               os.pathsep.join(entry["path"] for entry in engine), "-d", str(classes), *map(str, sources)]
    result = capture(command, output / "compile", 180)
    if result["exitCode"] or result["timedOut"]:
        print(json.dumps(result, indent=2))
        return 1
    compiled = {str(path.relative_to(classes)): path.read_bytes()
                for path in content_paths(classes) if path.suffix == ".class"}
    nested = {name: data for name, data in compiled.items() if name.startswith("fabricaot/b0/nested/")}
    nested.update({str(path.relative_to(fixture / "nested-resources")): path.read_bytes()
                   for path in content_paths(fixture / "nested-resources") if path.is_file()})
    nested_data = jar_bytes(nested)
    (output / "nested.jar").write_bytes(nested_data)
    parent = {name: data for name, data in compiled.items()
              if not name.startswith(("fabricaot/b0/nested/", "fabricaot/b0/bootstrap/"))}
    parent.update({str(path.relative_to(fixture / "resources")): path.read_bytes()
                   for path in content_paths(fixture / "resources") if path.is_file()})
    parent["META-INF/jars/fabricaot-b0-nested.jar"] = nested_data
    (output / "fixture.jar").write_bytes(jar_bytes(parent))
    bootstrap = {name: data for name, data in compiled.items() if name.startswith("fabricaot/b0/bootstrap/")}
    bootstrap["META-INF/MANIFEST.MF"] = b"Manifest-Version: 1.0\r\nPremain-Class: fabricaot.b0.bootstrap.CaptureAgent\r\n\r\n"
    (output / "driver.jar").write_bytes(jar_bytes(bootstrap))
    libraries = []
    for entry in inventory["jars"]:
        path = Path(entry["path"])
        if path.is_relative_to(shared_meta):
            if not path.is_file() or sha256(path) != entry["sha256"]:
                raise ValueError("baseline library changed: " + str(path))
            libraries.append(entry)
    inputs = {str(p.relative_to(ROOT)): sha256(p) for p in content_paths(fixture) if p.is_file()}
    for name in ("tools/prepare_b0.py", "tools/run_b0_reference.py", "compiler/fabric.lock.json"):
        inputs[name] = sha256(ROOT / name)
    minecraft_version = inventory.get("minecraftVersion", DEFAULT_VERSION)
    version_fields = version_manifest_fields(inventory, shared_meta, minecraft_version)
    manifest = {"status": "PREPARED", "gateB0": "BLOCKED", "gateB1": "BLOCKED",
                "scope": "compiled first-party fixture only; no Loader/Mod initializer/test run",
                **version_fields,
                "javaHome": str(home), "javaSHA256": sha256(home / "bin/java"),
                "engineLibraries": engine, "referenceLibraries": libraries,
                "verifiedLoaders": versions, "loaderDrivers": drivers,
                "binaries": {name: {"bytes": (output / name).stat().st_size, "sha256": sha256(output / name)}
                             for name in ("fixture.jar", "nested.jar", "driver.jar")},
                "inputsSHA256": inputs}
    (output / "result.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({"status": "PREPARED", "classFiles": len(compiled), "testExecution": "NONE"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
