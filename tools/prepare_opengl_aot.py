"""Statically bind the audited LWJGL 3.3.3 OpenGL bootstrap to the iOS shim."""
import hashlib
import json
import os
from pathlib import Path
import zipfile

from bootstrap_toolchain import sha256
from capture import ROOT, capture

ENTRY = "org/lwjgl/opengl/GL.class"
AUDITED_SHA256 = "840202709ce57a1f54f9f4a0369dca1434576db430f0e9c1b6d4f940e65bb99c"
SOURCE = ROOT / "compiler/gl41/tools/fabricaot/gl41/PatchOpenGLStartup.java"


def prepare_opengl_aot(libraries, output, home, engine_libraries):
    """Replace only GL.create() in the exact audited upstream class, without loading it."""
    selected = [Path(p) for p in libraries if Path(p).name == "lwjgl-opengl-3.3.3.jar"]
    if not selected:
        return list(libraries), {"status": "NOT_REQUIRED"}
    if len(selected) != 1:
        raise ValueError("Ambiguous LWJGL OpenGL 3.3.3 input")
    source = selected[0]
    with zipfile.ZipFile(source) as jar:
        names = jar.namelist()
        if names.count(ENTRY) != 1:
            raise ValueError("Ambiguous or missing LWJGL OpenGL bootstrap class")
        original = jar.read(ENTRY)
        if hashlib.sha256(original).hexdigest() != AUDITED_SHA256:
            raise ValueError("LWJGL OpenGL bootstrap changed; audit required")
        if any(n.upper().endswith((".SF", ".RSA", ".DSA", ".EC"))
               or n.upper().startswith("META-INF/SIG-")
               for n in names if n.upper().startswith("META-INF/")):
            raise ValueError("Signed LWJGL input requires signature-aware adaptation")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    classes = output / "classes"
    classes.mkdir()
    before, after = output / "before.class", output / "after.class"
    before.write_bytes(original)
    asm = [Path(p) for p in engine_libraries if Path(p).name.startswith("asm-tree-")
           or (Path(p).name.startswith("asm-") and Path(p).name[4:5].isdigit())]
    if len(asm) != 2:
        raise ValueError("Pinned ASM core and tree inputs required")
    cp = os.pathsep.join(map(str, asm))
    for name, command in (
        ("compile", [home / "bin/javac", "-proc:none", "-cp", cp, "-d", classes, SOURCE]),
        ("transform", [home / "bin/java", "-cp", str(classes) + os.pathsep + cp,
                       "fabricaot.gl41.PatchOpenGLStartup", before, after]),
    ):
        result = capture(list(map(str, command)), output / name, 90)
        if result["exitCode"] or result["timedOut"]:
            raise RuntimeError(f"OpenGL {name} failed: {output / name}")
    target = output / source.name
    with zipfile.ZipFile(source) as jar, zipfile.ZipFile(target, "w") as patched:
        for entry in jar.infolist():
            patched.writestr(entry, after.read_bytes() if entry.filename == ENTRY else jar.read(entry))
    result = {"status": "LWJGL_OPENGL_STARTUP_PREPARED", "input": str(source),
              "inputSHA256": sha256(source), "classSHA256": AUDITED_SHA256,
              "sourceSHA256": sha256(SOURCE), "outputSHA256": sha256(target),
              "changedEntries": [ENTRY], "scope": "static iOS library selection; no library initialization"}
    (output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    return [target if Path(p) == source else p for p in libraries], result
