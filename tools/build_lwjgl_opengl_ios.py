#!/usr/bin/env python3
"""Build the iOS LWJGL OpenGL JNI shim that forwards to Mesa OSMesa.

The shim is generated from the real LWJGL OpenGL jar: every native method becomes a fixed
JNI entry point with the GL ABI taken from its verifier descriptor. No runtime code
generation, no libffi and no executable allocation are used.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path

from bootstrap_toolchain import sha256
from capture import ROOT, capture
from graphics_common import (expected_lwjgl_version, lwjgl_jar, owned_client_references,
                             register_tree, select_lwjgl_opengl)
from game_versions import DEFAULT_VERSION

JTYPES = {"V": "void", "Z": "jboolean", "B": "jbyte", "C": "jchar",
          "S": "jshort", "I": "jint", "J": "jlong", "F": "jfloat", "D": "jdouble"}
CTYPES = {"V": "void", "Z": "uint8_t", "B": "int8_t", "C": "uint16_t",
          "S": "int16_t", "I": "int32_t", "J": "uintptr_t", "F": "float", "D": "double"}
SKIP_PREFIXES = ("org.lwjgl.opengl.WGL", "org.lwjgl.opengl.GLX", "org.lwjgl.opengl.CGL")


def mangle(value: str) -> str:
    return "".join({"_": "_1", ";": "_2", "[": "_3", "/": "_"}.get(c, c if c.isascii() and c.isalnum() else "_0%04x" % ord(c)) for c in value)


def descriptor_parts(desc: str) -> tuple[str, str]:
    match = re.fullmatch(r"\(([ZBCSIJFD]*)\)([VZBCSIJFD])", desc)
    if not match:
        raise ValueError("OpenGL native descriptor must use primitive JVM types only: " + desc)
    return match.group(1), match.group(2)


def javap_natives(java_home: Path, jar: Path, classes: list[str]) -> dict[str, list[tuple[str, str]]]:
    output = subprocess.check_output(
        [str(java_home / "bin" / "javap"), "-p", "-s", "-cp", str(jar), *classes], text=True)
    result: dict[str, list[tuple[str, str]]] = {}
    current = None
    lines = output.splitlines()
    for i, line in enumerate(lines):
        declaration = re.match(r"(?:public |protected |private )?(?:final )?(?:abstract )?(?:class|interface|enum) ([\w.$]+)", line)
        if declaration:
            current = declaration.group(1)
            result.setdefault(current, [])
            continue
        native = re.search(r"\bnative\s+\S+\s+(\w+)\([^)]*\);", line)
        if not native or current is None:
            continue
        for follow in lines[i + 1:i + 4]:
            found = re.search(r"descriptor:\s*(\S+)", follow)
            if found:
                result[current].append((native.group(1), found.group(1)))
                break
        else:
            raise ValueError("missing descriptor for " + current + "." + native.group(1))
    return result


def generate(jar: Path, java_home: Path, available: set[str] | None = None) -> tuple[str, list[dict]]:
    with zipfile.ZipFile(jar) as archive:
        classes = sorted(
            name[:-6].replace("/", ".") for name in archive.namelist()
            if name.startswith("org/lwjgl/opengl/") and name.endswith(".class") and "$" not in name
        )
    if not classes:
        raise ValueError("LWJGL OpenGL jar contains no classes")
    selected = [name for name in classes if not name.startswith(SKIP_PREFIXES)]
    natives = javap_natives(java_home, jar, selected)
    lines = ["/* Generated from the verified LWJGL OpenGL jar. No runtime code generation. */",
             "#include <jni.h>", "#include <stdint.h>", ""]
    records = []
    declarations = set()
    bodies = []
    for owner, methods in sorted(natives.items()):
        for name, desc in methods:
            args, ret = descriptor_parts(desc)
            gl_name = name[1:] if name.startswith("ngl") else name
            if not gl_name.startswith("gl"):
                raise ValueError("native is not a GL entry: " + owner + "." + name)
            c_ret = CTYPES[ret]
            c_args = [CTYPES[t] for t in args]
            present = available is None or gl_name in available
            if present:
                prototype = "extern %s %s(%s);" % (c_ret, gl_name, ", ".join(c_args) if c_args else "void")
                if prototype not in declarations:
                    declarations.add(prototype)
            jni = "Java_" + mangle(owner.replace(".", "/")) + "_" + mangle(name) + "__" + mangle(args)
            params = ["JNIEnv *e", "jclass c"]
            params += ["%s a%d" % (JTYPES[t], i) for i, t in enumerate(args)]
            call_args = ", ".join("(%s)a%d" % (CTYPES[t], i) for i, t in enumerate(args))
            body = ["JNIEXPORT %s JNICALL %s(%s) {" % (JTYPES[ret], jni, ", ".join(params)),
                    "    (void)e; (void)c;"]
            if not present:
                for i in range(len(args)):
                    body.append("    (void)a%d;" % i)
                body.append('    jclass error = (*e)->FindClass(e, "java/lang/UnsatisfiedLinkError");')
                body.append('    if (error) (*e)->ThrowNew(e, error, "OpenGL entry unavailable in OSMesa: %s");' % gl_name)
                if ret != "V":
                    body.append("    return 0;")
            elif ret == "V":
                body.append("    %s(%s);" % (gl_name, call_args))
            else:
                body.append("    return (%s)%s(%s);" % (JTYPES[ret], gl_name, call_args))
            body.append("}")
            bodies.append("\n".join(body))
            records.append({"class": owner, "method": name, "descriptor": desc,
                            "gl": gl_name, "jni": jni, "available": present})
    lines.extend(sorted(declarations))
    lines.append("")
    lines.extend(bodies)
    return "\n".join(lines) + "\n", records


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lwjgl-opengl", type=Path, required=True)
    parser.add_argument("--osmesa", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--java-home", type=Path, required=True)
    parser.add_argument("--prepared", type=Path,
                        help="prepared client directory whose referenceLibraries pin the OpenGL jar")
    parser.add_argument("--owned-client", type=Path,
                        help="verified owned-client directory; use instead of --prepared")
    parser.add_argument("--minecraft-version", default=None,
                        help="selected Minecraft line; defaults to the prepared record or " + DEFAULT_VERSION)
    parser.add_argument("--sdk", choices=("iphoneos", "iphonesimulator"), default="iphoneos")
    args = parser.parse_args(argv)
    if args.prepared is not None and args.owned_client is not None:
        parser.error("choose either --prepared or --owned-client, not both")
    jar = args.lwjgl_opengl.resolve(strict=True)
    osmesa = args.osmesa.resolve(strict=True)
    prepared = None
    references = None
    if args.prepared is not None:
        prepared = json.loads((args.prepared.resolve(strict=True) / "result.json").read_text())
        references = prepared.get("referenceLibraries")
    minecraft_version = args.minecraft_version or (
        prepared.get("minecraftVersion", DEFAULT_VERSION) if prepared else DEFAULT_VERSION)
    if args.owned_client is not None:
        try:
            references = owned_client_references(args.owned_client, minecraft_version)
        except (OSError, ValueError) as error:
            parser.error(str(error))
    expected = expected_lwjgl_version(minecraft_version)
    parsed = lwjgl_jar(jar)
    if parsed is None or parsed[0] != "lwjgl-opengl" or parsed[2]:
        parser.error("--lwjgl-opengl must be the non-native lwjgl-opengl jar: " + jar.name)
    if parsed[1] != expected:
        parser.error("Minecraft " + minecraft_version + " needs LWJGL " + expected +
                     " OpenGL, got " + parsed[1])
    if references is not None:
        entry = select_lwjgl_opengl(references, minecraft_version)
        if Path(entry["path"]).name != jar.name or entry.get("sha256") != sha256(jar):
            parser.error("--lwjgl-opengl is not the hash-pinned prepared OpenGL jar")
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    home = args.java_home.resolve(strict=True)
    exports = subprocess.check_output(["xcrun", "nm", "-gUj", str(osmesa)], text=True)
    available = {line.strip().removeprefix("_") for line in exports.splitlines() if line.strip()}
    source, records = generate(jar, home, available)
    (out / "jni_opengl.c").write_text(source)
    target = "arm64-apple-ios17.0" + ("-simulator" if args.sdk == "iphonesimulator" else "")
    binary = out / "liblwjgl_opengl.dylib"
    command = ["xcrun", "--sdk", args.sdk, "clang", "-target", target, "-std=c11", "-O2",
               "-fno-common", "-Wall", "-Wextra", "-Werror",
               "-I", str(home / "include"), "-I", str(home / "include/darwin"),
               "-dynamiclib", str(out / "jni_opengl.c"), str(osmesa),
               "-install_name", "@rpath/liblwjgl_opengl.dylib", "-o", str(binary)]
    built = capture(command, out / "compile", 900)
    result = {"status": "BLOCKED", "target": target, "nativeMethods": len(records),
              "availableMethods": sum(1 for row in records if row["available"]),
              "unavailableMethods": sorted(row["gl"] for row in records if not row["available"]),
              "scope": "JNI forwarding to Mesa OSMesa; no game execution or device acceptance",
              "minecraftVersion": minecraft_version, "lwjglVersion": expected,
              "prepared": str(Path(args.prepared).resolve()) if args.prepared else None,
              "ownedClient": str(Path(args.owned_client).resolve()) if args.owned_client else None,
              "lwjglOpenGLJar": str(jar), "lwjglOpenGLJarSHA256": sha256(jar),
              "osmesa": str(osmesa), "osmesaSHA256": sha256(osmesa),
              "sourcesSHA256": {str(Path(__file__).relative_to(ROOT)): sha256(Path(__file__))}}
    if built["exitCode"] == 0:
        exports = subprocess.check_output(["xcrun", "nm", "-gUj", str(binary)], text=True)
        exported = {line.strip().removeprefix("_") for line in exports.splitlines() if line.strip()}
        missing = sorted(row["jni"] for row in records if row["jni"] not in exported)
        if missing:
            raise RuntimeError("Generated JNI entry missing from binary: " + missing[0])
        result.update(status="LWJGL_OPENGL_SHIM_BUILT", binarySHA256=sha256(binary),
                      binaryBytes=binary.stat().st_size, exports=len(records))
    (out / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    register_tree(out, [sys.executable, "-B", str(Path(__file__).resolve()),
                        *(sys.argv[1:] if argv is None else argv)])
    print(json.dumps({k: v for k, v in result.items() if k != "sourcesSHA256"}, indent=2))
    return 0 if result["status"] == "LWJGL_OPENGL_SHIM_BUILT" else 2


if __name__ == "__main__":
    raise SystemExit(main())
