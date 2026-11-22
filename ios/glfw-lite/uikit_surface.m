#import <UIKit/UIKit.h>
#import <QuartzCore/CAMetalLayer.h>
#import <Metal/Metal.h>
#import <GameController/GameController.h>
#import <os/lock.h>
#include <math.h>
#include <string.h>
#include "pointer_motion.h"
#include "glfw_lite.h"
#include "input_mapping.h"

/* The on-screen control surface lives in the app (ios/C0Harness/Control). The GLFW dylib is
   also built standalone for CI, so it reaches the surface through the ObjC runtime instead
   of linking the app's control layer. */
@protocol FAOTControlSurface <NSObject>
- (void)setInputWindow:(void *)window enqueue:(void *)enqueue;
- (BOOL)loadLayoutNamed:(NSString *)name error:(NSString *_Nullable *_Nullable)error;
- (void)setCaptured:(BOOL)captured;
- (void)releaseAllInput;
- (BOOL)isHoldingKey:(int)keycode;
@end

static void onMain(dispatch_block_t block) {
    if ([NSThread isMainThread]) block(); else dispatch_sync(dispatch_get_main_queue(),block);
}
static UIWindowScene *activeScene(void) {
    UIWindowScene *inactive=nil;
    for (UIScene *scene in UIApplication.sharedApplication.connectedScenes) {
        if (![scene isKindOfClass:UIWindowScene.class]) continue;
        if (scene.activationState==UISceneActivationStateForegroundActive) return (UIWindowScene *)scene;
        // At cold launch the scene can still be inactive while the first frame is requested.
        if (!inactive && scene.activationState==UISceneActivationStateForegroundInactive) inactive=(UIWindowScene *)scene;
    }
    return inactive;
}

