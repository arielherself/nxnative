#!/usr/bin/env python3
# Build the unsigned DmIOS.app without Xcode (Linux): what xcodebuild does with the project build_ipa.sh generates, done with a cross toolchain. build_ipa.sh calls this instead of
# xcodegen + xcodebuild when it does not run on a Mac; the IPA is then packed from the app as on the Mac (pack_ipa.py).
#   Toolchain: clang and ld64.lld (LLVM; the distribution's packages), swiftc for Launcher.swift (a Swift toolchain, e.g. the one xtool uses) and an iPhoneOS SDK with the Swift
#   resources for iOS: the Darwin Swift SDK that `xtool setup` / `xtool sdk install <Xcode.xip>` makes from Xcode (~/.swiftpm/swift-sdks/darwin.artifactbundle), found by its swift-sdk.json.
#   The target's settings come from project.yml as xcodegen reads them (deployment target, OTHER_CFLAGS, Swift version, the Info.plist properties), the host's sources, header paths, defines,
#   frameworks and link flags from Gen/ (gen_build.py's Gen/src and Gen/dm.xcconfig, gen_embed.py's Gen/embed.S): one description of the app for both builds.
#   As Xcode's Release configuration: -Os for C / Objective-C (ARC), -O with whole-module optimization for Swift, DWARF debug information, warnings off (GCC_WARN_INHIBIT_ALL_WARNINGS),
#   NS_BLOCK_ASSERTIONS; the linker signs ad hoc (as ld64 does for arm64; the sideloading tool signs it again). There is no asset catalog compiler (actool) here: the icon goes in as
#   PNG files named by CFBundleIcons. Compiles are incremental: an object is made again when its command or any file its .d lists changed.
# Usage: build_app_linux.py --check              say what is missing (exit 1 if anything)
#        build_app_linux.py <ios dir> <out dir>  ios dir = dm/ios (or a prepared copy) with Gen/ generated; writes <out dir>/DmIOS.app, its objects to <out dir>/obj and the build log to <out dir>/build.log
#   Environment: DM_BUNDLE_ID, DM_DISPLAY_NAME, DM_APP_VERSION, DM_APP_CATEGORY, DM_SHOW_FPS, DM_LANG (build_ipa.sh's, as for xcodegen); DM_ICON (the icon image, needs Pillow);
#   DM_IOS_SDK (an iPhoneOS.sdk) and DM_SWIFT_RESOURCES (its Swift resource folder) to use another SDK; CC (clang), SWIFTC (swiftc); J (parallel compiles, default the CPU count)
import concurrent.futures as cf, glob, hashlib, json, os, plistlib, re, shlex, shutil, subprocess, sys, time

APP = 'DmIOS'
SDK_DIRS = ['~/.swiftpm/swift-sdks', '~/.config/swiftpm/swift-sdks', '~/.local/share/swiftpm/swift-sdks', '~/Library/org.swift.swiftpm/swift-sdks']   # where SwiftPM (and so xtool) installs Swift SDKs
FRAMEWORK_RE = re.compile(rb'^\s*[#@](?:import|include)\s*<(\w+)/', re.M)


def die(msg):
    sys.exit(f'[RED] build_app_linux: {msg}')


def find_sdk():
    """(iPhoneOS.sdk, Swift resource folder, the Swift SDK bundle's folder) from DM_IOS_SDK / DM_SWIFT_RESOURCES, else from the arm64-apple-ios entry of an installed Swift SDK's
    swift-sdk.json; (None, None, None) without one"""
    if os.environ.get('DM_IOS_SDK'): return os.environ['DM_IOS_SDK'], os.environ.get('DM_SWIFT_RESOURCES'), None
    for root in SDK_DIRS:
        base = os.path.expanduser(root)   # <bundle>/swift-sdk.json or <bundle>/<variant>/swift-sdk.json (not deeper: xtool's bundle holds a whole Xcode.app)
        for meta in sorted(glob.glob(os.path.join(base, '*', 'swift-sdk.json')) + glob.glob(os.path.join(base, '*', '*', 'swift-sdk.json'))):
            try: ios = json.load(open(meta))['targetTriples']['arm64-apple-ios']
            except (OSError, ValueError, KeyError): continue
            base = os.path.dirname(meta); sdk = os.path.join(base, ios['sdkRootPath'])
            if os.path.isdir(sdk): return os.path.realpath(sdk), os.path.join(base, ios['swiftResourcesPath']) if ios.get('swiftResourcesPath') else None, base
    return None, None, None


