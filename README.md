# The Sims 3 Mobile: standalone Windows test build

A Windows x64 application for the user-supplied Android game version 1.6.11.
The original ARM game executes through bundled Unicorn, with host implementations
of the required Marmalade services. ANGLE presents graphics in a native Win32
window. No Android virtual machine, separate emulator, or Python installation is
needed by the person running the packaged application.

**Experimental: audio is not implemented and complete gameplay is unverified.**
The original first-run EA agreement remains for the user to review and accept.
No acceptance is supplied or bypassed by the runtime.

## Running the Windows build

[Download Sims3-Windows-test.zip from GitHub](https://github.com/kdmallen/sim3/blob/main/downloads/Sims3-Windows-test.zip)
using the file page's **Download raw file** button. The ZIP is stored using Git
LFS. Download this file rather than GitHub's automatic source-code ZIP.

Extract the complete portable ZIP and double-click `Sims3.exe`. Keep the adjacent
folders and DLLs together. The first run prepares both resource resolutions.
Use mouse clicks/drags for touch input. Arrow keys or WASD provide virtual tilt.
Saves, cache, `player.log`, and `last-run.json` live in
`%LOCALAPPDATA%\Sims3Mobile`. No administrator access is required.

The intended target is Windows 11 x64. Validation here uses Wine 10 with ANGLE
D3D11, plus Linux Mesa for diagnostics; execution on a physical Windows 11 PC
has not been tested. Wine is only a development test tool and is not packaged.
A game fault reports its real error and writes a log; unknown platform APIs stop.

## Verified work

- Reassembled and ZIP-CRC checked the 104,470,809-byte OBB from the 14 RAR volumes.
- Extracted and validated 679 DTRZ files per resolution (1,358 total).
- Loaded the original XE3U ARM image, with 334 imports, 342 references, and 8,204 relocations.
- Rendered the EA splash, Sims splash, language selection, and 3D title/agreement scene using real GLES draws.
- Executed pointer callbacks in the original ARM game; supplied isolated save-file services.
- Built a native PE x64 GUI entry point with bundled Python 3.13.17, Unicorn 2.1.4, and ANGLE.
- Passed 11 ABI/rendering regression tests on Linux and the packaged Windows Python under Wine.
- Passed native Win32 mouse, keyboard tilt, and orderly window-close smoke checks under Wine.

Character creation, actual town gameplay, save-game loading, audio, and sustained
performance remain unverified. Filesystem save round-trip tests do not establish
that a complete game save/load cycle succeeds. A pending GL_INVALID_OPERATION
from the original rendering sequence is retained in diagnostic reports; errors
are not silently discarded.

## Development environment

Use the existing `/workspace/sim3` checkout; cloud tasks are already isolated.
Do not create an additional worktree. Keep original game files, unpacked assets,
private saves, and binary builds outside source control (`local-data/` is ignored).

```sh
python -m venv .venv
.venv/bin/python -m pip install -r requirements-research.txt
.venv/bin/python -m unittest discover -s tests -v
```

Linux rendering needs system EGL/GLES with surfaceless EGL (Mesa works). The
headless runner is a diagnostic tool and has an explicit execution budget:

```sh
.venv/bin/python tools/boot_trace.py local-data/game.apk \
  --obb local-data/content.obb --resources local-data/resources \
  --saves local-data/test-profile --frames 330 --tap 220:239:143 \
  --instructions 5000000000 --seconds 60 --report reports/boot-trace.json
```

Use a fresh profile for repeatable language selection. The back-arrow tap timing
is diagnostic automation, not a legal agreement acceptance. Exit code 0 means a
frame limit or normal return; inspect the report before interpreting the result.
Unknown services and guest faults return a failure. Screenshots are actual frame
captures under `reports/frames/`.

## Rebuilding Windows

`research/windows-dependencies.json` pins official dependency URLs and hashes.
`tools/fetch_windows_dependencies.py` verifies TLS and the pinned hashes, reuses
valid files, and extracts the required Unicorn notices. Supply the original APK
and complete OBB; the builder rejects other hashes.

```sh
.venv/bin/python tools/fetch_windows_dependencies.py
.venv/bin/python tools/compile_launcher.py \
  --python-dll local-data/builds/previous/python313.dll \
  --windows-libs /path/to/wine/x86_64-windows --output local-data/Sims3.exe
.venv/bin/python tools/build_windows.py --apk local-data/game.apk \
  --obb local-data/content.obb --launcher local-data/Sims3.exe \
  --output local-data/builds/Sims3-Windows-test
```

For the first build, extract `python/python313.dll` from the verified Python
tarball before compiling the launcher. GNU gcc/ld use the Microsoft x64 ABI and
PE exports from Python, kernel32, and shell32. Wine's export DLLs serve only as
link-time import definitions; those DLLs are not shipped. The output imports
Windows system libraries by name. No compiler C runtime is required by the launcher.
The Windows build includes dependency notices and the unmodified Unicorn engine
source archive. Source-control contents exclude the original game binaries.

The player can run bounded Windows diagnostics with `--frames N`,
`--profile PATH`, and `--tap FRAME:X:Y`. Run `tests/windows_window_smoke.py` with
the bundled `python.exe` for the native message test.
