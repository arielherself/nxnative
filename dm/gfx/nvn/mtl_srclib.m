// MSL source libraries (see mtl_srclib.h): a shader library file that holds Metal source instead of a metallib, compiled on the device. The shader chain makes them where no Metal
// compiler runs (Linux: dm/ios/tools/ios_metal.py SOURCE); the options here are that file's compile convention (Metal 3.2 as -mios-version-min=18.0, -fmetal-math-mode=relaxed,
// -fpreserve-invariance), so a source library builds what the offline compile would. Compiled libraries are not files the app can keep: the system's Metal shader cache
// (Library/Caches/<bundle>/com.apple.metal) keeps each compile keyed by its source and options, so the same source compiles again from the cache, and the first start compiles
// them all once in the background (dm_srclib_prepare_start) while the launcher is up, with front_ios.m showing the count before the game starts
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <fcntl.h>
#include <os/lock.h>
#include <stdatomic.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>
#include "mtl_srclib.h"

#define SLOG(...) do { fprintf(stderr, "[dm] [srclib] " __VA_ARGS__); fputc('\n', stderr); } while (0)

int dm_srclib_is_source(const void *bytes, size_t len) { return len >= 4 && memcmp(bytes, "MTLB", 4) != 0; }

MTLCompileOptions *dm_srclib_options(void) {
    static MTLCompileOptions *o; static dispatch_once_t once;
    dispatch_once(&once, ^{
        o = [MTLCompileOptions new];
        o.languageVersion = MTLLanguageVersion3_2;   // the default of -mios-version-min=18.0
        o.mathMode = MTLMathModeRelaxed;             // -fmetal-math-mode=relaxed: fast math that keeps NaN / Inf (the translated shaders' isnan selects)
        o.preserveInvariance = YES;                  // -fpreserve-invariance: [[invariant]] positions agree across shaders
    });
    return o;
}

// Sources the first-start pass found not to compile (by file name): a later load fails at once instead of compiling them again on the submit thread
static NSMutableSet<NSString *> *g_bad;
static os_unfair_lock g_bad_lock = OS_UNFAIR_LOCK_INIT;
static _Atomic int g_live_compiles;

id<MTLLibrary> dm_srclib_load(id<MTLDevice> dev, const void *bytes, size_t len, dispatch_data_t data, NSString *name, NSError **err) {
    if (!dm_srclib_is_source(bytes, len)) return [dev newLibraryWithData:data error:err];
    os_unfair_lock_lock(&g_bad_lock); BOOL bad = [g_bad containsObject:name]; os_unfair_lock_unlock(&g_bad_lock);
    if (bad) { if (err) *err = [NSError errorWithDomain:@"dm.srclib" code:1 userInfo:@{NSLocalizedDescriptionKey: @"its MSL did not compile at the first start"}]; return nil; }
    NSString *src = [[NSString alloc] initWithBytes:bytes length:len encoding:NSUTF8StringEncoding];
    if (!src) { if (err) *err = [NSError errorWithDomain:@"dm.srclib" code:2 userInfo:@{NSLocalizedDescriptionKey: @"neither a metallib nor UTF-8 Metal source"}]; return nil; }
    uint64_t t0 = clock_gettime_nsec_np(CLOCK_UPTIME_RAW);
    id<MTLLibrary> lib = [dev newLibraryWithSource:src options:dm_srclib_options() error:err];
    double ms = (clock_gettime_nsec_np(CLOCK_UPTIME_RAW) - t0) / 1e6;
    if (ms > 50 && atomic_fetch_add(&g_live_compiles, 1) < 16) SLOG("%s compiled at first use in %.0f ms (not from the Metal cache)", name.UTF8String, ms);
    return lib;
}

