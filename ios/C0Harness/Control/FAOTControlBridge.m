//
//  FAOTControlBridge.m
//
#import "FAOTControlBridge.h"
#import "FAOTLauncherViewController.h"
#import "FAOTControlLayout.h"
#import "CustomControlsUtils.h"
#include "glfw_keycodes.h"

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

// Older NCL layouts use normalized coordinates and a `controls` array. Convert in
// memory only; opening an editor must never rewrite the original file.
static NSMutableDictionary *FAOTConvertLegacyLayout(NSMutableDictionary *legacy) {
    if (legacy[@"mControlDataList"] || ![legacy[@"controls"] isKindOfClass:NSArray.class]) return legacy;
    NSMutableDictionary *converted = [@{@"version": @8, @"scaledAt": @(FAOTControlButtonScale()),
        @"mControlDataList": [NSMutableArray array], @"mDrawerDataList": [NSMutableArray array],
        @"mJoystickDataList": [NSMutableArray array]} mutableCopy];
    for (id item in legacy[@"controls"]) {
        if (![item isKindOfClass:NSDictionary.class]) continue;
        FAOTControlModel *model = [FAOTControlModel modelWithDictionary:item];
        CGFloat r = 0, g = 0, b = 0, a = 1;
        [model.background getRed:&r green:&g blue:&b alpha:&a];
        uint32_t color = ((uint32_t)lround(a * 255) << 24) | ((uint32_t)lround(r * 255) << 16) |
                         ((uint32_t)lround(g * 255) << 8) | (uint32_t)lround(b * 255);
        NSInteger keycode = model.keycode <= -101 && model.keycode >= -108 ? model.keycode + 100 : model.keycode;
        NSMutableDictionary *button = [@{
            @"name": model.name ?: @"", @"keycodes": [@[@(keycode), @0, @0, @0] mutableCopy],
            @"dynamicX": [NSString stringWithFormat:@"%f * ${screen_width}", model.x],
            @"dynamicY": [NSString stringWithFormat:@"%f * ${screen_height}", model.y],
            @"width": @(MAX(10, model.width * 844)), @"height": @(MAX(10, model.height * 390)),
            @"opacity": @(model.opacity), @"cornerRadius": @(model.cornerRadius), @"bgColor": @(color),
            @"isToggle": @(model.toggle), @"displayInMenu": @(model.showInMenu),
            @"displayInGame": @(model.showInGame)
        } mutableCopy];
        [converted[@"mControlDataList"] addObject:button];
    }
    return converted;
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
    if (layout) return FAOTConvertLegacyLayout(layout);
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

/* The GLFW surface supplies its live window and queue function on the UIKit thread. */
typedef int (*FAOTEnqueueFn)(void *, int, double, double, int, int);
static void *inputWindow;
static FAOTEnqueueFn inputEnqueue;
void FAOTControlSetInputTarget(void *window, void *enqueue) {
    inputWindow = window;
    inputEnqueue = (FAOTEnqueueFn)enqueue;
}

static void *FAOTControlWindow(void) {
    return inputWindow;
}

static BOOL FAOTControlEnqueue(int kind, double a, double b, int c, int d) {
    void *window = FAOTControlWindow();
    if (!window || !inputEnqueue) return NO;
    return inputEnqueue(window, kind, a, b, c, d) != 0;
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
