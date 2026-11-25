import java.nio.ByteBuffer;
import java.nio.file.Files;
import java.nio.file.Path;
import top.fifthlight.touchcontroller.proxy.message.AddPointerMessage;
import top.fifthlight.touchcontroller.proxy.message.ClearPointerMessage;
import top.fifthlight.touchcontroller.proxy.message.ProxyMessage;
import top.fifthlight.touchcontroller.proxy.message.RemovePointerMessage;

/**
 * Decodes the bytes the native queue writes with the Mod's own message classes.
 *
 * <p>The C fixture asserts the byte layout it emits; this probe proves the other half, that
 * {@code ProxyMessage.Companion} accepts exactly those bytes. It mirrors
 * {@code IosPlatform.pollSmallEvent}: one receive() call carries one message, so the probe
 * slices the stream by the length each type declares before decoding.
 *
 * <p>Usage: {@code TouchWireProbe <stream.bin>}. Prints one line per message and exits
 * non-zero if a message does not decode.
 */
public final class TouchWireProbe {
    private static int sizeOf(int type) {
        // Payload lengths from the Mod's decoders: ADD is index + x + y, REMOVE is index,
        // CLEAR carries nothing.
        return switch (type) {
            case 1 -> 16;
            case 2 -> 8;
            case 3 -> 4;
            default -> 0;
        };
    }

    public static void main(String[] args) throws Exception {
        if (args.length != 1) throw new IllegalArgumentException("Usage: TouchWireProbe <stream.bin>");
        byte[] stream = Files.readAllBytes(Path.of(args[0]));
        int offset = 0;
        int messages = 0;
        while (offset < stream.length) {
            if (offset + 4 > stream.length) {
                System.err.println("Truncated header at " + offset);
                System.exit(1);
            }
            int type = ByteBuffer.wrap(stream, offset, 4).getInt();
            int size = sizeOf(type);
            if (size == 0 || offset + size > stream.length) {
                System.err.println("Bad message type=" + type + " at " + offset);
                System.exit(1);
            }
            ByteBuffer buffer = ByteBuffer.wrap(stream, offset, size).slice();
            buffer.getInt();
            ProxyMessage message = ProxyMessage.Companion.decode(type, buffer);
            messages++;
            if (message instanceof AddPointerMessage add) {
                System.out.printf("ADD index=%d x=%.3f y=%.3f%n", add.getIndex(), add.getX(), add.getY());
            } else if (message instanceof RemovePointerMessage remove) {
                System.out.println("REMOVE index=" + remove.getIndex());
            } else if (message instanceof ClearPointerMessage) {
                System.out.println("CLEAR");
            } else {
                System.out.println("OTHER " + message.getClass().getSimpleName());
            }
            offset += size;
        }
        System.out.println("messages=" + messages + " consumed=" + offset + "/" + stream.length);
    }
}
