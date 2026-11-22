#include <stdio.h>
#include <string.h>

/* First-party UI failure fixture. Contains no game code or Graal runtime. */
int run_main(int argc, char **argv) {
    int jmx_disabled = 0;
    for (int i = 1; i < argc && strncmp(argv[i], "--", 2) != 0; ++i)
        if (strcmp(argv[i], "-Dlog4j2.disable.jmx=true") == 0)
            jmx_disabled = 1;
    if (!jmx_disabled) {
        puts("MCFAO_JMX_ARGUMENT_MISSING");
        return 2;
    }
    puts("MCFAO_RUNTIME_FAILURE_FIXTURE");
    puts("MCFAO_JMX_DISABLED_ARGUMENT_PASS");
    for (int i = 0; i < 12000; ++i)
        puts("MCFAO verbose diagnostic padding");
    puts("MCFAO_RUNTIME_FAILURE_END");
    return 1;
}