// ---- Source packs: the Vulkan and GL layers' one library (vk_shaders.py / gl_shaders.py pack_msl). The file starts with PACK_MAGIC; each part with a line "//@@part <entry> [<entry>...]",
// then that shader's MSL up to the next part line ----
#define PACK_MAGIC "//@@dmsh-msl-pack 1\n"
#define PART_TAG "//@@part "
static int is_pack(const void *b, size_t n) { return n >= sizeof PACK_MAGIC - 1 && !memcmp(b, PACK_MAGIC, sizeof PACK_MAGIC - 1); }
// the parts of a pack: [(entry names, MSL)]
static NSArray<NSArray *> *pack_parts(NSString *text) {
    NSMutableArray *out = [NSMutableArray array];
    NSArray<NSString *> *chunks = [text componentsSeparatedByString:@"\n" PART_TAG];
    for (NSUInteger i = 1; i < chunks.count; i++) {
        NSRange nl = [chunks[i] rangeOfString:@"\n"]; if (nl.location == NSNotFound) continue;
        NSArray *names = [[chunks[i] substringToIndex:nl.location] componentsSeparatedByString:@" "];
        [out addObject:@[names, [chunks[i] substringFromIndex:nl.location + 1]]];
    }
    return out;
}
@interface DmSrcPack : NSObject { @public id<MTLDevice> dev; NSMutableDictionary<NSString *, NSString *> *src; NSMutableDictionary<NSString *, id> *lib; NSString *path; os_unfair_lock lock; } @end
@implementation DmSrcPack @end

id dm_srclib_open(id<MTLDevice> dev, NSString *path, NSError **err) {
    NSData *d = [NSData dataWithContentsOfFile:path options:NSDataReadingMappedIfSafe error:err];
    if (!d) return nil;
    if (!is_pack(d.bytes, d.length)) return [dev newLibraryWithURL:[NSURL fileURLWithPath:path] error:err];
    DmSrcPack *p = [DmSrcPack new]; p->dev = dev; p->src = [NSMutableDictionary dictionary]; p->lib = [NSMutableDictionary dictionary]; p->path = path; p->lock = OS_UNFAIR_LOCK_INIT;
    NSString *text = [[NSString alloc] initWithData:d encoding:NSUTF8StringEncoding];
    if (!text) { if (err) *err = [NSError errorWithDomain:@"dm.srclib" code:2 userInfo:@{NSLocalizedDescriptionKey: @"an MSL source pack that is not UTF-8"}]; return nil; }
    for (NSArray *part in pack_parts(text)) for (NSString *n in part[0]) p->src[n] = part[1];
    SLOG("%s: MSL source pack of %lu entry points, compiled on first use", path.lastPathComponent.UTF8String, (unsigned long)p->src.count);
    return p;
}

id<MTLFunction> dm_srclib_function(id lib, NSString *name) {
    if (![lib isKindOfClass:DmSrcPack.class]) return [(id<MTLLibrary>)lib newFunctionWithName:name];
    DmSrcPack *p = lib;
    os_unfair_lock_lock(&p->lock); NSString *src = p->src[name]; id have = p->lib[src ?: @""]; os_unfair_lock_unlock(&p->lock);
    if (!src) return nil;
    if (!have) {   // a part compiles once (its entry points share the library); a part that does not is remembered as NSNull
        NSError *e = nil; id<MTLLibrary> l = [p->dev newLibraryWithSource:src options:dm_srclib_options() error:&e];
        if (!l) SLOG("[RED] %s: the part of %s does not compile: %s", p->path.lastPathComponent.UTF8String, name.UTF8String, [[e.localizedDescription componentsSeparatedByString:@"\n"].firstObject UTF8String]);
        have = l ?: (id)NSNull.null;
        os_unfair_lock_lock(&p->lock); p->lib[src] = have; os_unfair_lock_unlock(&p->lock);
    }
    return have == (id)NSNull.null ? nil : [(id<MTLLibrary>)have newFunctionWithName:name];
}

// ---- First start ----
static _Atomic int g_running, g_done, g_total, g_failed;

int dm_srclib_prepare_running(int *done, int *total, int *failed) {
    if (done) *done = atomic_load(&g_done);
    if (total) *total = atomic_load(&g_total);
    if (failed) *failed = atomic_load(&g_failed);
    return atomic_load(&g_running);
}

