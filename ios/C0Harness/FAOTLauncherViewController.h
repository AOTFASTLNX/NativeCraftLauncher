//
//  FAOTLauncherViewController.h
//  Pre-game launcher: instance/profile summary, memory budget, control layout selection
//  and the editable control editor. Starting the game hands off to the existing harness.
//
#import <UIKit/UIKit.h>
#import "FAOTControlLayout.h"

#import "CustomControlsViewController.h"

#import "test_version.h"

NS_ASSUME_NONNULL_BEGIN


extern NSString *const FAOTPlayerNameDefaultsKey;   // NSString
extern NSString *const FAOTMemoryGiBDefaultsKey;    // NSNumber
extern NSString *const FAOTControlLayoutDefaultsKey; // NSString, layout name under Documents/controlmap
extern NSString *const FAOTLanguageDefaultsKey;       // NSString, Minecraft locale (en_us/zh_cn/ja_jp)

@interface FAOTLauncherViewController : UIViewController

/// Invoked by the 启动游戏 button. main.m wires this to the AOT harness. Arguments are the
/// player name, memory in GiB, control layout name and Minecraft locale.
@property (nonatomic, copy, nullable) void (^onLaunch)(NSString *playerName, NSInteger memoryGiB, NSString *layoutName, NSString *languageCode);

/// Ends the 加载中 state and shows why the runtime stopped plus a log entry point.
- (void)runtimeFinishedSuccess:(BOOL)succeeded summary:(NSString *)summary logPath:(nullable NSString *)logPath;

+ (FAOTControlLayout *)activeLayout;

/// Switches the visible sidebar page: game, account, resources or settings.
- (void)showPage:(NSString *)key;

@end

/// Full-screen editor; a subclass of the ported Pojav/Amethyst control editor so the
/// launcher and the game share one control implementation.
@interface FAOTControlEditorViewController : CustomControlsViewController
- (instancetype)initWithLayout:(FAOTControlLayout *)layout
                    layoutName:(NSString *)layoutName
                    completion:(void (^)(FAOTControlLayout *layout, BOOL saved))completion;
@end

/// The default control set that ships with Pojav (generateAndSaveDefaultControl in
/// CustomControlsUtils.m), kept as JSON so the launcher can hand it to the importer.
NSString *FAOTPojavDefaultLayoutJSON(void);

NS_ASSUME_NONNULL_END
