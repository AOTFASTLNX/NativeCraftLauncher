package mcfao.build;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import mcfao.aot.MappingTable;
import net.fabricmc.loader.impl.lib.mappingio.MappingReader;
import net.fabricmc.loader.impl.lib.mappingio.tree.MemoryMappingTree;

/** First-party fixture for the build/runtime mapping boundary. */
public final class VersionMappingProbe {
    private static void equal(Object expected, Object actual) {
        if (!expected.equals(actual)) throw new AssertionError(expected + " != " + actual);
    }
    public static void main(String[] args) throws Exception {
        Path root = Path.of(args[0]);
        Path tiny = root.resolve("fixture.tiny");
        Files.writeString(tiny, String.join("\n", List.of(
                "tiny\t2\t0\tobfuscated\tintermediary\tofficial",
                "c\ta\tnet/minecraft/class_1\tnet/minecraft/client/Fixture",
                "\tf\tI\ta\tfield_1\tcount",
                "\tm\t([La;I)La;\ta\tmethod_1\tcopy",
                "\tm\t(I)V\tb\tmethod_2\tupdate",
                "c\tb\tnet/minecraft/class_2\tnet/minecraft/client/Other",
                "\tm\t(I)V\tc\tmethod_2\tdifferent")) + "\n");
        var tree = new MemoryMappingTree();
        MappingReader.read(tiny, tree);
        checkMixinRemapModes(root, tree);
        checkResidualSelectors(tree);
        equal("Lnet/minecraft/client/Fixture;copy([Lnet/minecraft/client/Fixture;I)Lnet/minecraft/client/Fixture;",
                VersionModRemapper.selector("Lnet/minecraft/class_1;method_1([Lnet/minecraft/class_1;I)Lnet/minecraft/class_1;", tree));
        equal("Lnet/minecraft/client/Fixture;count:I",
                VersionModRemapper.selector("Lnet/minecraft/class_1;field_1:I", tree));
        equal("Lnet/minecraft/client/Other;count:I",
                VersionModRemapper.selector("Lnet/minecraft/class_2;field_1:I", tree));
        equal("Lnet/minecraft/client/Other;copy([Lnet/minecraft/client/Fixture;I)Lnet/minecraft/client/Fixture;",
                VersionModRemapper.selector("Lnet/minecraft/class_2;method_1([Lnet/minecraft/class_1;I)Lnet/minecraft/class_1;", tree));
        equal("copy([Lnet/minecraft/client/Fixture;I)Lnet/minecraft/client/Fixture;",
                VersionModRemapper.selector("method_1([Lnet/minecraft/class_1;I)Lnet/minecraft/class_1;", tree));
        equal("net.minecraft.client.Fixture", VersionModRemapper.selector("net.minecraft.class_1", tree));
        equal("ordinary text", VersionModRemapper.selector("ordinary text", tree));
        try { VersionModRemapper.selector("method_2(I)V", tree); throw new AssertionError("ambiguous selector accepted"); }
        catch (IllegalArgumentException expected) { }
        byte[] table = VersionMappings.table(tree);
        if (!java.util.Arrays.equals(table, VersionMappings.table(tree))) throw new AssertionError("unstable table");
        Path resource = root.resolve("mcfao/runtime-mappings.tsv");
        Files.createDirectories(resource.getParent());
        Files.write(resource, table);
        equal("official", MappingTable.runtimeNamespace());
        equal(java.util.Set.of("official", "intermediary", "obfuscated"), java.util.Set.copyOf(MappingTable.namespaces()));
        equal("net.minecraft.client.Fixture", MappingTable.mapClassName("intermediary", "net.minecraft.class_1"));
        equal("net.minecraft.class_1", MappingTable.unmapClassName("intermediary", "net.minecraft.client.Fixture"));
        equal("net.minecraft.client.Fixture", MappingTable.mapClassName("obfuscated", "a"));
        equal("copy", MappingTable.mapMethodName("intermediary", "net.minecraft.class_1", "method_1",
                "([Lnet/minecraft/class_1;I)Lnet/minecraft/class_1;"));
        equal("count", MappingTable.mapFieldName("intermediary", "net.minecraft.class_1", "field_1", "I"));
        equal("unchanged", MappingTable.mapMethodName("intermediary", "mod.Type", "unchanged", "()V"));
        equal("officialName", MappingTable.mapFieldName("official", "mod.Type", "officialName", "I"));
        try { MappingTable.mapClassName("named", "x"); throw new AssertionError("unknown namespace accepted"); }
        catch (IllegalArgumentException expected) { }
        try { MappingTable.mapClassName("intermediary", "net/minecraft/class_1"); throw new AssertionError("slash name accepted"); }
        catch (IllegalArgumentException expected) { }
        checkGeneratedHooks();
        System.out.println("MCFAO_VERSION_MAPPING_PASS");
    }