@interface FAOTMetalView : UIView <UIKeyInput> {
    FAOTPointerMotion _hoverMotion;
}
@property(nonatomic,assign) GLFWwindow *handle;
@property(nonatomic,assign) BOOL textEnabled;
@property(nonatomic,assign) BOOL captured;
@property(nonatomic,assign) BOOL hasCapturedGame;
@property(nonatomic,assign) BOOL nativeMouseConnected;
@property(nonatomic,assign) BOOL pointerIsLocked;
@property(nonatomic,assign) unsigned heldNativeMouseButtons;
@property(nonatomic,strong) UIView *noKeyboard;
@property(nonatomic,strong) UITouch *primaryTouch;
@property(nonatomic,assign) int primaryButton;
@property(nonatomic,strong) NSMutableIndexSet *forwardedKeys;
@property(nonatomic,strong) UIView<FAOTControlSurface> *controls;
- (void)installControls;
- (void)setGameCapture:(BOOL)captured;
- (void)releaseGameControls;
- (void)releaseNativeMouseButtons;
- (void)nativeMouseButton:(int)index pressed:(BOOL)pressed;
- (void)pointerHovered:(UIHoverGestureRecognizer *)recognizer;
- (void)resetHoverMotion;
@end
@implementation FAOTMetalView
+ (Class)layerClass { return CAMetalLayer.class; }
- (BOOL)canBecomeFirstResponder { return YES; }
- (UIView *)inputView {
    if(self.textEnabled)return nil;
    if(!self.noKeyboard)self.noKeyboard=[[UIView alloc] initWithFrame:CGRectZero];
    return self.noKeyboard;
}
- (BOOL)forwardKeyPress:(UIPress *)press action:(int)action {
    if(!self.handle||!press.key||(action!=0&&self.textEnabled))return NO;
    UIKey *key=press.key;int code=faot_glfw_key_from_hid((unsigned)key.keyCode);if(code<0)return NO;
    if(!self.forwardedKeys)self.forwardedKeys=[NSMutableIndexSet indexSet];
    BOOL already=[self.forwardedKeys containsIndex:(NSUInteger)code];
    if(action==0&&!already)return NO;
    UIKeyModifierFlags flags=key.modifierFlags;
    int mods=((flags&UIKeyModifierShift)?1:0)|((flags&UIKeyModifierControl)?2:0)|((flags&UIKeyModifierAlternate)?4:0)|((flags&UIKeyModifierCommand)?8:0)|((flags&UIKeyModifierAlphaShift)?16:0);
    BOOL touchHeld=self.controls ? [self.controls isHoldingKey:code] : NO;
    if(!touchHeld&&!faot_glfw_enqueue(self.handle,FAOT_EVENT_KEY,code,key.keyCode,action==1&&already?2:action,mods))return NO;
    if(action)[self.forwardedKeys addIndex:(NSUInteger)code];else[self.forwardedKeys removeIndex:(NSUInteger)code];
    return YES;
}
- (void)pressesBegan:(NSSet<UIPress *> *)presses withEvent:(UIPressesEvent *)event {
    NSMutableSet<UIPress *> *rest=[presses mutableCopy];
    for(UIPress *p in presses)if([self forwardKeyPress:p action:1])[rest removeObject:p];
    if(rest.count)[super pressesBegan:rest withEvent:event];
}
- (void)pressesEnded:(NSSet<UIPress *> *)presses withEvent:(UIPressesEvent *)event {
    NSMutableSet<UIPress *> *rest=[presses mutableCopy];
    for(UIPress *p in presses)if([self forwardKeyPress:p action:0])[rest removeObject:p];
    if(rest.count)[super pressesEnded:rest withEvent:event];
}
- (void)pressesCancelled:(NSSet<UIPress *> *)presses withEvent:(UIPressesEvent *)event {
    NSMutableSet<UIPress *> *rest=[presses mutableCopy];
    for(UIPress *p in presses)if([self forwardKeyPress:p action:0])[rest removeObject:p];
    if(rest.count)[super pressesCancelled:rest withEvent:event];
}
- (void)layoutSubviews {
    [super layoutSubviews];
    CGFloat scale=self.window.screen.scale ?: UIScreen.mainScreen.scale;
    self.contentScaleFactor=scale;
    CAMetalLayer *layer=(CAMetalLayer *)self.layer;
    layer.contentsScale=scale;
    layer.drawableSize=CGSizeMake(round(self.bounds.size.width*scale),round(self.bounds.size.height*scale));
    if(self.handle)faot_glfw_resize(self.handle,(int)self.bounds.size.width,(int)self.bounds.size.height,
                                  (int)layer.drawableSize.width,(int)layer.drawableSize.height);
}
- (void)installControls {
    /* The launcher editor owns the layout. FAOTControlsView loads the same Pojav JSON the
       editor writes, so a button edited in the launcher behaves identically in game. */
    Class controlsClass=NSClassFromString(@"FAOTControlsView");
    if(!controlsClass){
        fprintf(stderr,"[FAOT] controls unavailable: FAOTControlsView is not linked\n");
        return;
    }
    UIView<FAOTControlSurface> *controls=[(UIView<FAOTControlSurface> *)[controlsClass alloc] initWithFrame:self.bounds];
    controls.autoresizingMask=UIViewAutoresizingFlexibleWidth|UIViewAutoresizingFlexibleHeight;
    [controls setInputWindow:self.handle enqueue:(void *)faot_glfw_enqueue];
    [controls setCaptured:self.captured];
    NSString *name=[NSUserDefaults.standardUserDefaults stringForKey:@"FAOTControlLayout"];
    if(!name.length)name=@"default";
    NSString *error=nil;
    if(![controls loadLayoutNamed:name error:&error])
        fprintf(stderr,"[FAOT] controls load failed: %s\n",error.UTF8String ?: "unknown");
    [self addSubview:controls];
    self.controls=controls;
    // Menu pointer positions are independent of clicks. UIKit touches alone cannot track hover.
    UIHoverGestureRecognizer *hover=[[UIHoverGestureRecognizer alloc] initWithTarget:self action:@selector(pointerHovered:)];
    hover.cancelsTouchesInView=NO;
    [self addGestureRecognizer:hover];
}
- (void)releaseNativeMouseButtons {
    if(self.handle)for(int i=0;i<3;i++)if(self.heldNativeMouseButtons&(1u<<i))
        faot_glfw_enqueue(self.handle,FAOT_EVENT_BUTTON,i,0,0,0);
    self.heldNativeMouseButtons=0;
}
- (void)nativeMouseButton:(int)index pressed:(BOOL)pressed {
    if(!self.handle||!self.captured||index<0||index>2)return;
    unsigned bit=1u<<index;
    if(((self.heldNativeMouseButtons&bit)!=0)==pressed)return;
    if(!faot_glfw_enqueue(self.handle,FAOT_EVENT_BUTTON,index,pressed,0,0))return;
    if(pressed)self.heldNativeMouseButtons|=bit;
    else self.heldNativeMouseButtons&=~bit;
}
- (void)releaseGameControls {
    [self releaseNativeMouseButtons];
    [self.controls releaseAllInput];
}
- (void)pointerHovered:(UIHoverGestureRecognizer *)recognizer {
    if(!self.handle)return;
    if(recognizer.state!=UIGestureRecognizerStateBegan&&recognizer.state!=UIGestureRecognizerStateChanged){
        [self resetHoverMotion];return;
    }
    CGPoint p=[recognizer locationInView:self];
    if(!self.captured){
        [self resetHoverMotion];
        faot_glfw_enqueue(self.handle,FAOT_EVENT_CURSOR,p.x,p.y,0,0);
    }else if(!self.pointerIsLocked){
        double dx,dy;
        if(faot_pointer_hover_delta(&_hoverMotion,p.x,p.y,&dx,&dy))
            faot_glfw_enqueue(self.handle,FAOT_EVENT_CURSOR_DELTA,dx,dy,0,0);
    }else [self resetHoverMotion];
}
- (void)resetHoverMotion { faot_pointer_reset(&_hoverMotion); }
- (void)setGameCapture:(BOOL)captured {
    if(self.captured==captured)return;
    [self resetHoverMotion];
    if(!captured)[self releaseGameControls];
    self.captured=captured;
    self.hasCapturedGame|=captured;
    // iPadOS may deny pointer lock (for example in Split View). Observe the actual lock state.
    [(UIViewController *)self.window.rootViewController setNeedsUpdateOfPrefersPointerLocked];
    [self.controls setCaptured:captured];
}
/* Absolute point coordinates match glfwGetWindowSize. Pixel scale belongs to framebuffer size. */
- (void)touchesBegan:(NSSet<UITouch *> *)touches withEvent:(UIEvent *)event {
    if(!self.handle||self.primaryTouch)return;
    UITouch *touch=touches.anyObject;
    // GCMouse supplies hardware mouse clicks in game; do not forward UIKit duplicates.
    if(self.captured&&self.nativeMouseConnected&&touch.type==UITouchTypeIndirectPointer)return;
    self.primaryTouch=touch;
    CGPoint p=[touch locationInView:self];
    if(!self.captured)faot_glfw_enqueue(self.handle,FAOT_EVENT_CURSOR,p.x,p.y,0,0);
    // Finger drags look around without also attacking. A connected mouse retains both buttons.
    self.primaryButton=self.captured&&touch.type!=UITouchTypeIndirectPointer?-1:
        ((event.buttonMask&UIEventButtonMaskSecondary)?1:0);
    if(self.primaryButton>=0)faot_glfw_enqueue(self.handle,FAOT_EVENT_BUTTON,self.primaryButton,1,0,0);
}
- (void)touchesMoved:(NSSet<UITouch *> *)touches withEvent:(UIEvent *)event {
    if(!self.handle)return;
    UITouch *touch=self.primaryTouch;if(!touch||![touches containsObject:touch])return;
    CGPoint p=[touch locationInView:self],old=[touch previousLocationInView:self];
    if(self.captured)faot_glfw_enqueue(self.handle,FAOT_EVENT_CURSOR_DELTA,p.x-old.x,p.y-old.y,0,0);
    else if(event.allTouches.count>1)faot_glfw_enqueue(self.handle,FAOT_EVENT_SCROLL,(p.x-old.x)/24.0,(old.y-p.y)/24.0,0,0);
    else faot_glfw_enqueue(self.handle,FAOT_EVENT_CURSOR,p.x,p.y,0,0);
}
- (void)touchesEnded:(NSSet<UITouch *> *)touches withEvent:(UIEvent *)event {
    (void)event;
    if(self.primaryTouch&&[touches containsObject:self.primaryTouch]){
        if(self.handle&&self.primaryButton>=0)faot_glfw_enqueue(self.handle,FAOT_EVENT_BUTTON,self.primaryButton,0,0,0);
        self.primaryTouch=nil;
    }
}
- (void)touchesCancelled:(NSSet<UITouch *> *)touches withEvent:(UIEvent *)event { [self touchesEnded:touches withEvent:event]; }
- (BOOL)hasText { return YES; }
- (void)insertText:(NSString *)text {
    if(!self.handle||!self.textEnabled)return;
    /* Convert UTF-16 surrogate pairs to Unicode scalar values for GLFWCharCallback. */
    NSData *data=[text dataUsingEncoding:NSUTF32LittleEndianStringEncoding];
    const uint32_t *chars=data.bytes;
    for(NSUInteger i=0;i<data.length/4;i++)faot_glfw_enqueue(self.handle,FAOT_EVENT_CHAR,chars[i],0,0,0);
}
- (void)deleteBackward {
    if(!self.handle)return;
    faot_glfw_enqueue(self.handle,FAOT_EVENT_KEY,259,0,1,0);
    faot_glfw_enqueue(self.handle,FAOT_EVENT_KEY,259,0,0,0);
}
@end

