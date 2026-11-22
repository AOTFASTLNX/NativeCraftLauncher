"""Shared graphics build evidence and central registration; no private registry."""
from __future__ import annotations
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from capture import ROOT
from bootstrap_toolchain import sha256
from game_versions import DEFAULT_VERSION

# Each supported Minecraft line pins one LWJGL release. A bridge built for one
# line must never be mixed with the other: the JNI adapters, overlay classes and
# STB headers are generated from that exact jar set.
LWJGL_BY_MINECRAFT = {"26.1.2": "3.4.1", "1.21.1": "3.3.3"}
BRIDGE_MODULES = ("lwjgl", "lwjgl-glfw", "lwjgl-stb", "lwjgl-openal")
_LWJGL_JAR = re.compile(r"^(lwjgl(?:-[a-z]+)?)-([0-9]+(?:[.][0-9]+)*)(-natives[^/]*)?[.]jar$")


class BridgeVersionMismatch(ValueError):
    """A built bridge belongs to another Minecraft/LWJGL line and must be rebuilt."""


def lwjgl_jar(name):
    """Split an LWJGL jar name into (module, version, is_native) or None."""
    match = _LWJGL_JAR.match(Path(str(name)).name)
    if match is None:
        return None
    module, version, natives = match.groups()
    return module, version, bool(natives)


def expected_lwjgl_version(minecraft_version=DEFAULT_VERSION) -> str:
    """The LWJGL release a supported Minecraft version ships."""
    if not isinstance(minecraft_version, str) or minecraft_version not in LWJGL_BY_MINECRAFT:
        raise ValueError("不支持的 Minecraft 版本：" + repr(minecraft_version) +
                         "；可选：" + "、".join(LWJGL_BY_MINECRAFT))
    return LWJGL_BY_MINECRAFT[minecraft_version]


def select_lwjgl_modules(reference_libraries, minecraft_version=DEFAULT_VERSION) -> dict[str, dict]:
    """Pick the four non-native LWJGL modules from a prepared reference list.

    Natives jars are host-only artifacts and are excluded. All four modules must
    share one version and that version must match the selected Minecraft line,
    so a 26.1.2 bridge can never be assembled from 1.21.1 jars or vice versa.
    """
    expected = expected_lwjgl_version(minecraft_version)
    if not isinstance(reference_libraries, list):
        raise ValueError("prepared referenceLibraries must be a list")
    found: dict[str, dict] = {}
    for row in reference_libraries:
        if not isinstance(row, dict):
            raise ValueError("prepared referenceLibraries entries must be objects")
        parsed = lwjgl_jar(row.get("path", ""))
        if parsed is None:
            continue
        module, version, natives = parsed
        if natives or module not in BRIDGE_MODULES:
            continue
        if module in found:
            raise ValueError("重复的 LWJGL 模块输入：" + module + "（" +
                             Path(str(row.get("path", ""))).name + "）")
        found[module] = dict(row, version=version)
    missing = [module for module in BRIDGE_MODULES if module not in found]
    if missing:
        raise ValueError("缺少 LWJGL 模块：" + "、".join(missing) +
                         "；需要 core/GLFW/STB/OpenAL 的非 natives JAR")
    versions = {row["version"] for row in found.values()}
    if len(versions) != 1:
        raise ValueError("LWJGL 模块版本不一致：" + "、".join(sorted(versions)))
    version = versions.pop()
    if version != expected:
        raise ValueError("Minecraft " + minecraft_version + " 需要 LWJGL " + expected +
                         "，prepared 输入是 " + version)
    return {module: found[module] for module in BRIDGE_MODULES}


