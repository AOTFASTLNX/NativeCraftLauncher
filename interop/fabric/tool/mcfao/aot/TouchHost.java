package mcfao.aot;

import java.nio.ByteBuffer;
import top.fifthlight.touchcontroller.proxy.message.AddPointerMessage;
import top.fifthlight.touchcontroller.proxy.message.ClearPointerMessage;
import top.fifthlight.touchcontroller.proxy.message.InputCursorMessage;
import top.fifthlight.touchcontroller.proxy.message.KeyboardShowMessage;
import top.fifthlight.touchcontroller.proxy.message.ProxyMessage;
import top.fifthlight.touchcontroller.proxy.message.RemovePointerMessage;

/**
 * Default {@link IosTransport.Host} for TouchController.
 *
 * <p>The Mod owns the wire format, so this decodes with its own
 * {@code ProxyMessage.Companion.decode} and switches on the resulting types. The types are named
 * directly rather than reflectively: this class compiles against the Mod jar, exactly like the
 * Sodium vertex replacement, so a closed image can resolve every call at build time.
 *
 * <p>What is left is the part a host app would write - a pointer becomes a cursor position and a
 * keyboard message toggles the on-screen keyboard. Messages the Mod uses to describe its own UI
 * state are dropped; they exist for the launcher panel this build does not show.
 */
public final class TouchHost implements IosTransport.Host {
    /** Input the host can inject. Installed by the app before the Mod initialises. */
    public interface Sink {
        void moveCursor(float x, float y);

        void keyboard(boolean visible);
    }

    private final Sink sink;
    private int lastCursorX = Integer.MIN_VALUE;
    private int lastCursorY = Integer.MIN_VALUE;
    private Boolean lastKeyboard;

    public TouchHost(Sink sink) {
        if (sink == null) throw new IllegalArgumentException("Touch host needs a sink");
        this.sink = sink;
    }

    /**
     * Reads one queued message.
     *
     * <p>The transport is pull-based on the Mod side and the host pushes from touch handling, so
     * the queue is empty here. Answering 0 is the Mod's own "no message this frame".
     */
    @Override
    public int receive(byte[] buffer) {
        return 0;
    }

    /**
     * Consumes one encoded message from the Mod.
     *
     * <p>An unrecognised message is dropped: a newer Mod build adding a type must not break the
     * frame that carries it.
     */
    @Override
    public void send(byte[] buffer, int offset, int length) {
        if (length < 4) return;
        ByteBuffer view = ByteBuffer.wrap(buffer, offset, length).slice();
        int type = view.getInt();
        ProxyMessage message;
        try {
            message = ProxyMessage.Companion.decode(type, view);
        } catch (RuntimeException error) {
            return;
        }
        if (message instanceof InputCursorMessage cursor) {
            var rect = cursor.getCursorRect();
            if (rect == null) return;
            float x = rect.getLeft();
            float y = rect.getTop();
            // The Mod reports the cursor every frame; only a real move reaches the game.
            int qx = Math.round(x * 1000f);
            int qy = Math.round(y * 1000f);
            if (qx == lastCursorX && qy == lastCursorY) return;
            lastCursorX = qx;
            lastCursorY = qy;
            sink.moveCursor(x, y);
        } else if (message instanceof KeyboardShowMessage keyboard) {
            boolean show = keyboard.getShow();
            if (lastKeyboard != null && lastKeyboard == show) return;
            lastKeyboard = show;
            sink.keyboard(show);
        }
        // AddPointer, RemovePointer, ClearPointer and the capability messages describe the Mod's
        // own control state; the game receives the resulting input through the cursor and keyboard
        // callbacks above.
        if (message instanceof AddPointerMessage || message instanceof RemovePointerMessage
                || message instanceof ClearPointerMessage) {
            return;
        }
    }
}
