// The iOS Simulator's Metal driver refuses heaps that are not
// MTLStorageModePrivate:
//   -[MTLSimDevice newHeapWithDescriptor:]: failed assertion
//   `MTLStorageModePrivate is required for heaps'
// Unity asks for a shared heap, which real devices accept. Rewriting the
// descriptor on the way in is enough to run the game in the simulator; the
// shipped app is not modified -- this dylib is injected only for the CI test.
//
// Timing matters: MTLSimDevice lives in MTLSimDriver.dylib, which is not loaded
// when this library's constructor runs, so hooking by class name at load time
// finds nothing. Interpose device creation and hook the class we are handed.
#import <Foundation/Foundation.h>
#import <AVFoundation/AVFoundation.h>
#import <Metal/Metal.h>
#import <objc/runtime.h>

static IMP orig_newHeap;

// Forcing the heap to Private gets past the driver assert but then the sim's
// GPU host dies when Unity sub-allocates CPU-visible buffers out of it. Try the
// other direction: hand back nil and see whether Unity's Metal backend has a
// no-heap fallback. If it does not, the simulator simply cannot run this build.
static BOOL simhook_nil_heaps(void) {
    const char *v = getenv("SIMHOOK_NIL_HEAPS");
    return v && v[0] == '1';
}

static id hooked_newHeapWithDescriptor(id self, SEL _cmd, id descriptor) {
    if (simhook_nil_heaps()) {
        NSLog(@"[simhook] refusing heap creation (nil)");
        return nil;
    }
    @try {
        NSNumber *mode = [descriptor valueForKey:@"storageMode"];
        if (mode.unsignedLongValue != MTLStorageModePrivate) {
            [descriptor setValue:@(MTLStorageModePrivate) forKey:@"storageMode"];
            NSLog(@"[simhook] heap storageMode %@ -> Private", mode);
        }
    } @catch (NSException *e) {
        NSLog(@"[simhook] descriptor rewrite failed: %@", e);
    }
    return ((id (*)(id, SEL, id))orig_newHeap)(self, _cmd, descriptor);
}

static void hook_device(id<MTLDevice> dev) {
    if (!dev || orig_newHeap) return;
    Class cls = object_getClass(dev);
    Method m = class_getInstanceMethod(cls, @selector(newHeapWithDescriptor:));
    if (!m) {
        NSLog(@"[simhook] %s has no newHeapWithDescriptor:", class_getName(cls));
        return;
    }
    orig_newHeap = method_getImplementation(m);
    method_setImplementation(m, (IMP)hooked_newHeapWithDescriptor);
    NSLog(@"[simhook] hooked -[%s newHeapWithDescriptor:]", class_getName(cls));
}

// dyld interposing: swap MTLCreateSystemDefaultDevice for ours in every image
// that links Metal, which is how we get hold of the concrete device class.
static id<MTLDevice> hooked_MTLCreateSystemDefaultDevice(void) {
    id<MTLDevice> dev = MTLCreateSystemDefaultDevice();
    hook_device(dev);
    return dev;
}

__attribute__((used)) static struct {
    const void *replacement;
    const void *replacee;
} interposers[] __attribute__((section("__DATA,__interpose"))) = {
    {(const void *)(unsigned long)&hooked_MTLCreateSystemDefaultDevice,
     (const void *)(unsigned long)&MTLCreateSystemDefaultDevice},
};

// The audio fix is a PlayerSettings bool, and the only way to see whether it
// took effect at runtime is the AVAudioSession Unity ends up configuring:
// PlayAndRecord without DefaultToSpeaker is what routes a phone's playback to
// the earpiece and makes the game sound quiet.
static void log_audio_session(void) {
    AVAudioSession *s = [AVAudioSession sharedInstance];
    NSLog(@"[simhook] audio category=%@ mode=%@ options=0x%lx defaultToSpeaker=%@",
          s.category, s.mode, (unsigned long)s.categoryOptions,
          (s.categoryOptions & AVAudioSessionCategoryOptionDefaultToSpeaker) ? @"YES" : @"NO");
}

__attribute__((constructor))
static void simhook_init(void) {
    NSLog(@"[simhook] loaded, waiting for MTLCreateSystemDefaultDevice");
    for (int i = 1; i <= 4; i++) {
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(i * 15 * NSEC_PER_SEC)),
                       dispatch_get_main_queue(), ^{ log_audio_session(); });
    }
}
