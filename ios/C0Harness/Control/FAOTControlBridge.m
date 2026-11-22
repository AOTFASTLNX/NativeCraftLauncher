//
//  FAOTControlBridge.m
//
#import "FAOTControlBridge.h"
#import "FAOTLauncherViewController.h"
#import "CustomControlsUtils.h"
#include "glfw_keycodes.h"
#include <dlfcn.h>

BOOL isControlModifiable = NO;

static NSString *const FAOTControlScaleKey = @"FAOTControlButtonScale";
static NSString *const FAOTControlDefaultName = @"default";

NSString *FAOTControlMapDirectory(void) {
    NSString *documents = NSSearchPathForDirectoriesInDomains(NSDocumentDirectory, NSUserDomainMask, YES).firstObject;
    return [documents stringByAppendingPathComponent:@"controlmap"];
}

NSString *FAOTControlMapPath(NSString *name) {
    if (name.length == 0 || ![name isEqualToString:name.lastPathComponent] ||
        [name isEqualToString:@"."] || [name isEqualToString:@".."]) return nil;
    return [[FAOTControlMapDirectory() stringByAppendingPathComponent:name]
            stringByAppendingPathExtension:@"json"];
}

NSArray<NSDictionary<NSString *, id> *> *FAOTControlMapEntries(void) {
    NSFileManager *manager = NSFileManager.defaultManager;
    NSString *directory = FAOTControlMapDirectory();
    NSMutableArray<NSDictionary<NSString *, id> *> *entries = [NSMutableArray array];
    for (NSString *file in [manager contentsOfDirectoryAtPath:directory error:nil]) {
        if (![file.pathExtension isEqualToString:@"json"] || [file hasPrefix:@"."]) continue;
        NSString *path = [directory stringByAppendingPathComponent:file];
        NSDictionary *attributes = [manager attributesOfItemAtPath:path error:nil];
        if (!attributes) continue;
        [entries addObject:@{
            @"name": file.stringByDeletingPathExtension,
            @"modified": attributes[NSFileModificationDate] ?: NSDate.distantPast,
            @"bytes": attributes[NSFileSize] ?: @0,
        }];
    }
    [entries sortUsingComparator:^NSComparisonResult(NSDictionary *a, NSDictionary *b) {
        NSComparisonResult byDate = [b[@"modified"] compare:a[@"modified"]];
        return byDate != NSOrderedSame ? byDate : [a[@"name"] localizedStandardCompare:b[@"name"]];
    }];
    return entries;
}

CGFloat FAOTControlButtonScale(void) {
    CGFloat value = [NSUserDefaults.standardUserDefaults doubleForKey:FAOTControlScaleKey];
    return value >= 40.0 && value <= 200.0 ? value : 100.0;
}

NSString *FAOTControlActiveLayoutName(void) {
    NSString *name = [NSUserDefaults.standardUserDefaults stringForKey:FAOTControlLayoutDefaultsKey];
    if (name.length == 0) name = FAOTControlDefaultName;
    return name;
}

void FAOTControlSetActiveLayoutName(NSString *name) {
    [NSUserDefaults.standardUserDefaults setObject:name.length ? name : FAOTControlDefaultName
                                            forKey:FAOTControlLayoutDefaultsKey];
}

static NSMutableDictionary *FAOTControlReadJSON(NSString *path) {
    NSData *data = [NSData dataWithContentsOfFile:path];
    if (!data) return nil;
    id root = [NSJSONSerialization JSONObjectWithData:data options:NSJSONReadingMutableContainers error:nil];
    return [root isKindOfClass:NSMutableDictionary.class] ? root : nil;
}

void FAOTControlEnsureDefaults(void) {
    NSFileManager *manager = NSFileManager.defaultManager;
    NSString *directory = FAOTControlMapDirectory();
    [manager createDirectoryAtPath:directory withIntermediateDirectories:YES attributes:nil error:nil];
    NSString *path = FAOTControlMapPath(FAOTControlDefaultName);
    if ([manager fileExistsAtPath:path]) return;
    NSMutableDictionary *layout = FAOTDefaultControlLayoutDictionary();
    if (!layout) return;
    NSData *data = [NSJSONSerialization dataWithJSONObject:layout options:NSJSONWritingPrettyPrinted error:nil];
    [data writeToFile:path options:NSDataWritingAtomic error:nil];
}

