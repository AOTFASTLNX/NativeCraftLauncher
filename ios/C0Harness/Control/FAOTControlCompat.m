//
//  FAOTControlCompat.m
//
#import "FAOTControlCompat.h"
#import "FAOTControlBridge.h"

BOOL isGrabbing = NO;
CGFloat lastXValue = 0;
CGFloat lastYValue = 0;
BOOL leftShiftHeld = NO;

static NSString *const FAOTControlSafeAreaKey = @"FAOTControlSafeArea";
static NSString *const FAOTControlScaleKey = @"FAOTControlButtonScale";

id getPrefObject(NSString *key) {
    if ([key isEqualToString:@"control.button_scale"]) return @(FAOTControlButtonScale());
    if ([key isEqualToString:@"control.control_safe_area"]) {
        return [NSUserDefaults.standardUserDefaults stringForKey:FAOTControlSafeAreaKey] ?: @"{0, 0, 0, 0}";
    }
    return [NSUserDefaults.standardUserDefaults objectForKey:key];
}

float getPrefFloat(NSString *key) {
    if ([key isEqualToString:@"control.button_scale"]) return (float)FAOTControlButtonScale();
    return [NSUserDefaults.standardUserDefaults floatForKey:key];
}

BOOL getPrefBool(NSString *key) { return [NSUserDefaults.standardUserDefaults boolForKey:key]; }

void setPrefObject(NSString *key, id value) {
    if ([key isEqualToString:@"control.button_scale"]) {
        [NSUserDefaults.standardUserDefaults setDouble:[value doubleValue] forKey:FAOTControlScaleKey];
        return;
    }
    if ([key isEqualToString:@"control.control_safe_area"]) {
        [NSUserDefaults.standardUserDefaults setObject:[value description] forKey:FAOTControlSafeAreaKey];
        return;
    }
    [NSUserDefaults.standardUserDefaults setObject:value forKey:key];
}

void setPrefBool(NSString *key, BOOL value) {
    [NSUserDefaults.standardUserDefaults setBool:value forKey:key];
}

UIEdgeInsets getDefaultSafeArea(void) {
    UIWindow *window = UIApplication.sharedApplication.windows.firstObject;
    UIEdgeInsets safeArea = window ? window.safeAreaInsets : UIEdgeInsetsZero;
    CGSize screenSize = UIScreen.mainScreen.bounds.size;
    if (screenSize.width < screenSize.height) {
        safeArea.left = safeArea.top;
        safeArea.right = safeArea.bottom;
    }
    safeArea.top = safeArea.bottom = 0;
    return safeArea;
}

CGRect getSafeArea(CGRect screenBounds) {
    NSString *stored = [NSUserDefaults.standardUserDefaults stringForKey:FAOTControlSafeAreaKey];
    UIEdgeInsets safeArea = stored ? UIEdgeInsetsFromString(stored) : getDefaultSafeArea();
    if (screenBounds.size.width < screenBounds.size.height) {
        safeArea = UIEdgeInsetsMake(safeArea.right, safeArea.top, safeArea.left, safeArea.bottom);
    }
    return UIEdgeInsetsInsetRect(screenBounds, safeArea);
}

void setSafeArea(CGSize screenSize, CGRect frame) {
    UIEdgeInsets safeArea;
    if (screenSize.width < screenSize.height) {
        safeArea = UIEdgeInsetsMake(frame.origin.x,
                                    screenSize.height - CGRectGetMaxY(frame),
                                    screenSize.width - CGRectGetMaxX(frame),
                                    frame.origin.y);
    } else {
        safeArea = UIEdgeInsetsMake(frame.origin.y,
                                    frame.origin.x,
                                    screenSize.height - CGRectGetMaxY(frame),
                                    screenSize.width - CGRectGetMaxX(frame));
    }
    setPrefObject(@"control.control_safe_area", NSStringFromUIEdgeInsets(safeArea));
}

void showDialog(NSString *title, NSString *message) {
    UIViewController *presenter = currentVC();
    if (!presenter) return;
    UIAlertController *alert = [UIAlertController alertControllerWithTitle:title
                                                                  message:message
                                                           preferredStyle:UIAlertControllerStyleAlert];
    [alert addAction:[UIAlertAction actionWithTitle:@"好" style:UIAlertActionStyleCancel handler:nil]];
    [presenter presentViewController:alert animated:YES completion:nil];
}