@interface FAOTGameController : UIViewController
@end
@implementation FAOTGameController
- (BOOL)prefersStatusBarHidden { return YES; }
- (BOOL)prefersPointerLocked { return ((FAOTMetalView *)self.view).captured; }
- (void)viewDidAppear:(BOOL)animated {
    [super viewDidAppear:animated];
    [self setNeedsUpdateOfPrefersPointerLocked];
}
@end

@class FAOTPresentStats;
@interface FAOTPresentSlot : NSObject
@property(nonatomic,strong) id<MTLTexture> texture;
@property(nonatomic,strong) dispatch_semaphore_t freeSlot;
@property(nonatomic,strong) FAOTPresentStats *stats;
@end

/* CADisplayLink keeps a strong reference to its target. This stateless target requests the
   120 Hz range without creating a reference cycle back to the game surface. */
@interface FAOTRefreshTicker : NSObject
@end
@implementation FAOTRefreshTicker
- (void)tick:(CADisplayLink *)link { (void)link; }
@end

/* MTLDrawable callbacks may run on different threads. Only timestamps and counters
   are protected; printing happens after the lock is released. */
@interface FAOTPresentStats : NSObject {
    os_unfair_lock _lock;
    double _last, _start;
    unsigned _intervals, _longFrames, _drops;
    unsigned _buckets[101];
    unsigned _reserved, _drawables, _texturesCreated, _texturesDestroyed;
    unsigned long long _textureBytes;
}
- (void)dropped;
- (void)presented:(double)time;
- (void)reserved:(int)delta drawable:(int)drawableDelta;
- (void)textureBytes:(NSUInteger)bytes created:(BOOL)created;
@end

