package fabricaot.gl41;

import java.nio.file.Files;
import java.nio.file.Path;
import org.objectweb.asm.*;
import org.objectweb.asm.tree.*;

/** Bind the audited LWJGL 3.3.3 desktop-only bootstrap to the signed iOS shim. */
public final class PatchOpenGLStartup implements Opcodes {
    public static void main(String[] args) throws Exception {
        ClassNode type = new ClassNode();
        new ClassReader(Files.readAllBytes(Path.of(args[0]))).accept(type, 0);
        if (!type.name.equals("org/lwjgl/opengl/GL"))
            throw new IllegalArgumentException("Unexpected OpenGL class");
        int changed = 0;
        for (MethodNode method : type.methods) {
            if (!method.name.equals("create") || !method.desc.equals("()V")) continue;
            if ((method.access & (ACC_PUBLIC | ACC_STATIC)) != (ACC_PUBLIC | ACC_STATIC))
                throw new IllegalArgumentException("Unexpected OpenGL bootstrap access");
            method.instructions.clear();
            method.tryCatchBlocks.clear();
            if (method.localVariables != null) method.localVariables.clear();
            method.visibleLocalVariableAnnotations = null;
            method.invisibleLocalVariableAnnotations = null;
            method.instructions.add(new LdcInsnNode("liblwjgl_opengl.dylib"));
            method.instructions.add(new MethodInsnNode(INVOKESTATIC, type.name, "create",
                    "(Ljava/lang/String;)V", false));
            method.instructions.add(new InsnNode(RETURN));
            changed++;
        }
        if (changed != 1) throw new IllegalArgumentException("Missing OpenGL bootstrap");
        ClassWriter writer = new ClassWriter(ClassWriter.COMPUTE_MAXS);
        type.accept(writer);
        Files.write(Path.of(args[1]), writer.toByteArray());
    }
}
