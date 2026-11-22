package fabricaot.version;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.io.Reader;
import java.io.Writer;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.nio.file.FileSystem;
import java.nio.file.FileSystems;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;
import java.util.jar.Attributes;
import java.util.jar.Manifest;
import java.util.zip.ZipEntry;
import java.util.zip.ZipFile;
import java.util.zip.ZipOutputStream;

import net.fabricmc.loader.impl.lib.mappingio.MappingReader;
import net.fabricmc.loader.impl.lib.mappingio.format.proguard.ProGuardFileReader;
import net.fabricmc.loader.impl.lib.mappingio.tree.MappingTree;
import net.fabricmc.loader.impl.lib.mappingio.tree.MemoryMappingTree;
import net.fabricmc.loader.impl.lib.tinyremapper.IMappingProvider;
import net.fabricmc.loader.impl.lib.tinyremapper.InputTag;
import net.fabricmc.loader.impl.lib.tinyremapper.NonClassCopyMode;
import net.fabricmc.loader.impl.lib.tinyremapper.OutputConsumerPath;
import net.fabricmc.loader.impl.lib.tinyremapper.TinyRemapper;
import net.fabricmc.loader.impl.lib.tinyremapper.TinyUtils;
import net.fabricmc.loader.impl.lib.tinyremapper.api.TrLogger;

/**
 * Build-time remapper for a Mojang client jar.
 *
 * <p>The official Mojang ProGuard file maps {@code official -> obfuscated}; Fabric
 * intermediary maps {@code obfuscated -> intermediary}. This class joins both into one
 * Tiny v2 table with the namespaces {@code obfuscated, intermediary, official}, writes
 * the table, and then produces two jars from the untouched input:
 *
 * <ul>
 *   <li>{@code client.jar} in the official (Mojmap) namespace, which is the runtime
 *       namespace reused by the 26.1.2 adapters;</li>
 *   <li>{@code intermediary-client.jar} in the intermediary namespace, used only as a
 *       remap classpath by Fabric's runtime Mod remapper.</li>
 * </ul>
 *
 * <p>No game code is loaded or executed. The only runtime classes touched are the shaded
 * mapping-io and TinyRemapper copies inside Fabric Loader, plus the ASM jars beside it.
 */
public final class GameRemapper {
    private GameRemapper() {}

    private static final String OBF = "obfuscated";
    private static final String INTER = "intermediary";
    private static final String OFFICIAL = "official";

    public static void main(String[] argv) throws Exception {
        Map<String, List<String>> options = parse(argv);
        Path client = Path.of(required(options, "--client"));
        Path output = Path.of(required(options, "--output"));
        Path proguard = Path.of(required(options, "--proguard"));
        Path intermediary = Path.of(required(options, "--intermediary"));
        String expectedClientSha1 = required(options, "--expect-client-sha1");
        long expectedClientSize = Long.parseLong(required(options, "--expect-client-size"));
        String expectedMappingSha1 = required(options, "--expect-mappings-sha1");
        long expectedMappingSize = Long.parseLong(required(options, "--expect-mappings-size"));

        verify(client, expectedClientSha1, expectedClientSize, "client jar");
        verify(proguard, expectedMappingSha1, expectedMappingSize, "client mappings");
        if (Files.isSymbolicLink(client) || Files.isSymbolicLink(proguard)) {
            throw new IllegalArgumentException("input must not be a symlink");
        }

        Files.createDirectories(output);
        MappingTable table = load(proguard, intermediary);
        Path tiny = output.resolve("mappings.tiny");
        writeTiny(table, tiny);

        Path officialJar = output.resolve("client.jar");
        remap(client, officialJar, table, OFFICIAL);
        Path intermediaryJar = output.resolve("intermediary-client.jar");
        remap(client, intermediaryJar, table, INTER);

        // Prove a renamed class actually moved: prefer the Minecraft entry point when the
        // table contains it, otherwise use the first class whose names differ.
        ClassRow probe = table.classes.get("fgo");
        if (probe == null) {
            for (ClassRow row : table.classes.values()) {
                if (!row.obf.equals(row.official) && !row.obf.equals(row.intermediary)) {
                    probe = row;
                    break;
                }
            }
        }
        if (probe == null) throw new IOException("mapping table has no renamed class to verify");
        verifyMapped(officialJar, probe.official + ".class", probe.obf + ".class");
        verifyMapped(intermediaryJar, probe.intermediary + ".class", probe.obf + ".class");

        Map<String, Object> result = new LinkedHashMap<>();
        result.put("client", officialJar.toString());
        result.put("clientSHA256", sha256(officialJar));
        result.put("intermediaryClient", intermediaryJar.toString());
        result.put("intermediaryClientSHA256", sha256(intermediaryJar));
        result.put("mappings", tiny.toString());
        result.put("mappingsSHA256", sha256(tiny));
        result.put("namespaces", List.of(OBF, INTER, OFFICIAL));
        result.put("classes", table.classes.size());
        result.put("unmatchedIntermediaryClasses", table.unmatchedClasses);
        result.put("unmatchedIntermediaryMembers", table.unmatchedMembers);
        result.put("sourceClientSHA1", expectedClientSha1);
        result.put("sourceMappingsSHA1", expectedMappingSha1);
        System.out.println(json(result));
    }