@implementation FAOTPresentStats
- (instancetype)init {
    if((self=[super init]))_lock=OS_UNFAIR_LOCK_INIT;
    return self;
}
- (void)dropped {
    os_unfair_lock_lock(&_lock);
    _drops++;
    os_unfair_lock_unlock(&_lock);
}
- (void)reserved:(int)delta drawable:(int)drawableDelta {
    os_unfair_lock_lock(&_lock);
    _reserved+=delta; _drawables+=drawableDelta;
    os_unfair_lock_unlock(&_lock);
}
- (void)textureBytes:(NSUInteger)bytes created:(BOOL)created {
    os_unfair_lock_lock(&_lock);
    if(created){_texturesCreated++;_textureBytes+=bytes;}
    else {_texturesDestroyed++;_textureBytes-=bytes;}
    os_unfair_lock_unlock(&_lock);
}
- (void)presented:(double)time {
    if(time<=0){[self dropped];return;}
    double fps=0;
    unsigned p95=0,longFrames=0,drops=0;
    unsigned reserved=0,drawables=0,created=0,destroyed=0;
    unsigned long long textureBytes=0;
    os_unfair_lock_lock(&_lock);
    if(!_last || time-_last>1.0){
        _start=_last=time;
        _intervals=_longFrames=_drops=0;
        memset(_buckets,0,sizeof _buckets);
    }else if(time>_last){
        double ms=(time-_last)*1000.0;
        unsigned bin=ms>=100.0?100:(unsigned)ceil(ms);
        _buckets[bin]++;
        _intervals++;
        if(ms>34.0)_longFrames++;
        _last=time;
        if(time-_start>=5.0){
            fps=_intervals/(time-_start);
            unsigned rank=(_intervals*95+99)/100, seen=0;
            for(;p95<100;p95++){
                seen+=_buckets[p95];
                if(seen>=rank)break;
            }
            longFrames=_longFrames;
            drops=_drops;
            reserved=_reserved;drawables=_drawables;created=_texturesCreated;destroyed=_texturesDestroyed;textureBytes=_textureBytes;
            _start=time;
            _intervals=_longFrames=_drops=0;
            memset(_buckets,0,sizeof _buckets);
        }
    }
    os_unfair_lock_unlock(&_lock);
    if(fps>0)fprintf(stderr,"[GL41] display fps=%.1f p95=%ums long34=%u drops=%u\n",
                     fps,p95,longFrames,drops);
    const char *diagnostics=getenv("FAOT_GL41_DIAGNOSTICS");
    if(fps>0&&diagnostics&&!strcmp(diagnostics,"1"))
        fprintf(stderr,"[GL41-DIAG] Metal reserved=%u drawableInFlight=%u textures(created/destroyed)=%u/%u textureBytes=%llu\n",
                reserved,drawables,created,destroyed,textureBytes);
}
@end

@implementation FAOTPresentSlot
- (void)dealloc {
    if(_texture)[_stats textureBytes:_texture.allocatedSize created:NO];
}
@end

