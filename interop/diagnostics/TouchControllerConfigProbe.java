package mcfao.build;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.Set;
import org.objectweb.asm.ClassReader;
import org.objectweb.asm.Opcodes;
import org.objectweb.asm.tree.ClassNode;
import org.objectweb.asm.tree.MethodInsnNode;
import org.objectweb.asm.tree.MethodNode;

/**
 * Runs the rewrite against the real TouchController class that crashed on device.
 *
 * <p>Pass the extracted class file as the single argument. The probe fails if the lowering still
 * reports the config-directory lambda as unsupported, or if any Loader reference survives in the
 * class once it has been lowered.
 */
public final class TouchControllerConfigProbe implements Opcodes {
    private static final String LOADER = "net/fabricmc/loader/api/FabricLoader";

    public static void main(String[] arguments) throws Exception {
        Path classFile = Path.of(arguments[0]);
        var node = new ClassNode();
        new ClassReader(Files.readAllBytes(classFile)).accept(node, 0);
        var adaptations = new ArrayList<String>();
        var stubbed = new HashSet<String>();
        RuntimeLowering.lowerQueries(node, Set.of("touchcontroller", "touchcontroller-26-1"),
                java.util.Map.of("touchcontroller", "0.3.1-alpha17"), adaptations, stubbed, Set.of());
        for (String report : stubbed) {
            if (report.contains("ConfigDirectoryProviderImpl")) {
                throw new AssertionError("The config directory provider is still unsupported: " + report);
            }
        }
        MethodNode lambda = null;
        for (MethodNode method : node.methods) {
            if (method.name.startsWith("configDirectory_delegate")) lambda = method;
            for (var instruction : method.instructions) {
                if (instruction instanceof MethodInsnNode call && call.owner.equals(LOADER)) {
                    throw new AssertionError("A Loader reference survived in " + method.name + ": " + call.name);
                }
            }
        }
        if (lambda == null) throw new AssertionError("The lambda method was not found");
        if (adaptations.stream().noneMatch(text -> text.contains("spilled receiver"))) {
            throw new AssertionError("The rewrite did not record the spilled receiver: " + adaptations);
        }
        System.out.println("TOUCHCONTROLLER_CONFIG_PASS");
    }
}