    // ------------------------------------------------------------------ model

    private static final class ClassRow {
        final String obf;
        final String official;
        String intermediary;
        final List<MemberRow> fields = new ArrayList<>();
        final List<MemberRow> methods = new ArrayList<>();

        ClassRow(String obf, String official) {
            this.obf = obf;
            this.official = official;
            this.intermediary = obf;
        }
    }

    private static final class MemberRow {
        final String obfName;
        String officialName;
        final String obfDesc;
        String intermediaryName;

        MemberRow(String obfName, String officialName, String obfDesc) {
            this.obfName = obfName;
            this.officialName = officialName;
            this.obfDesc = obfDesc;
            this.intermediaryName = obfName;
        }
    }

    private static final class MappingTable {
        final TreeMap<String, ClassRow> classes = new TreeMap<>();
        final Map<String, String> officialToObf = new LinkedHashMap<>();
        final Map<String, String> obfToOfficial = new LinkedHashMap<>();
        final Map<String, String> obfToIntermediary = new LinkedHashMap<>();
        int unmatchedClasses;
        int unmatchedMembers;
    }

    private static MappingTable load(Path proguard, Path intermediary) throws IOException {
        MemoryMappingTree officialTree = new MemoryMappingTree();
        try (Reader reader = Files.newBufferedReader(proguard, StandardCharsets.UTF_8)) {
            ProGuardFileReader.read(reader, OFFICIAL, OBF, officialTree);
        }

        MemoryMappingTree intermediaryTree = new MemoryMappingTree();
        Path tinySource = intermediary;
        Path extracted = null;
        if (intermediary.toString().endsWith(".jar")) {
            extracted = Files.createTempFile("fabricaot-intermediary", ".tiny");
            try (ZipFile zip = new ZipFile(intermediary.toFile())) {
                ZipEntry entry = zip.getEntry("mappings/mappings.tiny");
                if (entry == null) throw new IOException("intermediary jar has no mappings/mappings.tiny");
                try (InputStream in = zip.getInputStream(entry)) {
                    Files.copy(in, extracted, StandardCopyOption.REPLACE_EXISTING);
                }
            }
            tinySource = extracted;
        }
        try {
            MappingReader.read(tinySource, intermediaryTree);
        } finally {
            if (extracted != null) Files.deleteIfExists(extracted);
        }
        // Fabric's intermediary Tiny v2 names the obfuscated side "official" (its historical
        // header) even though column 0 holds the obfuscated names; accept both spellings and
        // rely on the ProGuard join below to prove the mapping is the obfuscated client.
        String intermediarySource = intermediaryTree.getSrcNamespace();
        if (!(OBF.equals(intermediarySource) || OFFICIAL.equals(intermediarySource))
                || !intermediaryTree.getDstNamespaces().contains(INTER)) {
            throw new IOException("intermediary mapping must be obfuscated -> " + INTER
                    + ", got " + intermediarySource + " -> "
                    + intermediaryTree.getDstNamespaces());
        }
        // MappingTreeView indexes 0..n-1 over *destination* namespaces; the source name is
        // only available through getSrcName(). ProGuard therefore yields obf via getName(0)
        // and obf descriptors via getDesc(0), while intermediary yields intermediary names
        // via getName(0) and its source columns hold the obfuscated names/descriptors.
        int interIndex = intermediaryTree.getDstNamespaces().indexOf(INTER);

        MappingTable table = new MappingTable();
        for (MappingTree.ClassMapping cls : officialTree.getClasses()) {
            String official = cls.getSrcName();
            String obf = cls.getName(0);
            ClassRow row = new ClassRow(obf, official);
            table.classes.put(obf, row);
            table.officialToObf.put(official, obf);
            table.obfToOfficial.put(obf, official);
        }
        for (MappingTree.ClassMapping cls : officialTree.getClasses()) {
            ClassRow row = table.classes.get(cls.getName(0));
            for (MappingTree.FieldMapping field : cls.getFields()) {
                String obfName = field.getName(0);
                String obfDesc = field.getDesc(0);
                if (obfDesc == null) obfDesc = mapDesc(field.getSrcDesc(), table.officialToObf);
                row.fields.add(new MemberRow(obfName, field.getSrcName(), obfDesc));
            }
            for (MappingTree.MethodMapping method : cls.getMethods()) {
                String obfName = method.getName(0);
                String obfDesc = method.getDesc(0);
                if (obfDesc == null) obfDesc = mapDesc(method.getSrcDesc(), table.officialToObf);
                row.methods.add(new MemberRow(obfName, method.getSrcName(), obfDesc));
            }
        }

        // Pass 1: complete the obf -> intermediary class table so descriptor mapping can use it.
        for (MappingTree.ClassMapping cls : intermediaryTree.getClasses()) {
            String obf = cls.getSrcName();
            String inter = cls.getDstName(interIndex);
            if (inter == null) continue;
            table.obfToIntermediary.put(obf, inter);
            ClassRow row = table.classes.get(obf);
            if (row == null) {
                table.unmatchedClasses++;
                continue;
            }
            row.intermediary = inter;
        }

        // Pass 2: join members on the obfuscated (owner, name, descriptor) key.
        for (MappingTree.ClassMapping cls : intermediaryTree.getClasses()) {
            ClassRow row = table.classes.get(cls.getSrcName());
            if (row == null) continue;
            Map<String, MemberRow> fields = new LinkedHashMap<>();
            for (MemberRow member : row.fields) fields.put(member.obfName + member.obfDesc, member);
            for (MappingTree.FieldMapping field : cls.getFields()) {
                MemberRow member = fields.get(field.getSrcName() + field.getSrcDesc());
                if (member == null) {
                    table.unmatchedMembers++;
                    continue;
                }
                member.intermediaryName = field.getDstName(interIndex);
            }
            Map<String, MemberRow> methods = new LinkedHashMap<>();
            for (MemberRow member : row.methods) methods.put(member.obfName + member.obfDesc, member);
            for (MappingTree.MethodMapping method : cls.getMethods()) {
                MemberRow member = methods.get(method.getSrcName() + method.getSrcDesc());
                if (member == null) {
                    table.unmatchedMembers++;
                    continue;
                }
                member.intermediaryName = method.getDstName(interIndex);
            }
        }

        for (ClassRow row : table.classes.values()) {
            for (MemberRow member : row.fields) {
                if (member.intermediaryName == null) member.intermediaryName = member.obfName;
            }
            for (MemberRow member : row.methods) {
                if (member.intermediaryName == null) member.intermediaryName = member.obfName;
            }
        }
        return table;
    }

