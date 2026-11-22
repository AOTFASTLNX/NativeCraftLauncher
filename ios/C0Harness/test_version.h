#ifndef FAOT_TEST_VERSION_H
#define FAOT_TEST_VERSION_H
#import <Foundation/Foundation.h>
NS_ASSUME_NONNULL_BEGIN
static inline NSString *FAOTMinecraftVersionFromInfo(NSDictionary *info) {
    id value = info[@"FAOTMinecraftVersion"];
    return [value isKindOfClass:NSString.class] && [value isEqual:@"1.21.1"] ? value : @"26.1.2";
}
static inline NSString *FAOTCurrentMinecraftVersion(void) {
    return FAOTMinecraftVersionFromInfo(NSBundle.mainBundle.infoDictionary ?: @{});
}
static inline NSString *FAOTCurrentAssetIndex(void) {
    id value = NSBundle.mainBundle.infoDictionary[@"FAOTAssetIndex"];
    if ([value isKindOfClass:NSString.class] &&
        [value rangeOfString:@"^[0-9]+$" options:NSRegularExpressionSearch].location != NSNotFound) return value;
    return [FAOTCurrentMinecraftVersion() isEqual:@"1.21.1"] ? @"17" : @"30";
}
static inline NSString *FAOTTestVersionFromInfo(NSDictionary *info) {
    id value = info[@"FAOTTestVersion"];
    if (![value isKindOfClass:NSString.class]) return @"";
    NSString *label = [value stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet];
    if (label.length && [label rangeOfString:@"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$" options:NSRegularExpressionSearch].location == NSNotFound) return @"";
    return label;
}
static inline NSString *FAOTCurrentTestVersion(void) {
    return FAOTTestVersionFromInfo(NSBundle.mainBundle.infoDictionary ?: @{});
}
static inline NSString *FAOTTestVersionSubtitle(NSString *label) {
    NSString *base = [NSString stringWithFormat:@"你的方块世界，随身出发。\nMinecraft Java %@ · Fabric", FAOTCurrentMinecraftVersion()];
    return label.length ? [base stringByAppendingFormat:@"\n测试版本：%@", label] : base;
}
static inline NSString *FAOTTestVersionLogName(NSString *label, double milliseconds) {
    return label.length ? [NSString stringWithFormat:@"run-%.0f-%@.log", milliseconds, label]
                        : [NSString stringWithFormat:@"run-%.0f.log", milliseconds];
}
NS_ASSUME_NONNULL_END
#endif
