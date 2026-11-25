#ifndef FAOT_TOUCH_INPUT_H
#define FAOT_TOUCH_INPUT_H

/* TouchController's iOS transport is pull-based: the Mod asks for one encoded message per
   poll and the host answers. UIKit pushes contacts from the main thread, the game drains
   them on the render thread, so the queue below is the only shared state. */

/* Maximum simultaneous contacts the queue tracks. UIKit delivers far fewer on iPhone. */
#define FAOT_TOUCH_MAX_POINTERS 64
/* Encoded messages held between two snapshots. One ADD per contact plus removals. */
#define FAOT_TOUCH_QUEUE_CAPACITY 128
/* Largest encoded message: type + index + x + y. */
#define FAOT_TOUCH_MESSAGE_BYTES 16

int faot_touch_enabled(void);
int faot_touch_start(float x, float y);
void faot_touch_move(int id, float x, float y);
void faot_touch_end(int id);
void faot_touch_clear(void);
void faot_touch_disconnect(void);
void faot_touch_snapshot(int width, int height, int enabled);
int faot_touch_receive(void *buffer, int capacity);
unsigned faot_touch_overflows(void);

/* Called from the GLFW bridge; they carry no UIKit dependency so the C fixture links alone. */
void faot_touch_begin_frame(int enabled);
void faot_touch_command(int kind, int value);

#endif