@interface FAOTSurface : NSObject
@property(nonatomic,strong) UIWindow *window;
@property(nonatomic,weak) UIWindow *previous;
@property(nonatomic,strong) FAOTMetalView *view;
@property(nonatomic,strong) id foregroundToken;
@property(nonatomic,strong) id backgroundToken;
@property(nonatomic,strong) id pointerToken;
@property(nonatomic,strong) id mouseConnectToken;
@property(nonatomic,strong) id mouseDisconnectToken;
@property(nonatomic,strong) GCMouse *mouse;
@property(nonatomic,strong) id<MTLCommandQueue> presentQueue;
@property(nonatomic,strong) NSArray<FAOTPresentSlot *> *presentSlots;
@property(nonatomic,strong) dispatch_queue_t presentWorker;
@property(nonatomic,strong) FAOTPresentStats *presentStats;
@property(nonatomic,strong) CADisplayLink *refreshLink;
@property(atomic,assign) BOOL canPresent;
@property(atomic,assign) BOOL presentFailed;
@property(nonatomic,assign) NSUInteger presentSlot;
@property(nonatomic,assign) int presentWidth, presentHeight;
@property(nonatomic,strong) id<MTLRenderPipelineState> presentPipeline;
@property(nonatomic,strong) id<MTLSamplerState> presentSampler;
- (void)attachMouse:(GCMouse *)mouse;
@end
@implementation FAOTSurface
- (void)attachMouse:(GCMouse *)mouse {
    if(self.mouse==mouse)return;
    if(self.mouse){
        GCMouseInput *old=self.mouse.mouseInput;
        old.mouseMovedHandler=nil;
        old.leftButton.valueChangedHandler=nil;
        old.rightButton.valueChangedHandler=nil;
        old.middleButton.valueChangedHandler=nil;
        old.scroll.valueChangedHandler=nil;
    }
    [self.view releaseNativeMouseButtons];
    self.mouse=mouse;
    self.view.nativeMouseConnected=mouse!=nil;
    if(!mouse)return;

    __weak FAOTMetalView *weakView=self.view;
    GCMouseInput *input=mouse.mouseInput;
    // GameController reports unaccelerated relative motion; GLFW expects Y increasing down.
    input.mouseMovedHandler=^(GCMouseInput *sender,float dx,float dy){
        (void)sender;
        dispatch_block_t forward=^{
            FAOTMetalView *view=weakView;
            if(view&&view.handle&&view.captured&&view.pointerIsLocked)
                faot_glfw_enqueue(view.handle,FAOT_EVENT_CURSOR_DELTA,dx,-dy,0,0);
        };
        if([NSThread isMainThread])forward();else dispatch_async(dispatch_get_main_queue(),forward);
    };
    void (^bindButton)(GCControllerButtonInput *,int)=^(GCControllerButtonInput *button,int index){
        if(!button)return;
        button.valueChangedHandler=^(GCControllerButtonInput *sender,float value,BOOL pressed){
            (void)sender;(void)value;
            dispatch_block_t forward=^{ FAOTMetalView *view=weakView; if(view)[view nativeMouseButton:index pressed:pressed]; };
            if([NSThread isMainThread])forward();else dispatch_async(dispatch_get_main_queue(),forward);
        };
    };
    bindButton(input.leftButton,0);
    bindButton(input.rightButton,1);
    bindButton(input.middleButton,2);
    input.scroll.valueChangedHandler=^(GCControllerDirectionPad *sender,float dx,float dy){
        (void)sender;
        dispatch_block_t forward=^{
            FAOTMetalView *view=weakView;
            if(view&&view.handle)
                faot_glfw_enqueue(view.handle,FAOT_EVENT_SCROLL,dx,dy,0,0);
        };
        if([NSThread isMainThread])forward();else dispatch_async(dispatch_get_main_queue(),forward);
    };
}
@end

