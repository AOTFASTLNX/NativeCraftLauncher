package mcfao.aot;

import java.nio.ByteBuffer;
import java.util.function.BooleanSupplier;
import org.lwjgl.glfw.GLFW;
import org.lwjgl.system.JNI;
import org.lwjgl.system.MemoryUtil;
import top.fifthlight.touchcontroller.proxy.message.CapabilityMessage;
import top.fifthlight.touchcontroller.proxy.message.InitializeMessage;
import top.fifthlight.touchcontroller.proxy.message.KeyboardShowMessage;
import top.fifthlight.touchcontroller.proxy.message.MessageDecodeException;
import top.fifthlight.touchcontroller.proxy.message.ProxyMessage;
import top.fifthlight.touchcontroller.proxy.message.VibrateMessage;

/**
 * The iOS host side of TouchController's transport.
 *
 * <p>The Mod already speaks its own proxy protocol: {@code IosPlatform} polls a byte array and
 * decodes it with {@code ProxyMessage.Companion}, exactly as the Android and SDL platforms do.
 * The only missing piece is the native transport, so the C side encodes the small input subset
 * (ADD, REMOVE, CLEAR) and this class decodes the Mod's replies with the Mod's own classes.
 *
 * <p>Native symbols resolve through the GLFW library the process already loaded. Adding a second
 * dylib would give the queue two copies, and the image is the only writer of the bytes either
 * side reads.
 */
public final class TouchHost implements IosTransport.Host {
    /** Native hooks, split out so the decode logic can be exercised without a device. */
    public interface Bridge {
        void beginFrame(boolean enabled);

        int receive(byte[] buffer);

        void keyboard(boolean visible);

        void vibrate(int kind);
    }

    private final Bridge bridge;
    private final BooleanSupplier enabled;
    private int pendingCapabilities = 2;
    private int warnings;
    private Boolean lastKeyboard;

    public TouchHost() {
        this(new NativeBridge(), TouchHost::configured);
    }

    TouchHost(Bridge bridge, BooleanSupplier enabled) {
        if (bridge == null || enabled == null) throw new IllegalArgumentException("Touch host needs a bridge");
        this.bridge = bridge;
        this.enabled = enabled;
    }

    /**
     * Reads the player's status from the Mod's own config.
     *
     * <p>Disabled means the Mod itself ignores input, and forwarding contacts then steals touches
     * from the game's look controls. Only a live config is consulted; an unreadable one keeps the
     * bridge active, which is what a fresh install expects.
     */
    private static boolean configured() {
        try {
            // Typed, not reflective: the overlay compiles against the Mod and its nested jars, so
            // the closed image can resolve this chain at build time.
            return top.fifthlight.touchcontroller.common.config.holder.GlobalConfigHolder.INSTANCE
                    .getConfig().getValue().getStatus().getStatus()
                    != top.fifthlight.touchcontroller.common.config.data.StatusConfig.Status.DISABLED;
        } catch (RuntimeException unavailable) {
            // A fresh install has no config file yet; keeping the bridge active matches the Mod's
            // own default, which is enabled.
            return true;
        }
    }

    @Override
    public void beginFrame() {
        bridge.beginFrame(enabled.getAsBoolean());
    }

    @Override
    public int receive(byte[] buffer) {
        // A new connection advertises what this host can render. The Mod sends Initialize when it
        // is ready, which asks for the same set again; both paths use the same two capabilities.
        if (pendingCapabilities > 0) {
            String capability = pendingCapabilities == 2 ? "keyboard_show" : "text_status";
            ByteBuffer encoded = ByteBuffer.allocate(64);
            new CapabilityMessage(capability, pendingCapabilities == 2).encode(encoded);
            int length = encoded.position();
            if (buffer.length < length) return 0;
            System.arraycopy(encoded.array(), 0, buffer, 0, length);
            pendingCapabilities--;
            return length;
        }
        return bridge.receive(buffer);
    }

    @Override
    public void send(byte[] buffer, int offset, int length) {
        if (length < 4) {
            warn("truncated output");
            return;
        }
        ByteBuffer view = ByteBuffer.wrap(buffer, offset, length).slice();
        int type = view.getInt();
        ProxyMessage message;
        try {
            message = decode(type, view);
        } catch (MessageDecodeException | RuntimeException error) {
            warn("undecodable output type=" + type + " " + error.getClass().getSimpleName());
            return;
        }
        if (message instanceof InitializeMessage) {
            pendingCapabilities = 2;
            lastKeyboard = null;
        } else if (message instanceof KeyboardShowMessage keyboard) {
            boolean show = keyboard.getShow();
            if (lastKeyboard == null || lastKeyboard != show) bridge.keyboard(show);
            lastKeyboard = show;
        } else if (message instanceof VibrateMessage vibration) {
            bridge.vibrate(vibration.getKind().getIndex());
        } else {
            // Cursor and area rectangles describe the launcher's own panel, which this build does
            // not show; the game already receives the resulting mouse input through GLFW.
            warn("ignored output type=" + type);
        }
    }

    private static ProxyMessage decode(int type, ByteBuffer payload) throws MessageDecodeException {
        return ProxyMessage.Companion.decode(type, payload);
    }

    private void warn(String message) {
        // A malformed message repeats every frame; eight lines are enough to identify it.
        if (warnings++ < 8) System.err.println("[TouchBridge] " + message);
    }

    /** The device bridge; symbols come from the GLFW dylib that is already resident. */
    static final class NativeBridge implements Bridge {
        private final ByteBuffer bytes = ByteBuffer.allocateDirect(256);
        private long frame;
        private long read;
        private long command;
        private boolean ready;

        /**
         * Resolves the queue's symbols on first use.
         *
         * <p>The Mod's static initialiser runs while the platform provider is being constructed,
         * which on this build can precede the point where GLFW has opened its library. Binding in
         * the constructor would then fail once, permanently, and no later frame could recover.
         * A library that is not open yet is not an error: the next frame retries.
         */
        private boolean ensure() {
            if (ready) return true;
            var library = GLFW.getLibrary();
            if (library == null) return false;
            frame = symbol(library, "faot_touch_begin_frame");
            read = symbol(library, "faot_touch_receive");
            command = symbol(library, "faot_touch_command");
            ready = true;
            // Kind 8 is KeyboardShow; starting hidden matches the game's own keyboard state.
            JNI.invokePV(8L, 0, command);
            System.err.println("[TouchBridge] host installed protocol=alpha17 frame-snapshot-v1");
            return true;
        }

        private static long symbol(org.lwjgl.system.SharedLibrary library, String name) {
            long address = library.getFunctionAddress(name);
            if (address == 0) throw new IllegalStateException("Touch transport symbol missing: " + name);
            return address;
        }

        @Override
        public void beginFrame(boolean enabled) {
            if (!ensure()) return;
            JNI.invokeV(enabled ? 1 : 0, frame);
        }

        @Override
        public int receive(byte[] buffer) {
            if (!ensure()) return 0;
            int capacity = Math.min(buffer.length, bytes.capacity());
            int length = JNI.invokePI(MemoryUtil.memAddress(bytes), capacity, read);
            if (length < 0 || length > capacity) {
                throw new IllegalStateException("Touch transport returned " + length + " bytes");
            }
            for (int index = 0; index < length; index++) buffer[index] = bytes.get(index);
            return length;
        }

        @Override
        public void keyboard(boolean visible) {
            if (!ensure()) return;
            JNI.invokePV(8L, visible ? 1 : 0, command);
        }

        @Override
        public void vibrate(int kind) {
            if (!ensure()) return;
            JNI.invokePV(4L, kind, command);
        }
    }
}
