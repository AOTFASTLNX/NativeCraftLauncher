//
//  FAOTControlsView.m
//
#import "FAOTControlsView.h"
#import "FAOTControlBridge.h"
#import "ControlButton.h"
#import "ControlDrawer.h"
#import "ControlJoystick.h"
#import "ControlSubButton.h"
#import "CustomControlsUtils.h"
#import "FAOTControlCompat.h"
#include "glfw_keycodes.h"

@protocol FAOTControlHost <NSObject>
- (void)toggleOnScreenKeyboard;
- (void)toggleControlVisibility;
@end

@interface FAOTControlsView ()
@property (nonatomic, strong, readwrite) ControlLayout *layoutView;
@property (nonatomic, copy, readwrite) NSString *layoutName;
@property (nonatomic, strong) NSMutableSet<NSNumber *> *heldKeys;
@property (nonatomic, strong) NSMutableSet<NSNumber *> *heldButtons;
@end

@implementation FAOTControlsView

- (void)setInputWindow:(void *)window enqueue:(void *)enqueue {
    FAOTControlSetInputTarget(window, enqueue);
}

- (instancetype)initWithFrame:(CGRect)frame {
    if ((self = [super initWithFrame:frame])) {
        self.backgroundColor = UIColor.clearColor;
        self.multipleTouchEnabled = YES;
        _heldKeys = [NSMutableSet set];
        _heldButtons = [NSMutableSet set];
    }
    return self;
}

- (BOOL)loadLayoutNamed:(NSString *)name error:(NSString *_Nullable *_Nullable)error {
    NSMutableDictionary *dictionary = FAOTControlLoadLayout(name);
    if (!dictionary) {
        if (error) *error = [NSString stringWithFormat:@"找不到布局 %@", name];
        return NO;
    }
    if (dictionary[@"NSErrorObject"]) {
        if (error) *error = [dictionary[@"NSErrorObject"] localizedDescription];
        return NO;
    }
    self.layoutName = name;
    [self installLayout:dictionary];
    [self applyVisibility];
    return YES;
}

- (void)installLayout:(NSMutableDictionary *)dictionary {
    [self releaseAllInput];
    [self.layoutView removeFromSuperview];
    ControlLayout *layout = [[ControlLayout alloc] initWithFrame:self.bounds];
    layout.autoresizingMask = UIViewAutoresizingNone;
    layout.backgroundColor = UIColor.clearColor;
    [self addSubview:layout];
    self.layoutView = layout;
    [layout loadControlLayout:dictionary];

    for (ControlButton *button in layout.subviews) {
        if (![button isKindOfClass:ControlButton.class]) continue;
        [button addTarget:self action:@selector(controlDown:) forControlEvents:UIControlEventTouchDown];
        [button addTarget:self action:@selector(controlUp:)
         forControlEvents:UIControlEventTouchUpInside | UIControlEventTouchUpOutside |
                          UIControlEventTouchCancel];
    }
    [self setNeedsLayout];
}

- (void)reloadActiveLayout {
    NSString *name = FAOTControlActiveLayoutName();
    NSString *error = nil;
    if (![self loadLayoutNamed:name error:&error]) NSLog(@"[FAOT] controls: %@", error);
}

- (void)setFrame:(CGRect)frame {
    [super setFrame:frame];
    self.layoutView.frame = getSafeArea(self.bounds);
}

- (void)layoutSubviews {
    [super layoutSubviews];
    self.layoutView.frame = getSafeArea(self.bounds);
    [self.layoutView.subviews makeObjectsPerformSelector:@selector(update)];
}

- (void)setCaptured:(BOOL)captured {
    isGrabbing = captured;
    if (_captured == captured) return;
    if (!captured) [self releaseAllInput];
    _captured = captured;
    [self applyVisibility];
}

- (void)setMenuMode:(BOOL)menuMode {
    _menuMode = menuMode;
    [self applyVisibility];
}

