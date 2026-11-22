package mcfao.build;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.zip.ZipEntry;
import java.util.zip.ZipFile;
import java.util.zip.ZipOutputStream;
import net.fabricmc.api.EnvType;
import net.fabricmc.loader.impl.FabricLoaderImpl;
import net.fabricmc.loader.impl.discovery.DirectoryModCandidateFinder;
import net.fabricmc.loader.impl.discovery.ClasspathModCandidateFinder;
import net.fabricmc.loader.impl.discovery.ModCandidateImpl;
import net.fabricmc.loader.impl.discovery.ModDiscoverer;
import net.fabricmc.loader.impl.discovery.ModResolver;
import net.fabricmc.loader.impl.discovery.RuntimeModRemapper;
import net.fabricmc.loader.impl.game.minecraft.MinecraftGameProvider;
import net.fabricmc.loader.impl.launch.knot.Knot;
import net.fabricmc.loader.impl.metadata.BuiltinModMetadata;
import net.fabricmc.loader.impl.metadata.DependencyOverrides;
import net.fabricmc.loader.impl.metadata.VersionOverrides;
import net.fabricmc.loader.impl.lib.mappingio.tree.MappingTree;

/** First-party build tool. No game or Mod entrypoint is loaded during remapping. */
final class VersionModRemapper {
    private VersionModRemapper() {}

    static Map<String, ?> remap(Path client, Path work, Path mods, Path output, String version) throws Exception {
        var knot = new Knot(EnvType.CLIENT);
        var loader = FabricLoaderImpl.INSTANCE;
        loader.setGameProvider(new MinecraftGameProvider() {
            @Override public Path getLaunchDirectory() { return work; }
            @Override public java.util.Collection<BuiltinMod> getBuiltinMods() {
                return List.of(new BuiltinMod(List.of(client),
                        new BuiltinModMetadata.Builder("minecraft", version).build()));
            }
        });
        var discoverer = new ModDiscoverer(new VersionOverrides(), new DependencyOverrides(work.resolve("config")));
        discoverer.addCandidateFinder(new ClasspathModCandidateFinder());
        discoverer.addCandidateFinder(new DirectoryModCandidateFinder(mods, true));
        Map<String, Set<ModCandidateImpl>> disabled = new HashMap<>();
        var selected = ModResolver.resolve(discoverer.discoverMods(loader, disabled), EnvType.CLIENT, disabled);
        var remap = selected.stream().filter(ModCandidateImpl::getRequiresRemap).toList();
        Files.createDirectories(output);
        Path temporary = work.resolve("remap-tmp");
        Files.createDirectories(temporary);
        Path remapped = work.resolve("remap-jars");
        Files.createDirectories(remapped);
        Set<String> reserved = Set.of("minecraft", "java", "fabricloader", "mixinextras");
        MappingTree tree = knot.getMappingConfiguration().getMappings();
        Map<String, String> originalPaths = new HashMap<>();
        var provided = new ArrayList<String>();
        for (var mod : remap) {
            if (reserved.contains(mod.getId()) && mod.isRoot())
                throw new IllegalArgumentException("Reserved dependency identity: " + mod.getId());
            Path input = mod.hasPath() ? mod.getPaths().get(0) : mod.copyToDir(temporary, true);
            originalPaths.put(mod.getId(), input.toString());
            // Expand legacy refmap selectors before the static extension remaps
            // shadow/accessor members and annotation selectors together.
            Path staged = temporary.resolve(mod.getId() + "-input.jar");
            stageMixinInput(input, staged);
            mod.setPaths(List.of(staged));
        }
        RuntimeModRemapper.remap(remap, temporary, remapped);
        var rows = new ArrayList<Map<String, ?>>();
        for (var mod : remap) {
            if (reserved.contains(mod.getId())) { provided.add(mod.getId()); continue; }
            if (mod.getPaths().size() != 1) throw new IllegalArgumentException("Expected one remapped Mod path");
            Path input = mod.getPaths().get(0);
            Path destination = output.resolve(mod.getId() + ".jar");
            // The resolved graph becomes a flat, explicit closure. Embedded copies must not
            // reintroduce intermediary bytecode after Loader has remapped the selected nodes.
            try (var zip = new ZipFile(input.toFile());
                 var target = new ZipOutputStream(Files.newOutputStream(destination))) {
                JsonObject metadata = JsonParser.parseString(new String(zip.getInputStream(
                        zip.getEntry("fabric.mod.json")).readAllBytes(), StandardCharsets.UTF_8)).getAsJsonObject();
                Set<String> nested = new java.util.HashSet<>();
                if (metadata.has("jars")) for (var child : metadata.getAsJsonArray("jars"))
                    nested.add(child.getAsJsonObject().get("file").getAsString());
                metadata.remove("jars");
                Set<String> refmaps = new java.util.HashSet<>();
                for (String config : mod.getMetadata().getMixinConfigs(EnvType.CLIENT)) {
                    var entry = zip.getEntry(config);
                    if (entry == null) throw new IllegalArgumentException("Missing Mixin config " + config);
                    JsonObject parsed = JsonParser.parseString(new String(zip.getInputStream(entry).readAllBytes(),
                            StandardCharsets.UTF_8)).getAsJsonObject();
                    if (parsed.has("refmap")) refmaps.add(parsed.get("refmap").getAsString());
                }
                for (var entry : zip.stream().sorted(java.util.Comparator.comparing(ZipEntry::getName)).toList()) {
                    String name = entry.getName();
                    if (nested.contains(name)) continue;
                    byte[] data = zip.getInputStream(entry).readAllBytes();
                    if (name.equals("fabric.mod.json")) data = metadata.toString().getBytes(StandardCharsets.UTF_8);
                    else if (name.endsWith(".class")) data = finishSelectors(data, tree);
                    else if (refmaps.contains(name)) {
                        JsonObject refmap = JsonParser.parseString(new String(data, StandardCharsets.UTF_8)).getAsJsonObject();
                        rewriteValues(refmap, tree);
                        data = refmap.toString().getBytes(StandardCharsets.UTF_8);
                    }
                    var copy = new ZipEntry(name);
                    copy.setTime(0);
                    target.putNextEntry(copy);
                    target.write(data);
                    target.closeEntry();
                }
            }
            rows.add(Map.of("id", mod.getId(), "version", mod.getVersion().getFriendlyString(),
                    "path", destination.toString(), "sourceNamespace", "intermediary",
                    "runtimeNamespace", "official", "originalPath", originalPaths.get(mod.getId()),
                    "root", mod.isRoot()));
        }
        return Map.of("status", "MODS_REMAPPED", "minecraftVersion", version, "mods", rows,
                "providedRuntimeDependencies", provided);
    }

