package mcfao.aot;

/**
 * Stands in for TouchController's platform/ios/Transport native entry points.
 *
 * <p>The Mod ships a real iOS platform: IosPlatform extends LargeMessageWrappedPlatform and
 * speaks the same ProxyMessage protocol the desktop transports use. Only the three native
 * methods are missing, because on iOS they are expected to be supplied by the host app. A
 * closed-world image cannot carry a native method, so the export rewrites those three into calls
 * here and this class forwards them to the host bridge.
 *
 * <p>The contract is read from the Mod's own bytecode, not invented: receive fills the caller's
 * buffer and returns the number of bytes written (a non-positive result means nothing yet), and
 * send is handed the backing array, its array offset and the byte count. Everything above this
 * layer - message framing, the 40 message types, the large-message split - stays the Mod's own
 * code.
 */
public final class IosTransport {
    /** Host hooks, installed once before the game starts. */
    private static volatile Host host;

    private IosTransport() {}

    /** The native side of the bridge. */
    public interface Host {
        /** Marks a render frame boundary; contacts pressed since the last one become visible. */
        default void beginFrame() {}

        /** Fill buffer with at most one message; return the byte count, or 0. */
        int receive(byte[] buffer);

        /** Consume length bytes starting at offset. */
        void send(byte[] buffer, int offset, int length);
    }

    /**
     * Installs the host hooks.
     *
     * <p>Called from the generated entrypoint before the Mod's client initialiser runs. A second
     * call replaces the previous host, which keeps a restart in the same process honest.
     */
    public static void install(Host replacement) {
        host = replacement;
    }

    /**
     * Installs the real iOS host.
     *
     * <p>Called from the Mod's own {@code Transport.init()} after the rewrite, which is the one
     * point the Mod already treats as "the host is ready". Constructing the host here is what
     * keeps {@link TouchHost} reachable: an image built without a caller prunes it, the field
     * stays null and every poll silently returns "no input".
     */
    public static void init() {
        if (host != null) return;
        try {
            install(new TouchHost());
        } catch (RuntimeException | LinkageError unavailable) {
            // A missing symbol or an unreadable config must not turn every poll into a throw:
            // the Mod would fail its whole render tick. Idle instead, and say why once.
            System.err.println("[TouchBridge] host unavailable: " + unavailable);
        }
    }

    public static boolean installed() {
        return host != null;
    }

    /** Publishes the contacts down at this frame; the Mod polls them from its render event. */
    public static void beginFrame() {
        Host current = host;
        if (current != null) current.beginFrame();
    }

    /**
     * Reads one queued message into buffer.
     *
     * <p>Returns 0 when no host is installed. The Mod treats a non-positive result as no message
     * this frame, so an unconnected transport idles instead of throwing on every poll.
     */
    public static int receive(byte[] buffer) {
        Host current = host;
        if (current == null || buffer == null || buffer.length == 0) return 0;
        return current.receive(buffer);
    }

    /** Hands one encoded message to the host. */
    public static void send(byte[] buffer, int offset, int length) {
        Host current = host;
        if (current == null || buffer == null) return;
        if (offset < 0 || length < 0 || offset > buffer.length - length) {
            throw new IllegalArgumentException("Transport send outside the buffer");
        }
        current.send(buffer, offset, length);
    }

    /**
     * Stands in for System.load on a desktop native library.
     *
     * <p>The platform provider checks isIos before this path, so iOS never calls it. It exists so
     * the unreachable desktop branch keeps its shape without a native-library load a closed-world
     * image cannot express.
     */
    public static void ignoreNativeLibrary(String path) {
        // Intentionally empty: the library belongs to a host this image does not run on.
    }
}
