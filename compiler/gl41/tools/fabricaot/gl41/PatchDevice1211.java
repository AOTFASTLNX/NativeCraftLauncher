package fabricaot.gl41;

import fabricaot.metal.InjectBackend;
import org.objectweb.asm.*;
import org.objectweb.asm.tree.*;
import java.nio.file.*;
import java.security.MessageDigest;
import java.util.*;
import java.util.function.UnaryOperator;
import java.util.zip.*;

/** Mojmap 1.21.1 profile. Every semantic target is required before publishing output. */
public final class PatchDevice1211 implements Opcodes {
    private static final String DIAG = "fabricaot/diag/PlayDiagnostics";
    private static final String MC = "net/minecraft/client/Minecraft.class";
    private static final String LISTENER = "net/minecraft/client/multiplayer/ClientPacketListener.class";
    private static final String CACHE = "net/minecraft/client/multiplayer/ClientChunkCache.class";
    private static final Map<String, UnaryOperator<byte[]>> PATCHES = Map.of(
        MC, PatchDevice1211::tick, LISTENER, PatchDevice1211::packets,
        CACHE, PatchDevice1211::chunks, InjectBackend.GLX, PatchDevice1211::hardware,
        "net/minecraft/util/profiling/jfr/JvmProfiler.class", PatchDevice1211::withoutFlightRecorder,
        InjectBackend.SYSTEM_REPORT, InjectBackend::useIosHardwareDiagnostics,
        "net/minecraft/client/main/Main.class", PatchDevice1211::startupFailures,
        "net/minecraft/network/Connection.class", InjectBackend::instrumentConnection);