NSString *localize(NSString *key, NSString *comment) {
    static NSDictionary<NSString *, NSString *> *chinese;
    static dispatch_once_t once;
    dispatch_once(&once, ^{
        chinese = @{
            @"Always": @"始终显示",
            @"Cancel": @"取消",
            @"Copy": @"复制",
            @"Edit": @"编辑",
            @"Error": @"错误",
            @"In game": @"仅游戏中",
            @"In menu": @"仅菜单",
            @"OK": @"好",
            @"Remove": @"删除",
            @"Move": @"移动",
            @"Resize": @"调整大小",
            @"custom_controls.hint": @"点按编辑 · 拖动移动 · 右下角手柄缩放 · 长按空白处打开菜单",
            @"custom_controls.control_menu.exit": @"完成",
            @"custom_controls.control_menu.save": @"保存布局",
            @"custom_controls.control_menu.load": @"载入布局",
            @"custom_controls.control_menu.safe_area": @"安全区",
            @"custom_controls.control_menu.add_button": @"添加按键",
            @"custom_controls.control_menu.add_drawer": @"添加抽屉",
            @"custom_controls.control_menu.add_joystick": @"添加摇杆",
            @"custom_controls.control_menu.exit.warn": @"还有未保存的修改。保存到当前布局后再退出？",
            @"custom_controls.control_menu.discard_changes": @"放弃修改",
            @"custom_controls.control_menu.save.error.json": @"布局 JSON 无法生成",
            @"custom_controls.control_menu.save.error.write": @"布局写入失败",
            @"custom_controls.error.incompatible": @"不支持的布局版本 %@",
            @"custom_controls.button_menu.add_subbutton": @"添加抽屉按键",
            @"custom_controls.button_edit.name": @"名称",
            @"custom_controls.button_edit.size": @"大小",
            @"custom_controls.button_edit.orientation": @"排列",
            @"custom_controls.button_edit.forward_lock": @"前推锁定",
            @"custom_controls.button_edit.mapping": @"绑定",
            @"custom_controls.button_edit.toggleable": @"点按切换",
            @"custom_controls.button_edit.mouse_pass": @"鼠标穿透",
            @"custom_controls.button_edit.swipeable": @"滑动触发",
            @"custom_controls.button_edit.bg_color": @"背景色",
            @"custom_controls.button_edit.stroke_width": @"边框宽度",
            @"custom_controls.button_edit.stroke_color": @"边框颜色",
            @"custom_controls.button_edit.corner_radius": @"圆角",
            @"custom_controls.button_edit.opacity": @"不透明度",
            @"custom_controls.button_edit.visibility": @"显示位置",
        };
    });
    return chinese[key] ?: key;
}

UIViewController *currentVC(void) {
    UIWindow *window = UIApplication.sharedApplication.keyWindow;
    if (!window) {
        for (UIWindow *candidate in UIApplication.sharedApplication.windows) {
            if (candidate.isKeyWindow) { window = candidate; break; }
        }
    }
    UIViewController *controller = window.rootViewController;
    while (controller.presentedViewController) controller = controller.presentedViewController;
    return controller;
}

NSMutableDictionary *parseJSONFromFile(NSString *path) {
    NSError *error = nil;
    NSData *data = [NSData dataWithContentsOfFile:path options:0 error:&error];
    if (!data) {
        NSLog(@"[FAOT] layout unreadable %@: %@", path, error.localizedDescription);
        return error ? [@{@"NSErrorObject": error} mutableCopy] : [NSMutableDictionary dictionary];
    }
    id root = [NSJSONSerialization JSONObjectWithData:data options:NSJSONReadingMutableContainers error:&error];
    if (![root isKindOfClass:NSMutableDictionary.class]) {
        if (!error) error = [NSError errorWithDomain:@"FAOTControlLayout" code:1
                                            userInfo:@{NSLocalizedDescriptionKey: @"布局根对象不是 JSON 对象"}];
        return [@{@"NSErrorObject": error} mutableCopy];
    }
    return root;
}

NSError *saveJSONToFile(NSDictionary *dict, NSString *path) {
    NSError *error = nil;
    NSData *data = [NSJSONSerialization dataWithJSONObject:dict options:NSJSONWritingPrettyPrinted error:&error];
    if (!data) return error;
    if (![data writeToFile:path options:NSDataWritingAtomic error:&error]) return error;
    return nil;
}

void customNSLog(const char *file, int lineNumber, const char *functionName, NSString *format, ...) {
    va_list ap;
    va_start(ap, format);
    NSString *body = [[NSString alloc] initWithFormat:format arguments:ap];
    va_end(ap);
    NSLog(@"%@", body);
}

CGFloat MathUtils_dist(CGFloat x1, CGFloat y1, CGFloat x2, CGFloat y2) {
    return (CGFloat)hypot(x2 - x1, y2 - y1);
}

CGFloat MathUtils_map(CGFloat x, CGFloat in_min, CGFloat in_max, CGFloat out_min, CGFloat out_max) {
    return (x - in_min) * (out_max - out_min) / (in_max - in_min) + out_min;
}

CGFloat dpToPx(CGFloat dp) { return dp * UIScreen.mainScreen.scale; }
CGFloat pxToDp(CGFloat px) { return px / UIScreen.mainScreen.scale; }
