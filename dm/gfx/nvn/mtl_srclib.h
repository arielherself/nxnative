// MSL source libraries (dm/ios/tools/ios_metal.py SOURCE): where the shader libraries were built without a Metal compiler (Linux), each .metallib file holds the shader's Metal source,
// and the device compiles it (mtl_srclib.m). A file with the metallib magic (MTLB) is loaded as before.
#pragma once
#include <stddef.h>
#ifdef __OBJC__
#import <Metal/Metal.h>
// A library from a file's bytes: a metallib through newLibraryWithData (data: the same bytes as a dispatch_data_t), an MSL source library compiled with the offline compile's options
// (dm_srclib_options); name (the file name) is for the log and the first-start pass's list of sources that do not compile
id<MTLLibrary> dm_srclib_load(id<MTLDevice> dev, const void *bytes, size_t len, dispatch_data_t data, NSString *name, NSError **err);
MTLCompileOptions *dm_srclib_options(void);
#endif
int dm_srclib_is_source(const void *bytes, size_t len);   // no metallib magic: an MSL source library
// First start: compile every MSL source library under dir (DM_MSL_DIR) once, in the background, so the system's Metal shader cache holds them before the game asks for them (a compile
// on first use would be a stall on the submit thread). A marker in Library/Caches (where the Metal cache lives, and is purged with it) says it is done for this set of files.
// Starting it again is a no-op. dm_srclib_prepare_running: whether it is still running, with the counts so far
void dm_srclib_prepare_start(const char *dir);
int dm_srclib_prepare_running(int *done, int *total, int *failed);
