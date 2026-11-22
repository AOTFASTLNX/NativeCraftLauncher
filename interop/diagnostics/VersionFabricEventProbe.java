package mcfao.build;

import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.jar.JarEntry;
import java.util.jar.JarFile;
import org.objectweb.asm.ClassReader;
import org.objectweb.asm.ClassWriter;
import org.objectweb.asm.ConstantDynamic;
import org.objectweb.asm.Handle;
import org.objectweb.asm.Opcodes;
import org.objectweb.asm.tree.AbstractInsnNode;
import org.objectweb.asm.tree.ClassNode;
import org.objectweb.asm.tree.FieldInsnNode;
import org.objectweb.asm.tree.InvokeDynamicInsnNode;
import org.objectweb.asm.tree.LdcInsnNode;
import org.objectweb.asm.tree.MethodInsnNode;
import org.objectweb.asm.tree.MethodNode;

/**
 * Bytecode regression for the audited Fabric API 1.21.1 event-factory lowering.
 *
 * <p>Runs only on bytes: the Mod JAR is opened with {@link JarFile}, no class from it is
 * defined or initialized, and no Mod entrypoint runs. The probe proves the safety
 * precondition the lowering relies on - the two private proxy helpers have no caller
 * anywhere in the class, including lambda bootstrap method handles - and then proves the
 * rewrite removes exactly those two methods and leaves every other method byte-identical.
 */
public final class VersionFabricEventProbe {
    /** Original class bytes of EventFactoryImpl.class in the audited 1.21.1 build. */
    static final String AUDITED_SHA256 =
            "6d468bd557975d377f7f925b60289e7853bb4a10f585e4a5e2a03e24478bcc7f";
    static final String TARGET = "net/fabricmc/fabric/impl/base/event/EventFactoryImpl.class";
    static final String MOD = "fabric-api-base";
    static final String HELPER = "buildEmptyInvoker";
    static final String LAMBDA_HELPER = "lambda$buildEmptyInvoker$0";

    private VersionFabricEventProbe() {}

    static void check(boolean value, String message) {
        if (!value) throw new AssertionError(message);
    }

