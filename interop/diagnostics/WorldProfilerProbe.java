package fabricaot.probe;

import net.minecraft.util.profiling.jfr.JvmProfiler;

/** Exercises Minecraft's unsupported-JFR world hooks without creating a world. */
public final class WorldProfilerProbe {
    public static void main(String[] args) {
        System.setProperty("log4j2.disable.jmx", "true");
        JvmProfiler profiler = JvmProfiler.INSTANCE;
        if (!(profiler instanceof JvmProfiler.NoOpProfiler))
            throw new AssertionError("Wrong profiler: " + profiler.getClass().getName());
        if (profiler.isAvailable() || profiler.isRunning() || profiler.start(null))
            throw new AssertionError("Unsupported recorder reported as available");
        // These are the actual callbacks used during world load and chunk generation.
        var duration = profiler.onWorldLoadedStarted();
        if (duration == null) throw new AssertionError("World-load callback missing");
        duration.finish();
        if (profiler.onChunkGenerate(null, null, "fixture") != null)
            throw new AssertionError("Unexpected chunk recording");
        profiler.onServerTick(0);
        profiler.onPacketSent(null, null, null, 0);
        profiler.onPacketReceived(null, null, null, 0);
        profiler.onRegionFileRead(null, null, null, 0);
        profiler.onRegionFileWrite(null, null, null, 0);
        boolean stopped = false;
        try { profiler.stop(); } catch (IllegalStateException expected) { stopped = true; }
        if (!stopped) throw new AssertionError("Inactive recorder stop accepted");
        System.out.println("WORLD_PROFILER_PASS");
    }
}
