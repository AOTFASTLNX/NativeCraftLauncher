/* Host state-fixture only: the real presentation lives in uikit_surface.m. */
int faot_surface_present_bgra(void *surface, const void *pixels, int width, int height) {
    (void)surface; (void)pixels; (void)width; (void)height;
    return 1;
}
/* Haptics need UIKit, which the host fixture does not link. Accepting the call keeps the
   command channel's contract intact so the fixture still exercises the dispatch. */
void faot_surface_vibrate(int kind) {
    (void)kind;
}
