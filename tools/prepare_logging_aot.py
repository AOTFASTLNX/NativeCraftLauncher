"""Statically adapt the audited Log4j service lookup without executing library code."""
import hashlib
import json
import os
from pathlib import Path
import zipfile

from bootstrap_toolchain import sha256
from capture import ROOT, capture

ENTRY = "org/apache/logging/log4j/util/ServiceLoaderUtil.class"
AUDITED_SHA256 = "f17a1b460774ba05b90fb62651f2e220ede4873968ba3a2c298695d5ac0f64d1"
SOURCE = ROOT / "compiler/logging-tools/src/fabricaot/logging/PatchServiceLoader.java"


def prepare_logging_aot(libraries, output, home, engine_libraries):
    """Return a copied classpath with only Log4j 2.22.1's audited class replaced."""
    selected = [Path(p) for p in libraries if Path(p).name == "log4j-api-2.22.1.jar"]
    if not selected:
        return list(libraries), {"status": "NOT_REQUIRED"}
    if len(selected) != 1:
        raise ValueError("Ambiguous Log4j 2.22.1 input")
    source = selected[0]
    with zipfile.ZipFile(source) as jar:
        original = jar.read(ENTRY)
        if hashlib.sha256(original).hexdigest() != AUDITED_SHA256:
            raise ValueError("Log4j service lookup changed; audit required")
        if any(n.upper().endswith((".SF", ".RSA", ".DSA", ".EC")) for n in jar.namelist()
               if n.upper().startswith("META-INF/")):
            raise ValueError("Signed Log4j input requires signature-aware adaptation")
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
                       "fabricaot.logging.PatchServiceLoader", before, after]),
    ):
        result = capture(list(map(str, command)), output / name, 90)
        if result["exitCode"] or result["timedOut"]:
            raise RuntimeError(f"Log4j {name} failed: {output / name}")
    target = output / source.name
    with zipfile.ZipFile(source) as jar, zipfile.ZipFile(target, "w") as patched:
        for entry in jar.infolist():
            patched.writestr(entry, after.read_bytes() if entry.filename == ENTRY else jar.read(entry))
    result = {"status": "LOG4J_SERVICE_LOOKUP_PREPARED", "input": str(source),
              "inputSHA256": sha256(source), "classSHA256": AUDITED_SHA256,
              "sourceSHA256": sha256(SOURCE), "outputSHA256": sha256(target),
              "changedEntries": [ENTRY], "scope": "static bytecode adaptation; no library initialization"}
    (output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    return [target if Path(p) == source else p for p in libraries], result