    static void stageMixinInput(Path input, Path destination) throws Exception {
        try (var source = new ZipFile(input.toFile());
             var target = new ZipOutputStream(Files.newOutputStream(destination))) {
            var original = source.getEntry("META-INF/MANIFEST.MF");
            var manifest = original == null ? new java.util.jar.Manifest()
                    : new java.util.jar.Manifest(source.getInputStream(original));
            manifest.getMainAttributes().putValue("Manifest-Version", "1.0");
            boolean legacy = !"static".equalsIgnoreCase(manifest.getMainAttributes()
                    .getValue("Fabric-Loom-Mixin-Remap-Type"));
            Map<String, Map<String, String>> references = legacy ? legacyReferences(source) : Map.of();
            manifest.getMainAttributes().putValue("Fabric-Loom-Mixin-Remap-Type", "static");
            manifest.getEntries().clear();
            target.putNextEntry(new ZipEntry("META-INF/MANIFEST.MF"));
            manifest.write(target);
            target.closeEntry();
            for (var entry : source.stream().sorted(java.util.Comparator.comparing(ZipEntry::getName)).toList()) {
                String name = entry.getName(), upper = name.toUpperCase(java.util.Locale.ROOT);
                if (upper.equals("META-INF/MANIFEST.MF") || (upper.startsWith("META-INF/")
                        && (upper.endsWith(".SF") || upper.endsWith(".RSA") || upper.endsWith(".DSA")
                            || upper.endsWith(".EC")))) continue;
                var copy = new ZipEntry(name); copy.setTime(0);
                target.putNextEntry(copy);
                try (var stream = source.getInputStream(entry)) {
                    byte[] data = stream.readAllBytes();
                    if (name.endsWith(".class") && references.containsKey(name.substring(0, name.length() - 6)))
                        data = expandReferences(data, references.get(name.substring(0, name.length() - 6)));
                    target.write(data);
                }
                target.closeEntry();
            }
        }
    }

