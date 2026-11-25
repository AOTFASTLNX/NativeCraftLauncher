#!/usr/bin/env python3
"""Build the UIKit/CAMetalLayer GLFW subset for iOS; optionally run the C state fixture."""
from __future__ import annotations
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from bootstrap_toolchain import sha256
from capture import ROOT, capture
from storage_config import cache_path
from graphics_common import register_tree
from glfw_runtime_contract import validate_glfw_renderer
from inventory_client_native_calls import glfw_symbols
from mod_file_policy import is_metadata

# Mod polling is absent from the vanilla client call inventory. These functions
# are required by malilib's per-tick key recheck as well as direct game input.
INPUT_POLL_EXPORTS = frozenset({
    "glfwGetMouseButton", "glfwGetKey", "glfwGetCursorPos",
})
# TouchController's iOS transport resolves these from the GLFW dylib. A build without them
# links, starts and then fails on the first Mod frame, so the contract is checked here.
TOUCH_EXPORTS = frozenset({
    "faot_touch_begin_frame", "faot_touch_receive", "faot_touch_command",
})

# Drives the queue through the events a first frame produces and writes the encoded bytes, so
# the Mod's own decoder can read them. Kept here rather than in the fixture because that file
# asserts in place and must stay free of file output.
TOUCH_DUMP_SOURCE = r"""
#include <stdio.h>
#include "touch_input.h"

int main(int argc, char **argv) {
    if (argc != 2) return 2;
    FILE *out = fopen(argv[1], "wb");
    if (!out) return 3;
    unsigned char bytes[256];
    int length;
    faot_touch_snapshot(400, 800, 1);
    int first = faot_touch_start(0.25f, 0.75f);
    int second = faot_touch_start(0.5f, 0.2f);
    faot_touch_move(first, 0.6f, 0.3f);
    faot_touch_end(second);
    faot_touch_snapshot(400, 800, 1);
    while ((length = faot_touch_receive(bytes, sizeof bytes)) > 0) {
        fwrite(bytes, 1, (size_t)length, out);
    }
    faot_touch_clear();
    while ((length = faot_touch_receive(bytes, sizeof bytes)) > 0) {
        fwrite(bytes, 1, (size_t)length, out);
    }
    fclose(out);
    return 0;
}
"""

# Generated evidence is not part of a packaged source tree, so the caller
# (workbench or CLI) supplies either the client JAR or an inventory that was
# produced from it.
DEFAULT_INVENTORY = ROOT / "artifacts/evidence/client-native-call-inventory.json"


def load_inventory(path: Path) -> dict:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(
            "client native call inventory missing: " + str(path) +
            "; pass --client-inventory with the inventory generated for this client")
    return json.loads(path.read_text())

def validate_input_exports(symbols: set[str]) -> None:
    missing = (INPUT_POLL_EXPORTS | TOUCH_EXPORTS) - symbols
    if missing:
        raise ValueError("GLFW input/touch exports missing; rebuild GLFW: " + ", ".join(sorted(missing)))

def exported_symbols(text: str) -> set[str]:
    return {line.split()[-1].removeprefix("_") for line in text.splitlines() if line.split()}

def required_symbols(inventory: dict) -> set[str]:
    # Java structure accessors and Callback.free are NOT C exports. Their ABI is in lwjgl core.
    return set(INPUT_POLL_EXPORTS) | {m["name"] for m in inventory["methods"] if m["owner"] in
            ("org/lwjgl/glfw/GLFW","org/lwjgl/glfw/GLFWNativeCocoa") and m["name"].startswith("glfw")}


