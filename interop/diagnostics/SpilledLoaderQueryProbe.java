package mcfao.build;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import org.objectweb.asm.Opcodes;
import org.objectweb.asm.tree.AbstractInsnNode;
import org.objectweb.asm.tree.ClassNode;
import org.objectweb.asm.tree.FieldInsnNode;
import org.objectweb.asm.tree.InsnNode;
import org.objectweb.asm.tree.MethodInsnNode;
import org.objectweb.asm.tree.MethodNode;
import org.objectweb.asm.tree.VarInsnNode;

/**
 * Checks that a Loader query whose receiver was spilled to a slot is still lowered.
 *
 * <p>TouchController's Kotlin config directory provider keeps {@code FabricLoader.getInstance()} in
 * a local before calling {@code getConfigDir()}. The chain rewrite never saw it, the residue pass
 * turned the method into a throwing stub, and the client died during startup with
 * {@code ExceptionInInitializerError}. The rewrite must fold the spilled form to the same hook,
 * leave a receiver that is read twice alone, and keep the existing failure for a Loader call it
 * does not understand.
 */
public final class SpilledLoaderQueryProbe implements Opcodes {
    private static final String LOADER = "net/fabricmc/loader/api/FabricLoader";

    public static void main(String[] arguments) {
        lowered("getConfigDir", "()Ljava/nio/file/Path;", INVOKEINTERFACE, "configDir",
                "()Ljava/nio/file/Path;", INVOKESTATIC);
        lowered("getGameDir", "()Ljava/nio/file/Path;", INVOKEINTERFACE, null, "GAME_DIR", GETSTATIC);
        // The value is reused after the query: rewriting it would leave a Loader reference behind.
        reuseKeepsTheFailure();
        // A query the rewrite does not know keeps reporting its own location.
        unknownStaysUnsupported();
        System.out.println("SPILLED_LOADER_QUERY_PASS");
    }

    private static ClassNode holder(String name, String method, String descriptor) {
        var node = new ClassNode();
        node.version = V25;
        node.access = ACC_PUBLIC | ACC_SUPER;
        node.name = name;
        node.superName = "java/lang/Object";
        node.methods.add(new MethodNode(ACC_PUBLIC | ACC_STATIC, method, descriptor, null, null));
        return node;
    }

    private static MethodNode method(ClassNode node, String name) {
        return node.methods.stream().filter(candidate -> candidate.name.equals(name)).findFirst().orElseThrow();
    }

    /** Builds {@code getInstance(); astore 0; aload 0; <query>; areturn} and lowers it. */
    private static void lowered(String query, String descriptor, int opcode, String hook, String hookDescriptor,
            int hookOpcode) {
        var node = holder("probe/Spilled", "query", "()Ljava/nio/file/Path;");
        var body = method(node, "query");
        body.instructions.add(new MethodInsnNode(INVOKESTATIC, LOADER, "getInstance", "()L" + LOADER + ";", false));
        body.instructions.add(new VarInsnNode(ASTORE, 0));
        body.instructions.add(new VarInsnNode(ALOAD, 0));
        body.instructions.add(new MethodInsnNode(opcode, LOADER, query, descriptor, true));
        body.instructions.add(new InsnNode(ARETURN));
        body.maxLocals = 1;
        body.maxStack = 2;
        var adaptations = new ArrayList<String>();
        var stubbed = new HashSet<String>();
        RuntimeLowering.lowerQueries(node, Set.of(), java.util.Map.of(), adaptations, stubbed, Set.of());
        if (!stubbed.isEmpty()) throw new AssertionError(query + " still reported as unsupported: " + stubbed);
        if (adaptations.stream().noneMatch(text -> text.contains(query) && text.contains("spilled receiver"))) {
            throw new AssertionError(query + " was not recorded as a spilled-receiver rewrite: " + adaptations);
        }
        if (body.instructions.size() != 2) {
            throw new AssertionError(query + " kept instructions: " + body.instructions.size());
        }
        var head = body.instructions.getFirst();
        var tail = body.instructions.getLast();
        if (!(tail instanceof InsnNode) || tail.getOpcode() != ARETURN) throw new AssertionError(query + " lost its return");
        if (hook == null) {
            if (!(head instanceof FieldInsnNode field) || field.getOpcode() != hookOpcode
                    || !field.owner.equals("mcfao/aot/Entrypoints") || !field.name.equals(hookDescriptor)) {
                throw new AssertionError(query + " did not read the launch directory: " + head);
            }
        } else if (!(head instanceof MethodInsnNode call) || call.getOpcode() != hookOpcode
                || !call.owner.equals("mcfao/aot/Entrypoints") || !call.name.equals(hook)
                || !call.desc.equals(hookDescriptor)) {
            throw new AssertionError(query + " did not call the hook: " + head);
        }
    }