def owned_client_references(directory, minecraft_version=DEFAULT_VERSION) -> list[dict]:
    """Build a reference library list from an acquired owned-client directory.

    This is the bounded alternative to a prepared B0 manifest: the LWJGL jars
    a bridge is generated from are already present in the verified owned-client
    tree, so a bridge can be built before the rest of the pack inputs exist.
    Nothing is downloaded and nothing is guessed: the same rule filter as
    acquire_client selects the supported libraries, every selected path must
    stay inside the client tree, and each file is checked against its official
    SHA-1 and size.
    """
    import hashlib
    from pathlib import PurePosixPath

    def sha1(path: Path) -> str:
        digest = hashlib.sha1()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1 << 20), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def library_path(name: str) -> Path:
        # Same containment rule as acquire_client.library_path.
        parts = PurePosixPath(name)
        if not name or parts.is_absolute() or ".." in parts.parts or "\\" in name or ":" in name:
            raise ValueError("Unsafe library path: " + repr(name))
        return Path(*parts.parts)

    def supported(library: dict) -> bool:
        # Mirror acquire_client: only artifacts the acquire step would keep for
        # this host. Windows/Linux natives carry rules that never match.
        rules = library.get("rules", [])
        allowed = not rules
        for rule in rules:
            os_rule = rule.get("os", {})
            if (os_rule.get("name", "osx") == "osx"
                    and os_rule.get("arch", "arm64") in ("arm64", "aarch64")
                    and not rule.get("features")):
                allowed = rule.get("action") == "allow"
        return allowed

    def inside(root: Path, path: Path) -> Path:
        resolved = path.resolve()
        if not resolved.is_relative_to(root):
            raise ValueError("Owned client library escapes its directory: " + str(path))
        return resolved

    expected_lwjgl_version(minecraft_version)
    directory = Path(directory).resolve(strict=True)
    inventory = directory / "client-inputs.json"
    rows: list[dict] = []
    if inventory.is_file():
        record = json.loads(inventory.read_text())
        if record.get("status") != "OWNED_CLIENT_INPUTS_VERIFIED":
            raise ValueError("owned client inputs are not verified: " + str(inventory))
        if record.get("minecraftVersion", DEFAULT_VERSION) != minecraft_version:
            raise ValueError("owned client inputs belong to Minecraft " +
                             str(record.get("minecraftVersion", DEFAULT_VERSION)))
        for row in record.get("jars", []):
            path = inside(directory, Path(str(row.get("path", ""))))
            if not path.is_file():
                raise ValueError("owned client library missing: " + str(path))
            digest = sha256(path)
            if row.get("sha256") and row["sha256"] != digest:
                raise ValueError("owned client library changed: " + str(path))
            if isinstance(row.get("size"), int) and path.stat().st_size != row["size"]:
                raise ValueError("owned client library size changed: " + str(path))
            rows.append({"path": str(path), "sha256": digest,
                         "size": path.stat().st_size})
        return rows
    metadata = directory / (minecraft_version + ".json")
    if not metadata.is_file():
        metadata = directory / "versions" / minecraft_version / (minecraft_version + ".json")
    if not metadata.is_file():
        raise ValueError("owned client directory has no " + minecraft_version + " metadata: " +
                         str(directory))
    document = json.loads(metadata.read_text())
    if document.get("id") != minecraft_version:
        raise ValueError("owned client metadata declares another Minecraft version")
    libraries = directory / "meta" / "libraries"
    for library in document.get("libraries", []):
        if not supported(library):
            continue
        artifact = (library.get("downloads") or {}).get("artifact")
        if not isinstance(artifact, dict) or not isinstance(artifact.get("path"), str):
            continue
        path = inside(directory, libraries / library_path(artifact["path"]))
        if not path.is_file():
            raise ValueError("owned client library missing: " + str(path))
        if isinstance(artifact.get("size"), int) and path.stat().st_size != artifact["size"]:
            raise ValueError("owned client library size changed: " + str(path))
        if isinstance(artifact.get("sha1"), str) and sha1(path) != artifact["sha1"]:
            raise ValueError("owned client library SHA-1 changed: " + str(path))
        rows.append({"path": str(path), "sha256": sha256(path), "size": path.stat().st_size})
    client = inside(directory, directory / "meta" / "versions" / minecraft_version /
                    (minecraft_version + ".jar"))
    descriptor = (document.get("downloads") or {}).get("client") or {}
    if not client.is_file():
        raise ValueError("owned client jar missing: " + str(client))
    if isinstance(descriptor.get("size"), int) and client.stat().st_size != descriptor["size"]:
        raise ValueError("owned client jar size changed: " + str(client))
    if isinstance(descriptor.get("sha1"), str) and sha1(client) != descriptor["sha1"]:
        raise ValueError("owned client jar SHA-1 changed: " + str(client))
    rows.append({"path": str(client), "sha256": sha256(client), "size": client.stat().st_size})
    # The bounded bridge needs the four modules and the OpenGL jar; fail here,
    # before any compile, if the acquired tree cannot supply them.
    select_lwjgl_modules(rows, minecraft_version)
    select_lwjgl_opengl(rows, minecraft_version)
    return rows