    private static Map<String, Map<String, String>> legacyReferences(ZipFile source) throws Exception {
        var metadataEntry = source.getEntry("fabric.mod.json");
        if (metadataEntry == null) return Map.of();
        JsonObject metadata;
        try (var stream = source.getInputStream(metadataEntry)) {
            metadata = JsonParser.parseString(new String(stream.readAllBytes(), StandardCharsets.UTF_8)).getAsJsonObject();
        }
        Map<String, Map<String, String>> result = new HashMap<>();
        if (!metadata.has("mixins")) return result;
        for (var mixin : metadata.getAsJsonArray("mixins")) {
            String config = mixin.isJsonPrimitive() ? mixin.getAsString()
                    : mixin.getAsJsonObject().get("config").getAsString();
            var configEntry = source.getEntry(config);
            if (configEntry == null) throw new IllegalArgumentException("Missing Mixin config " + config);
            JsonObject parsed;
            try (var stream = source.getInputStream(configEntry)) {
                parsed = JsonParser.parseString(new String(stream.readAllBytes(), StandardCharsets.UTF_8)).getAsJsonObject();
            }
            if (!parsed.has("refmap")) continue;
            String path = parsed.get("refmap").getAsString();
            var refmapEntry = source.getEntry(path);
            if (refmapEntry == null) throw new IllegalArgumentException("Missing Mixin refmap " + path);
            JsonObject refmap;
            try (var stream = source.getInputStream(refmapEntry)) {
                refmap = JsonParser.parseString(new String(stream.readAllBytes(), StandardCharsets.UTF_8)).getAsJsonObject();
            }
            if (!refmap.has("mappings")) continue;
            for (var cls : refmap.getAsJsonObject("mappings").entrySet()) {
                var selectors = result.computeIfAbsent(cls.getKey().replace('.', '/'), ignored -> new HashMap<>());
                for (var selector : cls.getValue().getAsJsonObject().entrySet()) {
                    String value = selector.getValue().getAsString();
                    String previous = selectors.putIfAbsent(selector.getKey(), value);
                    if (previous != null && !previous.equals(value))
                        throw new IllegalArgumentException("Conflicting Mixin refmap selector " + cls.getKey() + " / " + selector.getKey());
                }
            }
        }
        return result;
    }

    private static byte[] expandReferences(byte[] data, Map<String, String> selectors) {
        var cls = new org.objectweb.asm.tree.ClassNode();
        new org.objectweb.asm.ClassReader(data).accept(cls, 0);
        expandAnnotations(cls.visibleAnnotations, selectors);
        expandAnnotations(cls.invisibleAnnotations, selectors);
        for (var method : cls.methods) {
            expandAnnotations(method.visibleAnnotations, selectors);
            expandAnnotations(method.invisibleAnnotations, selectors);
        }
        for (var field : cls.fields) {
            expandAnnotations(field.visibleAnnotations, selectors);
            expandAnnotations(field.invisibleAnnotations, selectors);
        }
        var writer = new org.objectweb.asm.ClassWriter(0);
        cls.accept(writer);
        return writer.toByteArray();
    }

    private static byte[] finishSelectors(byte[] data, MappingTree tree) {
        var strings = new java.util.HashSet<String>();
        var cls = new org.objectweb.asm.tree.ClassNode();
        new org.objectweb.asm.ClassReader(data).accept(cls, 0);
        java.util.function.Consumer<Object> collect = new java.util.function.Consumer<>() {
            @Override public void accept(Object value) {
                if (value instanceof String text && (text.contains("net/minecraft/class_")
                        || text.contains("net.minecraft.class_")
                        || text.matches("(?:method|field)_\\d+(?:\\([^\\s]*\\)[^\\s]+|:[^\\s]+)?")))
                    strings.add(text);
                else if (value instanceof org.objectweb.asm.tree.AnnotationNode annotation) accept(annotation.values);
                else if (value instanceof List<?> values) values.forEach(this);
            }
        };
        collect.accept(cls.visibleAnnotations); collect.accept(cls.invisibleAnnotations);
        for (var method : cls.methods) { collect.accept(method.visibleAnnotations); collect.accept(method.invisibleAnnotations); }
        for (var field : cls.fields) { collect.accept(field.visibleAnnotations); collect.accept(field.invisibleAnnotations); }
        if (strings.isEmpty()) return data;
        Map<String, String> selectors = new HashMap<>();
        for (String text : strings) selectors.put(text, text.startsWith("(") || text.startsWith("[")
                ? tree.mapDesc(text, tree.getNamespaceId("intermediary"), tree.getNamespaceId("official"))
                : selector(text, tree));
        return expandReferences(data, selectors);
    }

    private static void expandAnnotations(List<? extends org.objectweb.asm.tree.AnnotationNode> annotations,
                                          Map<String, String> selectors) {
        if (annotations == null) return;
        for (var annotation : annotations) if (annotation.values != null) {
            boolean accessor = annotation.desc.equals("Lorg/spongepowered/asm/mixin/gen/Accessor;")
                    || annotation.desc.equals("Lorg/spongepowered/asm/mixin/gen/Invoker;");
            for (int i = 1; i < annotation.values.size(); i += 2) {
                Object value = expandAnnotationValue(annotation.values.get(i), selectors);
                // Accessors store a member name, whereas refmaps include its descriptor.
                // The extension infers that descriptor from the accessor method itself.
                if (accessor && annotation.values.get(i - 1).equals("value") && value instanceof String text) {
                    int end = text.indexOf('(');
                    if (end < 0) end = text.indexOf(':');
                    if (end >= 0) value = text.substring(0, end);
                }
                annotation.values.set(i, value);
            }
        }
    }

