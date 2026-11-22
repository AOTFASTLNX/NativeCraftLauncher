package mcfao.aot;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.util.Collection;
import java.util.HashMap;
import java.util.LinkedHashSet;
import java.util.Map;
import java.util.Set;

/** Immutable build-derived mappings; no Loader, reflection, or runtime class generation. */
public final class MappingTable {
    private static final String RUNTIME = "official";
    private static final Set<String> NAMESPACES = new LinkedHashSet<>();
    private static final Map<String, String> CLASSES = new HashMap<>();
    private static final Map<String, String> REVERSE_CLASSES = new HashMap<>();
    private static final Map<String, String> MEMBERS = new HashMap<>();

    static {
        NAMESPACES.add(RUNTIME);
        try (var stream = MappingTable.class.getResourceAsStream("/mcfao/runtime-mappings.tsv")) {
            if (stream == null) throw new IllegalStateException("Missing build-derived runtime mapping table");
            try (var reader = new BufferedReader(new InputStreamReader(stream, StandardCharsets.UTF_8))) {
                String line;
                while ((line = reader.readLine()) != null) {
                    String[] row = line.split("\t", -1);
                    if (row.length < 2) throw new IllegalStateException("Invalid runtime mapping row");
                    NAMESPACES.add(row[1]);
                    if (row[0].equals("N") && row.length == 2) continue;
                    if (row[0].equals("C") && row.length == 4) {
                        put(CLASSES, row[1] + '\t' + row[2], row[3]);
                        put(REVERSE_CLASSES, row[1] + '\t' + row[3], row[2]);
                    } else if ((row[0].equals("F") || row[0].equals("M")) && row.length == 6) {
                        put(MEMBERS, row[0] + '\t' + row[1] + '\t' + row[2] + '\t' + row[3] + '\t' + row[4], row[5]);
                    } else throw new IllegalStateException("Invalid runtime mapping row");
                }
            }
        } catch (java.io.IOException error) {
            throw new ExceptionInInitializerError(error);
        }
    }

    private MappingTable() {}
    private static void put(Map<String, String> table, String key, String value) {
        String prior = table.putIfAbsent(key, value);
        if (prior != null && !prior.equals(value)) throw new IllegalStateException("Conflicting runtime mapping " + key);
    }
    private static void namespace(String namespace) {
        if (!NAMESPACES.contains(namespace)) throw new IllegalArgumentException("Unknown mapping namespace " + namespace);
    }
    private static void binaryName(String name) {
        if (name.indexOf('/') >= 0) throw new IllegalArgumentException("Expected a binary class name " + name);
    }
    public static Collection<String> namespaces() { return Set.copyOf(NAMESPACES); }
    public static String runtimeNamespace() { return RUNTIME; }
    public static String mapClassName(String namespace, String name) {
        namespace(namespace); binaryName(name);
        return CLASSES.getOrDefault(namespace + '\t' + name, name);
    }
    public static String unmapClassName(String namespace, String name) {
        namespace(namespace); binaryName(name);
        return REVERSE_CLASSES.getOrDefault(namespace + '\t' + name, name);
    }
    public static String mapFieldName(String namespace, String owner, String name, String descriptor) {
        return member("F", namespace, owner, name, descriptor);
    }
    public static String mapMethodName(String namespace, String owner, String name, String descriptor) {
        return member("M", namespace, owner, name, descriptor);
    }
    private static String member(String kind, String namespace, String owner, String name, String descriptor) {
        namespace(namespace); binaryName(owner);
        return MEMBERS.getOrDefault(kind + '\t' + namespace + '\t' + owner + '\t' + name + '\t' + descriptor, name);
    }
}