def select_lwjgl_opengl(reference_libraries, minecraft_version=DEFAULT_VERSION) -> dict:
    """Pick the non-native LWJGL OpenGL jar matching the selected Minecraft line."""
    expected = expected_lwjgl_version(minecraft_version)
    if not isinstance(reference_libraries, list):
        raise ValueError("prepared referenceLibraries must be a list")
    found = None
    for row in reference_libraries:
        if not isinstance(row, dict):
            raise ValueError("prepared referenceLibraries entries must be objects")
        parsed = lwjgl_jar(row.get("path", ""))
        if parsed is None:
            continue
        module, version, natives = parsed
        if module != "lwjgl-opengl" or natives:
            continue
        if found is not None:
            raise ValueError("重复的 LWJGL OpenGL 输入：" +
                             Path(str(row.get("path", ""))).name)
        found = dict(row, version=version)
    if found is None:
        raise ValueError("缺少 LWJGL OpenGL 模块：需要非 natives 的 lwjgl-opengl JAR")
    if found["version"] != expected:
        raise ValueError("Minecraft " + minecraft_version + " 需要 LWJGL " + expected +
                         "，prepared 的 OpenGL 输入是 " + found["version"])
    return found


def _bridge_input_paths(reference_libraries, minecraft_version):
    modules = select_lwjgl_modules(reference_libraries, minecraft_version)
    return modules, [Path(modules[module]["path"]) for module in BRIDGE_MODULES]


def _check_recorded_inputs(modules, paths, recorded) -> None:
    if not isinstance(recorded, dict):
        raise ValueError("LWJGL 桥没有记录 inputsSHA256")
    if set(recorded) != {str(path) for path in paths}:
        raise ValueError("LWJGL 桥的输入集合与选中 prepared 四库不一致；"
                         "26.1.2 与 1.21.1 的桥不可混用，请重新构建")
    for module, path in zip(BRIDGE_MODULES, paths):
        pinned = modules[module].get("sha256")
        if not isinstance(pinned, str) or sha256(path) != pinned:
            raise ValueError("prepared LWJGL 输入已改变：" + str(path))
        if recorded[str(path)] != pinned:
            raise ValueError("LWJGL 桥记录的输入哈希与 prepared 不一致：" + str(path))