static NSData *map_file(NSString *path) {   // the file's bytes, mapped and closed at once (thousands of them)
    int fd = open(path.fileSystemRepresentation, O_RDONLY); struct stat st;
    if (fd < 0 || fstat(fd, &st) || st.st_size <= 0) { if (fd >= 0) close(fd); return nil; }
    void *m = mmap(NULL, (size_t)st.st_size, PROT_READ, MAP_PRIVATE, fd, 0); close(fd);
    if (m == MAP_FAILED) return nil;
    size_t n = (size_t)st.st_size;
    return [[NSData alloc] initWithBytesNoCopy:m length:n deallocator:^(void *p, NSUInteger l) { munmap(p, l); }];
}

void dm_srclib_prepare_start(const char *cdir) {
    static atomic_flag started = ATOMIC_FLAG_INIT;
    if (!cdir || atomic_flag_test_and_set(&started)) return;
    NSString *dir = @(cdir);
    atomic_store(&g_running, 1);
    dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), ^{ @autoreleasepool {
        uint64_t t0 = clock_gettime_nsec_np(CLOCK_UPTIME_RAW);
        NSFileManager *fm = NSFileManager.defaultManager;
        // the source libraries and what identifies this set of them: their paths and sizes
        NSMutableArray<NSString *> *srcs = [NSMutableArray array]; NSMutableSet<NSString *> *packs = [NSMutableSet set]; NSMutableString *ident = [NSMutableString stringWithString:@"dm_srclib 1\n"];
        NSDirectoryEnumerator *en = [fm enumeratorAtPath:dir];
        for (NSString *rel in en) {
            if (![rel hasSuffix:@".metallib"]) continue;
            NSString *p = [dir stringByAppendingPathComponent:rel];
            int fd = open(p.fileSystemRepresentation, O_RDONLY); char m[sizeof PACK_MAGIC] = {0}; ssize_t got = fd >= 0 ? read(fd, m, sizeof m - 1) : -1; struct stat st = {0}; if (fd >= 0) { fstat(fd, &st); close(fd); }
            if (got < 4 || !dm_srclib_is_source(m, (size_t)got)) continue;   // a metallib
            [srcs addObject:rel]; [ident appendFormat:@"%@\t%lld\n", rel, (long long)st.st_size];   // a source library, or a source pack
            if (is_pack(m, (size_t)got)) [packs addObject:rel];
        }
        if (!srcs.count) { atomic_store(&g_running, 0); return; }   // compiled metallibs (a Mac build): nothing to do
        // the marker: Library/Caches/dm_srclib/<hash of the set>.done, holding the sources that did not compile
        NSString *caches = NSSearchPathForDirectoriesInDomains(NSCachesDirectory, NSUserDomainMask, YES).firstObject;
        uint64_t h = 1469598103934665603ull; for (const char *c = ident.UTF8String; *c; c++) h = (h ^ (uint8_t)*c) * 1099511628211ull;
        NSString *mdir = [caches stringByAppendingPathComponent:@"dm_srclib"], *marker = [mdir stringByAppendingPathComponent:[NSString stringWithFormat:@"%016llx.done", h]];
        NSString *prev = [NSString stringWithContentsOfFile:marker encoding:NSUTF8StringEncoding error:nil];
        if (prev) {
            NSMutableSet *bad = [NSMutableSet set];
            for (NSString *ln in [prev componentsSeparatedByString:@"\n"]) if (ln.length && ![ln hasPrefix:@"#"]) [bad addObject:ln.lastPathComponent];
            os_unfair_lock_lock(&g_bad_lock); g_bad = bad; os_unfair_lock_unlock(&g_bad_lock);
            SLOG("%lu MSL source libraries in %s, compiled at an earlier start (%lu do not compile); the Metal cache has them", (unsigned long)srcs.count, cdir, (unsigned long)bad.count);
            atomic_store(&g_running, 0); return;
        }
        atomic_store(&g_total, (int)srcs.count);
        SLOG("first start: compiling %lu MSL source libraries in %s for this device", (unsigned long)srcs.count, cdir);
        id<MTLDevice> dev = MTLCreateSystemDefaultDevice();
        NSUInteger cores = NSProcessInfo.processInfo.activeProcessorCount;
        dispatch_semaphore_t slots = dispatch_semaphore_create((long)MAX(cores, 2));   // compiles in flight: the compiler service runs them in parallel
        dispatch_group_t all = dispatch_group_create();
        NSMutableArray<NSString *> *bad = [NSMutableArray array]; NSLock *bl = [NSLock new];
        // the units to compile: each source library, and each part of a source pack (by its first entry point)
        NSMutableArray<NSArray *> *units = [NSMutableArray array];
        for (NSString *rel in srcs) {
            if ([packs containsObject:rel]) {
                NSData *d = map_file([dir stringByAppendingPathComponent:rel]);
                NSString *text = d ? [[NSString alloc] initWithData:d encoding:NSUTF8StringEncoding] : nil;
                for (NSArray *part in pack_parts(text ?: @"")) [units addObject:@[[NSString stringWithFormat:@"%@#%@", rel, [part[0] firstObject]], part[1]]];
            } else [units addObject:@[rel, NSNull.null]];
        }
        atomic_store(&g_total, (int)units.count);
        for (NSArray *unit in units) { @autoreleasepool {
            NSString *rel = unit[0];
            dispatch_semaphore_wait(slots, DISPATCH_TIME_FOREVER); dispatch_group_enter(all);
            NSData *d = unit[1] == NSNull.null ? map_file([dir stringByAppendingPathComponent:rel]) : nil;
            NSString *src = unit[1] != NSNull.null ? unit[1] : d ? [[NSString alloc] initWithData:d encoding:NSUTF8StringEncoding] : nil;
            void (^finish)(BOOL, NSString *) = ^(BOOL ok, NSString *why) {
                if (!ok) { [bl lock]; [bad addObject:rel]; [bl unlock]; if (atomic_fetch_add(&g_failed, 1) < 20) SLOG("[RED] %s does not compile: %s", rel.UTF8String, why.UTF8String); }
                atomic_fetch_add(&g_done, 1);
                dispatch_semaphore_signal(slots); dispatch_group_leave(all);
            };
            if (!src) { finish(NO, @"unreadable"); continue; }
            [dev newLibraryWithSource:src options:dm_srclib_options() completionHandler:^(id<MTLLibrary> lib, NSError *e) {
                NSString *first = [[e.localizedDescription componentsSeparatedByString:@"\n"] firstObject];
                finish(lib != nil, first ?: @"no library");
            }];
        } }
        dispatch_group_wait(all, DISPATCH_TIME_FOREVER);
        [fm createDirectoryAtPath:mdir withIntermediateDirectories:YES attributes:nil error:nil];
        for (NSString *old in [fm contentsOfDirectoryAtPath:mdir error:nil]) [fm removeItemAtPath:[mdir stringByAppendingPathComponent:old] error:nil];   // another install's set
        NSString *body = [NSString stringWithFormat:@"# MSL source libraries that did not compile on this device (dm/gfx/nvn/mtl_srclib.m)\n%@%@", [bad componentsJoinedByString:@"\n"], bad.count ? @"\n" : @""];
        [body writeToFile:marker atomically:YES encoding:NSUTF8StringEncoding error:nil];
        NSMutableSet *bs = [NSMutableSet set]; for (NSString *b in bad) [bs addObject:b.lastPathComponent];
        os_unfair_lock_lock(&g_bad_lock); g_bad = bs; os_unfair_lock_unlock(&g_bad_lock);
        SLOG("first start: compiled %lu MSL source libraries in %.1f s, %lu do not compile", (unsigned long)srcs.count, (clock_gettime_nsec_np(CLOCK_UPTIME_RAW) - t0) / 1e9, (unsigned long)bad.count);
        atomic_store(&g_running, 0);
    } });
}