    private static ClassNode read(byte[] bytes) {
        ClassNode cls = new ClassNode(); new ClassReader(bytes).accept(cls, 0); return cls;
    }
    private static byte[] write(ClassNode cls) {
        ClassWriter writer = new ClassWriter(ClassWriter.COMPUTE_MAXS);
        cls.accept(writer); return writer.toByteArray();
    }
    private static MethodNode require(ClassNode cls, String name, String desc) {
        var methods = cls.methods.stream().filter(m -> m.name.equals(name) && m.desc.equals(desc)).toList();
        if (methods.size() != 1) throw new IllegalArgumentException("Expected one " + cls.name + "." + name + desc);
        return methods.getFirst();
    }
    private static MethodInsnNode diag(String name, String desc) {
        return new MethodInsnNode(INVOKESTATIC, DIAG, name, desc, false);
    }
    static byte[] withoutFlightRecorder(byte[] bytes) {
        try {
            String digest = HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes));
            if (!digest.equals("05bc243d0c863f1c6762f18697366f721d7b3eeeecdb58be9d9f8871b0b72da7"))
                throw new IllegalArgumentException("1.21.1 JvmProfiler changed; audit required");
        } catch (java.security.NoSuchAlgorithmException impossible) {
            throw new AssertionError(impossible);
        }
        ClassNode cls = read(bytes);
        MethodNode init = require(cls, "<clinit>", "()V");
        // The desktop selector checks module presence, but this iOS image includes jdk.jfr
        // without a recorder. Select Minecraft's existing unsupported-VM implementation.
        init.instructions.clear(); init.tryCatchBlocks.clear(); init.localVariables = null;
        init.visibleLocalVariableAnnotations = null; init.invisibleLocalVariableAnnotations = null;
        init.instructions.add(new TypeInsnNode(NEW, cls.name + "$NoOpProfiler"));
        init.instructions.add(new InsnNode(DUP));
        init.instructions.add(new MethodInsnNode(INVOKESPECIAL, cls.name + "$NoOpProfiler",
            "<init>", "()V", false));
        init.instructions.add(new FieldInsnNode(PUTSTATIC, cls.name, "INSTANCE", "L" + cls.name + ";"));
        init.instructions.add(new InsnNode(RETURN));
        return write(cls);
    }
    static byte[] startupFailures(byte[] bytes) {
        ClassNode cls = read(bytes);
        MethodNode main = require(cls, "main", "([Ljava/lang/String;)V");
        Set<LabelNode> handlers = new HashSet<>();
        for (TryCatchBlockNode block : main.tryCatchBlocks) {
            if (!"java/lang/Throwable".equals(block.type) || !handlers.add(block.handler)) continue;
            AbstractInsnNode store = block.handler.getNext();
            while (store != null && store.getOpcode() < 0) store = store.getNext();
            if (!(store instanceof VarInsnNode local) || store.getOpcode() != ASTORE)
                throw new IllegalArgumentException("Unexpected 1.21.1 startup exception handler");
            // CrashReport also initializes LogUtils. Print the original throwable before that
            // second failure can replace the useful initializer error in the device log.
            InsnList report = new InsnList();
            report.add(new VarInsnNode(ALOAD, local.var));
            report.add(new MethodInsnNode(INVOKEVIRTUAL, "java/lang/Throwable",
                "printStackTrace", "()V", false));
            main.instructions.insert(store, report);
        }
        if (handlers.size() != 2)
            throw new IllegalArgumentException("Expected two 1.21.1 startup Throwable handlers: " + handlers.size());
        return write(cls);
    }
    static byte[] tick(byte[] bytes) {
        ClassNode cls = read(bytes);
        require(cls, "runTick", "(Z)V").instructions.insert(diag("tick", "()V"));
        int count = 0;
        for (MethodNode method : cls.methods) for (AbstractInsnNode insn : method.instructions.toArray()) {
            if (insn instanceof MethodInsnNode call && call.getOpcode() == INVOKESTATIC
                && call.owner.equals("com/mojang/blaze3d/systems/RenderSystem")
                && call.name.equals("limitDisplayFPS") && call.desc.equals("(I)V")) {
                method.instructions.set(call, new MethodInsnNode(INVOKESTATIC,
                    "fabricaot/gl41/FramePacing", "limitDisplayFPS", "(I)V", false));
                count++;
            }
        }
        if (count != 1) throw new IllegalArgumentException("Expected one 1.21.1 RenderSystem frame limiter: " + count);
        return write(cls);
    }
    static byte[] hardware(byte[] bytes) {
        ClassNode cls = read(bytes);
        MethodNode init = require(cls, "_init", "(IZ)V");
        var alloc = Arrays.stream(init.instructions.toArray()).filter(n -> n instanceof TypeInsnNode t
            && t.getOpcode() == NEW && t.desc.equals("oshi/SystemInfo")).toList();
        var debug = Arrays.stream(init.instructions.toArray()).filter(n -> n instanceof MethodInsnNode c
            && c.getOpcode() == INVOKESTATIC && c.owner.equals("com/mojang/blaze3d/platform/GlDebug")
            && c.name.equals("enableDebugCallback") && c.desc.equals("(IZ)V")).toList();
        if (alloc.size() != 1 || debug.size() != 1) throw new IllegalArgumentException("Unexpected 1.21.1 GLX initialization");
        init.instructions.clear(); init.tryCatchBlocks.clear(); init.localVariables = null;
        init.instructions.add(new LdcInsnNode("os.arch"));
        init.instructions.add(new LdcInsnNode("unknown"));
        init.instructions.add(new MethodInsnNode(INVOKESTATIC, "java/lang/System", "getProperty",
            "(Ljava/lang/String;Ljava/lang/String;)Ljava/lang/String;", false));
        init.instructions.add(new FieldInsnNode(PUTSTATIC, cls.name, "cpuInfo", "Ljava/lang/String;"));
        init.instructions.add(new VarInsnNode(ILOAD, 0)); init.instructions.add(new VarInsnNode(ILOAD, 1));
        init.instructions.add(new MethodInsnNode(INVOKESTATIC, "com/mojang/blaze3d/platform/GlDebug",
            "enableDebugCallback", "(IZ)V", false));
        init.instructions.add(new InsnNode(RETURN));
        return write(cls);
    }
    static byte[] packets(byte[] bytes) {
        ClassNode cls = read(avoidUnneededChatKeyWait(bytes));
        require(cls, "handlePlayerInfoUpdate", "(Lnet/minecraft/network/protocol/game/ClientboundPlayerInfoUpdatePacket;)V");
        int count = 0;
        for (MethodNode method : cls.methods) {
            if (!method.name.startsWith("handle") || !method.desc.endsWith(";)V")) continue;
            for (AbstractInsnNode insn : method.instructions.toArray()) {
                if (!(insn instanceof MethodInsnNode call) || call.getOpcode() != INVOKESTATIC
                    || !call.owner.equals("net/minecraft/network/protocol/PacketUtils")
                    || !call.name.equals("ensureRunningOnSameThread")) continue;
                InsnList begin = new InsnList(); begin.add(new LdcInsnNode(method.name));
                begin.add(diag("packetBegin", "(Ljava/lang/String;)V")); method.instructions.insert(insn, begin);
                for (AbstractInsnNode end : method.instructions.toArray())
                    if (end.getOpcode() == RETURN) method.instructions.insertBefore(end, diag("packetEnd", "()V"));
                count++; break;
            }
        }
        if (count == 0) throw new IllegalArgumentException("Missing 1.21.1 packet thread handoffs");
        return write(cls);
    }
    static byte[] avoidUnneededChatKeyWait(byte[] bytes) {
        ClassNode cls = read(bytes);
        String listener = LISTENER.substring(0, LISTENER.length() - 6);
        String minecraft = MC.substring(0, MC.length() - 6);
        String player = "net/minecraft/client/multiplayer/PlayerInfo";
        String entry = "net/minecraft/network/protocol/game/ClientboundPlayerInfoUpdatePacket$Entry";
        String session = "net/minecraft/network/chat/RemoteChatSession$Data";
        if (!cls.name.equals(listener))
            throw new IllegalArgumentException("Unexpected 1.21.1 packet listener class");
        MethodNode predicate = require(cls, "enforcesSecureChat", "()Z");
        MethodNode initialize = require(cls, "initializeChatSession", "(L" + entry + ";L" + player + ";)V");
        long checks = Arrays.stream(predicate.instructions.toArray()).filter(n -> n instanceof MethodInsnNode c
            && c.getOpcode() == INVOKEVIRTUAL && c.owner.equals(minecraft)
            && c.name.equals("canValidateProfileKeys") && c.desc.equals("()Z")).count();
        long flags = Arrays.stream(predicate.instructions.toArray()).filter(n -> n instanceof FieldInsnNode f
            && f.getOpcode() == GETFIELD && f.owner.equals(listener)
            && f.name.equals("serverEnforcesSecureChat") && f.desc.equals("Z")).count();
        long validators = Arrays.stream(initialize.instructions.toArray()).filter(n -> n instanceof MethodInsnNode c
            && c.getOpcode() == INVOKEVIRTUAL && c.owner.equals(minecraft)
            && c.name.equals("getProfileKeySignatureValidator")
            && c.desc.equals("()Lnet/minecraft/util/SignatureValidator;")).count();
        long sessions = Arrays.stream(initialize.instructions.toArray()).filter(n -> n instanceof MethodInsnNode c
            && c.getOpcode() == INVOKEVIRTUAL && c.owner.equals(entry)
            && c.name.equals("chatSession") && c.desc.equals("()L" + session + ";")).count();
        if (checks != 1 || flags != 1 || validators != 1 || sessions != 1)
            throw new IllegalArgumentException("Unexpected 1.21.1 secure chat methods; audit required");

        // Vanilla tests (keys && flag). The false flag already determines the result,
        // so the render thread need not join the services-key future for offline play.
        LabelNode checkKeys = new LabelNode();
        InsnList flagGuard = new InsnList();
        flagGuard.add(new VarInsnNode(ALOAD, 0));
        flagGuard.add(new FieldInsnNode(GETFIELD, listener, "serverEnforcesSecureChat", "Z"));
        flagGuard.add(new JumpInsnNode(IFNE, checkKeys));
        flagGuard.add(new InsnNode(ICONST_0)); flagGuard.add(new InsnNode(IRETURN));
        flagGuard.add(checkKeys); flagGuard.add(new FrameNode(F_SAME, 0, null, 0, null));
        predicate.instructions.insert(flagGuard);

        // No session data always clears the session. Keep the original non-null path,
        // including signature validation and its exception handling, intact.
        LabelNode validateSession = new LabelNode();
        InsnList sessionGuard = new InsnList();
        sessionGuard.add(new VarInsnNode(ALOAD, 1));
        sessionGuard.add(new MethodInsnNode(INVOKEVIRTUAL, entry, "chatSession", "()L" + session + ";", false));
        sessionGuard.add(new JumpInsnNode(IFNONNULL, validateSession));
        sessionGuard.add(new VarInsnNode(ALOAD, 2)); sessionGuard.add(new VarInsnNode(ALOAD, 0));
        sessionGuard.add(new MethodInsnNode(INVOKEVIRTUAL, listener, "enforcesSecureChat", "()Z", false));
        sessionGuard.add(new MethodInsnNode(INVOKEVIRTUAL, player, "clearChatSession", "(Z)V", false));
        sessionGuard.add(new InsnNode(RETURN)); sessionGuard.add(validateSession);
        sessionGuard.add(new FrameNode(F_SAME, 0, null, 0, null));
        initialize.instructions.insert(sessionGuard);
        return write(cls);
    }
    static byte[] chunks(byte[] bytes) {
        ClassNode cls = read(bytes);
        MethodNode method = require(cls, "replaceWithPacketData", "(IILnet/minecraft/network/FriendlyByteBuf;"
            + "Lnet/minecraft/nbt/CompoundTag;Ljava/util/function/Consumer;)Lnet/minecraft/world/level/chunk/LevelChunk;");
        InsnList entry = new InsnList(); entry.add(new VarInsnNode(ILOAD, 1)); entry.add(new VarInsnNode(ILOAD, 2));
        entry.add(diag("chunkDecode", "(II)V")); method.instructions.insert(entry); return write(cls);
    }
    public static void main(String[] args) throws Exception {
        Set<String> pending = new TreeSet<>(PATCHES.keySet());
        try (ZipInputStream source = new ZipInputStream(Files.newInputStream(Path.of(args[0])));
             ZipOutputStream destination = new ZipOutputStream(Files.newOutputStream(Path.of(args[1])))) {
            ZipEntry entry;
            while ((entry = source.getNextEntry()) != null) {
                byte[] data = source.readAllBytes(); String name = entry.getName();
                var patch = PATCHES.get(name);
                if (patch != null) {
                    byte[] changed = patch.apply(data);
                    if (Arrays.equals(data, changed)) throw new IllegalArgumentException("Unchanged target " + name);
                    data = changed; pending.remove(name);
                } else if (name.equals("META-INF/MANIFEST.MF")) data = PatchDevice.cleanManifest(data);
                ZipEntry copy = new ZipEntry(name); copy.setTime(entry.getTime());
                destination.putNextEntry(copy); destination.write(data); destination.closeEntry();
            }
            if (!pending.isEmpty()) throw new IllegalArgumentException("Missing 1.21.1 targets: " + pending);
            for (String helper : List.of(DIAG, "fabricaot/gl41/FramePacing")) {
                ZipEntry copy = new ZipEntry(helper + ".class"); copy.setTime(0); destination.putNextEntry(copy);
                destination.write(Files.readAllBytes(Path.of(args[2]).resolve(helper + ".class"))); destination.closeEntry();
            }
        }
        System.out.println("GL41_1211_PROFILE_APPLIED");
    }
}