def required_symbols_for_client(client: Path) -> set[str]:
    client = Path(client)
    if not client.is_file():
        raise FileNotFoundError("client JAR missing: " + str(client))
    return set(INPUT_POLL_EXPORTS) | glfw_symbols(client)

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--sdk",choices=("iphoneos","iphonesimulator"),default="iphonesimulator")
    parser.add_argument("--client",type=Path,help="client JAR the GLFW subset must satisfy")
    parser.add_argument("--client-inventory",type=Path,
                        help="client-native-call-inventory.json generated from the client being built")
    parser.add_argument("--test-host",action="store_true")
    parser.add_argument("--touch-java-classpath",
                        help="LWJGL jars needed by the Java touch-host probe; "
                             "omit to skip that probe")
    args=parser.parse_args(argv)
    if args.client:
        client=Path(args.client).resolve()
        required=required_symbols_for_client(client)
        inputs={"client":str(client),"clientSHA256":sha256(client)}
    else:
        inventory_path=Path(args.client_inventory or DEFAULT_INVENTORY).resolve()
        required=required_symbols(load_inventory(inventory_path))
        inputs={"clientInventory":str(inventory_path),"clientInventorySHA256":sha256(inventory_path)}
    output=args.output.resolve();output.mkdir(parents=True,exist_ok=False)
    source=ROOT/"ios/glfw-lite"
    target="arm64-apple-ios17.0"+("-simulator" if args.sdk=="iphonesimulator" else "")
    binary=output/"libglfw.dylib"
    cmd=["xcrun","--sdk",args.sdk,"clang","-target",target,"-std=c11","-fobjc-arc","-O2",
         "-Wall","-Wextra","-Werror","-Wno-deprecated-declarations","-dynamiclib",
         str(source/"glfw_lite.c"),str(source/"touch_input.c"),str(source/"uikit_surface.m"),str(source/"osmesa_gl.c"),"-framework","UIKit","-framework","Foundation",
         "-framework","QuartzCore","-framework","CoreGraphics","-framework","Metal","-framework","GameController","-install_name","@rpath/libglfw.dylib","-o",str(binary)]
    result={"status":"BLOCKED","gateC0":"BLOCKED","target":target,
            "scope":"GLFW native subset only; no Java bridge or Minecraft renderer acceptance",
            **inputs,
            "sourcesSHA256":{str(p.relative_to(ROOT)):sha256(p) for p in source.iterdir()
                             if p.is_file() and not is_metadata(p.name)}}
    build=capture(cmd,output/"compile",180);result["compile"]=build
    if build["exitCode"]==0:
        symbols=subprocess.check_output(["xcrun","nm","-gUj",str(binary)],text=True)
        (output/"exports.txt").write_text(symbols)
        result["requiredCExports"]=sorted(required)
        result["missingCExports"]=sorted(required-exported_symbols(symbols))
        platform=subprocess.check_output(["xcrun","vtool","-show-build",str(binary)],text=True)
        (output/"platform.txt").write_text(platform)
        expected="IOSSIMULATOR" if args.sdk=="iphonesimulator" else "IOS"
        if f"platform {expected}\n" in platform and not result["missingCExports"]:
            result['rendererABI'] = validate_glfw_renderer(binary, 'gl41')
            result['supportedClientAPIs'] = ['NO_API', 'OPENGL_OSMESA']
            result["status"]="NATIVE_SUBSET_BUILT"
        result["binarySHA256"]=sha256(binary)
    if args.test_host:
        fixture=output/"glfw-state-test"
        host=capture(["xcrun","clang","-std=c11","-Wall","-Wextra","-Werror","-g","-fsanitize=address,undefined",
                      "-I",str(source),str(source/"glfw_lite.c"),str(source/"touch_input.c"),str(source/"osmesa_gl.c"),
                      str(source/"osmesa_present_host_stub.c"),str(ROOT/"interop/graphics/glfw_state_test.c"),
                      "-o",str(fixture)],output/"fixture-compile",120)
        result["fixtureCompile"]=host
        if host["exitCode"]==0:
            tested=capture([str(fixture)],output/"fixture-run",30);result["fixtureRun"]=tested
            result["hostStateFixture"]="PASS" if tested["exitCode"]==0 and "GLFW_STATE_PASS" in (output/"fixture-run/stdout.log").read_text() else "FAIL"
        else: result["hostStateFixture"]="FAIL"
        touch=output/"touch-input-test"
        touch_build=capture(["xcrun","clang","-std=c11","-Wall","-Wextra","-Werror","-g",
                      "-fsanitize=address,undefined","-I",str(source),str(source/"touch_input.c"),
                      str(ROOT/"interop/graphics/touch_input_test.c"),"-o",str(touch)],output/"touch-fixture-compile",120)
        result["touchFixtureCompile"]=touch_build
        if touch_build["exitCode"]==0:
            tested=capture([str(touch)],output/"touch-fixture-run",60);result["touchFixtureRun"]=tested
            result["touchFixture"]="PASS" if tested["exitCode"]==0 and "TOUCH_INPUT_PASS" in (output/"touch-fixture-run/stdout.log").read_text() else "FAIL"
        else: result["touchFixture"]="FAIL"
        # The fixture above proves the bytes the queue emits. Decoding them with the Mod's own
        # message classes proves the other half of the contract, which is the part a wrong
        # length or byte order would break silently on the device.
        result["touchWireProbe"]="SKIPPED"
        if touch_build["exitCode"]==0 and args.client and args.test_host:
            wire=output/"touch-wire"
            wire.mkdir()
            dump=wire/"dump.c"
            dump.write_text(TOUCH_DUMP_SOURCE)
            built=capture(["xcrun","clang","-std=c11","-I",str(source),str(dump),
                           str(source/"touch_input.c"),"-o",str(wire/"dump")],wire/"compile",120)
            if built["exitCode"]==0:
                stream=wire/"stream.bin"
                capture([str(wire/"dump"),str(stream)],wire/"run",60)
                if stream.is_file():
                    java_home=cache_path('toolchains/graalvm-25.0.2/'
                                         'graalvm-community-openjdk-25.0.2+10.1/Contents/Home')
                    classes=wire/"classes"
                    compile_probe=capture([str(java_home/"bin/javac"),"-proc:none","-cp",str(args.client),
                                           "-d",str(classes),str(ROOT/"interop/graphics/TouchWireProbe.java")],
                                          wire/"javac",120)
                    if compile_probe["exitCode"]==0:
                        decoded=capture([str(java_home/"bin/java"),"-cp",
                                         str(classes)+os.pathsep+str(args.client),"TouchWireProbe",str(stream)],
                                        wire/"probe",120)
                        expected=["ADD index=1 x=0.600 y=0.300","ADD index=2 x=0.500 y=0.200",
                                  "REMOVE index=2","CLEAR","messages=4 consumed=44/44"]
                        lines=(wire/"probe"/"stdout.log").read_text().splitlines()
                        result["touchWireProbe"]="PASS" if decoded["exitCode"]==0 and lines==expected else "FAIL"
                    # The bridge's Java half has its own failure mode: an init that never
                    # installs a host is pruned by the closed-world image and input simply
                    # never arrives. The probe drives install, handshake and reply routing.
                    result["touchHostProbe"]="SKIPPED"
                    overlay=ROOT/"interop/fabric/tool/mcfao/aot"
                    host_classes=wire/"host-classes"
                    if args.touch_java_classpath:
                        host_cp=os.pathsep.join([str(args.client),args.touch_java_classpath])
                        host_compile=capture([str(java_home/"bin/javac"),"-proc:none","-cp",host_cp,
                                              "-d",str(host_classes),
                                              str(overlay/"IosTransport.java"),str(overlay/"TouchHost.java"),
                                              str(ROOT/"interop/graphics/TouchHostProbe.java")],wire/"host-javac",180)
                        if host_compile["exitCode"]==0:
                            host_run=capture([str(java_home/"bin/java"),"-cp",
                                              str(host_classes)+os.pathsep+host_cp,
                                              "mcfao.aot.TouchHostProbe"],wire/"host-probe",120)
                            result["touchHostProbe"]="PASS" if host_run["exitCode"]==0 and \
                                "TOUCH_HOST_PASS" in (wire/"host-probe"/"stdout.log").read_text() else "FAIL"
    (output/"result.json").write_text(json.dumps(result,indent=2)+"\n")
    command=[sys.executable,"-B",str(Path(__file__).resolve()),*(sys.argv[1:] if argv is None else argv)]
    try: register_tree(output,command)
    except (OSError,RuntimeError) as exc:
        print(str(exc),file=sys.stderr);return 1
    print(json.dumps({k:v for k,v in result.items() if k not in ("compile","fixtureCompile","fixtureRun")},indent=2))
    return 0 if (result["status"]=="NATIVE_SUBSET_BUILT" and result.get("hostStateFixture")!="FAIL"
                 and result.get("touchFixture")!="FAIL") else 2

if __name__=="__main__":raise SystemExit(main())
