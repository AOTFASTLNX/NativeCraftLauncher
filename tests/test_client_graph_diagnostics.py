"""Audit real metadata shapes without executing JAR code or retaining all classes."""
import contextlib
import hashlib
import io
import json
import sys
import tempfile
import unittest
import unittest.mock
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"tools"))
from client_graph_diagnostics import audit_metadata, parse_time_rss, summarize_jfr, duration_seconds, summarize_gc_logs
import probe_client_graph


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _preflight_fixture(root):
    """Smallest prepared/runtime/caps tree that reaches the host-memory gate.

    Every hashed input is a real file whose digest is recorded in the manifest, so the
    probe's own verification passes and only the memory reading decides the outcome.
    """
    java = _write(root/"java/bin/java", "#!/bin/sh\nexit 0\n")
    java.chmod(0o755)
    _write(root/"java/bin/native-image", "#!/bin/sh\nexit 0\n")
    libraries = [_write(root/"libs/a.jar", "a"), _write(root/"libs/b.jar", "b")]
    driver = _write(root/"prepared/driver.jar", "driver")
    prepared = root/"prepared"
    _write(prepared/"result.json", json.dumps({
        "minecraftVersion": "26.1.2",
        "javaHome": str(root/"java"),
        "javaSHA256": _sha(java),
        "referenceLibraries": [{"path": str(p), "sha256": _sha(p)} for p in libraries],
        "binaries": {"driver.jar": {"sha256": _sha(driver)}}}))
    runtime = root/"runtime"
    _write(runtime/"result.json", json.dumps({"target": "arm64-apple-ios17.0", "status": "COMPILED", "archives": {}}))
    caps = root/"caps"
    caps.mkdir()
    evidence = _write(root/"evidence.json", json.dumps({"gateB1": "PASS"}))
    return prepared, runtime, caps, evidence


