package fabricaot.gl41;

import java.nio.file.Files;
import java.nio.file.Path;

/** Applies the audited guard to an extracted ClientPacketListener class file. */
public final class RealClientPatch {
    private RealClientPatch() {}

    public static void main(String[] args) throws Exception {
        byte[] original = Files.readAllBytes(Path.of(args[0]));
        Path output = Path.of(args[1]);
        if (output.getParent() != null) Files.createDirectories(output.getParent());
        Files.write(output, PatchDevice1211.avoidUnneededChatKeyWait(original));
    }
}