    private static void reuseKeepsTheFailure() {
        var node = holder("probe/Reused", "query", "()Ljava/nio/file/Path;");
        var body = method(node, "query");
        body.instructions.add(new MethodInsnNode(INVOKESTATIC, LOADER, "getInstance", "()L" + LOADER + ";", false));
        body.instructions.add(new VarInsnNode(ASTORE, 0));
        body.instructions.add(new VarInsnNode(ALOAD, 0));
        body.instructions.add(new MethodInsnNode(INVOKEINTERFACE, LOADER, "getConfigDir", "()Ljava/nio/file/Path;", true));
        body.instructions.add(new VarInsnNode(ALOAD, 0));
        body.instructions.add(new MethodInsnNode(INVOKEINTERFACE, LOADER, "getGameDir", "()Ljava/nio/file/Path;", true));
        body.instructions.add(new InsnNode(POP));
        body.instructions.add(new InsnNode(ARETURN));
        body.maxLocals = 1;
        body.maxStack = 2;
        var adaptations = new ArrayList<String>();
        var stubbed = new HashSet<String>();
        RuntimeLowering.lowerQueries(node, Set.of(), java.util.Map.of(), adaptations, stubbed, Set.of());
        if (stubbed.isEmpty()) throw new AssertionError("A reused receiver must keep the unsupported report");
        // The residue pass replaces the whole body with a throw that names the original API, so
        // the reported location is the only thing left to check here.
        if (stubbed.stream().noneMatch(text -> text.contains("getInstance"))) {
            throw new AssertionError("The report must name the reused query: " + stubbed);
        }
        if (adaptations.stream().anyMatch(text -> text.contains("spilled receiver"))) {
            throw new AssertionError("A reused receiver must not be half rewritten: " + adaptations);
        }
    }

    private static void unknownStaysUnsupported() {
        var node = holder("probe/Unknown", "query", "()V");
        var body = method(node, "query");
        body.instructions.add(new MethodInsnNode(INVOKESTATIC, LOADER, "getInstance", "()L" + LOADER + ";", false));
        body.instructions.add(new VarInsnNode(ASTORE, 0));
        body.instructions.add(new VarInsnNode(ALOAD, 0));
        body.instructions.add(new MethodInsnNode(INVOKEINTERFACE, LOADER, "getModOriginPaths", "()Ljava/util/List;", true));
        body.instructions.add(new InsnNode(POP));
        body.instructions.add(new InsnNode(RETURN));
        body.maxLocals = 1;
        body.maxStack = 2;
        var adaptations = new ArrayList<String>();
        var stubbed = new HashSet<String>();
        RuntimeLowering.lowerQueries(node, Set.of(), java.util.Map.of(), adaptations, stubbed, Set.of());
        if (stubbed.size() != 1) throw new AssertionError("An unknown query must be reported once: " + stubbed);
        // The report names the first Loader call it found, which is the entry into the API.
        if (!stubbed.iterator().next().contains("getInstance")) {
            throw new AssertionError("The report must name the original API: " + stubbed);
        }
    }
}