class ClientDiagnosticsTests(unittest.TestCase):
    def test_detects_broad_preservation_and_conditioned_bulk_registration(self):
        with tempfile.TemporaryDirectory() as directory:
            jar=Path(directory)/"fixture.jar"
            with zipfile.ZipFile(jar,"w") as archive:
                archive.writestr("META-INF/native-image/a/b/native-image.properties", "Args = -H:Preserve=all\n")
                archive.writestr("META-INF/native-image/a/b/reflect-config.json",json.dumps([
                    {"name":"example.A","allDeclaredMethods":True,"condition":{"typeReached":"example.Root"}},
                    {"name":"example.B","allPublicMethods":False}]))
            audit=audit_metadata([jar],[])
            self.assertEqual(len(audit["preserveAllOccurrences"]),1)
            group=next(x["groups"]["reflection"] for x in audit["metadataFiles"] if "groups" in x)
            self.assertEqual(group["entries"],2)
            self.assertEqual(group["unconditionalEntries"],1)
            self.assertTrue(group["bulkMemberFlags"][0]["conditional"])
            self.assertEqual(len(group["bulkMemberFlags"]),1)

    def test_resource_wildcard_and_new_metadata_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            jar=Path(directory)/"fixture.jar"
            with zipfile.ZipFile(jar,"w") as archive:
                archive.writestr("META-INF/native-image/a/b/reachability-metadata.json",
                    json.dumps({"reflection":[{"type":"example.A"}],"resources":[{"glob":"**"}]}))
            groups=audit_metadata([jar],[])["metadataFiles"][0]["groups"]
            self.assertEqual(groups["reflection"]["entries"],1)
            self.assertEqual(groups["resources"]["allResourcePatterns"],[{"glob":"**"}])

    def test_oversized_metadata_is_rejected_before_decompression(self):
        with tempfile.TemporaryDirectory() as directory:
            jar=Path(directory)/"fixture.jar"
            with zipfile.ZipFile(jar,"w",compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("META-INF/native-image/a/b/reflect-config.json",b" "*(2*1024**2+1))
            with self.assertRaises(ValueError):audit_metadata([jar],[])

    def test_missing_rss_is_unknown_and_darwin_bytes_are_preserved(self):
        self.assertIsNone(parse_time_rss("interrupted"))
        self.assertEqual(parse_time_rss("  123456  maximum resident set size\n"),123456)

    def test_heap_gc_and_thread_metrics_have_distinct_meanings(self):
        summary=summarize_jfr({"recording":{"events":[
            {"type":"jdk.GCHeapSummary","values":{"when":"Before GC","heapUsed":600}},
            {"type":"jdk.GCHeapSummary","values":{"when":"After GC","heapUsed":500}},
            {"type":"jdk.GarbageCollection","values":{"duration":"PT2S","sumOfPauses":"PT0.1S"}},
            {"type":"jdk.JavaThreadStatistics","values":{"activeCount":12}}]}})
        self.assertEqual(summary["peakObservedHeapUsedBytes"],600)
        self.assertEqual(summary["lastObservedHeapAfterGCBytes"],500)
        self.assertEqual(summary["gcDurationSumSeconds"],2)
        self.assertEqual(summary["gcPauseSumSeconds"],0.1)
        self.assertEqual(summary["peakObservedJavaThreads"],12)
        self.assertIsNone(summarize_jfr({"recording":{"events":[]}})["peakObservedHeapUsedBytes"])
        self.assertEqual(duration_seconds("PT1M2.5S"),62.5)
        with self.assertRaises(ValueError):duration_seconds("unknown")

    def test_gc_log_parser_ignores_partial_events_and_keeps_full_gc_distinct(self):
        summary=summarize_gc_logs('''[time][gc] GC(1) Pause Young (Allocation Failure) 512M->400M(768M) 12.5ms
[time][gc,start] GC(2) Pause Full (Ergonomics)
[time][gc] GC(2) Pause Full (Ergonomics) 6G->5G(6G) 1000.0ms
[time][gc,init] Parallel Workers: 9
[time][safepoint] Safepoint "Test", Threads: 4 runnable, 22 total''')
        self.assertEqual(summary["completedPauseEvents"],2)
        self.assertEqual(summary["fullGCPauseEvents"],1)
        self.assertEqual(summary["lastHeapAfterPauseBytes"],5*1024**3)
        self.assertEqual(summary["pauseSumSeconds"],1.0125)
        self.assertEqual(summary["peakThreadsAtSafepoint"],22)
        self.assertEqual(summary["parallelGCWorkers"],9)
        self.assertIsNone(summarize_gc_logs("")["lastHeapAfterPauseBytes"])

    def test_low_host_memory_preflight_records_blocked_and_returns_two(self):
        """The 35% headroom gate must report, not crash.

        A refused preflight used to lose its whole result when the error path raised while
        reporting: the exception handler itself called sys.stderr, which was missing from the
        module imports. The run then ended with a NameError instead of a written BLOCKED record.
        """
        pressure = {"returncode": 0, "stdout": "System-wide memory free percentage: 3%\n"
                    "The system has 17179869184 bytes of physical memory\n", "stderr": ""}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prepared, runtime, caps, evidence = _preflight_fixture(root)
            output = root/"out"
            original = probe_client_graph.subprocess.run

            def fake_run(argv, **kwargs):
                if argv[:1] == ["/usr/bin/memory_pressure"]:
                    return type("Completed", (), pressure)()
                return original(argv, **kwargs)

            stderr = io.StringIO()
            with unittest.mock.patch.object(probe_client_graph.subprocess, "run", fake_run):
                # The probe echoes its whole result record to stdout; keep the test output readable.
                with contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(io.StringIO()):
                    code = probe_client_graph.main([
                        "--output", str(output), "--prepared", str(prepared),
                        "--runtime", str(runtime), "--caps", str(caps),
                        "--b1-evidence", str(evidence)])

            self.assertEqual(code, 2)
            self.assertIn("Host memory headroom below bounded experiment threshold", stderr.getvalue())
            record = json.loads((output/"result.json").read_text())
            self.assertEqual(record["status"], "BLOCKED")
            self.assertEqual(record["error"], "Host memory headroom below bounded experiment threshold")
            self.assertEqual(record["hostMemory"]["availablePercent"], 3)
            self.assertEqual(record["hostMemory"]["physicalBytes"], 17179869184)
            self.assertFalse(record["analysisCompleted"])


if __name__=="__main__":unittest.main()
