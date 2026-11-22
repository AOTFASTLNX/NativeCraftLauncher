//
//  FAOTControlEditorViewController.m
//  Hosts the ported Pojav/Amethyst control editor (CustomControlsViewController) inside
//  the NCL launcher and reports the saved layout back to the settings page.
//
#import "FAOTLauncherViewController.h"
#import "FAOTControlBridge.h"
#import "CustomControlsViewController.h"

@interface FAOTControlEditorViewController ()
@property (nonatomic, copy) NSString *layoutName;
@property (nonatomic, copy) void (^completion)(FAOTControlLayout *, BOOL);
@end

@implementation FAOTControlEditorViewController

- (BOOL)prefersStatusBarHidden { return YES; }
- (UIInterfaceOrientationMask)supportedInterfaceOrientations { return UIInterfaceOrientationMaskLandscape; }

- (instancetype)initWithLayout:(FAOTControlLayout *)layout
                    layoutName:(NSString *)layoutName
                    completion:(void (^)(FAOTControlLayout *, BOOL))completion {
    if ((self = [super initWithNibName:nil bundle:nil])) {
        (void)layout; // the ported editor reads the layout file itself
        _layoutName = [layoutName copy];
        _completion = [completion copy];
    }
    return self;
}

- (void)viewDidLoad {
    __weak typeof(self) weakSelf = self;
    self.getDefaultCtrl = ^NSString *{
        return weakSelf.layoutName.length ? weakSelf.layoutName : FAOTControlActiveLayoutName();
    };
    self.setDefaultCtrl = ^(NSString *name) {
        FAOTControlSetActiveLayoutName(name);
        weakSelf.layoutName = name;
    };
    [super viewDidLoad];
}

- (void)viewDidDisappear:(BOOL)animated {
    [super viewDidDisappear:animated];
    if (self.completion) self.completion(nil, YES);
}

@end