def bridge_selection(directory: Path, reference_libraries,
                     minecraft_version=DEFAULT_VERSION) -> dict:
    """Validate a built bridge against the selected Minecraft/LWJGL line.

    Reports written before multi-version support carry neither version field.
    Those may only be inferred as the default 26.1.2 line, and only when the
    recorded inputs are exactly the four pinned 3.4.1 jars. Anything else is a
    mixed or stale bridge and is rejected instead of guessed.
    """
    directory = Path(directory).resolve(strict=True)
    report = json.loads((directory / "result.json").read_text())
    if report.get("status") != "STATIC_JNI_BRIDGE_BUILT":
        raise ValueError("A successfully built static JNI bridge is required")
    expected = expected_lwjgl_version(minecraft_version)
    recorded_version = report.get("minecraftVersion")
    recorded_lwjgl = report.get("lwjglVersion")
    # Version coupling is checked before the input jars so a bridge from the
    # other Minecraft line reports that fact instead of a jar-name mismatch.
    if recorded_version is not None and recorded_version != minecraft_version:
        raise BridgeVersionMismatch(
            "LWJGL 桥属于 Minecraft " + repr(recorded_version) + "，当前选择 " +
            minecraft_version + "；请重新构建")
    if recorded_lwjgl is not None and recorded_lwjgl != expected:
        raise BridgeVersionMismatch(
            "LWJGL 桥版本 " + repr(recorded_lwjgl) + " 与 Minecraft " +
            minecraft_version + " 需要的 " + expected + " 不一致；请重新构建")
    if recorded_version is None and recorded_lwjgl is None:
        if minecraft_version != DEFAULT_VERSION:
            raise BridgeVersionMismatch(
                "LWJGL 桥没有记录 minecraftVersion，只能用于 " + DEFAULT_VERSION +
                "；请为 Minecraft " + minecraft_version + " 重新构建")
        modules, paths = _bridge_input_paths(reference_libraries, minecraft_version)
        _check_recorded_inputs(modules, paths, report.get("inputsSHA256"))
        return {"minecraftVersion": DEFAULT_VERSION, "lwjglVersion": expected,
                "modules": modules, "paths": paths, "legacy": True}
    if recorded_version is None or recorded_lwjgl is None:
        raise ValueError("LWJGL 桥的版本字段不完整；请重新构建")
    modules, paths = _bridge_input_paths(reference_libraries, minecraft_version)
    _check_recorded_inputs(modules, paths, report.get("inputsSHA256"))
    return {"minecraftVersion": minecraft_version, "lwjglVersion": expected,
            "modules": modules, "paths": paths, "legacy": False}


def opengl_shim_selection(directory: Path, reference_libraries,
                          minecraft_version=DEFAULT_VERSION) -> Path:
    """Validate an OpenGL shim report against the selected Minecraft line.

    The shim is generated from the OpenGL jar, so the same legacy rule applies:
    a report without version fields is only the default 26.1.2 shim and only
    when its recorded jar hash is the pinned 3.4.1 OpenGL jar.
    """
    directory = Path(directory).resolve(strict=True)
    report = json.loads((directory / "result.json").read_text())
    if report.get("status") != "LWJGL_OPENGL_SHIM_BUILT":
        raise ValueError("A successfully built LWJGL OpenGL shim is required")
    expected = expected_lwjgl_version(minecraft_version)
    recorded_version = report.get("minecraftVersion")
    recorded_lwjgl = report.get("lwjglVersion")
    if recorded_version is not None and recorded_version != minecraft_version:
        raise BridgeVersionMismatch(
            "LWJGL OpenGL 桥属于 Minecraft " + repr(recorded_version) + "，当前选择 " +
            minecraft_version + "；请重新构建")
    if recorded_lwjgl is not None and recorded_lwjgl != expected:
        raise BridgeVersionMismatch(
            "LWJGL OpenGL 桥版本 " + repr(recorded_lwjgl) + " 与 Minecraft " +
            minecraft_version + " 需要的 " + expected + " 不一致；请重新构建")
    if recorded_version is None and recorded_lwjgl is None:
        if minecraft_version != DEFAULT_VERSION:
            raise BridgeVersionMismatch(
                "LWJGL OpenGL 桥没有记录 minecraftVersion，只能用于 " + DEFAULT_VERSION +
                "；请为 Minecraft " + minecraft_version + " 重新构建")
    else:
        if recorded_version is None or recorded_lwjgl is None:
            raise ValueError("LWJGL OpenGL 桥的版本字段不完整；请重新构建")
    entry = select_lwjgl_opengl(reference_libraries, minecraft_version)
    jar = Path(entry["path"])
    pinned = entry.get("sha256")
    if not isinstance(pinned, str) or not jar.is_file() or sha256(jar) != pinned:
        raise ValueError("prepared LWJGL OpenGL 输入已改变：" + str(jar))
    recorded_jar = report.get("lwjglOpenGLJar")
    recorded_hash = report.get("lwjglOpenGLJarSHA256")
    if not isinstance(recorded_jar, str) or Path(recorded_jar).name != jar.name:
        raise ValueError("LWJGL OpenGL 桥记录的 JAR 与 prepared 选中的 " + jar.name + " 不一致")
    if recorded_hash != pinned:
        raise ValueError("LWJGL OpenGL 桥记录的 JAR 哈希与 prepared 不一致：" + str(jar))
    return jar