    private static void checkMixinRemapModes(Path root, MemoryMappingTree tree) throws Exception {
        for (String mode : List.of("absent", "mixin", "static")) {
            Path input = root.resolve(mode + "-input.jar");
            Path staged = root.resolve(mode + "-staged.jar");
            var manifest = new java.util.jar.Manifest();
            manifest.getMainAttributes().putValue("Manifest-Version", "1.0");
            if (!mode.equals("absent"))
                manifest.getMainAttributes().putValue("Fabric-Loom-Mixin-Remap-Type", mode);
            var fixture = new org.objectweb.asm.ClassWriter(0);
            fixture.visit(65, org.objectweb.asm.Opcodes.ACC_PUBLIC, "fixture/Mixin", null, "java/lang/Object", null);
            var accessor = fixture.visitMethod(org.objectweb.asm.Opcodes.ACC_PUBLIC | org.objectweb.asm.Opcodes.ACC_ABSTRACT,
                    "getCount", "()I", null, null);
            var annotation = accessor.visitAnnotation("Lorg/spongepowered/asm/mixin/gen/Accessor;", true);
            annotation.visit("value", "yarnCount");
            annotation.visitEnd();
            accessor.visitEnd();
            var injection = fixture.visitAnnotation("Lorg/spongepowered/asm/mixin/injection/Inject;", true);
            var targets = injection.visitArray("method");
            targets.visit(null, "Owner.method([LOwner;I)LOwner;"); targets.visitEnd();
            var at = injection.visitAnnotation("at", "Lorg/spongepowered/asm/mixin/injection/At;");
            at.visit("value", "INVOKE");
            at.visit("target", "Owner.method([LOwner;I)LOwner;"); at.visitEnd();
            injection.visitEnd(); fixture.visitEnd();
            try (var zip = new java.util.jar.JarOutputStream(Files.newOutputStream(input), manifest)) {
                for (var entry : java.util.Map.of(
                        "fabric.mod.json", "{\"mixins\":[\"fixture.mixins.json\"]}",
                        "fixture.mixins.json", "{\"refmap\":\"legacy.refmap.json\"}").entrySet()) {
                    zip.putNextEntry(new java.util.zip.ZipEntry(entry.getKey()));
                    zip.write(entry.getValue().getBytes(java.nio.charset.StandardCharsets.UTF_8)); zip.closeEntry();
                }
                zip.putNextEntry(new java.util.zip.ZipEntry("fixture/Mixin.class"));
                zip.write(fixture.toByteArray()); zip.closeEntry();
                zip.putNextEntry(new java.util.zip.ZipEntry("legacy.refmap.json"));
                zip.write("{\"mappings\":{\"fixture/Mixin\":{\"yarnCount\":\"field_1:I\",\"Owner.method([LOwner;I)LOwner;\":\"Lnet/minecraft/class_1;method_1([Lnet/minecraft/class_1;I)Lnet/minecraft/class_1;\"}}}".getBytes(java.nio.charset.StandardCharsets.UTF_8));
                zip.closeEntry();
                zip.putNextEntry(new java.util.zip.ZipEntry("META-INF/OLD.SF"));
                zip.write(new byte[]{1});
                zip.closeEntry();
            }
            VersionModRemapper.stageMixinInput(input, staged);
            try (var before = new java.util.jar.JarFile(input.toFile());
                 var after = new java.util.jar.JarFile(staged.toFile())) {
                equal("static", after.getManifest().getMainAttributes().getValue("Fabric-Loom-Mixin-Remap-Type"));
                equal(new String(before.getInputStream(before.getJarEntry("legacy.refmap.json")).readAllBytes()),
                        new String(after.getInputStream(after.getJarEntry("legacy.refmap.json")).readAllBytes()));
                if (after.getEntry("META-INF/OLD.SF") != null) throw new AssertionError("stale signature retained");
                var node = new org.objectweb.asm.tree.ClassNode();
                new org.objectweb.asm.ClassReader(after.getInputStream(after.getJarEntry("fixture/Mixin.class")))
                        .accept(node, 0);
                equal(mode.equals("static") ? "yarnCount" : "field_1",
                        node.methods.getFirst().visibleAnnotations.getFirst().values.get(1));
                var injected = node.visibleAnnotations.getFirst();
                String expected = mode.equals("static") ? "Owner.method([LOwner;I)LOwner;"
                        : "Lnet/minecraft/class_1;method_1([Lnet/minecraft/class_1;I)Lnet/minecraft/class_1;";
                equal(List.of(expected), injected.values.get(1));
                var nested = (org.objectweb.asm.tree.AnnotationNode) injected.values.get(3);
                equal("INVOKE", nested.values.get(1));
                equal(expected, nested.values.get(3));
            }
        }
    }

