package mcfao.build;

import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import net.fabricmc.loader.impl.lib.mappingio.tree.MappingTree;

/** Serialize only value data for MappingResolver's AOT implementation. */
final class VersionMappings {
    private VersionMappings() {}
    static byte[] table(MappingTree tree) {
        var namespaces = new ArrayList<>(tree.getDstNamespaces());
        namespaces.add(tree.getSrcNamespace());
        int runtime = tree.getNamespaceId("official");
        var lines = new java.util.TreeSet<String>();
        for (String namespace : namespaces) {
            int source = tree.getNamespaceId(namespace);
            lines.add("N\t" + namespace);
            if (source == runtime) continue;
            for (var cls : tree.getClasses()) {
                String from = cls.getName(source), to = cls.getName(runtime);
                if (from == null || to == null) continue;
                String owner = from.replace('/', '.');
                lines.add("C\t" + namespace + '\t' + owner + '\t' + to.replace('/', '.'));
                for (var field : cls.getFields()) if (field.getName(source) != null && field.getName(runtime) != null)
                    lines.add("F\t" + namespace + '\t' + owner + '\t' + field.getName(source)
                            + '\t' + field.getDesc(source) + '\t' + field.getName(runtime));
                for (var method : cls.getMethods()) if (method.getName(source) != null && method.getName(runtime) != null)
                    lines.add("M\t" + namespace + '\t' + owner + '\t' + method.getName(source)
                            + '\t' + method.getDesc(source) + '\t' + method.getName(runtime));
            }
        }
        return (String.join("\n", lines) + "\n").getBytes(StandardCharsets.UTF_8);
    }
}