def toolchain():
    """{'cc', 'ld', 'swiftc', 'sdk', 'swiftres', 'sdkver'} and the list of what is missing"""
    t, miss = {}, []
    t['cc'] = shutil.which(os.environ.get('CC') or 'clang')
    if not t['cc']: miss.append('clang (LLVM: pacman -S clang / apt install clang)')
    t['ld'] = shutil.which('ld64.lld')
    if not t['ld']: miss.append('ld64.lld (LLVM\'s lld: pacman -S lld / apt install lld)')
    t['swiftc'] = shutil.which(os.environ.get('SWIFTC') or 'swiftc')
    if not t['swiftc']: miss.append('swiftc (a Swift toolchain, as xtool uses: https://www.swift.org/install/linux/)')
    t['sdk'], t['swiftres'], t['bundle'] = find_sdk()
    if not t['sdk']: miss.append('an iPhoneOS SDK: install xtool (https://github.com/xtool-org/xtool) and run `xtool setup` with an Xcode.xip, or set DM_IOS_SDK')
    elif not t['swiftres'] or not os.path.isdir(os.path.join(t['swiftres'], 'iphoneos')): miss.append(f'the Swift resources for iOS beside {t["sdk"]} (set DM_SWIFT_RESOURCES)')
    if t['sdk']:
        try: t['sdkver'] = json.load(open(os.path.join(t['sdk'], 'SDKSettings.json')))['Version']
        except (OSError, ValueError, KeyError): t['sdkver'] = '26.0'
    return t, miss


def project(ios):
    """The DmIOS target of project.yml, with the deployment target"""
    try: import yaml
    except ImportError: die(f'the Python running this ({sys.executable}) has no PyYAML (pip install pyyaml): it reads project.yml')
    y = yaml.safe_load(open(os.path.join(ios, 'project.yml')))
    return y['targets'][APP], str(y['options']['deploymentTarget']['iOS'])


def xcconfig(path):
    """Gen/dm.xcconfig's settings, $(inherited) dropped (nothing is inherited here); later lines append to a setting they name, as xcconfig files do"""
    out = {}
    for ln in open(path):
        m = re.match(r'\s*([A-Z_]+)\s*=\s*(.*)', ln)
        if not m: continue
        k, v = m.groups(); v = v.replace('$(inherited)', '').strip()
        v = re.sub(r'\$\((\w+)\)', lambda x: out.get(x.group(1), ''), v)
        out[k] = v
    return out


def yes(v):   # an Xcode boolean setting: YES, or the true PyYAML reads YES as
    return v is True or str(v).upper() == 'YES'


def subst(v, env, settings):   # ${VAR} from the environment (xcodegen), $(VAR) from the build settings (Xcode)
    if not isinstance(v, str): return v
    v = re.sub(r'\$\{(\w+)\}', lambda m: env.get(m.group(1), ''), v)
    return re.sub(r'\$\((\w+)\)', lambda m: str(settings.get(m.group(1), env.get(m.group(1), ''))), v)


def deep_subst(v, env, settings):
    if isinstance(v, dict): return {k: deep_subst(x, env, settings) for k, x in v.items()}
    if isinstance(v, list): return [deep_subst(x, env, settings) for x in v]
    return subst(v, env, settings)


