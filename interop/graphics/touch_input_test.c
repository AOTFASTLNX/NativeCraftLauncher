/* Host fixture for the TouchController queue. It links touch_input.c alone: no UIKit, no
   Minecraft, no Java. The assertions describe the wire format the Mod decodes. */
#include <assert.h>
#include <math.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "touch_input.h"

static int integer(const unsigned char *bytes) {
    return (int)((uint32_t)bytes[0] << 24 | (uint32_t)bytes[1] << 16
                 | (uint32_t)bytes[2] << 8 | (uint32_t)bytes[3]);
}

static float real(const unsigned char *bytes) {
    uint32_t bits = (uint32_t)bytes[0] << 24 | (uint32_t)bytes[1] << 16
                    | (uint32_t)bytes[2] << 8 | (uint32_t)bytes[3];
    float value;
    memcpy(&value, &bits, sizeof value);
    return value;
}

static void drain(void) {
    unsigned char buffer[256];
    while (faot_touch_receive(buffer, sizeof buffer)) {}
}

static void *writer(void *unused) {
    (void)unused;
    for (int i = 0; i < 4000; i++) {
        int id = faot_touch_start(0.25f, 0.75f);
        faot_touch_move(id, 0.5f, 0.6f);
        faot_touch_end(id);
    }
    return NULL;
}

int main(void) {
    unsigned char message[256];
    /* Disabled by default: no contact is accepted until a frame publishes a live viewport. */
    faot_touch_disconnect();
    assert(!faot_touch_enabled());
    assert(!faot_touch_start(0, 0));
    faot_touch_snapshot(400, 800, 1);
    assert(faot_touch_enabled());

    /* A read smaller than one message leaves the queue intact. */
    assert(!faot_touch_receive(message, 3));
    assert(!faot_touch_receive(NULL, 256));

    int tap = faot_touch_start(0.25f, 0.75f), other = faot_touch_start(0.5f, 0.2f);
    assert(tap && other && tap != other);
    faot_touch_end(tap);
    faot_touch_move(other, 0.6f, 0.3f);
    faot_touch_snapshot(400, 800, 1);
    /* The release of a contact pressed since the last boundary must arrive with its press. */
    assert(faot_touch_receive(message, sizeof message) == 16);
    assert(integer(message) == 1 && integer(message + 4) == tap);
    assert(fabsf(real(message + 8) - 0.25f) < 0.01f);
    assert(fabsf(real(message + 12) - 0.75f) < 0.01f);
    assert(faot_touch_receive(message, sizeof message) == 8);
    assert(integer(message) == 2 && integer(message + 4) == tap);
    assert(faot_touch_receive(message, sizeof message) == 16);
    assert(integer(message) == 1 && integer(message + 4) == other);
    assert(fabsf(real(message + 8) - 0.6f) < 0.01f);
    assert(fabsf(real(message + 12) - 0.3f) < 0.01f);
    assert(!faot_touch_receive(message, sizeof message));

    /* A move between boundaries merges into one message with the latest position. */
    for (int i = 0; i < 10000; i++) faot_touch_move(other, 0.7f, 0.4f);
    faot_touch_snapshot(400, 800, 1);
    assert(faot_touch_receive(message, sizeof message) == 16);
    assert(fabsf(real(message + 8) - 0.7f) < 0.01f);
    assert(fabsf(real(message + 12) - 0.4f) < 0.01f);
    assert(!faot_touch_receive(message, sizeof message));

    /* Viewport change rescales the next report instead of reusing stale pixels. */
    faot_touch_move(other, 0.75f, 0.5f);
    faot_touch_snapshot(800, 400, 1);
    assert(faot_touch_receive(message, sizeof message) == 16);
    assert(fabsf(real(message + 8) - 0.75f) < 0.01f);

    faot_touch_clear();
    faot_touch_snapshot(800, 400, 1);
    drain();
    faot_touch_move(other, 0.9f, 0.9f);
    faot_touch_snapshot(800, 400, 1);
    assert(!faot_touch_receive(message, sizeof message));

    /* Capacity recovery: a full pointer table clears the set rather than stranding presses.
       The contacts must already be published, or the Mod holds nothing worth releasing. */
    unsigned before = faot_touch_overflows();
    for (int i = 0; i < FAOT_TOUCH_MAX_POINTERS; i++) faot_touch_start(0.5f, 0.5f);
    faot_touch_snapshot(100, 200, 1);
    drain();
    assert(!faot_touch_start(0.5f, 0.5f));
    assert(faot_touch_overflows() == before + 1);
    faot_touch_snapshot(100, 200, 1);
    assert(faot_touch_receive(message, sizeof message) == 4 && integer(message) == 3);
    drain();

    /* Concurrent producers and consumers must not corrupt the ring or deadlock. */
    faot_touch_snapshot(100, 200, 1);
    pthread_t thread;
    assert(!pthread_create(&thread, NULL, writer, NULL));
    for (int i = 0; i < 4000; i++) {
        faot_touch_snapshot(100, 200, 1);
        drain();
    }
    assert(!pthread_join(thread, NULL));
    faot_touch_clear();
    faot_touch_snapshot(100, 200, 1);
    drain();

    /* A non-finite contact is refused; disabling releases everything still down. */
    assert(!faot_touch_start(NAN, 0));
    int live = faot_touch_start(0.1f, 0.2f);
    assert(live);
    faot_touch_snapshot(100, 200, 0);
    drain();
    assert(!faot_touch_start(0, 0));
    faot_touch_disconnect();
    puts("TOUCH_INPUT_PASS");
    return 0;
}