NSMutableDictionary *FAOTControlLoadLayout(NSString *name) {
    FAOTControlEnsureDefaults();
    NSString *path = FAOTControlMapPath(name);
    if (!path) return nil;
    NSMutableDictionary *layout = FAOTControlReadJSON(path);
    if (layout) return layout;
    if (![name isEqualToString:FAOTControlDefaultName]) return nil;
    return FAOTDefaultControlLayoutDictionary();
}

NSString *FAOTControlSaveLayout(NSString *name, NSDictionary *layout) {
    NSString *path = FAOTControlMapPath(name);
    if (!path) return @"布局名称无效";
    NSFileManager *manager = NSFileManager.defaultManager;
    [manager createDirectoryAtPath:FAOTControlMapDirectory() withIntermediateDirectories:YES attributes:nil error:nil];
    NSError *error = nil;
    NSData *data = [NSJSONSerialization dataWithJSONObject:layout options:NSJSONWritingPrettyPrinted error:&error];
    if (!data) return error.localizedDescription ?: @"无法序列化布局";
    if (![data writeToFile:path options:NSDataWritingAtomic error:&error]) {
        return error.localizedDescription ?: @"写入失败";
    }
    return nil;
}

BOOL FAOTControlDuplicateLayout(NSString *source, NSString *destination) {
    NSDictionary *layout = FAOTControlLoadLayout(source);
    if (!layout) return NO;
    return FAOTControlSaveLayout(destination, layout) == nil;
}

BOOL FAOTControlDeleteLayout(NSString *name) {
    if ([name isEqualToString:FAOTControlDefaultName]) return NO;
    NSString *path = FAOTControlMapPath(name);
    return path ? [NSFileManager.defaultManager removeItemAtPath:path error:nil] : NO;
}

#pragma mark - Input

/* libglfw is dlopen'ed by main.m, so its symbols are resolved at runtime rather than
   linked. Looking them up here keeps the app linkable and makes an absent surface a
   no-op instead of a crash. */
typedef void *(*FAOTCurrentContextFn)(void);
typedef int (*FAOTEnqueueFn)(void *, int, double, double, int, int);

static void *FAOTControlWindow(void) {
    static FAOTCurrentContextFn current;
    static dispatch_once_t once;
    dispatch_once(&once, ^{ current = (FAOTCurrentContextFn)dlsym(RTLD_DEFAULT, "glfwGetCurrentContext"); });
    return current ? current() : NULL;
}

static BOOL FAOTControlEnqueue(int kind, double a, double b, int c, int d) {
    static FAOTEnqueueFn enqueue;
    static dispatch_once_t once;
    dispatch_once(&once, ^{ enqueue = (FAOTEnqueueFn)dlsym(RTLD_DEFAULT, "faot_glfw_enqueue"); });
    void *window = FAOTControlWindow();
    if (!window || !enqueue) return NO;
    return enqueue(window, kind, a, b, c, d) != 0;
}

enum { FAOT_EVENT_CURSOR = 2, FAOT_EVENT_BUTTON = 3, FAOT_EVENT_SCROLL = 4,
       FAOT_EVENT_KEY = 6, FAOT_EVENT_CURSOR_DELTA = 8 };

BOOL FAOTControlHasGameSurface(void) { return FAOTControlWindow() != NULL; }

void FAOTControlSendKey(int keycode, BOOL down) {
    FAOTControlEnqueue(FAOT_EVENT_KEY, keycode, 0, down ? 1 : 0, 0);
}

void FAOTControlSendMouseButton(int button, BOOL down) {
    FAOTControlEnqueue(FAOT_EVENT_BUTTON, button, down ? 1 : 0, 0, 0);
}

void FAOTControlSendScroll(double dy) {
    FAOTControlEnqueue(FAOT_EVENT_SCROLL, 0.0, dy, 0, 0);
}

void FAOTControlSendCursorDelta(double dx, double dy) {
    FAOTControlEnqueue(FAOT_EVENT_CURSOR_DELTA, dx, dy, 0, 0);
}