void faot_surface_screen(GLFWvidmode *mode) {
    onMain(^{
        UIScreen *screen=activeScene().screen ?: UIScreen.mainScreen;
        *mode=(GLFWvidmode){(int)screen.bounds.size.width,(int)screen.bounds.size.height,8,8,8,(int)screen.maximumFramesPerSecond};
    });
}
void *faot_surface_create(GLFWwindow *handle,int *width,int *height,int *pw,int *ph) {
    __block FAOTSurface *surface=nil;
    onMain(^{
        UIWindowScene *scene=activeScene();
        if(!scene)return;
        id<MTLDevice> device=MTLCreateSystemDefaultDevice();
        if(!device)return;
        surface=[FAOTSurface new];
        surface.canPresent=YES;
        for(UIWindow *window in scene.windows)if(window.isKeyWindow)surface.previous=window;
        surface.window=[[UIWindow alloc] initWithWindowScene:scene];
        UIViewController *controller=[FAOTGameController new];
        surface.view=[[FAOTMetalView alloc] initWithFrame:scene.coordinateSpace.bounds];
        surface.view.handle=handle;
        surface.view.multipleTouchEnabled=YES;
        [surface.view installControls];
        CAMetalLayer *layer=(CAMetalLayer *)surface.view.layer;
        layer.device=device;
        layer.pixelFormat=MTLPixelFormatBGRA8Unorm;
        layer.framebufferOnly=NO;
        layer.maximumDrawableCount=3;
        layer.opaque=YES;
        NSInteger maximumHz=scene.screen.maximumFramesPerSecond;
        if(maximumHz<60)maximumHz=60;
        NSInteger minimumHz=maximumHz>=120?80:maximumHz;
        CADisplayLink *refreshLink=[CADisplayLink displayLinkWithTarget:[FAOTRefreshTicker new]
            selector:@selector(tick:)];
        refreshLink.preferredFrameRateRange=CAFrameRateRangeMake(
            (float)minimumHz,(float)maximumHz,(float)maximumHz);
        [refreshLink addToRunLoop:NSRunLoop.mainRunLoop forMode:NSRunLoopCommonModes];
        surface.refreshLink=refreshLink;
        fprintf(stderr,"[GL41] refresh request=%ld-%ld preferred=%ld maximum=%ld drawables=3\n",
            (long)minimumHz,(long)maximumHz,(long)maximumHz,
            (long)scene.screen.maximumFramesPerSecond);
        controller.view=surface.view;
        surface.window.rootViewController=controller;
        [surface.window makeKeyAndVisible];
        [surface.view becomeFirstResponder];
        [surface.view layoutIfNeeded];
        CGFloat scale=scene.screen.scale;
        layer.contentsScale=scale;
        layer.drawableSize=CGSizeMake(round(surface.view.bounds.size.width*scale),round(surface.view.bounds.size.height*scale));
        *width=(int)surface.view.bounds.size.width;*height=(int)surface.view.bounds.size.height;
        *pw=(int)layer.drawableSize.width;*ph=(int)layer.drawableSize.height;
        NSNotificationCenter *nc=NSNotificationCenter.defaultCenter;
        __weak FAOTSurface *weakSurface=surface;
        __weak FAOTMetalView *pointerView=surface.view;
        surface.pointerToken=[nc addObserverForName:UIPointerLockStateDidChangeNotification object:scene.pointerLockState queue:NSOperationQueue.mainQueue usingBlock:^(NSNotification *note){
            (void)note;
            FAOTMetalView *view=pointerView;
            [view resetHoverMotion];
            view.pointerIsLocked=scene.pointerLockState.isLocked;
            fprintf(stderr,"[GLFW-lite] iPad pointer lock: %s\\n",view.pointerIsLocked?"locked":"unlocked");
        }];
        surface.view.pointerIsLocked=scene.pointerLockState.isLocked;
        surface.mouseConnectToken=[nc addObserverForName:GCMouseDidConnectNotification object:nil queue:NSOperationQueue.mainQueue usingBlock:^(NSNotification *note){
            [weakSurface attachMouse:(GCMouse *)note.object];
        }];
        surface.mouseDisconnectToken=[nc addObserverForName:GCMouseDidDisconnectNotification object:nil queue:NSOperationQueue.mainQueue usingBlock:^(NSNotification *note){
            FAOTSurface *current=weakSurface;
            if(current.mouse==note.object)[current attachMouse:[GCMouse mice].firstObject];
        }];
        [surface attachMouse:[GCMouse mice].firstObject];
        surface.foregroundToken=[nc addObserverForName:UISceneDidActivateNotification object:scene queue:NSOperationQueue.mainQueue usingBlock:^(NSNotification *note){
            (void)note;weakSurface.canPresent=YES;weakSurface.refreshLink.paused=NO;
            faot_glfw_enqueue(handle,FAOT_EVENT_FOCUS,1,0,0,0);
        }];
        __weak FAOTMetalView *inputView=surface.view;
        surface.backgroundToken=[nc addObserverForName:UISceneWillDeactivateNotification object:scene queue:NSOperationQueue.mainQueue usingBlock:^(NSNotification *note){
            (void)note;weakSurface.canPresent=NO;weakSurface.refreshLink.paused=YES;FAOTMetalView *view=inputView;
            if(view.primaryTouch){if(view.primaryButton>=0)faot_glfw_enqueue(handle,FAOT_EVENT_BUTTON,view.primaryButton,0,0,0);view.primaryTouch=nil;}
            [view releaseGameControls];
            [view resetHoverMotion];
            view.pointerIsLocked=NO;
            [view.forwardedKeys removeAllIndexes];faot_glfw_enqueue(handle,FAOT_EVENT_FOCUS,0,0,0,0);
        }];
    });
    return (__bridge_retained void *)surface;
}
void faot_surface_destroy(void *raw) {
    if(!raw)return;
    onMain(^{
        FAOTSurface *surface=(__bridge_transfer FAOTSurface *)raw;
        surface.canPresent=NO;
        [surface.refreshLink invalidate];
        surface.refreshLink=nil;
        surface.view.handle=NULL;
        [surface.view.controls setInputWindow:NULL enqueue:NULL];
        NSNotificationCenter *nc=NSNotificationCenter.defaultCenter;
        if(surface.foregroundToken)[nc removeObserver:surface.foregroundToken];
        if(surface.backgroundToken)[nc removeObserver:surface.backgroundToken];
        if(surface.pointerToken)[nc removeObserver:surface.pointerToken];
        if(surface.mouseConnectToken)[nc removeObserver:surface.mouseConnectToken];
        if(surface.mouseDisconnectToken)[nc removeObserver:surface.mouseDisconnectToken];
        [surface attachMouse:nil];
        [surface.view resignFirstResponder];
        surface.window.hidden=YES;
        [surface.previous makeKeyAndVisible];
    });
}
void *faot_surface_layer(void *raw) {
    __block CAMetalLayer *layer=nil;
    onMain(^{layer=(CAMetalLayer *)((__bridge FAOTSurface *)raw).view.layer;});
    return (__bridge void *)layer;
}
static int FAOTEnsureUpscalePipeline(FAOTSurface *surface,id<MTLDevice> device) {
    if(surface.presentPipeline&&surface.presentSampler)return 1;
    static NSString *const source=
        @"#include <metal_stdlib>\n"
         "using namespace metal;\n"
         "struct VOut { float4 pos [[position]]; float2 uv; };\n"
         "vertex VOut v_main(uint vid [[vertex_id]]) {\n"
         "  float2 p[3] = { float2(-1.0,-1.0), float2(3.0,-1.0), float2(-1.0,3.0) };\n"
         "  VOut o; o.pos=float4(p[vid],0.0,1.0);\n"
         "  o.uv=float2((p[vid].x+1.0)*0.5,1.0-(p[vid].y+1.0)*0.5); return o;\n"
         "}\n"
         "fragment float4 f_main(VOut in [[stage_in]], texture2d<float> tex [[texture(0)]], sampler samp [[sampler(0)]]) {\n"
         "  return tex.sample(samp,in.uv);\n"
         "}\n";
    NSError *error=nil;
    id<MTLLibrary> library=[device newLibraryWithSource:source options:nil error:&error];
    if(!library){
        fprintf(stderr,"[GL41] Metal upscale shader failed: %s\n",error.localizedDescription.UTF8String ?: "unknown");
        return 0;
    }
    MTLRenderPipelineDescriptor *descriptor=[MTLRenderPipelineDescriptor new];
    descriptor.vertexFunction=[library newFunctionWithName:@"v_main"];
    descriptor.fragmentFunction=[library newFunctionWithName:@"f_main"];
    descriptor.colorAttachments[0].pixelFormat=MTLPixelFormatBGRA8Unorm;
    surface.presentPipeline=[device newRenderPipelineStateWithDescriptor:descriptor error:&error];
    if(!surface.presentPipeline){
        fprintf(stderr,"[GL41] Metal upscale pipeline failed: %s\n",error.localizedDescription.UTF8String ?: "unknown");
        return 0;
    }
    MTLSamplerDescriptor *sampler=[MTLSamplerDescriptor new];
    sampler.minFilter=MTLSamplerMinMagFilterLinear;
    sampler.magFilter=MTLSamplerMinMagFilterLinear;
    sampler.sAddressMode=MTLSamplerAddressModeClampToEdge;
    sampler.tAddressMode=MTLSamplerAddressModeClampToEdge;
    surface.presentSampler=[device newSamplerStateWithDescriptor:sampler];
    return surface.presentSampler!=nil;
}
/* Drawable acquisition and GPU submission stay off the game thread. The slot's
   reservation lasts through GPU completion, including when the surface resizes. */
