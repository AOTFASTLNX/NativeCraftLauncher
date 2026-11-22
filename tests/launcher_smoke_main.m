//
//  smoke_main.m
//  Minimal host used only to render the launcher and editor in CI for a screenshot.
//  It is not part of the shipping app: main.m in C0Harness wires the same controllers to
//  the AOT harness instead.
//
#import <UIKit/UIKit.h>
#include <math.h>
#include <stdlib.h>
#import "FAOTLauncherViewController.h"
#import "FAOTControlBridge.h"
#import "FAOTControlCompat.h"
#import "FAOTControlsView.h"
#import "ControlButton.h"
#include "glfw_keycodes.h"
static int smokeKinds[16], smokeKeys[16], smokeActions[16], smokeCount;
static int smokeEnqueue(void *window, int kind, double a, double b, int c, int d) {
    if (window != (void *)0x1234 || smokeCount >= 16) abort();
    smokeKinds[smokeCount] = kind; smokeKeys[smokeCount] = (int)a;
    smokeActions[smokeCount++] = kind == 6 ? c : (int)b;
    return 1;
}
@interface FAOTControlsView (SmokeInput)
- (void)setInputWindow:(void *)window enqueue:(void *)enqueue;
@end

@interface SmokeSceneDelegate : UIResponder <UIWindowSceneDelegate>
@property (nonatomic, strong) UIWindow *window;
@end

@implementation SmokeSceneDelegate

