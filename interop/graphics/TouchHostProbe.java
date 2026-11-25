package mcfao.aot;

import java.nio.ByteBuffer;
import top.fifthlight.touchcontroller.proxy.message.AddPointerMessage;
import top.fifthlight.touchcontroller.proxy.message.CapabilityMessage;
import top.fifthlight.touchcontroller.proxy.message.InitializeMessage;
import top.fifthlight.touchcontroller.proxy.message.KeyboardShowMessage;
import top.fifthlight.touchcontroller.proxy.message.ProxyMessage;
import top.fifthlight.touchcontroller.proxy.message.VibrateMessage;

/**
 * Exercises the Java half of the touch bridge without a device.
 *
 * <p>The failure this guards against is subtle: an init that never installs a host leaves the
 * field null, the closed-world image folds every poll to a constant miss, and the whole
 * TouchHost class is pruned. Nothing crashes; input simply never arrives. So the probe checks
 * that installation happens, that the capability handshake reaches the Mod, and that the
 * Mod's replies reach the native command channel.
 */
public final class TouchHostProbe {
    /** Records what the host asks the native side to do. */
    private static final class Fake implements TouchHost.Bridge {
        boolean began;
        boolean enabled;
        int reads;
        Boolean keyboard;
        Integer vibration;

        @Override
        public void beginFrame(boolean on) {
            began = true;
            enabled = on;
        }

        @Override
        public int receive(byte[] buffer) {
            reads++;
            return 0;
        }

        @Override
        public void keyboard(boolean visible) {
            keyboard = visible;
        }

        @Override
        public void vibrate(int kind) {
            vibration = kind;
        }
    }

    private static void require(boolean condition, String message) {
        if (!condition) throw new AssertionError(message);
    }

    private static ProxyMessage poll(byte[] buffer, int length) {
        ByteBuffer view = ByteBuffer.wrap(buffer, 0, length).slice();
        int type = view.getInt();
        return ProxyMessage.Companion.decode(type, view);
    }

    private static int encode(ProxyMessage message, byte[] into) {
        ByteBuffer buffer = ByteBuffer.allocate(256);
        message.encode(buffer);
        int length = buffer.position();
        System.arraycopy(buffer.array(), 0, into, 0, length);
        return length;
    }

    public static void main(String[] args) {
        // 1. Installation is the regression: without it nothing else can work.
        require(!IosTransport.installed() || true, "installed() must be readable");
        IosTransport.install(new TouchHost(new Fake(), () -> true));
        require(IosTransport.installed(), "install() did not take effect");

        Fake bridge = new Fake();
        IosTransport.install(new TouchHost(bridge, () -> true));

        // 2. A frame boundary reaches the native side with the Mod's own enabled state.
        IosTransport.beginFrame();
        require(bridge.began && bridge.enabled, "beginFrame did not reach the bridge");

        // 3. The host advertises both capabilities before it reads native input.
        byte[] bytes = new byte[256];
        int length = IosTransport.receive(bytes);
        ProxyMessage first = poll(bytes, length);
        require(first instanceof CapabilityMessage, "first message must be a capability");
        CapabilityMessage keyboardCapability = (CapabilityMessage) first;
        require("keyboard_show".equals(keyboardCapability.getCapability())
                && keyboardCapability.getEnabled(), "keyboard_show must be advertised as supported");

        length = IosTransport.receive(bytes);
        ProxyMessage second = poll(bytes, length);
        require(second instanceof CapabilityMessage, "second message must be a capability");
        CapabilityMessage textCapability = (CapabilityMessage) second;
        require("text_status".equals(textCapability.getCapability())
                && !textCapability.getEnabled(), "text_status must be advertised as unsupported");

        // 4. Once the handshake is done, polls fall through to the native queue.
        require(IosTransport.receive(bytes) == 0 && bridge.reads == 1,
                "poll must reach the native queue after the handshake");

        // 5. The Mod's replies are routed, not dropped.
        length = encode(new KeyboardShowMessage(true), bytes);
        IosTransport.send(bytes, 0, length);
        require(Boolean.TRUE.equals(bridge.keyboard), "KeyboardShowMessage must reach the surface");

        length = encode(new VibrateMessage(VibrateMessage.Kind.BLOCK_BROKEN), bytes);
        IosTransport.send(bytes, 0, length);
        // Kind.BLOCK_BROKEN carries index 0; UNKNOWN carries -1.
        require(Integer.valueOf(0).equals(bridge.vibration), "VibrateMessage must reach the device");

        // A repeated keyboard state must not be forwarded twice; the surface would reload input.
        bridge.keyboard = null;
        IosTransport.send(bytes, 0, encode(new KeyboardShowMessage(true), bytes));
        require(bridge.keyboard == null, "unchanged keyboard state must not be re-sent");

        // 6. Initialize re-arms the handshake, as a reconnect on the Mod's side expects.
        IosTransport.send(bytes, 0, encode(InitializeMessage.INSTANCE, bytes));
        ProxyMessage again = poll(bytes, IosTransport.receive(bytes));
        require(again instanceof CapabilityMessage, "Initialize must re-arm the capability handshake");

        // 7. Input messages travelling the other way are ignored rather than thrown.
        IosTransport.send(bytes, 0, encode(new AddPointerMessage(1, 0.5f, 0.5f), bytes));

        // 8. A range outside the buffer is a programming error, not silent corruption.
        boolean rejected = false;
        try {
            IosTransport.send(new byte[8], 4, 8);
        } catch (IllegalArgumentException expected) {
            rejected = true;
        }
        require(rejected, "an out-of-range send must be rejected");

        System.out.println("TOUCH_HOST_PASS");
    }
}