    private static Object expandAnnotationValue(Object value, Map<String, String> selectors) {
        if (value instanceof String text) return selectors.getOrDefault(text, text);
        if (value instanceof org.objectweb.asm.tree.AnnotationNode annotation)
            expandAnnotations(List.of(annotation), selectors);
        else if (value instanceof List<?> values)
            return values.stream().map(item -> expandAnnotationValue(item, selectors)).collect(java.util.stream.Collectors.toCollection(ArrayList::new));
        return value;
    }

    private static void rewriteValues(JsonElement value, MappingTree tree) {
        if (value.isJsonObject()) {
            for (var entry : value.getAsJsonObject().entrySet()) {
                JsonElement child = entry.getValue();
                if (child.isJsonPrimitive() && child.getAsJsonPrimitive().isString())
                    entry.setValue(new com.google.gson.JsonPrimitive(selector(child.getAsString(), tree)));
                else rewriteValues(child, tree);
            }
        } else if (value.isJsonArray()) for (var child : value.getAsJsonArray()) rewriteValues(child, tree);
    }

    static String selector(String value, MappingTree tree) {
        int source = tree.getNamespaceId("intermediary"), target = tree.getNamespaceId("official");
        if (value.startsWith("L") && value.contains(";")) {
            int end = value.indexOf(';');
            String owner = value.substring(1, end), tail = value.substring(end + 1);
            String mappedOwner = tree.mapClassName(owner, source, target);
            if (tail.isEmpty()) return "L" + mappedOwner + ";";
            int method = tail.indexOf('('), field = tail.indexOf(':');
            var cls = tree.getClass(owner, source);
            if (method >= 0) {
                String name = tail.substring(0, method), desc = tail.substring(method);
                String lookupDesc = tree.mapDesc(desc, target, source);
                var member = cls == null ? null : cls.getMethod(name, lookupDesc, source);
                String mapped = member == null || member.getName(target) == null
                        ? inheritedSelector(name, lookupDesc, true, tree) : member.getName(target);
                return "L" + mappedOwner + ";" + mapped + tree.mapDesc(desc, source, target);
            } else if (field >= 0) {
                String name = tail.substring(0, field), desc = tail.substring(field + 1);
                String lookupDesc = tree.mapDesc(desc, target, source);
                var member = cls == null ? null : cls.getField(name, lookupDesc, source);
                String mapped = member == null || member.getName(target) == null
                        ? inheritedSelector(name, lookupDesc, false, tree) : member.getName(target);
                return "L" + mappedOwner + ";" + mapped + ":" + tree.mapDesc(desc, source, target);
            }
        }
        if (value.startsWith("net/minecraft/") || value.startsWith("com/mojang/"))
            return tree.mapClassName(value, source, target);
        if (value.startsWith("net.minecraft.") || value.startsWith("com.mojang."))
            return tree.mapClassName(value.replace('.', '/'), source, target).replace('/', '.');
        // Ownerless intermediary selectors must resolve uniquely across the mapping tree.
        if (value.startsWith("method_") || value.startsWith("field_")) {
            int split = value.indexOf('(');
            if (split < 0) split = value.indexOf(':');
            String name = split < 0 ? value : value.substring(0, split);
            String desc = split < 0 ? null : value.substring(split + (value.charAt(split) == ':' ? 1 : 0));
            String lookupDesc = desc == null ? null : tree.mapDesc(desc, target, source);
            Set<String> names = new java.util.HashSet<>();
            for (var cls : tree.getClasses()) {
                var members = value.startsWith("method_") ? cls.getMethods() : cls.getFields();
                for (var member : members) if (name.equals(member.getName(source))
                        && (lookupDesc == null || lookupDesc.equals(member.getDesc(source))) && member.getName(target) != null)
                    names.add(member.getName(target));
            }
            if (names.size() != 1) throw new IllegalArgumentException("Ambiguous/unmapped intermediary selector " + value);
            return names.iterator().next() + (desc == null ? "" : (value.charAt(split) == ':' ? ":" : "")
                    + tree.mapDesc(desc, source, target));
        }
        return value;
    }

    private static String inheritedSelector(String name, String desc, boolean method, MappingTree tree) {
        // Intermediary member ids are global. A refmap may name an inheriting
        // owner whose mapping row does not repeat the declaring member.
        if (!(method ? name.startsWith("method_") : name.startsWith("field_"))) return name;
        String value = name + (method ? "" : ":") + desc;
        String mapped = selector(value, tree);
        int end = mapped.indexOf(method ? '(' : ':');
        return mapped.substring(0, end);
    }
}
