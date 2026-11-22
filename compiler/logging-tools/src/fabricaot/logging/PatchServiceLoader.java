package fabricaot.logging;

import java.nio.file.Files;
import java.nio.file.Path;
import org.objectweb.asm.*;
import org.objectweb.asm.tree.*;

/** Replace Log4j 2.22.1's runtime lambda factory with its public ServiceLoader call. */
public final class PatchServiceLoader implements Opcodes {
    public static void main(String[] args) throws Exception {
        ClassNode type = new ClassNode();
        new ClassReader(Files.readAllBytes(Path.of(args[0]))).accept(type, 0);
        if (!type.name.equals("org/apache/logging/log4j/util/ServiceLoaderUtil"))
            throw new IllegalArgumentException("Unexpected logging class");
        int changed = 0;
        for (MethodNode method : type.methods) {
            if (!method.name.equals("callServiceLoader") || !method.desc.equals(
                    "(Ljava/lang/invoke/MethodHandles$Lookup;Ljava/lang/Class;Ljava/lang/ClassLoader;Z)Ljava/lang/Iterable;"))
                continue;
            long factories = java.util.stream.StreamSupport.stream(method.instructions.spliterator(), false)
                    .filter(i -> i instanceof MethodInsnNode m && m.owner.equals("java/lang/invoke/LambdaMetafactory")
                            && m.name.equals("metafactory")).count();
            if (factories != 1) throw new IllegalArgumentException("Unexpected logging lambda shape");
            method.instructions.clear();
            method.tryCatchBlocks.clear();
            if (method.localVariables != null) method.localVariables.clear();
            method.visibleLocalVariableAnnotations = null;
            method.invisibleLocalVariableAnnotations = null;
            LabelNode begin = new LabelNode(), end = new LabelNode(), handler = new LabelNode(), done = new LabelNode();
            InsnList code = method.instructions;
            code.add(begin);
            code.add(new VarInsnNode(ALOAD, 1));
            code.add(new VarInsnNode(ALOAD, 2));
            code.add(new MethodInsnNode(INVOKESTATIC, "java/util/ServiceLoader", "load",
                    "(Ljava/lang/Class;Ljava/lang/ClassLoader;)Ljava/util/ServiceLoader;", false));
            code.add(end);
            code.add(new InsnNode(ARETURN));
            code.add(handler);
            code.add(new VarInsnNode(ASTORE, 4));
            code.add(new VarInsnNode(ILOAD, 3));
            code.add(new JumpInsnNode(IFEQ, done));
            code.add(new MethodInsnNode(INVOKESTATIC, "org/apache/logging/log4j/status/StatusLogger", "getLogger",
                    "()Lorg/apache/logging/log4j/status/StatusLogger;", false));
            code.add(new LdcInsnNode("Unable to load services for service {}"));
            code.add(new VarInsnNode(ALOAD, 1));
            code.add(new VarInsnNode(ALOAD, 4));
            code.add(new MethodInsnNode(INVOKEVIRTUAL, "org/apache/logging/log4j/status/StatusLogger", "error",
                    "(Ljava/lang/String;Ljava/lang/Object;Ljava/lang/Object;)V", false));
            code.add(done);
            code.add(new MethodInsnNode(INVOKESTATIC, "java/util/Collections", "emptyList", "()Ljava/util/List;", false));
            code.add(new InsnNode(ARETURN));
            method.tryCatchBlocks.add(new TryCatchBlockNode(begin, end, handler, "java/lang/Throwable"));
            changed++;
        }
        if (changed != 1) throw new IllegalArgumentException("Missing logging service method");
        ClassWriter writer = new ClassWriter(ClassWriter.COMPUTE_FRAMES | ClassWriter.COMPUTE_MAXS);
        type.accept(writer);
        Files.write(Path.of(args[1]), writer.toByteArray());
    }
}