    static String sha256(byte[] data) throws Exception {
        return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(data));
    }

    static byte[] read(JarFile jar, String name) throws Exception {
        JarEntry entry = jar.getJarEntry(name);
        check(entry != null, "missing " + name + " in " + jar.getName());
        try (InputStream stream = jar.getInputStream(entry)) {
            return stream.readAllBytes();
        }
    }

    static ClassNode parse(byte[] data) {
        ClassNode node = new ClassNode();
        new ClassReader(data).accept(node, 0);
        return node;
    }

    static byte[] write(ClassNode node) {
        ClassWriter writer = new ClassWriter(0);
        node.accept(writer);
        return writer.toByteArray();
    }

    /** A single method serialized on its own, so its bytes can be compared exactly. */
    static byte[] methodBytes(ClassNode node, MethodNode method) {
        ClassNode single = new ClassNode();
        single.version = node.version;
        single.access = node.access;
        single.name = node.name;
        single.superName = node.superName;
        single.methods.add(method);
        return write(single);
    }

    static Map<String, byte[]> methodBytes(ClassNode node) {
        Map<String, byte[]> bytes = new LinkedHashMap<>();
        for (MethodNode method : node.methods) {
            bytes.put(method.name + method.desc, methodBytes(node, method));
        }
        return bytes;
    }

    static List<MethodNode> named(ClassNode node, String name) {
        return node.methods.stream().filter(method -> method.name.equals(name)).toList();
    }

    /** Every handle or type mentioned by an instruction, including bootstrap arguments. */
    static List<Object> referencedConstants(AbstractInsnNode insn) {
        List<Object> values = new ArrayList<>();
        if (insn instanceof LdcInsnNode ldc) values.add(ldc.cst);
        if (insn instanceof InvokeDynamicInsnNode indy) {
            values.add(indy.bsm);
            values.addAll(List.of(indy.bsmArgs));
        }
        return values;
    }

    static void collectHandles(Object value, List<Handle> handles) {
        if (value instanceof Handle handle) {
            handles.add(handle);
        } else if (value instanceof ConstantDynamic dynamic) {
            handles.add(dynamic.getBootstrapMethod());
            for (int index = 0; index < dynamic.getBootstrapMethodArgumentCount(); index++) {
                collectHandles(dynamic.getBootstrapMethodArgument(index), handles);
            }
        }
    }

    static Set<String> targets(ClassNode node) {
        Set<String> names = new HashSet<>();
        for (MethodNode method : node.methods) {
            if (method.name.equals(HELPER) || method.name.equals(LAMBDA_HELPER)) {
                names.add(method.name + method.desc);
            }
        }
        return names;
    }

    /**
     * Prove the two helpers have no caller: no instruction outside them names them, and
     * any bootstrap entry holding their handle is used only from inside them.
     */
    static void assertNoExternalCallers(ClassNode node) {
        Set<String> helpers = targets(node);
        check(helpers.size() == 2, "expected exactly the two private helpers, found " + helpers);
        for (String key : helpers) {
            String name = key.substring(0, key.indexOf('('));
            MethodNode method = named(node, name).get(0);
            check((method.access & Opcodes.ACC_PRIVATE) != 0, name + " is not private");
            check((method.access & Opcodes.ACC_STATIC) != 0, name + " is not static");
        }

        for (MethodNode method : node.methods) {
            String owner = method.name + method.desc;
            if (helpers.contains(owner)) continue;
            for (AbstractInsnNode insn : method.instructions) {
                if (insn instanceof MethodInsnNode call) {
                    check(!(call.owner.equals(node.name) && helpers.contains(call.name + call.desc)),
                            owner + " still calls " + call.name);
                }
                if (insn instanceof FieldInsnNode field) {
                    check(!(field.owner.equals(node.name) && helpers.contains(field.name + field.desc)),
                            owner + " still reads " + field.name);
                }
                List<Handle> handles = new ArrayList<>();
                for (Object value : referencedConstants(insn)) collectHandles(value, handles);
                for (Handle handle : handles) {
                    check(!(handle.getOwner().equals(node.name) && helpers.contains(handle.getName() + handle.getDesc())),
                            owner + " still holds a handle to " + handle.getName());
                }
            }
        }
    }

    static void assertUnchanged(Map<String, byte[]> before, ClassNode after, Set<String> removed) {
        check(after.methods.size() == before.size() - removed.size(),
                "unexpected method count " + after.methods.size());
        for (MethodNode method : after.methods) {
            String key = method.name + method.desc;
            check(!removed.contains(key), "helper survived the rewrite: " + key);
            byte[] expected = before.get(key);
            check(expected != null, "rewrite introduced " + key);
            check(java.util.Arrays.equals(expected, methodBytes(after, method)),
                    "method body changed: " + key);
        }
    }

    public static void main(String[] args) throws Exception {
        Path jarPath = Path.of(args[0]);
        byte[] original;
        byte[] other;
        try (JarFile jar = new JarFile(jarPath.toFile())) {
            original = read(jar, TARGET);
            other = read(jar, "net/fabricmc/fabric/impl/base/event/ArrayBackedEvent.class");
        }
        String digest = sha256(original);
        check(digest.equals(AUDITED_SHA256), "unaudited EventFactoryImpl: " + digest);

        ClassNode node = parse(original);
        check(node.name.equals(TARGET.substring(0, TARGET.length() - 6)), "unexpected class " + node.name);
        assertNoExternalCallers(node);
        check(!named(node, "createArrayBacked").isEmpty(), "createArrayBacked missing");

        // The guard has teeth: a caller injected into the retained method is rejected.
        assertGuardRejectsInjectedCall(original);

        Map<String, byte[]> before = methodBytes(node);
        Set<String> helpers = targets(node);
        List<String> adaptations = new ArrayList<>();
        RuntimeLowering.removeUnusedEventProxy(MOD, digest, node, adaptations);

        assertUnchanged(before, node, helpers);
        check(adaptations.size() == 1, "expected one recorded adaptation, got " + adaptations);
        check(!named(node, "createArrayBacked").isEmpty(), "createArrayBacked was removed");
        check(!named(node, "<clinit>").isEmpty(), "static initializer was removed");
        // The rewritten class must not keep any trace of the deleted helpers: ASM
        // rebuilds the constant pool, so a stale handle or bootstrap entry would
        // still name them here.
        byte[] rewritten = write(node);
        check(rewritten.length > 0, "rewritten class is empty");
        String pool = new String(rewritten, java.nio.charset.StandardCharsets.ISO_8859_1);
        check(!pool.contains(HELPER), "rewritten class still names " + HELPER);
        check(!pool.contains("lambda$"), "rewritten class still names a lambda helper");
        check(pool.contains("createArrayBacked"), "rewritten class lost createArrayBacked");

        // Applying the rewrite twice is a build error, not a silent second deletion:
        // the helper count no longer matches the audited shape.
        int afterFirst = node.methods.size();
        byte[] afterFirstBytes = write(node);
        List<String> second = new ArrayList<>();
        boolean refused = false;
        try {
            RuntimeLowering.removeUnusedEventProxy(MOD, digest, node, second);
        } catch (IllegalStateException expected) {
            refused = true;
        }
        check(refused, "a second application was accepted");
        check(node.methods.size() == afterFirst, "a refused second application changed the class");
        check(java.util.Arrays.equals(afterFirstBytes, write(node)),
                "a refused second application changed the class bytes");
        check(second.isEmpty(), "a refused second application recorded an adaptation");

        // An unknown source hash must never delete anything.
        for (String hash : new String[] {"0".repeat(64),
                "065be26b910ddcf78f27e48d2808eb7ecbacfbcdc38292459ca2c8f496ae7d7"}) {
            ClassNode foreign = parse(original);
            List<String> untouched = new ArrayList<>();
            RuntimeLowering.removeUnusedEventProxy(MOD, hash, foreign, untouched);
            check(untouched.isEmpty(), "unknown hash " + hash + " applied an adaptation");
            check(java.util.Arrays.equals(original, write(foreign)),
                    "unknown hash " + hash + " modified the class");
        }

        // A different Mod id, and a different class with the audited hash, stay untouched.
        for (String mod : new String[] {"fabric-api", "fabric-api-base-extra", ""}) {
            ClassNode foreign = parse(original);
            List<String> untouched = new ArrayList<>();
            RuntimeLowering.removeUnusedEventProxy(mod, digest, foreign, untouched);
            check(untouched.isEmpty(), "mod id " + mod + " applied an adaptation");
            check(java.util.Arrays.equals(original, write(foreign)), "mod id " + mod + " modified the class");
        }
        ClassNode unrelated = parse(other);
        List<String> untouched = new ArrayList<>();
        RuntimeLowering.removeUnusedEventProxy(MOD, digest, unrelated, untouched);
        check(untouched.isEmpty(), "another class in the same jar was adapted");
        check(java.util.Arrays.equals(other, write(unrelated)), "another class in the same jar changed");

        System.out.println("VERSION_FABRIC_EVENTS_PASS sha256=" + digest
                + " removed=" + helpers.size()
                + " methods=" + node.methods.size()
                + " unchanged=" + before.size());
    }

    /**
     * Prove the pre-flight check is not vacuous: a real call to the helper from the
     * retained factory method must be refused before any method is deleted.
     */
    static void assertGuardRejectsInjectedCall(byte[] original) {
        ClassNode tampered = parse(original);
        MethodNode factory = named(tampered, "createArrayBacked").get(0);
        MethodNode helper = named(tampered, HELPER).get(0);
        factory.instructions.insert(new org.objectweb.asm.tree.MethodInsnNode(
                Opcodes.INVOKESTATIC, tampered.name, helper.name, helper.desc, false));
        boolean refused = false;
        try {
            assertNoExternalCallers(tampered);
        } catch (AssertionError expected) {
            refused = true;
        }
        check(refused, "an external call to " + HELPER + " was not detected");
    }
}