    // -------------------------------------------------------------- tiny output

    private static void writeTiny(MappingTable table, Path target) throws IOException {
        // Tiny v2 member lines are: kind, source descriptor, then one name column per
        // namespace. Descriptors in the intermediary/official columns are not permitted and
        // would be read as the destination name by TinyUtils.
        try (Writer writer = Files.newBufferedWriter(target, StandardCharsets.UTF_8)) {
            writer.write("tiny\t2\t0\t" + OBF + "\t" + INTER + "\t" + OFFICIAL + "\n");
            for (ClassRow row : table.classes.values()) {
                writer.write("c\t" + escape(row.obf) + "\t" + escape(row.intermediary)
                        + "\t" + escape(row.official) + "\n");
                for (MemberRow member : row.fields) {
                    writer.write("\tf\t" + escape(member.obfDesc) + "\t" + escape(member.obfName)
                            + "\t" + escape(member.intermediaryName)
                            + "\t" + escape(member.officialName) + "\n");
                }
                for (MemberRow member : row.methods) {
                    writer.write("\tm\t" + escape(member.obfDesc) + "\t" + escape(member.obfName)
                            + "\t" + escape(member.intermediaryName)
                            + "\t" + escape(member.officialName) + "\n");
                }
            }
        }
    }

    private static String escape(String value) {
        if (value == null) return "";
        StringBuilder out = new StringBuilder(value.length());
        for (int i = 0; i < value.length(); i++) {
            char c = value.charAt(i);
            switch (c) {
                case '\\' -> out.append("\\\\");
                case '\t' -> out.append("\\t");
                case '\n' -> out.append("\\n");
                case '\r' -> out.append("\\r");
                default -> out.append(c);
            }
        }
        return out.toString();
    }

