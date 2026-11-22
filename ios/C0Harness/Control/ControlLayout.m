#import "ControlButton.h"
#import "ControlLayout.h"
#import "ControlSubButton.h"
#import "CustomControlsUtils.h"
#import "FAOTControlCompat.h"
#import "FAOTControlBridge.h"

@interface ControlLayout ()
@end

@implementation ControlLayout

- (void)loadControlLayout:(NSMutableDictionary *)layoutDictionary {
    self.layoutDictionary = layoutDictionary;

    CGFloat currentScale = [self.layoutDictionary[@"scaledAt"] floatValue];
    CGFloat savedScale = getPrefFloat(@"control.button_scale");
    loadControlObject(self, self.layoutDictionary);

    self.layoutDictionary[@"scaledAt"] = @(savedScale);
}

- (void)loadControlFile:(NSString *)name {
    [self removeAllButtons];

    /* The bridge writes the shipped default on first use, so a fresh install always has
       something to edit instead of an error dialog. */
    NSMutableDictionary *layout = FAOTControlLoadLayout([name stringByDeletingPathExtension]);
    if (layout) {
        self.layoutDictionary = layout;
    } else {
        self.layoutDictionary = [@{@"NSErrorObject": [NSError errorWithDomain:@"FAOTControlLayout"
                                                                        code:1
                                                                    userInfo:@{NSLocalizedDescriptionKey:
                                                                        [NSString stringWithFormat:@"Could not open %@", name]}]} mutableCopy];
    }
    if (self.layoutDictionary[@"NSErrorObject"] != nil) {
        showDialog(localize(@"Error", nil), [self.layoutDictionary[@"NSErrorObject"] localizedDescription]);
        return;
    }
    [self loadControlLayout:self.layoutDictionary];
}

- (void)removeAllButtons {
    [self.subviews makeObjectsPerformSelector:@selector(removeFromSuperview)];
    [self.layoutDictionary removeAllObjects];
}

- (BOOL)canPerformAction:(SEL)action withSender:(id)sender
{
    return action == @selector(actionMenuExit:) ||
        action == @selector(actionMenuSave:) ||
        action == @selector(actionMenuLoad:) ||
        action == @selector(actionMenuSetDef:) ||
        action == @selector(actionMenuAddButton:) ||
        action == @selector(actionMenuAddDrawer:) ||
        action == @selector(actionMenuAddSubButton:) ||
        action == @selector(actionMenuBtnCopy:) ||
        action == @selector(actionMenuBtnDelete:) ||
        action == @selector(actionMenuBtnEdit:) ||
        action == @selector(actionMenuBtnSafeArea:);
}

- (BOOL)canBecomeFirstResponder {
    return YES;
}

- (UIView *)hitTest:(CGPoint)point withEvent:(UIEvent *)event {
    UIView *result = [super hitTest:point withEvent:event];
    if (result == self && !isControlModifiable) {
        return nil;
    }
    return result;
}

- (void)setFrame:(CGRect)frame {
    [super setFrame:frame];

    for (UIView *view in self.subviews) {
        if (![view isKindOfClass:ControlButton.class]) {
            continue;
        }
        [(ControlButton *)view update];
    }

    CGFloat savedScale = getPrefFloat(@"control.button_scale");
    self.layoutDictionary[@"scaledAt"] = @(savedScale);
}

// https://nsantoine.dev/posts/CALayerCaptureHiding
- (void)hideViewFromCapture:(BOOL)hide {
    if ([self.layer respondsToSelector:@selector(disableUpdateMask)]) {
        NSUInteger hideFlag = (1 << 1) | (1 << 4);
        self.layer.disableUpdateMask = hide ? hideFlag : 0;
    }
}

@end