- (void)scene:(UIScene *)scene willConnectToSession:(UISceneSession *)session
      options:(UISceneConnectionOptions *)options {
    if (![scene isKindOfClass:UIWindowScene.class]) return;
    // Exercise persistence using production code inside the simulator sandbox.
    FAOTControlLayout *probe=[FAOTControlLayout defaultLayout];
    FAOTControlModel *button=probe.controls.firstObject;
    button.x=0.321;button.width=0.123;button.keycode=71;
    NSString *probeName=[@"save-test-" stringByAppendingString:NSUUID.UUID.UUIDString];
    NSError *saveError=nil;
    if(![probe saveNamed:probeName error:&saveError]) abort();
    FAOTControlLayout *loaded=[FAOTControlLayout loadNamed:probeName];
    FAOTControlModel *restored=loaded.controls.firstObject;
    if(!restored || fabs(restored.x-0.321)>0.00001 || fabs(restored.width-0.123)>0.00001 || restored.keycode!=71) abort();
    [[NSFileManager defaultManager] removeItemAtPath:[[FAOTControlLayout layoutDirectory] stringByAppendingPathComponent:[probeName stringByAppendingPathExtension:@"json"]] error:nil];
    NSLog(@"CONTROL_LAYOUT_PERSISTENCE_PASS");
    self.window = [[UIWindow alloc] initWithWindowScene:(UIWindowScene *)scene];
    FAOTLauncherViewController *launcher = [FAOTLauncherViewController new];

    NSUserDefaults *defaults = NSUserDefaults.standardUserDefaults;
    if (![defaults boolForKey:@"FAOTSmokeSeeded"]) {
        [defaults setBool:YES forKey:@"FAOTSmokeSeeded"];
        [defaults setObject:@"Dev" forKey:FAOTPlayerNameDefaultsKey];
        [defaults setInteger:2 forKey:FAOTMemoryGiBDefaultsKey];
        [defaults setObject:@"default" forKey:FAOTControlLayoutDefaultsKey];
    }
    self.window.rootViewController = launcher;
    [self.window makeKeyAndVisible];

    NSString *mode = NSProcessInfo.processInfo.environment[@"FAOT_SMOKE_MODE"];
    if ([mode isEqualToString:@"editor-test"]) {
        NSString *name = [@"legacy-test-" stringByAppendingString:NSUUID.UUID.UUIDString];
        FAOTControlLayout *legacy = [FAOTControlLayout defaultLayout];
        if (![legacy saveNamed:name error:nil]) abort();
        NSData *before = [NSData dataWithContentsOfFile:FAOTControlMapPath(name)];
        NSMutableDictionary *converted = FAOTControlLoadLayout(name);
        if ([converted[@"mControlDataList"] count] != legacy.controls.count ||
            ![before isEqualToData:[NSData dataWithContentsOfFile:FAOTControlMapPath(name)]]) abort();
        for (NSUInteger i = 0; i < legacy.controls.count; i++) {
            NSInteger original = legacy.controls[i].keycode;
            NSInteger expected = original <= -101 && original >= -108 ? original + 100 : original;
            if ([converted[@"mControlDataList"][i][@"keycodes"][0] integerValue] != expected) abort();
        }
        FAOTControlEditorViewController *editor = [[FAOTControlEditorViewController alloc] initWithLayout:legacy layoutName:name completion:^(FAOTControlLayout *l, BOOL s) {}];
        editor.modalPresentationStyle = UIModalPresentationFullScreen;
        [launcher presentViewController:editor animated:NO completion:^{
            [editor.view layoutIfNeeded];
            if (editor.ctrlView.subviews.count != legacy.controls.count) abort();
            NSUInteger initial = [editor.ctrlView.layoutDictionary[@"mControlDataList"] count];
            [editor performSelector:NSSelectorFromString(@"actionMenuAddButton")];
            [editor performSelector:NSSelectorFromString(@"actionMenuAddJoystick")];
            [editor performSelector:NSSelectorFromString(@"actionMenuAddDrawer")];
            if ([editor.ctrlView.layoutDictionary[@"mControlDataList"] count] != initial + 1 ||
                [editor.ctrlView.layoutDictionary[@"mJoystickDataList"] count] != 1 ||
                [editor.ctrlView.layoutDictionary[@"mDrawerDataList"] count] != 1) abort();
            CGRect area = CGRectInset(editor.view.bounds, 30, 15);
            setSafeArea(editor.view.bounds.size, area);
            if (!CGRectEqualToRect(getSafeArea(editor.view.bounds), area)) abort();
            FAOTControlsView *gameControls = [[FAOTControlsView alloc] initWithFrame:editor.view.bounds];
            if (![gameControls loadLayoutNamed:name error:nil]) abort();
            [gameControls setInputWindow:(void *)0x1234 enqueue:(void *)smokeEnqueue];
            gameControls.captured = YES;
            if (!isGrabbing) abort();
            ControlButton *testButton = gameControls.layoutView.subviews.firstObject;
            testButton.properties[@"keycodes"] = @[@(GLFW_KEY_W)];
            [testButton sendActionsForControlEvents:UIControlEventTouchDown];
            [testButton sendActionsForControlEvents:UIControlEventTouchUpInside];
            testButton.properties[@"keycodes"] = @[@(SPECIALBTN_MENU)];
            [testButton sendActionsForControlEvents:UIControlEventTouchDown];
            [testButton sendActionsForControlEvents:UIControlEventTouchUpInside];
            if (smokeCount != 4 || smokeKeys[0] != GLFW_KEY_W || smokeActions[0] != 1 ||
                smokeActions[1] != 0 || smokeKeys[2] != GLFW_KEY_ESCAPE || smokeActions[2] != 1 ||
                smokeActions[3] != 0) abort();
            [gameControls setInputWindow:NULL enqueue:NULL];
            NSLog(@"CONTROL_INPUT_DISPATCH_PASS");
            [gameControls layoutIfNeeded];
            if (!CGRectEqualToRect(gameControls.layoutView.frame, area)) abort();
            [editor.undoManager removeAllActions];
            [editor performSelector:NSSelectorFromString(@"actionMenuExit")];
            [[NSFileManager defaultManager] removeItemAtPath:FAOTControlMapPath(name) error:nil];
            NSLog(@"CONTROL_EDITOR_BEHAVIOR_PASS");
        }];
    }
    if ([mode isEqualToString:@"settings"]) {
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(0.3 * NSEC_PER_SEC)),
                       dispatch_get_main_queue(), ^{ [launcher showPage:@"settings"]; });
    }
    if ([mode isEqualToString:@"controlpanel"]) {
        /* Open the per-button property sheet the way a tap on a layout button does. */
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(0.8 * NSEC_PER_SEC)),
                       dispatch_get_main_queue(), ^{
            CustomControlsViewController *editor =
                [[FAOTControlEditorViewController alloc] initWithLayout:nil
                                                            layoutName:@"default"
                                                            completion:^(FAOTControlLayout *l, BOOL s) {}];
            editor.modalPresentationStyle = UIModalPresentationFullScreen;
            [launcher presentViewController:editor animated:NO completion:^{
                dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(0.8 * NSEC_PER_SEC)),
                               dispatch_get_main_queue(), ^{
                    for (UIView *view in editor.ctrlView.subviews) {
                        if (![view isKindOfClass:ControlButton.class]) continue;
                        CCMenuViewController *panel = [CCMenuViewController new];
                        panel.targetButton = (ControlButton *)view;
                        panel.modalPresentationStyle = UIModalPresentationOverFullScreen;
                        [editor presentViewController:panel animated:NO completion:nil];
                        break;
                    }
                });
            }];
        });
    }
    if ([mode isEqualToString:@"editor"]) {
        FAOTControlLayout *layout = [FAOTControlLayout layoutFromPojavData:[FAOTPojavDefaultLayoutJSON() dataUsingEncoding:NSUTF8StringEncoding]
                                                                     error:nil] ?: [FAOTControlLayout defaultLayout];
        FAOTControlEditorViewController *editor =
            [[FAOTControlEditorViewController alloc] initWithLayout:layout
                                                        layoutName:@"default"
                                                        completion:^(FAOTControlLayout *updated, BOOL saved) {
                (void)updated; (void)saved;
            }];
        editor.modalPresentationStyle = UIModalPresentationFullScreen;
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(0.4 * NSEC_PER_SEC)),
                       dispatch_get_main_queue(), ^{
            [launcher presentViewController:editor animated:NO completion:nil];
        });
    }
}

@end

@interface SmokeAppDelegate : UIResponder <UIApplicationDelegate>
@end

@implementation SmokeAppDelegate
- (UISceneConfiguration *)application:(UIApplication *)application
    configurationForConnectingSceneSession:(UISceneSession *)session
                                   options:(UISceneConnectionOptions *)options {
    UISceneConfiguration *config = [[UISceneConfiguration alloc] initWithName:@"Smoke" sessionRole:session.role];
    config.delegateClass = SmokeSceneDelegate.class;
    return config;
}
@end

int main(int argc, char *argv[]) {
    @autoreleasepool {
        return UIApplicationMain(argc, argv, nil, NSStringFromClass(SmokeAppDelegate.class));
    }
}