static BOOL FAOTSubmitPresent(FAOTSurface *surface,CAMetalLayer *layer,FAOTPresentSlot *slot) {
    if(!surface.canPresent)return NO;
    id<CAMetalDrawable> drawable=[layer nextDrawable];
    if(!drawable||!surface.canPresent)return NO;
    id<MTLCommandBuffer> command=[surface.presentQueue commandBuffer];
    if(!command){surface.presentFailed=YES;fprintf(stderr,"[GL41] Metal command buffer creation failed\n");return NO;}
    id<MTLTexture> texture=slot.texture;
    if(texture.width==drawable.texture.width&&texture.height==drawable.texture.height){
        id<MTLBlitCommandEncoder> blit=[command blitCommandEncoder];
        if(!blit){surface.presentFailed=YES;return NO;}
        [blit copyFromTexture:texture sourceSlice:0 sourceLevel:0
            sourceOrigin:MTLOriginMake(0,0,0) sourceSize:MTLSizeMake(texture.width,texture.height,1)
            toTexture:drawable.texture destinationSlice:0 destinationLevel:0 destinationOrigin:MTLOriginMake(0,0,0)];
        [blit endEncoding];
    }else if(FAOTEnsureUpscalePipeline(surface,layer.device)){
        MTLRenderPassDescriptor *pass=[MTLRenderPassDescriptor renderPassDescriptor];
        pass.colorAttachments[0].texture=drawable.texture;
        pass.colorAttachments[0].loadAction=MTLLoadActionDontCare;
        pass.colorAttachments[0].storeAction=MTLStoreActionStore;
        id<MTLRenderCommandEncoder> encoder=[command renderCommandEncoderWithDescriptor:pass];
        if(!encoder){surface.presentFailed=YES;return NO;}
        [encoder setViewport:(MTLViewport){0,0,drawable.texture.width,drawable.texture.height,0,1}];
        [encoder setRenderPipelineState:surface.presentPipeline];
        [encoder setFragmentTexture:texture atIndex:0];
        [encoder setFragmentSamplerState:surface.presentSampler atIndex:0];
        [encoder drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];
        [encoder endEncoding];
    }else {surface.presentFailed=YES;return NO;}
    FAOTPresentStats *stats=surface.presentStats;
    [drawable addPresentedHandler:^(id<MTLDrawable> displayed){[stats presented:displayed.presentedTime];}];
    [command presentDrawable:drawable];
    [stats reserved:0 drawable:1];
    [command addCompletedHandler:^(id<MTLCommandBuffer> completed){
        if(completed.status==MTLCommandBufferStatusError){
            surface.presentFailed=YES;
            fprintf(stderr,"[GL41] Metal submission failed: %s\n",completed.error.description.UTF8String);
        }
        [stats reserved:-1 drawable:-1];
        dispatch_semaphore_signal(slot.freeSlot);
    }];
    [command commit];
    return YES;
}