    // ----------------------------------------------------------------- remapping

    private static void remap(Path input, Path target, MappingTable table, String destination) throws IOException {
        Files.deleteIfExists(target);
        IMappingProvider provider = provider(table, destination);
        TinyRemapper remapper = TinyRemapper.newRemapper(new Log())
                .withMappings(provider)
                .renameInvalidLocals(false)
                .build();
        InputTag tag = remapper.createInputTag();
        remapper.readInputsAsync(tag, input).join();
        // OutputConsumerPath opens an existing empty file as a zip and fails; it needs a path
        // that does not exist yet (or a valid archive).
        Path staging = Files.createTempDirectory("fabricaot-remap").resolve("staged.jar");
        try {
            try (OutputConsumerPath consumer = new OutputConsumerPath.Builder(staging).build()) {
                try (FileSystem zip = FileSystems.newFileSystem(URI.create("jar:" + input.toUri()),
                        Collections.emptyMap())) {
                    consumer.addNonClassFiles(zip.getPath("/"), NonClassCopyMode.FIX_META_INF, remapper);
                }
                remapper.apply(consumer, tag);
            }
            remapper.finish();
            sanitize(staging, target);
        } finally {
            Files.deleteIfExists(staging);
            Files.deleteIfExists(staging.getParent());
        }
    }

    private static IMappingProvider provider(MappingTable table, String destination) {
        boolean official = OFFICIAL.equals(destination);
        return acceptor -> {
            for (ClassRow row : table.classes.values()) {
                acceptor.acceptClass(row.obf, official ? row.official : row.intermediary);
                for (MemberRow member : row.fields) {
                    acceptor.acceptField(new IMappingProvider.Member(row.obf, member.obfName, member.obfDesc),
                            official ? member.officialName : member.intermediaryName);
                }
                for (MemberRow member : row.methods) {
                    acceptor.acceptMethod(new IMappingProvider.Member(row.obf, member.obfName, member.obfDesc),
                            official ? member.officialName : member.intermediaryName);
                }
            }
        };
    }

    private static final class Log implements TrLogger {
        @Override
        public void log(Level level, String message) {
            if (level == Level.WARN || level == Level.ERROR) {
                System.err.println("tinyremapper " + level + ": " + message);
            }
        }
    }

    // ------------------------------------------------------------- jar sanitation

    private static void sanitize(Path source, Path target) throws IOException {
        Manifest manifest = null;
        try (ZipFile zip = new ZipFile(source.toFile())) {
            ZipEntry entry = zip.getEntry("META-INF/MANIFEST.MF");
            if (entry != null) {
                try (InputStream in = zip.getInputStream(entry)) {
                    manifest = new Manifest(in);
                }
            }
        }
        if (manifest != null) {
            manifest.getEntries().clear();
            Attributes main = manifest.getMainAttributes();
            main.keySet().removeIf(key -> key.toString().endsWith("-Digest")
                    || key.toString().startsWith("SHA-"));
        }

        // TinyRemapper finishes classes on a thread pool, so its output order varies run to
        // run. Writing entries sorted by name with a fixed timestamp and deflate level makes
        // the mapped jars byte-identical for identical inputs, which keeps hashes meaningful.
        Map<String, byte[]> entries = new TreeMap<>();
        try (ZipFile zip = new ZipFile(source.toFile())) {
            for (ZipEntry entry : Collections.list(zip.entries())) {
                String name = entry.getName();
                if (entry.isDirectory()) continue;
                if (name.startsWith("META-INF/") && (name.endsWith(".SF") || name.endsWith(".RSA")
                        || name.endsWith(".DSA") || name.endsWith(".EC"))) {
                    continue;
                }
                try (InputStream in = zip.getInputStream(entry)) {
                    entries.put(name, in.readAllBytes());
                }
            }
        }
        try (ZipOutputStream out = new ZipOutputStream(Files.newOutputStream(target))) {
            out.setLevel(java.util.zip.Deflater.BEST_COMPRESSION);
            // JarInputStream only exposes a manifest that appears before other entries.
            byte[] manifestBytes = entries.remove("META-INF/MANIFEST.MF");
            if (manifestBytes != null) {
                ZipEntry copy = new ZipEntry("META-INF/MANIFEST.MF");
                copy.setTime(0L);
                out.putNextEntry(copy);
                if (manifest != null) manifest.write(out); else out.write(manifestBytes);
                out.closeEntry();
            }
            for (Map.Entry<String, byte[]> entry : entries.entrySet()) {
                ZipEntry copy = new ZipEntry(entry.getKey());
                copy.setTime(0L);
                out.putNextEntry(copy);
                out.write(entry.getValue());
                out.closeEntry();
            }
        }
    }

