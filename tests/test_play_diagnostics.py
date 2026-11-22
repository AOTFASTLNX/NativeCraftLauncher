import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from build_method_splitter import java_home
from capture import ROOT

DIAGNOSTICS = ROOT / "compiler/metal-backend/tools/fabricaot/diag/PlayDiagnostics.java"
FIXTURE = """
package fixture;

import fabricaot.diag.PlayDiagnostics;

public final class DiagFixture {
    public static void main(String[] args) throws Exception {
        if (args.length > 0) {
            var entered = new java.util.concurrent.CountDownLatch(1);
            var release = new java.util.concurrent.CountDownLatch(1);
            Thread render = new Thread(() -> {
                PlayDiagnostics.tick();
                PlayDiagnostics.tracker("WaitingForServer");
                PlayDiagnostics.packetBegin("handlePlayerInfoUpdate");
                entered.countDown();
                try { release.await(); } catch (InterruptedException error) { throw new AssertionError(error); }
                PlayDiagnostics.packetEnd();
                PlayDiagnostics.tick();
            }, "Render thread");
            render.start();
            if (!entered.await(5, java.util.concurrent.TimeUnit.SECONDS)) throw new AssertionError("no render entry");
            for (int i = 0; i < 18; i++) {
                PlayDiagnostics.chunk(i, 0);
                Thread.sleep(100);
            }
            release.countDown();
            render.join(5000);
            if (render.isAlive()) throw new AssertionError("render did not recover");
            Thread.sleep(250);
            System.out.println("STALL_FIXTURE_RECOVERED");
            return;
        }
        PlayDiagnostics.tick();
        PlayDiagnostics.login("ClientboundLoginPacket[playerId=7]");
        PlayDiagnostics.batchStart();
        PlayDiagnostics.chunk(0, 0);
        PlayDiagnostics.batch(9);
        PlayDiagnostics.chunkDecode(0, 0);
        PlayDiagnostics.radius(8);
        PlayDiagnostics.cacheCenter(0, 0);
        PlayDiagnostics.tracker("WaitingForServer");
        PlayDiagnostics.chunkReady(true);
        PlayDiagnostics.ready(true);
        PlayDiagnostics.sectionCheck("BlockPos{x=5, y=131, z=7}");
        for (int index = 0; index < 500; index++) {
            PlayDiagnostics.sectionVisible(index % 2 == 0);
        }
        PlayDiagnostics.clientPosition(5.5, 131.0, 7.5);
        PlayDiagnostics.serverPosition("ClientboundPlayerPositionPacket");
        PlayDiagnostics.connectionFailure(new RuntimeException("fixture failure"));
        PlayDiagnostics.channelInactive();
        Thread.sleep(2200L);
    }
}
"""

# Mirrors Bootstrap: both standard streams are replaced before the diagnostics class is first
# touched, so a sink that goes through System.out/System.err would lose every line.
REDIRECT_FIXTURE = """
package fixture;

import java.io.ByteArrayOutputStream;
import java.io.FileDescriptor;
import java.io.FileOutputStream;
import java.io.PrintStream;
import java.nio.charset.StandardCharsets;

import fabricaot.diag.PlayDiagnostics;

public final class RedirectFixture {
    public static void main(String[] args) throws Exception {
        ByteArrayOutputStream swallowed = new ByteArrayOutputStream();
        PrintStream mute = new PrintStream(swallowed, true, StandardCharsets.UTF_8);
        System.setOut(mute);
        System.setErr(mute);

        // Only now does PlayDiagnostics get initialized.
        PlayDiagnostics.tick();
        PlayDiagnostics.login("ClientboundLoginPacket[playerId=7]");
        PlayDiagnostics.chunk(0, 0);
        PlayDiagnostics.chunkDecode(0, 0);
        PlayDiagnostics.tracker("WaitingForServer");
        PlayDiagnostics.ready(true);
        PlayDiagnostics.channelInactive();
        Thread.sleep(450L);

        mute.flush();
        // A direct descriptor write still works, which proves the shared stderr was not closed.
        PrintStream descriptor = new PrintStream(
                new FileOutputStream(FileDescriptor.err), true, StandardCharsets.UTF_8);
        String leaked = swallowed.toString(StandardCharsets.UTF_8);
        long diagLines = leaked.lines().filter(line -> line.contains("[MCFAO_DIAG]")).count();
        descriptor.println("REDIRECT_LEAKED_DIAG_LINES=" + diagLines);
        descriptor.println("REDIRECT_DESCRIPTOR_OPEN");
        descriptor.flush();
    }
}
"""


class PlayDiagnosticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.home = Path(os.environ["FAOT_TEST_JAVA_HOME"]) if os.environ.get("FAOT_TEST_JAVA_HOME") else java_home()
        except (SystemExit, OSError, ValueError):
            raise unittest.SkipTest("verified Java 25 home is not available")
        if not DIAGNOSTICS.is_file():
            raise unittest.SkipTest("PlayDiagnostics source is not present")

    def compile_and_run(self, directory: Path, enabled: bool, stalled=False, graphics=False) -> str:
        code, stdout, stderr = self.compile_and_execute(directory, enabled, stalled, graphics)
        self.assertEqual(code, 0, stderr)
        return stdout + stderr

    def compile_and_execute(self, directory: Path, enabled: bool, stalled=False, graphics=False,
                            fixture: str = FIXTURE, main: str = "fixture.DiagFixture",
                            extra: tuple = (), heartbeat=None):
        source = directory / (main.rsplit(".", 1)[-1] + ".java")
        source.write_text(fixture)
        subprocess.run([str(self.home / "bin/javac"), "--release", "25", "-proc:none",
                        "-d", str(directory / "classes"), str(DIAGNOSTICS), str(source)],
                       check=True, capture_output=True, text=True, timeout=120)
        result = subprocess.run([
            str(self.home / "bin/java"), f"-Dmcfao.diag.play={'true' if enabled else 'false'}",
            "-Dmcfao.diag.tickStallSeconds=1" if stalled else "-Dmcfao.diag.tickStallSeconds=5",
            "-Dmcfao.diag.heartbeatMillis=100" if stalled or heartbeat == 100
            else "-Dmcfao.diag.heartbeatMillis=2000",
            "-cp", str(directory / "classes"), main,
            *extra, *(["stall"] if stalled else [])],
            capture_output=True, text=True, timeout=60,
            env={**os.environ, "FAOT_GL41_DIAGNOSTICS": "1" if graphics else "0"})
        return result.returncode, result.stdout, result.stderr

    def test_graphics_memory_diagnostics_are_opt_in_and_rate_limited(self):
        with tempfile.TemporaryDirectory() as temp:
            output = self.compile_and_run(Path(temp), True, stalled=True, graphics=True)
            self.assertEqual(output.count("[GL41-DIAG] javaHeapUsed="), 1)
            self.assertRegex(output, r"javaHeapCommitted=\d+ javaHeapMax=\d+")
        with tempfile.TemporaryDirectory() as temp:
            output = self.compile_and_run(Path(temp), True)
            self.assertNotIn("[GL41-DIAG]", output)

    def test_play_hooks_are_structured_and_switchable(self):
        with tempfile.TemporaryDirectory() as temp:
            enabled = self.compile_and_run(Path(temp), True)
            for event in ("configuration_to_play", "chunk_received", "chunk_decoded",
                          "chunk_batch_finished", "level_load_tracker", "level_load_ready",
                          "section_visibility", "client_position_send", "server_position",
                          "connection_exception", "channel_inactive", "heartbeat"):
                self.assertIn("event=" + event, enabled)
            self.assertIn("thread=main", enabled)
            # 500 section queries in one short run must not become 500 log lines.
            self.assertLessEqual(enabled.count("event=section_visibility"), 10)
        with tempfile.TemporaryDirectory() as temp:
            disabled = self.compile_and_run(Path(temp), False)
            self.assertNotIn("[MCFAO_DIAG]", disabled)

    def test_aot_enables_shared_arenas_and_runtime_diagnostics(self):
        builder = (ROOT / "tools/probe_client_graph.py").read_text()
        self.assertIn('"-H:+SharedArenaSupport"', builder)
        self.assertIn("io.netty,joptsimple,fabricaot", builder)
        injector = (ROOT / "tools/inject_metal_backend.py").read_text()
        self.assertIn("fabricaot/diag/PlayDiagnostics.class", injector)
        self.assertIn("LevelLoadTracker", injector)

    def test_connection_failure_keeps_the_original_error_name(self):
        with tempfile.TemporaryDirectory() as temp:
            output = self.compile_and_run(Path(temp), True)
            self.assertIn("error=java.lang.RuntimeException: fixture failure", output)
            self.assertIn("event=connection_exception_frame", output)

    def test_redirected_standard_streams_cannot_swallow_diagnostics(self):
        with tempfile.TemporaryDirectory() as temp:
            code, stdout, stderr = self.compile_and_execute(
                Path(temp), True, fixture=REDIRECT_FIXTURE, main="fixture.RedirectFixture")
        self.assertEqual(code, 0, stderr)
        # The sink that Bootstrap installs must stay empty; the descriptor must carry the lines.
        self.assertRegex(stderr, r"REDIRECT_LEAKED_DIAG_LINES=0")
        self.assertIn("REDIRECT_DESCRIPTOR_OPEN", stderr)
        self.assertNotIn("[MCFAO_DIAG]", stdout)
        for event in ("diagnostics_start", "configuration_to_play", "chunk_received",
                      "chunk_decoded", "level_load_tracker", "level_load_ready",
                      "channel_inactive"):
            self.assertIn("event=" + event, stderr)

    def test_waiting_client_tick_is_dumped_while_network_keeps_arriving(self):
        with tempfile.TemporaryDirectory() as temp:
            output = self.compile_and_run(Path(temp), True, stalled=True)
        self.assertIn("stallSeconds=35 tickStallSeconds=1", output)
        self.assertEqual(output.count("event=client_tick_stall"), 1)
        self.assertIn("clientHandler=handlePlayerInfoUpdate handlerActive=true", output)
        self.assertRegex(output, r"event=thread [^\n]* name=Render thread state=WAITING")
        self.assertRegex(output, r"event=thread_frame [^\n]* name=Render thread")
        self.assertIn("event=thread_dump_end", output)
        self.assertIn("handlerActive=false", output)
        self.assertIn("STALL_FIXTURE_RECOVERED", output)
        self.assertNotIn("event=stall_detected", output)

    def test_disabled_watchdog_does_not_dump_a_stalled_client(self):
        with tempfile.TemporaryDirectory() as temp:
            output = self.compile_and_run(Path(temp), False, stalled=True)
        self.assertNotIn("[MCFAO_DIAG]", output)
        self.assertIn("STALL_FIXTURE_RECOVERED", output)


if __name__ == "__main__":
    unittest.main()
