package fabricaot.gl41;

import java.nio.file.Files;
import java.nio.file.Path;

/** Structural variants must be rejected instead of patched on a guess. */
public final class FailClosed {
    private FailClosed() {}

    public static void main(String[] args) throws Exception {
        byte[] bytes = Files.readAllBytes(Path.of(args[0]));
        try {
            PatchDevice1211.avoidUnneededChatKeyWait(bytes);
        } catch (IllegalArgumentException expected) {
            System.out.println("FAIL_CLOSED: " + expected.getMessage());
            return;
        }
        throw new AssertionError("patch accepted an unexpected secure-chat structure");
    }
}
