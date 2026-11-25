//
//  FAOTControlCompat.h
//  Small host shims replacing the Amethyst launcher helpers the ported control layer
//  used to import (LauncherPreferences.h, utils.h, ios_uikit_bridge.h, glfw_keycodes.h).
//
#import <UIKit/UIKit.h>

NS_ASSUME_NONNULL_BEGIN

#define SPECIALBTN_KEYBOARD -1
#define SPECIALBTN_TOGGLECTRL -2
#define SPECIALBTN_MOUSEPRI -3
#define SPECIALBTN_MOUSESEC -4
#define SPECIALBTN_VIRTUALMOUSE -5
#define SPECIALBTN_MOUSEMID -6
#define SPECIALBTN_SCROLLUP -7
#define SPECIALBTN_SCROLLDOWN -8
#define SPECIALBTN_MENU -9

#define ACTION_DOWN 0
#define ACTION_UP 1
#define ACTION_MOVE 2
#define ACTION_MOVE_MOTION 3

#define GLFW_MOUSE_BUTTON_LEFT 0
#define GLFW_MOUSE_BUTTON_RIGHT 1
#define GLFW_MOUSE_BUTTON_MIDDLE 2

/// Button scale, 40–200. Mirrors the preference the layouts record as `scaledAt`.
float getPrefFloat(NSString *key);
BOOL getPrefBool(NSString *key);
id getPrefObject(NSString *key);
void setPrefObject(NSString *key, id value);
void setPrefBool(NSString *key, BOOL value);

UIEdgeInsets getDefaultSafeArea(void);
CGRect getSafeArea(CGRect screenBounds);
void setSafeArea(CGSize screenSize, CGRect frame);

/// Extra clearance, in points, kept between the in-game controls and the top and bottom
/// edges. Defaults to 12; the window's own safe-area insets are added on top of it.
FOUNDATION_EXPORT CGFloat FAOTControlVerticalSafeArea(void);
FOUNDATION_EXPORT void FAOTControlSetVerticalSafeArea(CGFloat points);

/// YES when the player dragged a custom canvas in the editor, which takes precedence over
/// the default insets (and the vertical-clearance preference).
FOUNDATION_EXPORT BOOL FAOTControlHasCustomSafeArea(void);

/// Drops the custom canvas and returns to the system safe area plus vertical clearance.
FOUNDATION_EXPORT void FAOTControlResetSafeArea(void);

/// The largest canvas the controls may occupy in a full-screen landscape host: the whole
/// screen minus the vertical clearance. The editor's "None" mode uses this so its preview
/// matches the in-game strip.
FOUNDATION_EXPORT CGRect FAOTControlFullBleedRect(CGRect bounds);

void showDialog(NSString *title, NSString *message);
NSString *localize(NSString *key, NSString *_Nullable comment);
UIViewController *_Nullable currentVC(void);

NSMutableDictionary *parseJSONFromFile(NSString *path);
NSError *_Nullable saveJSONToFile(NSDictionary *dict, NSString *path);
void customNSLog(const char *file, int lineNumber, const char *functionName, NSString *format, ...);

CGFloat MathUtils_dist(CGFloat x1, CGFloat y1, CGFloat x2, CGFloat y2);
CGFloat MathUtils_map(CGFloat x, CGFloat in_min, CGFloat in_max, CGFloat out_min, CGFloat out_max);
CGFloat dpToPx(CGFloat dp);
CGFloat pxToDp(CGFloat px);

static inline CGFloat clamp(CGFloat x, CGFloat lower, CGFloat upper) {
    return fmin(upper, fmax(x, lower));
}

/// The ported Pojav/Amethyst control views expect these launcher globals.
FOUNDATION_EXPORT BOOL isControlModifiable;
FOUNDATION_EXPORT BOOL isGrabbing;
FOUNDATION_EXPORT CGFloat lastXValue;
FOUNDATION_EXPORT CGFloat lastYValue;
FOUNDATION_EXPORT BOOL leftShiftHeld;

NS_ASSUME_NONNULL_END
