"""Pick a verified Fabric Loader for a pack's declared version.

The Builder ships a fixed set of Loader versions, each with its own hash-pinned
engine libraries. Selection is deliberately boring: an explicit request must be
one of the verified versions, and automatic selection takes the smallest
verified version that is not older than the pack's declaration. A pack that
declares something newer than every verified version falls back to the newest
verified version and says so; it is never silently downgraded.
"""
from __future__ import annotations

import re

DEFAULT_LOADER = "0.19.3"


def parse_version(value: str) -> tuple[int, ...]:
    if not isinstance(value, str):
        raise ValueError("Loader version must be text")
    match = re.fullmatch(r"(\d+(?:\.\d+)*)(?:[-+].*)?", value.strip())
    if match is None:
        raise ValueError(f"无法识别的 Fabric Loader 版本：{value}")
    return tuple(int(part) for part in match.group(1).split("."))


def verified(lock: dict) -> list[str]:
    loaders = lock.get("loaders")
    if not isinstance(loaders, dict) or not loaders:
        # Older prepared trees only carry one version; keep them loadable so an
        # update can finish before the environment is re-prepared.
        legacy = lock.get("fabricLoader")
        if not isinstance(legacy, str) or not legacy:
            raise ValueError("构建器锁文件没有可用的 Fabric Loader 版本")
        return [legacy]
    versions = sorted(loaders, key=parse_version)
    declared = lock.get("verifiedLoaders")
    if isinstance(declared, list) and sorted(declared, key=parse_version) != versions:
        raise ValueError("verifiedLoaders 与 loaders 清单不一致")
    return versions


def select(lock: dict, source: str | None, requested: str | None = None) -> dict:
    """Return the selected version, whether it was automatic, and any note."""
    versions = verified(lock)
    if requested and requested != "auto":
        if requested not in versions:
            raise ValueError(f"Fabric Loader {requested} 未经验证；可选：{'、'.join(versions)}")
        return {"version": requested, "automatic": False, "declared": source, "note": ""}
    if not source:
        return {"version": DEFAULT_LOADER if DEFAULT_LOADER in versions else versions[0],
                "automatic": True, "declared": None,
                "note": "来源未声明 Fabric Loader，使用默认验证版本"}
    wanted = parse_version(source)
    newer = [version for version in versions if parse_version(version) >= wanted]
    if newer:
        chosen = newer[0]
        note = "" if chosen == source else f"来源 {source} → 向上选用已验证 {chosen}"
        return {"version": chosen, "automatic": True, "declared": source, "note": note}
    chosen = versions[-1]
    return {"version": chosen, "automatic": True, "declared": source,
            "note": f"来源 {source} 高于全部已验证版本，使用最高验证版本 {chosen}"}


def engine_libraries(lock: dict, version: str) -> list[dict]:
    loaders = lock.get("loaders")
    if not isinstance(loaders, dict) or not loaders:
        if version != lock.get("fabricLoader"):
            raise ValueError(f"锁文件缺少 Fabric Loader {version} 的引擎清单")
        entries = lock.get("engineLibraries")
        if not isinstance(entries, list) or not entries:
            raise ValueError("锁文件的引擎清单为空")
        return entries
    if version not in loaders:
        raise ValueError(f"锁文件缺少 Fabric Loader {version} 的引擎清单")
    entries = loaders[version].get("engineLibraries")
    if not isinstance(entries, list) or not entries:
        raise ValueError(f"Fabric Loader {version} 的引擎清单为空")
    return entries


def default_version(lock: dict) -> str:
    versions = verified(lock)
    declared = lock.get("fabricLoader")
    return declared if declared in versions else versions[0]


def all_engine_libraries(lock: dict) -> list[dict]:
    """Every engine jar the Builder may need, deduplicated by file name."""
    seen: dict[str, dict] = {}
    for version in verified(lock):
        for entry in engine_libraries(lock, version):
            name = entry["file"]
            existing = seen.get(name)
            if existing is not None and (existing.get("sha256") != entry.get("sha256")
                                         or existing.get("bytes") != entry.get("bytes")):
                raise ValueError(f"同一引擎文件在不同 Loader 下哈希不一致：{name}")
            seen[name] = entry
    return [seen[name] for name in sorted(seen)]