- (void)applyVisibility {
    for (ControlButton *button in self.layoutView.subviews) {
        if (![button isKindOfClass:ControlButton.class]) continue;
        /* Before the game grabs input the surface is a menu, so show the smaller menu set.
           Once captured, the in-game set applies. */
        BOOL visible = (self.captured && !self.menuMode) ? button.displayInGame : button.displayInMenu;
        button.hidden = !visible;
    }
}

#pragma mark - Input

- (BOOL)isSpecial:(int)code { return code < 0; }

- (void)controlDown:(ControlButton *)button {
    BOOL toggle = [button.properties[@"isToggle"] boolValue];
    if (toggle) {
        button.isToggleOn = !button.isToggleOn;
        button.backgroundColor = button.isToggleOn ? button.savedBackgroundColor :
            [button.savedBackgroundColor colorWithAlphaComponent:0.25];
        [self dispatchButton:button down:button.isToggleOn];
        return;
    }
    [self dispatchButton:button down:YES];
}

- (void)controlUp:(ControlButton *)button {
    if ([button.properties[@"isToggle"] boolValue]) return;
    [self dispatchButton:button down:NO];
}

- (void)dispatchButton:(ControlButton *)button down:(BOOL)down {
    NSArray *keycodes = button.properties[@"keycodes"];
    if (![keycodes isKindOfClass:NSArray.class]) return;
    for (NSNumber *value in keycodes) {
        int code = value.intValue;
        if (code == 0) continue;
        if (!self.captured && code != GLFW_KEY_ESCAPE && code != SPECIALBTN_MENU) continue;
        if (code < 0) {
            [self dispatchSpecial:code down:down];
        } else {
            NSNumber *key = @(code);
            if (down) {
                if ([self.heldKeys containsObject:key]) continue;
                [self.heldKeys addObject:key];
            } else {
                if (![self.heldKeys containsObject:key]) continue;
                [self.heldKeys removeObject:key];
            }
            FAOTControlSendKey(code, down);
        }
    }
}

- (void)dispatchSpecial:(int)code down:(BOOL)down {
    switch (code) {
        case SPECIALBTN_MOUSEPRI: [self mouseButton:0 down:down]; break;
        case SPECIALBTN_MOUSESEC: [self mouseButton:1 down:down]; break;
        case SPECIALBTN_MOUSEMID: [self mouseButton:2 down:down]; break;
        case SPECIALBTN_SCROLLUP: if (!down) FAOTControlSendScroll(-1.0); break;
        case SPECIALBTN_SCROLLDOWN: if (!down) FAOTControlSendScroll(1.0); break;
        case SPECIALBTN_KEYBOARD:
            if (!down && [self.nextResponder respondsToSelector:@selector(toggleOnScreenKeyboard)]) [(id<FAOTControlHost>)self.nextResponder toggleOnScreenKeyboard];
            break;
        case SPECIALBTN_TOGGLECTRL:
            if (!down && [self.nextResponder respondsToSelector:@selector(toggleControlVisibility)]) [(id<FAOTControlHost>)self.nextResponder toggleControlVisibility];
            break;
        case SPECIALBTN_MENU:
            FAOTControlSendKey(GLFW_KEY_ESCAPE, down);
            break;
        default:
            break;
    }
}

- (void)mouseButton:(int)button down:(BOOL)down {
    NSNumber *key = @(button);
    if (down) {
        if ([self.heldButtons containsObject:key]) return;
        [self.heldButtons addObject:key];
    } else {
        if (![self.heldButtons containsObject:key]) return;
        [self.heldButtons removeObject:key];
    }
    FAOTControlSendMouseButton(button, down);
}

- (void)releaseAllInput {
    for (NSNumber *key in self.heldKeys) FAOTControlSendKey(key.intValue, NO);
    [self.heldKeys removeAllObjects];
    for (NSNumber *button in self.heldButtons) FAOTControlSendMouseButton(button.intValue, NO);
    [self.heldButtons removeAllObjects];
}

- (BOOL)isHoldingKey:(int)keycode {
    return [self.heldKeys containsObject:@(keycode)];
}

@end
