//
//  FAOTLauncherPages.h
//  Game, account, resource and settings pages hosted by the launcher shell.
//
#import <UIKit/UIKit.h>

NS_ASSUME_NONNULL_BEGIN

@protocol FAOTLauncherPageHost <NSObject>
- (void)pageRequestedLaunch;
- (void)pageRequestedLayoutEditor;
- (void)pageRequestedLayoutImport;
- (void)pageRequestedLayoutEditorNamed:(NSString *)layoutName;
- (void)pageAccountsChanged;
- (BOOL)pageGameHasLaunched;
- (void)pageSetGameStatus:(NSString *)status;
@end

@protocol FAOTLauncherPage <NSObject>
- (void)refreshPage;
@end

@interface FAOTGamePageController : UIViewController <FAOTLauncherPage>
- (instancetype)initWithHost:(id<FAOTLauncherPageHost>)host;
- (void)showStatus:(NSString *)status;
/// Shows 加载中 with a spinner and disables the button until -finishLaunching.
- (void)showLaunching;
- (void)finishLaunching;
/// The runtime has stopped. The button stops inviting another launch because the isolate is
/// single-use, and a log entry point is shown so the failure can be inspected on the device.
- (void)showRuntimeFinished:(BOOL)succeeded summary:(NSString *)summary logPath:(nullable NSString *)logPath;
@end

@interface FAOTAccountPageController : UIViewController <FAOTLauncherPage>
- (instancetype)initWithHost:(id<FAOTLauncherPageHost>)host;
@end

@interface FAOTResourcesPageController : UIViewController <FAOTLauncherPage>
- (instancetype)initWithHost:(id<FAOTLauncherPageHost>)host;
@end

@interface FAOTSettingsPageController : UIViewController <FAOTLauncherPage>
- (instancetype)initWithHost:(id<FAOTLauncherPageHost>)host;
@end

NS_ASSUME_NONNULL_END
