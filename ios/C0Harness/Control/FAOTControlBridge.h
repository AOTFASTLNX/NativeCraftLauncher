//
//  FAOTControlBridge.h
//  Host services the ported Pojav/Amethyst control layer needs from the NCL harness.
//
//  The control layer itself (ControlButton, ControlLayout, ControlJoystick,
//  ControlDrawer, CustomControlsUtils, CustomControlsViewController) is derived from
//  Amethyst-iOS, GPL-3.0. See docs/CONTROL_LAYOUT.md for provenance.
//
#import <UIKit/UIKit.h>

NS_ASSUME_NONNULL_BEGIN

/// Directory that holds `<name>.json` control layouts: Documents/controlmap.
FOUNDATION_EXPORT NSString *FAOTControlMapDirectory(void);

/// Absolute path for a layout name, rejecting path separators.
FOUNDATION_EXPORT NSString *_Nullable FAOTControlMapPath(NSString *name);

/// Layouts on disk, newest first. Entries carry name, modified and bytes.
FOUNDATION_EXPORT NSArray<NSDictionary<NSString *, id> *> *FAOTControlMapEntries(void);

/// Creates controlmap/ and writes default.json when it is missing.
FOUNDATION_EXPORT void FAOTControlEnsureDefaults(void);

/// Button scale as a percentage; Pojav stores this as `scaledAt`.
FOUNDATION_EXPORT CGFloat FAOTControlButtonScale(void);

/// The selected layout name, falling back to the shipped default.
FOUNDATION_EXPORT NSString *FAOTControlActiveLayoutName(void);
FOUNDATION_EXPORT void FAOTControlSetActiveLayoutName(NSString *name);

/// Loads a layout dictionary in any supported Pojav/Amethyst version.
FOUNDATION_EXPORT NSMutableDictionary *_Nullable FAOTControlLoadLayout(NSString *name);

/// Writes a layout atomically; returns a message on failure.
FOUNDATION_EXPORT NSString *_Nullable FAOTControlSaveLayout(NSString *name, NSDictionary *layout);

FOUNDATION_EXPORT BOOL FAOTControlDuplicateLayout(NSString *source, NSString *destination);
FOUNDATION_EXPORT BOOL FAOTControlDeleteLayout(NSString *name);

/// Sends input to the running game. No-ops when no game surface exists.
FOUNDATION_EXPORT void FAOTControlSendKey(int keycode, BOOL down);
FOUNDATION_EXPORT void FAOTControlSetInputTarget(void *window, void *enqueue);
FOUNDATION_EXPORT void FAOTControlSendMouseButton(int button, BOOL down);
FOUNDATION_EXPORT void FAOTControlSendScroll(double dy);
FOUNDATION_EXPORT void FAOTControlSendCursorDelta(double dx, double dy);
FOUNDATION_EXPORT BOOL FAOTControlHasGameSurface(void);

/// Set while the layout editor is open; the control views read it directly.
FOUNDATION_EXPORT BOOL isControlModifiable;

NS_ASSUME_NONNULL_END