    private static void checkResidualSelectors(MemoryMappingTree tree) throws Exception {
        String constructor = "Lnet/minecraft/class_1;<init>(Lnet/minecraft/client/Other;)V";
        String partial = "Lnet/minecraft/class_1;method_1([Lnet/minecraft/client/Fixture;I)Lnet/minecraft/client/Fixture;";
        equal("Lnet/minecraft/client/Fixture;copy([Lnet/minecraft/client/Fixture;I)Lnet/minecraft/client/Fixture;",
                VersionModRemapper.selector(partial, tree));
        var fixture = new org.objectweb.asm.ClassWriter(0);
        fixture.visit(65, org.objectweb.asm.Opcodes.ACC_PUBLIC, "fixture/Residual", null, "java/lang/Object", null);
        var injection = fixture.visitAnnotation("Lorg/spongepowered/asm/mixin/injection/Inject;", true);
        var targets = injection.visitArray("method");
        targets.visit(null, constructor);
        targets.visit(null, partial);
        targets.visitEnd();
        injection.visit("description", "method_123: Synthetic lambda body retained verbatim");
        injection.visitEnd(); fixture.visitEnd();
        var finish = VersionModRemapper.class.getDeclaredMethod("finishSelectors", byte[].class,
                net.fabricmc.loader.impl.lib.mappingio.tree.MappingTree.class);
        finish.setAccessible(true);
        byte[] result = (byte[]) finish.invoke(null, fixture.toByteArray(), tree);
        var node = new org.objectweb.asm.tree.ClassNode();
        new org.objectweb.asm.ClassReader(result).accept(node, 0);
        equal(List.of("Lnet/minecraft/client/Fixture;<init>(Lnet/minecraft/client/Other;)V",
                "Lnet/minecraft/client/Fixture;copy([Lnet/minecraft/client/Fixture;I)Lnet/minecraft/client/Fixture;"),
                node.visibleAnnotations.getFirst().values.get(1));
        equal("method_123: Synthetic lambda body retained verbatim", node.visibleAnnotations.getFirst().values.get(3));
    }

    private static void checkGeneratedHooks() throws Exception {
        var method = AotExport.class.getDeclaredMethod("staticHooks", List.class, java.util.Set.class,
                java.util.Map.class, java.util.Map.class, java.util.Set.class, java.util.Map.class,
                java.util.Map.class, java.util.Map.class, java.util.Set.class);
        method.setAccessible(true);
        String previous = System.getProperty("mcfao.minecraftVersion");
        try {
            for (String version : List.of("26.1.2", "1.21.1")) {
                System.setProperty("mcfao.minecraftVersion", version);
                byte[] code = (byte[]) method.invoke(null, List.of(), java.util.Set.of(),
                        java.util.Map.of(), java.util.Map.of(), java.util.Set.of(), java.util.Map.of(),
                        java.util.Map.of(), java.util.Map.of(), java.util.Set.of());
                var loader = new ClassLoader(VersionMappingProbe.class.getClassLoader()) {
                    Class<?> hooks() { return defineClass("mcfao.aot.Entrypoints", code, 0, code.length); }
                };
                Class<?> hooks = loader.hooks();
                equal("official", hooks.getMethod("mappingRuntimeNamespace").invoke(null));
                equal(version.equals("1.21.1") ? "net.minecraft.client.Fixture" : "net.minecraft.class_1",
                        hooks.getMethod("mappingClassName", String.class, String.class)
                        .invoke(null, "intermediary", "net.minecraft.class_1"));
                equal(version.equals("1.21.1") ? "count" : "field_1",
                        hooks.getMethod("mappingFieldName", String.class, String.class, String.class, String.class)
                        .invoke(null, "intermediary", "net.minecraft.class_1", "field_1", "I"));
            }
        } finally {
            if (previous == null) System.clearProperty("mcfao.minecraftVersion");
            else System.setProperty("mcfao.minecraftVersion", previous);
        }
    }
}
