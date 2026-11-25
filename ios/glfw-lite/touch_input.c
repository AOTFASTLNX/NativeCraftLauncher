/*
 * TouchController host transport.
 *
 * UIKit reports a contact by its pointer id; the Mod expects the proxy wire format it already
 * decodes on every other platform. Encoding lives here so the Java side only moves bytes:
 *
 *   type 1 ADD    int type, int index, float x, float y   (16 bytes)
 *   type 2 REMOVE int type, int index                     ( 8 bytes)
 *   type 3 CLEAR  int type                                ( 4 bytes)
 *
 * Coordinates are normalised to the drawable the game sees, matching the SDL platform, which
 * forwards SDL's own 0..1 finger positions. The queue is a snapshot: faot_touch_snapshot()
 * publishes the contacts that are down at that frame boundary, so a touch that begins and ends
 * between two frames still delivers its press and release in order.
 */
#include "touch_input.h"

#include <math.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "touch_diagnostics.h"

enum { MESSAGE_ADD = 1, MESSAGE_REMOVE = 2, MESSAGE_CLEAR = 3 };

typedef struct {
    int used;
    int id;
    /* Normalised coordinates are viewport independent: a rotation must not reinterpret a
       position captured under the previous geometry. */
    float x;
    float y;
    float moveX;
    float moveY;
    int move;
    int announced;
    int ended;
} Contact;

static pthread_mutex_t touchMutex = PTHREAD_MUTEX_INITIALIZER;
static Contact contacts[FAOT_TOUCH_MAX_POINTERS];
static int nextIdentifier = 1;
static int active;
static int width = 1, height = 1;
static unsigned overflows;
/* A full ring is not a pointer-table overflow: nothing leaves the Mod's state, the message
   waits for the next boundary. Counting them together made a drained-then-retried drag look
   like a capacity recovery. */
static unsigned queueOverflows;
static unsigned char queue[FAOT_TOUCH_QUEUE_CAPACITY][FAOT_TOUCH_MESSAGE_BYTES];
static int queueLength[FAOT_TOUCH_QUEUE_CAPACITY];
static int queueRead, queueWrite;

static void reset(void) {
    memset(contacts, 0, sizeof contacts);
    queueRead = queueWrite = 0;
}

/* Contact coordinates are quantised to the published viewport so an unchanged position is not
   re-sent every frame; the queue merges moves that arrive between two snapshots. */
static float normalise(float value) {
    if (!isfinite(value)) return 0.0f;
    if (value < 0.0f) return 0.0f;
    if (value > 1.0f) return 1.0f;
    return value;
}

static int push(int length, const unsigned char *bytes) {
    if (length <= 0 || length > FAOT_TOUCH_MESSAGE_BYTES) return 0;
    if (queueWrite - queueRead >= FAOT_TOUCH_QUEUE_CAPACITY) {
        queueOverflows++;
        return 0;
    }
    int slot = queueWrite % FAOT_TOUCH_QUEUE_CAPACITY;
    memcpy(queue[slot], bytes, (size_t)length);
    queueLength[slot] = length;
    queueWrite++;
    return 1;
}

static void putInt(unsigned char *out, int offset, int value) {
    uint32_t bits = (uint32_t)value;
    out[offset] = (unsigned char)(bits >> 24);
    out[offset + 1] = (unsigned char)(bits >> 16);
    out[offset + 2] = (unsigned char)(bits >> 8);
    out[offset + 3] = (unsigned char)bits;
}

static void putFloat(unsigned char *out, int offset, float value) {
    uint32_t bits;
    memcpy(&bits, &value, sizeof bits);
    out[offset] = (unsigned char)(bits >> 24);
    out[offset + 1] = (unsigned char)(bits >> 16);
    out[offset + 2] = (unsigned char)(bits >> 8);
    out[offset + 3] = (unsigned char)bits;
}

static int encodeAdd(unsigned char *out, int id, float x, float y) {
    putInt(out, 0, MESSAGE_ADD);
    putInt(out, 4, id);
    putFloat(out, 8, x);
    putFloat(out, 12, y);
    return FAOT_TOUCH_MESSAGE_BYTES;
}

static int encodeRemove(unsigned char *out, int id) {
    putInt(out, 0, MESSAGE_REMOVE);
    putInt(out, 4, id);
    return 8;
}

static int encodeClear(unsigned char *out) {
    putInt(out, 0, MESSAGE_CLEAR);
    return 4;
}

/* Drops every contact the Mod may still believe is down and queues the message that makes it
   release them. Clearing the table alone leaves the Mod holding stale pointers, which shows up
   as a control stuck on after a rotation, a cancel or a lost window. */
static void abandon(const char *reason) {
    int announced = 0;
    for (int i = 0; i < FAOT_TOUCH_MAX_POINTERS; i++) announced |= contacts[i].used && contacts[i].announced;
    reset();
    if (!announced) return;
    unsigned char clear[4];
    push(encodeClear(clear), clear);
    FAOT_TOUCH_LOG("[TouchBridge] clear pointers (%s)\n", reason);
}

static Contact *find(int id) {
    for (int i = 0; i < FAOT_TOUCH_MAX_POINTERS; i++) {
        if (contacts[i].used && contacts[i].id == id) return &contacts[i];
    }
    return NULL;
}

int faot_touch_enabled(void) {
    pthread_mutex_lock(&touchMutex);
    int result = active;
    pthread_mutex_unlock(&touchMutex);
    return result;
}

