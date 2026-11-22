"""Renderer-specific runtime contracts shared by local and CI packaging."""
from __future__ import annotations
import argparse
from pathlib import Path
import plistlib
import re
import subprocess
from uuid import UUID

COMMON = ("client-graph.dylib", "libglfw.dylib", "liblwjgl.dylib", "libopenal.dylib")
RENDERERS = ("metal", "gl41")
# Zink (OSMesa/MoltenVK) is the project renderer. The native Metal backend is
# kept as an explicitly selected comparison path, never the default.
DEFAULT_RENDERER = "gl41"
BUILD_PROVENANCE = "FAOTBuildProvenance.json"
# The packaged asset index is fixed by the client version. Only the default
# line may fall back to its historical index; another line must name its own,
# so a 1.21.1 app can never be validated against 26.1.2's asset tree.
DEFAULT_ASSET_INDEX = "30"
ASSET_INDEX_BY_VERSION = {"26.1.2": DEFAULT_ASSET_INDEX, "1.21.1": "17"}
ASSET_INDEX_ID = re.compile(r"[0-9]{1,8}")


def dylib_uuid(path: Path):
    output = subprocess.check_output(
        ["/usr/bin/xcrun", "dwarfdump", "--uuid", str(path)], text=True).splitlines()
    if len(output) != 1:
        raise ValueError(f"Expected one Mach-O UUID: {path}")
    label, identifier, architecture, *_ = output[0].split()
    if label != "UUID:" or architecture != "(arm64)":
        raise ValueError(f"Expected arm64 Mach-O UUID: {path}")
    return str(UUID(identifier)).upper()

def libraries(renderer):
    if renderer not in RENDERERS:
        raise ValueError("Unknown renderer: " + str(renderer))
    return COMMON + (("libfaot_metal.dylib",) if renderer == "metal" else
                     ("liblwjgl_opengl.dylib", "libOSMesa.8.dylib", "libMoltenVK.dylib"))

def config_names(renderer):
    libraries(renderer)
    return ("glfw", "lwjgl", "openal") + (("metal", "shaders") if renderer == "metal" else ("gl41", "lwjglOpengl"))


def _expanded(value):
    """A real Info.plist value, or None when it is absent or unexpanded.

    xcodebuild substitutes ``$(FAOT_ASSET_INDEX)`` at build time. A literal
    placeholder means the setting was never passed, which is indistinguishable
    from a missing key and must not be accepted as an asset index.
    """
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or "$(" in value:
        return None
    return value


def packaged_asset_index(app: Path) -> str:
    """The numeric asset index the packaged app declares, cross-checked by version.

    An app without the version key predates the explicit profile and is the
    default 26.1.2 line, which keeps its historical index. Any other version
    must carry its own index: falling back would validate the wrong tree.
    """
    from game_versions import DEFAULT_VERSION, SUPPORTED_VERSIONS
    info_path = Path(app) / "Info.plist"
    info = {}
    if info_path.is_file():
        info = plistlib.loads(info_path.read_bytes())
        if not isinstance(info, dict):
            raise ValueError("Packaged Info.plist is not a dictionary: " + str(info_path))
    version = _expanded(info.get("FAOTMinecraftVersion")) or DEFAULT_VERSION
    if version not in SUPPORTED_VERSIONS:
        raise ValueError("Packaged Minecraft version is not supported: " + repr(version))
    expected = ASSET_INDEX_BY_VERSION.get(version)
    if expected is None:
        raise ValueError("No packaged asset index contract for Minecraft " + version)
    index = _expanded(info.get("FAOTAssetIndex"))
    if index is None:
        if version != DEFAULT_VERSION:
            raise ValueError("Minecraft " + version +
                             " app must declare its own FAOTAssetIndex instead of the default")
        return expected
    if not ASSET_INDEX_ID.fullmatch(index):
        raise ValueError("Invalid packaged asset index: " + repr(index))
    if index != expected:
        raise ValueError("Packaged asset index " + index + " does not belong to Minecraft " +
                         version + " (expected " + expected + ")")
    return index


def validate_runtime(app: Path, renderer: str):
    from build_c0_harness import validate_dylib
    from mod_file_policy import is_metadata
    directory = app / "Frameworks"
    # exFAT volumes add `._name.dylib` sidecars; they are metadata, not runtime
    # libraries, and must not be reported as unexpected members.
    found = {p.name for p in directory.glob("*.dylib") if not is_metadata(p.name)}
    if found != set(libraries(renderer)):
        raise ValueError(f"Incomplete {renderer} runtime: missing={set(libraries(renderer))-found}, unexpected={found-set(libraries(renderer))}")
    for name in libraries(renderer):
        validate_dylib(directory / name)
    from glfw_runtime_contract import validate_glfw_renderer
    validate_glfw_renderer(directory / 'libglfw.dylib', renderer)
    if renderer == "metal":
        from build_c0_harness import validate_shader_bundle
        validate_shader_bundle(directory / "fabricaot-shaders", simulator=False)
        if not (directory / "fabricaot-present.metallib").stat().st_size:
            raise ValueError("Empty Metal presentation library")
    index = packaged_asset_index(app)
    index_path = app / "assets/indexes" / (index + ".json")
    # The identifier is already numeric, so the path cannot escape; a symlink
    # would still let the bundle point outside itself.
    if index_path.is_symlink() or not index_path.is_file():
        raise ValueError("Missing Minecraft asset index " + index)
    if not any((app / "assets/objects").glob("*/*")):
        raise ValueError("Missing Minecraft asset objects")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--app", type=Path, required=True)
    parser.add_argument("--renderer", choices=RENDERERS, required=True)
    args = parser.parse_args()
    validate_runtime(args.app, args.renderer)
