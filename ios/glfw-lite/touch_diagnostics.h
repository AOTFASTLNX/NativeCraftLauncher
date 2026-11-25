#ifndef FAOT_TOUCH_DIAGNOSTICS_H
#define FAOT_TOUCH_DIAGNOSTICS_H

#include <stdio.h>
#include <stdlib.h>

/* Touch tracing is opt-in: the default build stays silent on the frame path. */
static inline int faot_touch_should_log(void) {
    static int enabled = -1;
    if (enabled < 0) {
        const char *value = getenv("FAOT_TOUCH_DIAGNOSTICS");
        enabled = value && value[0] && value[0] != '0';
    }
    return enabled;
}

#define FAOT_TOUCH_LOG(...) do { if(faot_touch_should_log())fprintf(stderr,__VA_ARGS__); } while(0)

#endif
