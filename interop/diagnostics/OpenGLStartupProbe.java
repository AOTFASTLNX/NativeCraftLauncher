package fabricaot.probe;

/**
 * Proves the statically adapted LWJGL OpenGL bootstrap reaches the iOS shim.
 *
 * <p>Run against the iOS overlay ({@code lwjgl-ios.jar}) plus the patched official
 * {@code lwjgl-opengl-3.3.3.jar}. No game, window or GL context is created: the probe only asks
 * for the installed function provider, resolves {@code glGetString}, calls it through the
 * first-party shim, and re-enters the patched {@code create()} after {@code destroy()}.
 */
public final class OpenGLStartupProbe {
    private static final String MARKER = "OPENGL_STARTUP_PASS";
    private static final int GL_VERSION = 0x1F02;

    private OpenGLStartupProbe() {
    }

    public static void main(String[] args) {
        System.out.println("OPENGL_PROBE_BEGIN");
        // The patch only rewrites the iOS branch; on any other platform the probe would load a
        // desktop framework and prove nothing about the adapted path.
        if (org.lwjgl.system.Platform.get() != org.lwjgl.system.Platform.IOS)
            throw new AssertionError("Probe requires the iOS overlay, got " + org.lwjgl.system.Platform.get());

        // Pass 0: touching GL runs its static initialiser, which calls the patched create().
        // Pass 1: after destroy(), the explicit no-argument create() exercises the patch directly.
        for (int pass = 0; pass < 2; pass++) {
            if (pass == 1)
                org.lwjgl.opengl.GL.create();
            org.lwjgl.system.FunctionProvider provider = org.lwjgl.opengl.GL.getFunctionProvider();
            if (provider == null)
                throw new AssertionError("No function provider after GL.create");
            long address = provider.getFunctionAddress("glGetString");
            if (address == 0L)
                throw new AssertionError("glGetString unresolved in pass " + pass);
            String version = org.lwjgl.system.MemoryUtil.memASCII(
                    org.lwjgl.system.JNI.callP(GL_VERSION, address));
            if (version == null || version.isEmpty())
                throw new AssertionError("glGetString returned nothing in pass " + pass);
            System.out.println("OPENGL_GL_VERSION pass=" + pass + " value=" + version);

            boolean refused = false;
            try {
                org.lwjgl.opengl.GL.create();
            } catch (IllegalStateException expected) {
                refused = true;
            }
            if (!refused)
                throw new AssertionError("Duplicate GL.create was allowed in pass " + pass);

            org.lwjgl.opengl.GL.destroy();
            if (org.lwjgl.opengl.GL.getFunctionProvider() != null)
                throw new AssertionError("GL.destroy left a provider installed in pass " + pass);
        }
        System.out.println(MARKER);
    }
}