def validate_bridge_libraries(directory: Path, reference_libraries,
                              minecraft_version=DEFAULT_VERSION) -> list[Path]:
    """Bind a built bridge to the prepared LWJGL inputs it was generated from.

    Returns the four original prepared module jars so the caller can drop them
    from the classpath. The recorded inputsSHA256 must equal exactly those four
    hashes; anything else is a mixed or stale bridge and is rejected. Versionless
    reports are accepted only as the default line with those exact pinned inputs.
    """
    return bridge_selection(directory, reference_libraries, minecraft_version)["paths"]


def bridge_overlays(directory: Path) -> list[Path]:
    directory=directory.resolve(strict=True)
    report=json.loads((directory/"result.json").read_text())
    if report.get("status")!="STATIC_JNI_BRIDGE_BUILT":
        raise ValueError("A successfully built static JNI bridge is required")
    paths=[directory/"lwjgl-ios.jar",directory/"lwjgl-glfw-ios.jar"]
    for name in ('lwjgl-stb-ios.jar','lwjgl-openal-ios.jar'):
        if str(directory/name) in report["overlaysSHA256"]:paths.append(directory/name)
    for path in paths:
        if sha256(path)!=report["overlaysSHA256"].get(str(path)):
            raise ValueError("Modified graphics overlay: "+str(path))
    return paths

def verify_gl41_bridge(directory: Path) -> None:
    # Historical overlays can have valid artifact hashes but lack the iOS OpenGL defaults.
    # Platform.IOS then always throws during GL initialization and AOT prunes the game loop.
    report=json.loads((directory / "result.json").read_text())
    for name in ("compiler/lwjgl-lite/Overlay.java",
                 "compiler/lwjgl-lite/src/org/lwjgl/system/Library.java"):
        if report.get("sourcesSHA256", {}).get(name) != sha256(ROOT / name):
            raise ValueError("GL41 LWJGL overlay predates the current iOS adaptation; rebuild LWJGL: " + name)

def register_tree(path: Path, command: list[str], *, regenerable: bool = True, kind: str = "BUILD", recursive: bool = True) -> dict:
    if os.environ.get("AAR_HOME"):
        raise RuntimeError("AAR_HOME must be unset: the central registry is mandatory")
    aar = shutil.which("aar") or "aar"
    argv = [aar,"register","--path",str(path.resolve()),"--project",str(ROOT),"--type",kind,
            "--source","First-party graphics build; source and command fingerprints in result.json",
            "--argv-json",json.dumps(command),"--json"]
    if recursive:argv.append("--include-files")
    if regenerable: argv.append("--regenerable")
    try:
        result = subprocess.run(argv,capture_output=True,text=True,timeout=120)
    except FileNotFoundError:
        # Artifact discovery is separate from compilation and package validation.
        # Retain the exact registration request for replay when AAR is restored.
        pending = {"status": "REGISTRATION_PENDING", "reason": "AAR executable unavailable",
                   "argv": argv}
        ((path if path.is_dir() else path.parent) / ("registration-pending.json" if path.is_dir() else path.name + ".registration-pending.json")).write_text(json.dumps(pending, indent=2) + "\n")
        return pending
    if result.returncode:
        raise RuntimeError(f"Central AAR registration failed ({result.returncode}): {result.stderr or result.stdout}")
    return json.loads(result.stdout)