int faot_touch_start(float x, float y) {
    if (!isfinite(x) || !isfinite(y)) return 0;
    pthread_mutex_lock(&touchMutex);
    if (!active) {
        pthread_mutex_unlock(&touchMutex);
        return 0;
    }
    Contact *slot = NULL;
    for (int i = 0; i < FAOT_TOUCH_MAX_POINTERS; i++) {
        if (!contacts[i].used) {
            slot = &contacts[i];
            break;
        }
    }
    if (!slot) {
        /* A full table cannot describe the new contact, and leaving the old ones pressed is
           worse than dropping the whole set: the game would see fingers it no longer has. */
        overflows++;
        abandon("pointer capacity");
        pthread_mutex_unlock(&touchMutex);
        FAOT_TOUCH_LOG("[TouchBridge] pointer capacity recovery count=%u\n", overflows);
        return 0;
    }
    int id = nextIdentifier++;
    if (nextIdentifier <= 0) nextIdentifier = 1;
    *slot = (Contact){.used = 1, .id = id, .x = normalise(x), .y = normalise(y)};
    pthread_mutex_unlock(&touchMutex);
    return id;
}

void faot_touch_move(int id, float x, float y) {
    if (!isfinite(x) || !isfinite(y)) return;
    pthread_mutex_lock(&touchMutex);
    Contact *contact = find(id);
    if (contact && !contact->ended) {
        contact->moveX = normalise(x);
        contact->moveY = normalise(y);
        contact->move = 1;
    }
    pthread_mutex_unlock(&touchMutex);
}

void faot_touch_end(int id) {
    pthread_mutex_lock(&touchMutex);
    Contact *contact = find(id);
    if (contact) contact->ended = 1;
    pthread_mutex_unlock(&touchMutex);
}

void faot_touch_clear(void) {
    pthread_mutex_lock(&touchMutex);
    abandon("clear");
    pthread_mutex_unlock(&touchMutex);
}

void faot_touch_disconnect(void) {
    pthread_mutex_lock(&touchMutex);
    active = 0;
    abandon("disconnect");
    pthread_mutex_unlock(&touchMutex);
}

unsigned faot_touch_overflows(void) {
    pthread_mutex_lock(&touchMutex);
    unsigned value = overflows;
    pthread_mutex_unlock(&touchMutex);
    return value;
}

void faot_touch_snapshot(int nextWidth, int nextHeight, int enabled) {
    pthread_mutex_lock(&touchMutex);
    if (active != !!enabled) {
        active = !!enabled;
        abandon(enabled ? "enable" : "disable");
        FAOT_TOUCH_LOG("[TouchBridge] snapshot active=%d viewport=%dx%d\n", active, nextWidth, nextHeight);
    }
    if (nextWidth > 0 && nextHeight > 0) {
        width = nextWidth;
        height = nextHeight;
    }
    if (!active) {
        pthread_mutex_unlock(&touchMutex);
        return;
    }
    unsigned char message[FAOT_TOUCH_MESSAGE_BYTES];
    for (int i = 0; i < FAOT_TOUCH_MAX_POINTERS; i++) {
        Contact *contact = &contacts[i];
        if (!contact->used) continue;
        /* A press and release between two frames still has to reach the Mod: buttons are
           edge-driven, so dropping the pair would swallow a tap. Publish the press first, then
           the release, in that order. */
        if (!contact->announced) {
            /* A press carries whatever position it already reached. The move flag survives a
               failed push, so a full queue retries at the next boundary instead of announcing
               the press at a stale spot. */
            float x = contact->move ? contact->moveX : contact->x;
            float y = contact->move ? contact->moveY : contact->y;
            if (push(encodeAdd(message, contact->id, x, y), message)) {
                contact->x = x;
                contact->y = y;
                contact->move = 0;
                contact->announced = 1;
                FAOT_TOUCH_LOG("[TouchBridge] emit type=1 id=%d x=%.3f y=%.3f\n",
                               contact->id, (double)contact->x, (double)contact->y);
            }
        }
        if (contact->ended) {
            if (contact->announced && push(encodeRemove(message, contact->id), message)) {
                FAOT_TOUCH_LOG("[TouchBridge] emit type=2 id=%d\n", contact->id);
            }
            *contact = (Contact){0};
            continue;
        }
        if (contact->announced && contact->move
                && (contact->moveX != contact->x || contact->moveY != contact->y)) {
            /* Only advance the published position when the frame actually carried it. Writing
               first and pushing second loses the drag: the next boundary compares against the
               new value, sees no change and stays silent for the rest of the gesture. */
            if (push(encodeAdd(message, contact->id, contact->moveX, contact->moveY), message)) {
                contact->x = contact->moveX;
                contact->y = contact->moveY;
                FAOT_TOUCH_LOG("[TouchBridge] emit type=1 id=%d x=%.3f y=%.3f\n",
                               contact->id, (double)contact->x, (double)contact->y);
            }
        }
    }
    pthread_mutex_unlock(&touchMutex);
}

int faot_touch_receive(void *buffer, int capacity) {
    if (!buffer || capacity < 4) return 0;
    pthread_mutex_lock(&touchMutex);
    if (queueRead == queueWrite) {
        pthread_mutex_unlock(&touchMutex);
        return 0;
    }
    int slot = queueRead % FAOT_TOUCH_QUEUE_CAPACITY;
    int length = queueLength[slot];
    if (length > capacity) {
        pthread_mutex_unlock(&touchMutex);
        return 0;
    }
    memcpy(buffer, queue[slot], (size_t)length);
    queueRead++;
    pthread_mutex_unlock(&touchMutex);
    return length;
}

