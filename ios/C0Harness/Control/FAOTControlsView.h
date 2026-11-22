//
//  FAOTControlsView.h
//  The in-game on-screen control surface: Pojav/Amethyst ControlLayout plus the
//  key, mouse and joystick dispatch that used to live in SurfaceViewController.
//
#import <UIKit/UIKit.h>
#import "ControlLayout.h"

NS_ASSUME_NONNULL_BEGIN

@interface FAOTControlsView : UIView

/// The loaded layout view. Buttons are subviews of this, exactly as in the editor.
@property (nonatomic, strong, readonly) ControlLayout *layoutView;
@property (nonatomic, copy, readonly) NSString *layoutName;

/// Game capture gates most input: before it only the pause button works.
@property (nonatomic, assign, getter=isCaptured) BOOL captured;
@property (nonatomic, assign) BOOL menuMode;

/// Loads `<name>.json` from Documents/controlmap and wires button actions.
- (BOOL)loadLayoutNamed:(NSString *)name error:(NSString *_Nullable *_Nullable)error;

/// Reloads the selected layout after the editor saved it.
- (void)reloadActiveLayout;

/// Releases every held key and mouse button. Call when leaving the app or the world.
- (void)releaseAllInput;

/// YES while a layout button is holding `keycode` down.
- (BOOL)isHoldingKey:(int)keycode;

@end

NS_ASSUME_NONNULL_END
