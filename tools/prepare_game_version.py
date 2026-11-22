#!/usr/bin/env python3
"""Prepare a Minecraft client jar in the runtime namespace.

26.1.2 already ships Mojang-mapped classes, so the client is returned untouched.
1.21.1 ships obfuscated classes; this tool joins the official Mojang ProGuard
client mappings with Fabric intermediary into a Tiny v2 table
(``obfuscated, intermediary, official``), then remaps the client into the
official namespace used by the 26.1.2 adapters and into the intermediary
namespace used only as Fabric's runtime Mod remap classpath.

Nothing here executes game code. The client and mappings are verified against
the official version metadata before any bytecode is rewritten, and the
intermediary artifact is pinned by size and SHA-256 in
``compiler/game-versions.lock.json``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.parse
from pathlib import Path

from capture import ROOT, capture, redact_log
from external_storage import require_mount
from mod_file_policy import clean_staged_metadata
from storage_config import cache_path, require_space, storage_paths
from verified_download import TOOL_HOSTS, fetch as verified_fetch

LOCK = ROOT / "compiler/game-versions.lock.json"
HELPER = ROOT / "compiler/version-tools/src/fabricaot/version/GameRemapper.java"
NAMESPACES = ("obfuscated", "intermediary", "official")
LEGACY_VERSION = "26.1.2"


class PreparationError(ValueError):
    """A precondition failed; no output should be trusted."""


def _digest(path: Path, algorithm: str) -> str:
    digest = hashlib.new(algorithm)
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256(path: Path) -> str:
    return _digest(path, "sha256")


def _require_regular(path: Path, label: str) -> Path:
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise PreparationError(f"{label} is missing or a symlink: {path}")
    return path


def _require_hash(path: Path, label: str, *, sha256_value: str | None = None,
                  sha1_value: str | None = None, size: int | None = None) -> Path:
    path = _require_regular(path, label)
    actual = path.stat().st_size
    if size is not None and actual != size:
        raise PreparationError(f"{label} size mismatch: {actual} != {size}")
    if sha256_value and sha256(path) != sha256_value:
        raise PreparationError(f"{label} SHA-256 mismatch: {path}")
    if sha1_value and _digest(path, "sha1") != sha1_value:
        raise PreparationError(f"{label} SHA-1 mismatch: {path}")
    return path


def _read_json(path: Path, label: str) -> dict:
    try:
        value = json.loads(Path(path).read_text())
    except (OSError, ValueError) as exc:
        raise PreparationError(f"cannot read {label}: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise PreparationError(f"{label} must be a JSON object: {path}")
    return value


def _entry(path: Path, sha256_value: str | None = None, size: int | None = None) -> dict:
    return {"path": str(path), "sha256": sha256_value or sha256(path),
            "size": size if size is not None else path.stat().st_size}


def load_prepared(prepared: Path) -> dict:
    """Read the prepared manifest, accepting both a directory and its result.json."""
    prepared = Path(prepared)
    manifest = prepared if prepared.is_file() else prepared / "result.json"
    data = _read_json(manifest, "prepared manifest")
    data.setdefault("_manifest", str(manifest))
    data.setdefault("_preparedDir", str(manifest.parent))
    return data


def game_version(prepared: dict) -> str:
    """Return the prepared Minecraft version; older trees predate the field."""
    value = prepared.get("minecraftVersion")
    if value is None and isinstance(prepared.get("minecraft"), dict):
        value = prepared["minecraft"].get("version")
    if value is None:
        return LEGACY_VERSION
    if not isinstance(value, str) or not value.strip():
        raise PreparationError("prepared minecraftVersion must be a non-empty string")
    if value != value.strip():
        raise PreparationError(f"prepared minecraftVersion has surrounding whitespace: {value!r}")
    return value


def _version_metadata(prepared: dict) -> tuple[Path, str]:
    """Resolve the official version metadata path and its expected SHA-256."""
    declared = prepared.get("versionMetadata")
    if isinstance(declared, dict):
        path, expected = declared.get("path"), declared.get("sha256") or declared.get("metadataSHA256")
    else:
        path, expected = declared, prepared.get("metadataSHA256")
    if not isinstance(path, str) or not path:
        raise PreparationError(
            "prepared['versionMetadata'] (path, or {path, sha256}) is required to "
            "validate the official client and mappings descriptors")
    if not isinstance(expected, str) or len(expected) != 64:
        raise PreparationError("prepared metadataSHA256/versionMetadata.sha256 is required")
    return _require_hash(Path(path), "official version metadata", sha256_value=expected.lower()), expected.lower()


def _reference_library(prepared: dict, client: Path) -> Path:
    entries = prepared.get("referenceLibraries")
    if not isinstance(entries, list) or not entries:
        raise PreparationError("prepared referenceLibraries is required to verify the client jar")
    client = Path(client)
    matches = []
    for row in entries:
        if not isinstance(row, dict) or not isinstance(row.get("path"), str):
            continue
        candidate = Path(row["path"])
        if candidate.name == client.name or candidate.resolve() == client.resolve():
            matches.append(row)
    if len(matches) != 1:
        raise PreparationError(f"client jar is not uniquely listed in referenceLibraries: {client}")
    row = matches[0]
    _require_hash(client, "client jar", sha256_value=row.get("sha256"), size=row.get("size"))
    return client


def _official_mappings(prepared: dict, mappings: Path | None) -> Path:
    """Resolve the official client mappings file, never guessing an unverified path."""
    if mappings is not None:
        return _require_regular(Path(mappings), "official client mappings")
    declared = prepared.get("mappings")
    if isinstance(declared, dict) and isinstance(declared.get("path"), str):
        return _require_hash(Path(declared["path"]), "official client mappings",
                             sha256_value=declared.get("sha256"),
                             sha1_value=declared.get("sha1"), size=declared.get("size"))
    if isinstance(declared, str):
        return _require_regular(Path(declared), "official client mappings")
    raise PreparationError(
        "prepared['mappings'] {path, sha256, sha1, size} is required (or pass mappings=)")


def _official_descriptors(metadata: dict, version: str) -> tuple[dict, dict]:
    if metadata.get("id") != version:
        raise PreparationError(f"version metadata id {metadata.get('id')!r} != {version!r}")
    downloads = metadata.get("downloads")
    if not isinstance(downloads, dict):
        raise PreparationError("official version metadata has no downloads object")
    client, mappings = downloads.get("client"), downloads.get("client_mappings")
    for label, row in (("client", client), ("client_mappings", mappings)):
        if not isinstance(row, dict) or not isinstance(row.get("sha1"), str) or not isinstance(row.get("size"), int):
            raise PreparationError(f"official metadata downloads.{label} lacks sha1/size")
    return client, mappings


def load_lock(path: Path = LOCK) -> dict:
    data = _read_json(Path(path), "game-versions lock")
    versions = data.get("versions")
    if not isinstance(versions, dict) or not versions:
        raise PreparationError("game-versions lock has no versions")
    return data


def intermediary_pin(lock: dict, version: str) -> dict:
    row = lock.get("versions", {}).get(version, {}).get("intermediary")
    if not isinstance(row, dict):
        raise PreparationError(f"game-versions lock has no intermediary pin for {version}")
    url, digest, size = row.get("url"), row.get("sha256"), row.get("size")
    if not isinstance(url, str) or urllib.parse.urlsplit(url).scheme != "https":
        raise PreparationError("intermediary url must be HTTPS")
    host = urllib.parse.urlsplit(url).hostname or ""
    if host != "maven.fabricmc.net":
        raise PreparationError(f"intermediary url must come from maven.fabricmc.net, got {host}")
    if not isinstance(digest, str) or len(digest) != 64 or not isinstance(size, int) or size <= 0:
        raise PreparationError("intermediary pin needs a SHA-256 and a positive size")
    return row


def fetch_intermediary(lock: dict, version: str) -> Path:
    """Return the pinned intermediary jar, downloading it once if absent."""
    row = intermediary_pin(lock, version)
    target = cache_path(f"version-adaptation/{version}/intermediary-{version}-{row.get('version', 'v2')}.jar")
    if target.is_file():
        return _require_hash(target, "intermediary jar", sha256_value=row["sha256"], size=row["size"])
    require_mount("intermediary download")
    require_space(storage_paths())
    try:
        verified_fetch({"url": row["url"], "sha256": row["sha256"], "size": row["size"]}, target,
                       hosts=frozenset({"maven.fabricmc.net"}) & TOOL_HOSTS)
    except (OSError, ValueError) as exc:
        raise PreparationError(f"intermediary download failed: {exc}") from exc
    return _require_hash(target, "intermediary jar", sha256_value=row["sha256"], size=row["size"])


def _engine_libraries(prepared: dict) -> tuple[Path, list[Path]]:
    entries = prepared.get("engineLibraries")
    if not isinstance(entries, list) or not entries:
        raise PreparationError("prepared engineLibraries is required to compile the remapper")
    loader = None
    asm: list[Path] = []
    for row in entries:
        if not isinstance(row, dict) or not isinstance(row.get("path"), str):
            raise PreparationError("engineLibraries rows need a path")
        path = _require_hash(Path(row["path"]), "engine library",
                             sha256_value=row.get("sha256"), size=row.get("size"))
        name = path.name
        if name.startswith("fabric-loader-") and name.endswith(".jar"):
            loader = path
        elif name.startswith("asm-") and name.endswith(".jar"):
            asm.append(path)
    if loader is None:
        raise PreparationError("engineLibraries has no fabric-loader jar")
    if not asm:
        raise PreparationError("engineLibraries has no ASM jars")
    return loader, asm


def _java_home(prepared: dict) -> Path:
    declared = prepared.get("javaHome")
    candidates = [Path(declared)] if isinstance(declared, str) else []
    candidates.append(cache_path("toolchains/graalvm-25.0.2/graalvm-community-openjdk-25.0.2+10.1/Contents/Home"))
    for candidate in candidates:
        if (candidate / "bin/javac").is_file() and (candidate / "bin/java").is_file():
            return candidate
    raise PreparationError("no Java 25 toolchain with javac/java was found")


def _free_directory(parent: Path, stem: str) -> Path:
    """Return a fresh capture directory; ``capture`` refuses to reuse one."""
    for index in range(1000):
        candidate = parent / (stem if index == 0 else f"{stem}-{index}")
        if not candidate.exists():
            return candidate
    raise PreparationError(f"too many stale capture directories named {stem}")


def _compile_helper(home: Path, classpath: list[Path], output: Path) -> Path:
    """Compile the remapper once per helper source revision, without reusing a stale cache."""
    key = sha256(HELPER)[:16]
    classes = output / f"helper-classes-{key}"
    compiled = classes / "fabricaot/version/GameRemapper.class"
    if compiled.is_file():
        return classes
    classes.mkdir(parents=True, exist_ok=True)
    log = _free_directory(output, f"helper-compile-{key}")
    result = capture([str(home / "bin/javac"), "--release", "25", "-proc:none",
                      "-cp", os.pathsep.join(map(str, classpath)), "-d", str(classes), str(HELPER)],
                     log, 180)
    if result["exitCode"] or result["timedOut"] or not compiled.is_file():
        detail = (log / "stderr.log").read_text(errors="replace")[-2000:]
        raise PreparationError("GameRemapper compilation failed: " + redact_log(detail))
    return classes


def _cached_result(output: Path, inputs: dict, client_sha1: str, mapping_sha1: str,
                   version: str, artifact_root: Path | None = None) -> dict | None:
    """Rehash every cached output and input pin; never trust a path merely existing."""
    document = output / "result.json"
    if not document.is_file():
        return None
    try:
        cached = json.loads(document.read_text())
    except ValueError:
        return None
    if not isinstance(cached, dict):
        return None
    if cached.get("minecraftVersion") != version or cached.get("remapped") is not True:
        return None
    if cached.get("sourceClientSHA1") != client_sha1 or cached.get("sourceMappingsSHA1") != mapping_sha1:
        return None
    if cached.get("inputsSHA256") != inputs:
        return None
    allowed = [output.resolve()]
    if artifact_root is not None:
        allowed.append(Path(artifact_root).resolve())
    for key, hash_key in (("client", "clientSHA256"),
                          ("intermediaryClient", "intermediaryClientSHA256"),
                          ("mappings", "mappingsSHA256")):
        path, expected = cached.get(key), cached.get(hash_key)
        if not isinstance(path, str) or not isinstance(expected, str):
            return None
        candidate = Path(path)
        # A cached document may only point at this output or the shared artifact cache.
        if candidate.is_symlink() or not candidate.is_file():
            return None
        if not any(candidate.resolve().is_relative_to(root) for root in allowed):
            return None
        if sha256(candidate) != expected:
            return None
    return cached


def prepare_client(prepared: Path, client: Path, output: Path,
                   mappings: Path | None = None) -> dict:
    """Prepare one client jar for the runtime namespace.

    26.1.2 returns ``client`` unchanged and writes nothing. 1.21.1 verifies the
    client and official mappings against the official version metadata, then
    writes ``client.jar`` (official), ``intermediary-client.jar``
    (intermediary, for the Mod remap classpath) and ``mappings.tiny``.
    """
    manifest = load_prepared(prepared)
    version = game_version(manifest)
    client = _require_regular(Path(client), "client jar")
    if version == LEGACY_VERSION:
        return {"client": str(client), "mappings": None, "intermediaryClient": None,
                "remapped": False, "minecraftVersion": version}
    if version != "1.21.1":
        raise PreparationError(f"unsupported Minecraft version {version!r}")

    _reference_library(manifest, client)
    metadata_path, metadata_sha = _version_metadata(manifest)
    metadata = _read_json(metadata_path, "official version metadata")
    client_descriptor, mapping_descriptor = _official_descriptors(metadata, version)
    if client.stat().st_size != client_descriptor["size"] or _digest(client, "sha1") != client_descriptor["sha1"]:
        raise PreparationError("client jar does not match official downloads.client")

    mappings = _official_mappings(manifest, mappings)
    if mappings.stat().st_size != mapping_descriptor["size"] or _digest(mappings, "sha1") != mapping_descriptor["sha1"]:
        raise PreparationError("client mappings do not match official downloads.client_mappings")
    declared = manifest.get("mappings")
    if isinstance(declared, dict) and declared.get("sha1") and declared["sha1"] != mapping_descriptor["sha1"]:
        raise PreparationError("prepared mappings descriptor disagrees with official metadata")
    if isinstance(declared, dict) and declared.get("size") and declared["size"] != mapping_descriptor["size"]:
        raise PreparationError("prepared mappings size disagrees with official metadata")

    output = Path(output)
    if output.exists() and not output.is_dir():
        raise PreparationError(f"output must be a directory: {output}")
    # The remap rewrites a ~27 MB jar twice; require room before creating anything.
    require_space(storage_paths())

    lock_path = LOCK
    lock = load_lock(lock_path)
    intermediary = fetch_intermediary(lock, version)
    output.mkdir(parents=True, exist_ok=True)
    home = _java_home(manifest)
    loader, asm = _engine_libraries(manifest)
    inputs = {"helper": sha256(HELPER), "lock": sha256(lock_path),
              "intermediary": sha256(intermediary),
              "engine": {path.name: sha256(path) for path in [loader, *asm]}}
    # Keyed by the exact inputs so a stale or tampered output is rebuilt, not reused.
    key = hashlib.sha256(json.dumps([inputs, client_descriptor["sha1"],
                                     mapping_descriptor["sha1"]], sort_keys=True).encode()).hexdigest()[:16]
    # Native dependencies and Fabric preparation pass different output directories for the
    # same client, so the artifacts and their record live in one input-keyed cache that both
    # callers share. The record sits beside the jars so a second caller can find it.
    remap = cache_path(f"version-adaptation/{version}/remapped/{key}")
    cached = _cached_result(remap, inputs, client_descriptor["sha1"], mapping_descriptor["sha1"], version)
    if cached is not None:
        # Reuse the verified artifacts; the caller's own record points at them.
        return _write_record(output, cached)
    classpath = [loader, *asm]
    classes = _compile_helper(home, classpath, output)
    remap.mkdir(parents=True, exist_ok=True)
    run_log = _free_directory(remap, "remap-log")
    command = [str(home / "bin/java"), "-Xmx3g", "-cp", os.pathsep.join(map(str, [classes, *classpath])),
               "fabricaot.version.GameRemapper",
               "--client", str(client), "--output", str(remap),
               "--proguard", str(mappings), "--intermediary", str(intermediary),
               "--expect-client-sha1", client_descriptor["sha1"],
               "--expect-client-size", str(client_descriptor["size"]),
               "--expect-mappings-sha1", mapping_descriptor["sha1"],
               "--expect-mappings-size", str(mapping_descriptor["size"])]
    result = capture(command, run_log, 900)
    if result["exitCode"] or result["timedOut"]:
        detail = (run_log / "stderr.log").read_text(errors="replace")[-2000:]
        raise PreparationError("client remap failed: " + redact_log(detail))
    try:
        produced = json.loads((run_log / "stdout.log").read_text())
    except ValueError as exc:
        raise PreparationError("client remap did not emit a result object") from exc

    expected_outputs = {"client": ("client.jar", "clientSHA256"),
                        "intermediaryClient": ("intermediary-client.jar", "intermediaryClientSHA256"),
                        "mappings": ("mappings.tiny", "mappingsSHA256")}
    for key_name, (file_name, hash_key) in expected_outputs.items():
        path = remap / file_name
        declared_path = produced.get(key_name)
        if declared_path is None or Path(declared_path).resolve() != path.resolve():
            raise PreparationError(f"remap reported an unexpected {key_name} path: {declared_path!r}")
        if not path.is_file() or sha256(path) != produced.get(hash_key):
            raise PreparationError(f"remap output {file_name} is missing or does not match its hash")
    # exFAT/macOS can drop `._name` sidecars beside the outputs; they are not game content.
    clean_staged_metadata(remap)

    result_document = {
        **produced,
        "minecraftVersion": version,
        "remapped": True,
        "sourceClient": str(client),
        "sourceClientSHA1": client_descriptor["sha1"],
        "sourceMappings": str(mappings),
        "sourceMappingsSHA1": mapping_descriptor["sha1"],
        "versionMetadata": str(metadata_path),
        "versionMetadataSHA256": metadata_sha,
        "intermediary": str(intermediary),
        "intermediarySHA256": sha256(intermediary),
        "intermediaryUsage": "Fabric runtime Mod remap classpath only; never the runtime game jar",
        "mappingLicense": "Mojang client mappings are used locally and are not redistributed",
        "inputsSHA256": inputs,
    }
    _write_record(remap, result_document)
    return _write_record(output, result_document)


def _write_record(directory: Path, document: dict) -> dict:
    """Atomically publish a result record; the caller's copy is written last."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / "result.json"
    temporary = target.with_suffix(".json.part")
    temporary.write_text(json.dumps(document, indent=2) + "\n")
    temporary.replace(target)
    return document


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepared", type=Path, required=True)
    parser.add_argument("--client", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mappings", type=Path, help="official client mappings; defaults to prepared['mappings']")
    args = parser.parse_args(argv)
    try:
        result = prepare_client(args.prepared, args.client, args.output, args.mappings)
    except PreparationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