int faot_surface_present_bgra(void *raw,const void *pixels,int width,int height) {
    if(!raw||!pixels||width<=0||height<=0)return 0;
    @autoreleasepool {
        FAOTSurface *surface=(__bridge FAOTSurface *)raw;
        if(surface.presentFailed)return 0;
        if(!surface.canPresent)return 1;
        CAMetalLayer *layer=(CAMetalLayer *)surface.view.layer;
        id<MTLDevice> device=layer.device;
        if(!device)return 0;
        if(!surface.presentWorker){
            surface.presentQueue=[device newCommandQueue];
            if(!surface.presentQueue){surface.presentFailed=YES;return 0;}
            surface.presentStats=[FAOTPresentStats new];
            surface.presentWorker=dispatch_queue_create("org.fabricaot.gl41.present",
                dispatch_queue_attr_make_with_qos_class(DISPATCH_QUEUE_SERIAL,QOS_CLASS_USER_INTERACTIVE,0));
        }
        if(!surface.presentSlots||surface.presentWidth!=width||surface.presentHeight!=height){
            /* Retire a generation only after every queued/GPU user has released it.
               Nonblocking checks avoid waiting on UIKit's main thread or nextDrawable. */
            NSArray<FAOTPresentSlot *> *oldSlots=surface.presentSlots;
            NSUInteger acquired=0;
            for(FAOTPresentSlot *old in oldSlots){
                if(dispatch_semaphore_wait(old.freeSlot,DISPATCH_TIME_NOW)!=0)break;
                acquired++;
            }
            for(NSUInteger i=0;i<acquired;i++)dispatch_semaphore_signal(oldSlots[i].freeSlot);
            if(acquired!=oldSlots.count){[surface.presentStats dropped];return 1;}
            MTLTextureDescriptor *descriptor=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm
                width:(NSUInteger)width height:(NSUInteger)height mipmapped:NO];
            descriptor.usage=MTLTextureUsageShaderRead;
            descriptor.storageMode=MTLStorageModeShared;
            NSMutableArray<FAOTPresentSlot *> *slots=[NSMutableArray arrayWithCapacity:3];
            for(int i=0;i<3;i++){
                FAOTPresentSlot *slot=[FAOTPresentSlot new];
                slot.texture=[device newTextureWithDescriptor:descriptor];
                if(!slot.texture){
                    surface.presentFailed=YES;
                    fprintf(stderr,"[GL41] Metal present texture allocation failed %dx%d slot=%d\n",width,height,i);
                    return 0;
                }
                slot.stats=surface.presentStats;
                [slot.stats textureBytes:slot.texture.allocatedSize created:YES];
                slot.freeSlot=dispatch_semaphore_create(1);
                [slots addObject:slot];
            }
            surface.presentSlots=slots;
            surface.presentSlot=0;
            surface.presentWidth=width;surface.presentHeight=height;
        }
        NSUInteger count=surface.presentSlots.count;
        FAOTPresentSlot *slot=nil;
        for(NSUInteger i=0;i<count;i++){
            NSUInteger index=(surface.presentSlot+i)%count;
            FAOTPresentSlot *candidate=surface.presentSlots[index];
            if(dispatch_semaphore_wait(candidate.freeSlot,DISPATCH_TIME_NOW)==0){
                slot=candidate;
                surface.presentSlot=(index+1)%count;
                break;
            }
        }
        if(!slot){[surface.presentStats dropped];return 1;}
        [surface.presentStats reserved:1 drawable:0];
        // OSMesa will overwrite its frame on the next swap. Copy before returning to it.
        [slot.texture replaceRegion:MTLRegionMake2D(0,0,(NSUInteger)width,(NSUInteger)height)
            mipmapLevel:0 withBytes:pixels bytesPerRow:(NSUInteger)width*4];
        dispatch_async(surface.presentWorker,^{
            @autoreleasepool {
                if(!FAOTSubmitPresent(surface,layer,slot)){
                    [surface.presentStats dropped];
                    [surface.presentStats reserved:-1 drawable:0];
                    dispatch_semaphore_signal(slot.freeSlot);
                }
            }
        });
        return 1;
    }
}
void faot_surface_title(void *raw,const char *title) {
    NSString *text=title?[NSString stringWithUTF8String:title]:@"";
    onMain(^{((__bridge FAOTSurface *)raw).window.rootViewController.title=text;});
}
void faot_surface_input(void *raw,int mode,int value) {
    onMain(^{
        FAOTMetalView *view=((__bridge FAOTSurface *)raw).view;
        if(mode==GLFW_IME){view.textEnabled=!!value;[view reloadInputViews];}
        else if(mode==GLFW_CURSOR)[view setGameCapture:value==GLFW_CURSOR_DISABLED];
    });
}
void faot_surface_ime_rect(void *raw,int x,int y,int width,int height) {
    (void)raw;(void)x;(void)y;(void)width;(void)height;
    fprintf(stderr,"[GLFW-lite] IME preedit requires UITextInput; UIKeyInput currently provides committed text only\n");
}
void faot_surface_clipboard_set(const char *value) {
    NSString *text=[NSString stringWithUTF8String:value];
    onMain(^{UIPasteboard.generalPasteboard.string=text;});
}
char *faot_surface_clipboard_copy(void) {
    __block char *value=NULL;
    onMain(^{value=strdup((UIPasteboard.generalPasteboard.string?:@"").UTF8String);});
    return value;
}