class Builder:
    def __init__(self, ios, out):
        self.ios, self.out = os.path.abspath(ios), os.path.abspath(out)
        self.t, miss = toolchain()
        if miss: die('missing: ' + '; '.join(miss))
        self.target, self.dt = project(self.ios)
        self.settings = self.target.get('settings', {})
        self.xc = xcconfig(os.path.join(self.ios, 'Gen/dm.xcconfig'))
        self.obj = os.path.join(self.out, 'obj'); os.makedirs(self.obj, exist_ok=True)
        self.log = open(os.path.join(self.out, 'build.log'), 'w', buffering=1)
        self.triple = f'arm64-apple-ios{self.dt}'

    def say(self, line): self.log.write(line + '\n')

    # ---- what to compile ----
    def sources(self):
        """[(source, extra flags)]: the target's sources as project.yml lists them (Sources/ without Info.plist, Gen/embed.S, Gen/src/*, Gen/main/main.c with its compiler flags)"""
        out = []
        for s in self.target['sources']:
            path, kind = (s, None) if isinstance(s, str) else (s['path'], s.get('type'))
            if kind == 'folder' or (isinstance(s, dict) and s.get('buildPhase') == 'resources') or path.endswith('.xcassets'): continue
            full = os.path.join(self.ios, path); flags = shlex.split(s.get('compilerFlags', '')) if isinstance(s, dict) else []
            excl = set(s.get('excludes', [])) if isinstance(s, dict) else set()
            files = [full] if os.path.isfile(full) else [os.path.join(full, n) for n in sorted(os.listdir(full)) if n not in excl]
            out += [(f, flags) for f in files if f.endswith(('.c', '.m', '.S', '.swift'))]
        return out

    def cflags(self):
        incs = [f'-I{d}' for d in shlex.split(self.xc.get('HEADER_SEARCH_PATHS', ''))]
        defs = shlex.split(self.xc.get('OTHER_CFLAGS', ''))   # gen_build.py's -D flags
        own = shlex.split(subst(str(self.settings.get('OTHER_CFLAGS', '')), os.environ, {}).replace('$(inherited)', ''))   # project.yml's (-mcpu=...)
        std = str(self.settings.get('GCC_C_LANGUAGE_STANDARD', 'gnu11'))
        w = ['-w'] if yes(self.settings.get('GCC_WARN_INHIBIT_ALL_WARNINGS')) else []
        return ['-target', self.triple, '-isysroot', self.t['sdk'], '-Os', '-g', f'-std={std}', '-DNS_BLOCK_ASSERTIONS=1', *w, *own, *defs, *incs]

    # ---- incremental compiles ----
    def stale(self, o, cmd):
        try: old = open(o + '.cmd').read()
        except OSError: return True
        if old != '\0'.join(cmd) or not os.path.exists(o): return True
        t = os.path.getmtime(o)
        try: deps = open(o + '.d').read().replace('\\\n', ' ').split(':', 1)[1].split()
        except (OSError, IndexError): return True
        return any(not os.path.exists(d) or os.path.getmtime(d) > t for d in deps)

    def compile_one(self, src, flags):
        name = os.path.relpath(src, self.ios).replace('/', '__')
        o = os.path.join(self.obj, name + '.o')
        lang = {'.m': ['-x', 'objective-c', '-fobjc-arc'], '.c': ['-x', 'c'], '.S': ['-x', 'assembler-with-cpp']}[os.path.splitext(src)[1]]
        cmd = [self.t['cc'], *self.cflags(), *flags, *lang, '-c', os.path.realpath(src), '-o', o, '-MMD', '-MF', o + '.d']
        if not self.stale(o, cmd): return o, None
        self.say(f'CompileC {src}')
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode: return o, f'{src}:\n{shlex.join(cmd)}\n{r.stdout}{r.stderr}'
        if r.stderr.strip(): self.say(r.stderr.rstrip())
        open(o + '.cmd', 'w').write('\0'.join(cmd))
        return o, None

    def compile_swift(self, srcs):
        o = os.path.join(self.obj, 'swift.o')
        ver = str(self.settings.get('SWIFT_VERSION', '5.0')).split('.')[0]
        cmd = [self.t['swiftc'], '-target', self.triple, '-sdk', self.t['sdk'], '-resource-dir', self.t['swiftres'], '-swift-version', ver, '-O', '-wmo', '-g',
               '-parse-as-library', '-module-name', APP, '-module-cache-path', os.path.join(self.obj, 'swift-modules'), '-c', *srcs, '-o', o]
        newest = max(os.path.getmtime(s) for s in srcs)
        try: same = open(o + '.cmd').read() == '\0'.join(cmd) and os.path.getmtime(o) >= newest
        except OSError: same = False
        if same: return o
        self.say(f'CompileSwift {" ".join(srcs)}')
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode: die(f'swiftc failed:\n{shlex.join(cmd)}\n{r.stdout}{r.stderr}')
        open(o + '.cmd', 'w').write('\0'.join(cmd))
        return o

    def builtins(self):
        """compiler-rt's iOS builtins, which Apple's clang links into every app (Swift's and Objective-C's availability checks call __isPlatformVersionAtLeast): libclang_rt.ios.a from the
        Swift resources, an Xcode toolchain in the SDK's bundle or clang's own resource folder; without one (a slim SDK), that function alone, on dyld's _availability_version_check as compiler-rt does"""
        cands = [os.path.join(self.t['swiftres'], 'clang/lib/darwin/libclang_rt.ios.a')]
        if self.t['bundle']: cands += sorted(glob.glob(os.path.join(self.t['bundle'], 'Xcode.app/Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/lib/clang/*/lib/darwin/libclang_rt.ios.a')))
        rd = subprocess.run([self.t['cc'], '-print-resource-dir'], capture_output=True, text=True).stdout.strip()
        if rd: cands.append(os.path.join(rd, 'lib/darwin/libclang_rt.ios.a'))
        for c in cands:
            if os.path.isfile(c): self.say(f'builtins: {os.path.realpath(c)}'); return os.path.realpath(c)
        stub = os.path.join(self.obj, 'availability.c')
        open(stub, 'w').write('// compiler-rt\'s iOS builtins are not here: the availability check Swift and @available call, as compiler-rt implements it on dyld (build_app_linux.py builtins)\n'
                              '#include <stdint.h>\n#include <stdbool.h>\ntypedef struct { uint32_t platform, version; } dm_build_version_t;\n'
                              'extern bool _availability_version_check(uint32_t count, dm_build_version_t versions[]);\n'
                              'int32_t __isPlatformVersionAtLeast(uint32_t platform, uint32_t major, uint32_t minor, uint32_t subminor) {\n'
                              '    dm_build_version_t v = {platform, (major << 16) | ((minor & 0xff) << 8) | (subminor & 0xff)};\n    return _availability_version_check(1, &v);\n}\n')
        o, err = self.compile_one(stub, [])
        if err: die(err)
        self.say('builtins: no libclang_rt.ios.a, __isPlatformVersionAtLeast compiled here')
        return o

    def frameworks(self, srcs):
        """The frameworks to link: Gen/dm.xcconfig's DM_FRAMEWORKS (the host's, from the Makefile) and those the sources import (Xcode's module autolinking does this there), if the SDK has them"""
        have = {n[:-10] for n in os.listdir(os.path.join(self.t['sdk'], 'System/Library/Frameworks')) if n.endswith('.framework')}
        want = shlex.split(self.xc.get('DM_FRAMEWORKS', ''))[1::2]
        dirs = {os.path.dirname(os.path.realpath(s)) for s in srcs}
        for s in list(srcs) + [h for d in sorted(dirs) for h in sorted(glob.glob(os.path.join(d, '*.h')))]:
            for m in FRAMEWORK_RE.finditer(open(s, 'rb').read()): want.append(m.group(1).decode())
        out = []
        for f in want:
            if f in have and f not in out: out.append(f)
        return out

    # ---- the bundle ----
    def info_plist(self, app):
        env = dict(os.environ)
        settings = {k: subst(str(v), env, {}) for k, v in self.settings.items()}
        props = deep_subst(self.target['info'].get('properties', {}), env, settings)
        sdkver = self.t['sdkver']
        base = {'CFBundleDevelopmentRegion': 'en', 'CFBundleExecutable': APP, 'CFBundleIdentifier': settings.get('PRODUCT_BUNDLE_IDENTIFIER') or env.get('DM_BUNDLE_ID', ''),
                'CFBundleInfoDictionaryVersion': '6.0', 'CFBundleName': settings.get('PRODUCT_NAME', APP), 'CFBundlePackageType': 'APPL', 'CFBundleSupportedPlatforms': ['iPhoneOS'],
                'LSRequiresIPhoneOS': True, 'MinimumOSVersion': self.dt, 'UIDeviceFamily': [int(x) for x in str(settings.get('TARGETED_DEVICE_FAMILY', '1,2')).split(',')],
                'UIRequiredDeviceCapabilities': ['arm64'], 'DTPlatformName': 'iphoneos', 'DTPlatformVersion': sdkver, 'DTSDKName': f'iphoneos{sdkver}'}
        info = {**base, **props}
        info['CFBundleShortVersionString'] = info.get('CFBundleShortVersionString') or env.get('DM_APP_VERSION') or '1.0'
        info['CFBundleVersion'] = info.get('CFBundleVersion') or info['CFBundleShortVersionString']
        icons = self.icons(app)
        if icons:
            info['CFBundleIcons'] = {'CFBundlePrimaryIcon': {'CFBundleIconFiles': ['AppIcon60x60']}}
            info['CFBundleIcons~ipad'] = {'CFBundlePrimaryIcon': {'CFBundleIconFiles': ['AppIcon60x60', 'AppIcon76x76', 'AppIcon83.5x83.5']}}
        with open(os.path.join(app, 'Info.plist'), 'wb') as f: plistlib.dump(info, f, fmt=plistlib.FMT_BINARY)
        open(os.path.join(app, 'PkgInfo'), 'w').write('APPL????')
        return info

    def icons(self, app):
        """The game icon as the loose PNGs CFBundleIcons names (iPhone 60 pt @2x / @3x, iPad 76 pt @2x, 83.5 pt @2x); False without an icon or Pillow"""
        src = os.environ.get('DM_ICON')
        if not src or not os.path.isfile(src): self.say('no app icon (DM_ICON)'); return False
        try: from PIL import Image
        except ImportError: print('[RED] build_app_linux: no Pillow (pip install pillow): the app gets no icon', file=sys.stderr); return False
        im = Image.open(src).convert('RGB')
        for name, px in (('AppIcon60x60@2x.png', 120), ('AppIcon60x60@3x.png', 180), ('AppIcon76x76@2x~ipad.png', 152), ('AppIcon83.5x83.5@2x~ipad.png', 167)):
            im.resize((px, px), Image.LANCZOS).save(os.path.join(app, name))
        self.say(f'app icon: {src}')
        return True

    def build(self):
        t0 = time.time()
        srcs = self.sources()
        c = [(s, f) for s, f in srcs if not s.endswith('.swift')]; sw = [s for s, _ in srcs if s.endswith('.swift')]
        objs, errs = [], []
        with cf.ThreadPoolExecutor(int(os.environ.get('J') or os.cpu_count() or 8)) as ex:
            for o, err in ex.map(lambda x: self.compile_one(*x), c):
                objs.append(o)
                if err: errs.append(err)
        if errs:
            for e in errs[:5]: print(e, file=sys.stderr)
            die(f'{len(errs)} of {len(c)} compiles failed (log {self.log.name})')
        if sw: objs.append(self.compile_swift(sw))
        app = os.path.join(self.out, f'{APP}.app')
        if os.path.isdir(app): shutil.rmtree(app)
        os.makedirs(app)
        fws = self.frameworks([s for s, _ in c if not s.endswith('.S')])
        swlib = [f'-L{os.path.join(self.t["sdk"], "usr/lib/swift")}', f'-L{os.path.join(self.t["swiftres"], "iphoneos")}']
        ld = shlex.split(self.xc.get('OTHER_LDFLAGS', '').replace('$(DM_FRAMEWORKS)', ''))
        ld = [x for i, x in enumerate(ld) if x != '-framework' and (i == 0 or ld[i - 1] != '-framework')]   # the frameworks come from fws
        cmd = [self.t['cc'], '-target', self.triple, '-isysroot', self.t['sdk'], '-fuse-ld=lld', *objs, self.builtins(), *swlib, '-Wl,-rpath,/usr/lib/swift', '-Wl,-rpath,@executable_path/Frameworks',
               *[x for f in fws for x in ('-framework', f)], *ld, '-Wl,-adhoc_codesign', '-o', os.path.join(app, APP)]
        self.say(f'Ld {APP}'); self.say(shlex.join(cmd))
        env = dict(os.environ, PATH=os.path.dirname(self.t['ld']) + os.pathsep + os.environ.get('PATH', ''))   # -fuse-ld=lld runs the ld64.lld found first (clang then passes lld's arguments: -platform_version)
        r = subprocess.run(cmd, capture_output=True, text=True, env=env)
        if r.returncode: die(f'link failed:\n{r.stdout}{r.stderr[-4000:]}')
        if r.stderr.strip(): self.say(r.stderr.rstrip())
        res = [s for s in self.target['sources'] if isinstance(s, dict) and s.get('type') == 'folder']   # folder references (Gen/dm) go in whole
        for s in res: shutil.copytree(os.path.join(self.ios, s['path']), os.path.join(app, os.path.basename(s['path'])), symlinks=False)
        info = self.info_plist(app)
        self.say(f'built {app} in {time.time() - t0:.0f} s: {info["CFBundleIdentifier"]} {info["CFBundleShortVersionString"]}, frameworks {" ".join(fws)}')
        return app


def main(a):
    if a == ['--check']:
        _, miss = toolchain()
        for m in miss: print(f'missing: {m}')
        return 1 if miss else 0
    if len(a) != 2: sys.exit(__doc__ or 'usage: build_app_linux.py --check | <ios dir> <out dir>')
    print(Builder(*a).build())
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
