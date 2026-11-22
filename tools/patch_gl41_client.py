#!/usr/bin/env python3
"""Prepare the GL41 client and add world-join diagnostics without executing it."""
from __future__ import annotations
from storage_config import cache_path

import argparse
import json
import os
import sys
import zipfile
from pathlib import Path

from bootstrap_toolchain import sha256
from build_method_splitter import java_home
from capture import ROOT, capture, redact_log
from graphics_common import register_tree
from game_versions import DEFAULT_VERSION, SUPPORTED_VERSIONS


def report_failure(label, log, result):
    print(f"error: {label} (exit {result['exitCode']}, timeout={result['timedOut']}). Logs: {log}",
          file=sys.stderr)
    stderr = (log / "stderr.log").read_text(errors="replace").splitlines()
    # Keep the Java exception visible even if the command emitted earlier warnings.
    cause = next((line for line in stderr if line.startswith('Exception in thread ')), None)
    if cause is None:
        cause = "\n".join(line for line in stderr if line.strip()).strip()[-2000:]
    if cause:
        print(redact_log(cause[:2000]), file=sys.stderr)
    return 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--version", choices=SUPPORTED_VERSIONS, default=DEFAULT_VERSION)
    parser.add_argument("--graalvm-home", type=Path)
    parser.add_argument("--asm-jars", type=Path, nargs=2, metavar=("ASM_CORE", "ASM_TREE"))
    args = parser.parse_args(argv)
    client = args.client.resolve(strict=True)
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    home = args.graalvm_home.resolve(strict=True) if args.graalvm_home else java_home()
    asm = [path.resolve(strict=True) for path in (args.asm_jars or (cache_path('libs/asm-9.9/asm-9.9.jar'), cache_path('libs/asm-9.9/asm-tree-9.9.jar')))]
    classes = out / "classes"
    classes.mkdir()
    source = ROOT / "compiler/gl41/tools/fabricaot/gl41/PatchDevice.java"
    shared = ROOT / "compiler/metal-backend/tools/fabricaot"
    frame_pacing = ROOT / "compiler/gl41/tools/fabricaot/gl41/FramePacing.java"
    sources = [source, frame_pacing, shared / "metal/InjectBackend.java",
               shared / "diag/PlayDiagnostics.java"]
    if args.version == "1.21.1":
        sources.append(source.with_name("PatchDevice1211.java"))
    classpath = os.pathsep.join(map(str, asm))
    compiled = capture([str(home / "bin/javac"), "--release", "25", "-proc:none", "-cp", classpath,
                        "-d", str(classes), *map(str, sources)], out / "compile", 120)
    if compiled["exitCode"] or compiled["timedOut"]:
        return report_failure("GL41 patcher compilation failed", out / "compile", compiled)
    target = out / "client.jar"
    run = capture([str(home / "bin/java"), "-cp", str(classes) + os.pathsep + classpath,
                   "fabricaot.gl41." + ("PatchDevice1211" if args.version == "1.21.1" else "PatchDevice"),
                   str(client), str(target), str(classes)], out / "transform", 120)
    if run["exitCode"] or run["timedOut"]:
        return report_failure("GL41 client patch failed", out / "transform", run)
    marker = "GL41_1211_PROFILE_APPLIED" if args.version == "1.21.1" else "GL41_DIRECT_STATE_DISABLED"
    if marker not in (out / "transform/stdout.log").read_text().splitlines():
        return report_failure("GL41 client patch did not report success", out / "transform", run)
    with zipfile.ZipFile(client) as before, zipfile.ZipFile(target) as after:
        names_before = before.namelist()
        names_after = after.namelist()
        helpers = ["fabricaot/diag/PlayDiagnostics.class", "fabricaot/gl41/FramePacing.class"]
        if names_after != [*names_before, *helpers]:
            raise ValueError("Unexpected added or removed GL41 client entries")
        changed = [name for name in names_before if before.read(name) != after.read(name)]
        # Per-entry signatures no longer describe transformed classes. Keep main attributes only;
        # a multi-megabyte signed manifest otherwise makes Native Image rescan it per resource.
        changed_classes = [name for name in changed if name != "META-INF/MANIFEST.MF"]
        expected = ["com/mojang/blaze3d/opengl/GlDevice.class",
                    "com/mojang/blaze3d/platform/GLX.class",
                    "net/minecraft/SystemReport.class",
                    "net/minecraft/client/multiplayer/LevelLoadTracker$WaitingForPlayerChunk.class",
                    "net/minecraft/client/Minecraft.class",
                    "net/minecraft/client/multiplayer/ClientPacketListener.class",
                    "net/minecraft/client/multiplayer/LevelLoadTracker.class",
                    "net/minecraft/client/multiplayer/ClientChunkCache.class",
                    "net/minecraft/client/renderer/DynamicUniforms.class",
                    "net/minecraft/network/Connection.class"]
        iris_entity = "net/irisshaders/iris/pipeline/transform/transformer/EntityPatcher.class"
        if args.version == "1.21.1":
            expected = ["com/mojang/blaze3d/platform/GLX.class", "net/minecraft/SystemReport.class",
                        "net/minecraft/client/main/Main.class",
                        "net/minecraft/util/profiling/jfr/JvmProfiler.class",
                        "net/minecraft/client/Minecraft.class",
                        "net/minecraft/client/multiplayer/ClientPacketListener.class",
                        "net/minecraft/client/multiplayer/ClientChunkCache.class",
                        "net/minecraft/network/Connection.class"]
        elif iris_entity in names_before:
            expected.append(iris_entity)
        if sorted(changed_classes) != sorted(expected):
            raise ValueError("Unexpected patched entries: " + repr(changed))
    result = {"status": "GL41_CLIENT_PATCHED", "minecraftVersion": args.version,
              "sourcesSHA256": {str(p.relative_to(ROOT)): sha256(p) for p in sources},
              "client": str(client), "clientSHA256": sha256(client),
              "outputJar": str(target), "outputJarSHA256": sha256(target),
              "changedEntries": changed, "directStateAccess": "disabled for the 4.1 OSMesa surface",
              "worldJoin": "preserve vanilla readiness and chat signature checks; trace client loading",
              "framePacing": "fixed-deadline 120 FPS limiter; overdue frames do not accumulate",
              "uniformBuffers": "pre-size dynamic transform and chunk-section buffers to avoid frame hitches while loading terrain",
              "irisEntityAttribute": ("unsigned USHORT4 input with exact signed-ID conversion"
                                      if iris_entity in names_before else "Iris entity patcher absent"),
              "scope": "build-time bytecode adaptation; no game execution"}
    if args.version == "1.21.1":
        result.update(directStateAccess="vanilla 1.21.1 legacy OpenGL path",
                      flightRecorder="Minecraft NoOpProfiler; iOS AOT has no JFR recorder",
                      worldJoin="short-circuit absent chat sessions and disabled secure-chat flags; preserve signature validation; trace packet handoffs and chunk decoding",
                      uniformBuffers="vanilla 1.21.1 allocation",
                      irisEntityAttribute="version-specific Iris patch not applied")
    (out / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    register_tree(out, [sys.executable, "-B", str(Path(__file__).resolve()),
                        *(sys.argv[1:] if argv is None else argv)])
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