    // ------------------------------------------------------------------ helpers

    private static String mapDesc(String desc, Map<String, String> classMap) {
        if (desc == null) return null;
        StringBuilder out = new StringBuilder(desc.length());
        int i = 0;
        while (i < desc.length()) {
            char c = desc.charAt(i);
            if (c == 'L') {
                int end = desc.indexOf(';', i);
                if (end < 0) throw new IllegalArgumentException("invalid descriptor: " + desc);
                String name = desc.substring(i + 1, end);
                out.append('L').append(classMap.getOrDefault(name, name)).append(';');
                i = end + 1;
            } else {
                out.append(c);
                i++;
            }
        }
        return out.toString();
    }

    private static void verify(Path path, String expectedSha1, long expectedSize, String label) throws Exception {
        if (!Files.isRegularFile(path)) throw new IOException(label + " is missing: " + path);
        long size = Files.size(path);
        if (size != expectedSize) throw new IOException(label + " size mismatch: " + size + " != " + expectedSize);
        if (!sha1(path).equalsIgnoreCase(expectedSha1)) throw new IOException(label + " SHA-1 mismatch");
    }

    private static void verifyMapped(Path jar, String required, String forbidden) throws IOException {
        try (ZipFile zip = new ZipFile(jar.toFile())) {
            if (zip.getEntry(required) == null) throw new IOException("mapped jar lacks " + required);
            if (zip.getEntry(forbidden) != null) throw new IOException("mapped jar still contains " + forbidden);
        }
    }

    private static String sha1(Path path) throws Exception {
        return digest(path, "SHA-1");
    }

    private static String sha256(Path path) throws Exception {
        return digest(path, "SHA-256");
    }

    private static String digest(Path path, String algorithm) throws Exception {
        MessageDigest digest = MessageDigest.getInstance(algorithm);
        try (InputStream in = Files.newInputStream(path)) {
            byte[] buffer = new byte[65536];
            int read;
            while ((read = in.read(buffer)) > 0) digest.update(buffer, 0, read);
        }
        StringBuilder out = new StringBuilder();
        for (byte value : digest.digest()) out.append(String.format("%02x", value));
        return out.toString();
    }

    private static Map<String, List<String>> parse(String[] argv) {
        Map<String, List<String>> options = new LinkedHashMap<>();
        for (int i = 0; i < argv.length; i++) {
            String arg = argv[i];
            if (!arg.startsWith("--")) throw new IllegalArgumentException("unexpected argument: " + arg);
            if (i + 1 >= argv.length) throw new IllegalArgumentException("missing value for " + arg);
            options.computeIfAbsent(arg, key -> new ArrayList<>()).add(argv[++i]);
        }
        return options;
    }

    private static String required(Map<String, List<String>> options, String key) {
        List<String> values = options.get(key);
        if (values == null || values.size() != 1) throw new IllegalArgumentException("exactly one " + key + " is required");
        return values.get(0);
    }

    private static String json(Map<String, Object> values) {
        StringBuilder out = new StringBuilder("{");
        boolean first = true;
        for (Map.Entry<String, Object> entry : values.entrySet()) {
            if (!first) out.append(',');
            first = false;
            out.append('"').append(entry.getKey()).append("\":");
            Object value = entry.getValue();
            if (value instanceof Number) {
                out.append(value);
            } else if (value instanceof List<?> list) {
                out.append('[');
                for (int i = 0; i < list.size(); i++) {
                    if (i > 0) out.append(',');
                    out.append('"').append(list.get(i)).append('"');
                }
                out.append(']');
            } else {
                out.append('"').append(String.valueOf(value).replace("\\", "\\\\").replace("\"", "\\\"")).append('"');
            }
        }
        return out.append('}').toString();
    }
}
