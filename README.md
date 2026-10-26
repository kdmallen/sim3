# The Sims 3 Mobile: standalone Windows test build

A Windows x64 application for the user-supplied Android game version 1.6.11.
The original ARM game executes through bundled Unicorn, with host implementations
of the required Marmalade services. ANGLE presents graphics in a native Win32
window. No Android virtual machine, separate emulator, or Python installation is
needed by the person running the packaged application.

**Experimental: complete gameplay and physical Windows performance are unverified.**
Music and sound effects use Windows' built-in audio services.
The original first-run EA agreement remains for the user to review and accept.
No acceptance is supplied or bypassed by the runtime.

## Running the Windows build

[Already have a build? Download the small rendering update](https://github.com/kdmallen/sim3/blob/main/downloads/Sims3-Render-Update.zip).
Close the game, extract the update into the folder containing your existing
`Sims3.exe`, and replace the matching files. Start `Sims3.exe` again. Saves stay
in AppData. Use **Download raw file** on the GitHub file page.

[Download Sims3-Windows-test.zip from GitHub](https://github.com/kdmallen/sim3/blob/main/downloads/Sims3-Windows-test.zip)
using the file page's **Download raw file** button. The ZIP is stored using Git
LFS. Download this file rather than GitHub's automatic source-code ZIP.

Extract the complete portable ZIP and double-click `Sims3.exe`. Keep the adjacent
folders and DLLs together. The first run prepares both resource resolutions.
Use mouse clicks/drags for touch input. Name prompts open a normal Windows text
box: type, press Enter to confirm, or Escape to cancel. The mouse wheel zooms
through the actual camera slider; it locates the visible thumb before dragging.
If needed, Ctrl+wheel over the slider's middle calibrates its position.

| Control | Action |
| --- | --- |
| Mouse click / drag | Touch, camera pan, menu scrolling |
| Mouse wheel | Camera zoom |
| Arrow keys / WASD | Phone tilt |
| F4 / F6 (hold) | Gradually lean left / right; release to level |
| F5 | Phone shake; hold for repeated shaking |
| F7 | Quick upward jerk for fishing |
| Escape | Back |
| F1 | Controls help |
| F3 | Show or hide FPS |

Saves, cache, `player.log`, and `last-run.json` live in
`%LOCALAPPDATA%\Sims3Mobile`. No administrator access is required.
Switching to another application keeps gameplay running and releases held
keys/buttons. The game's own menus still control their normal pause behavior.
Diagnostic snapshots are saved every ten seconds and on normal/error exits;
the previous session's snapshot and log are preserved for crash investigation.

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
- Passed 31 ABI/rendering regression tests on Linux and the packaged Windows Python under Wine.
- Passed native Win32 mouse, keyboard tilt, F5 shake, and orderly window-close smoke checks under Wine.
- Played the original MP3 music and PCM effects through WinMM/MCI under Wine;
  native audio tests cover overlapping effects, looping, volume, pause/resume, and stop.
- Verified smooth F4/F6 against the original ARM fishing steering and pull detector.
- Matched GPU-buffer deletion to GLES state: detach bindings/attributes, clear
  CPU shadows, and translate subsequent client indices to checked host memory.

The stability update adds a cooperative deadline after platform callbacks, so
heavy guest work returns control to the native event pump. Host driver errors
are preserved in the final report. The reported pre-update silent app exit was
not reproduced here; the user reports the speed/sound build no longer crashes.
A separate freeze after repeated fishing remains unverified. Extended play here
included sleep/nap actions, meals, and a home-to-town transition; the complete
four-sleep/job/social/job-change sequence was not completed.

The smooth-tilt update ramps F4/F6 at 45 degrees per second, capped at 30 degrees,
then eases back to level on release. It rotates a 1g gravity vector rather than
adding sideways acceleration. All three SDK axes share one sample. Fishing uses
landscape Y for steering, so these controls now drive SDK X with the matching
sign. The original fishing detector measured a false pull of 0.414214g with the
old vector and 0.000022g at the new maximum tilt. Both steering directions were
checked in original ARM code; full manual fishing play remains unverified.

Character creation and typed naming were checked in the real game, followed by
entering the house, saving, restarting, and loading the saved Sim. Mouse-wheel
zoom was checked in both directions against the game's real camera slider.
Full gameplay and sustained performance on physical Windows remain
unverified. Diagnostic runs retain legacy GL errors; the guest's own error
queries still return actual driver errors during normal play.

The controls update reduces repeated guest/host calls and removes a forced GPU
wait after every frame. A 420-frame startup comparison under Wine took 73 seconds
with the original build and 41 seconds with the update. This is a development
startup measurement, not a promised Windows frame rate. F3 shows recent FPS.

The speed and sound update also reuses register-transfer buffers, shares checked
guest RAM with synchronous graphics calls, caches graphics call signatures, and
reads guest strings in chunks. Music control runs on a separate host thread;
the guest CPU remains on the game thread. A subsequent 420-frame comparison
under Wine took 45.3 seconds for controls-2 and 41.3 seconds for speed-audio-3
with sound enabled. The recent p95 frame time fell from 112.1 to 81.1 ms in
that startup sample; this does not establish steady gameplay FPS on Windows.

## Development environment

Use the existing `/workspace/sim3` checkout; cloud tasks are already isolated.
Do not create an additional worktree. Keep working copies of game files,
unpacked assets, private saves, and intermediate builds in ignored `local-data/`.
The user-authorized complete release ZIP is tracked through Git LFS in `downloads/`.

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
source archive. Original game binaries are included only in the user-authorized Git LFS download;
private save files and caches remain outside source control.

The player can run bounded Windows diagnostics with `--frames N`,
`--profile PATH`, and `--tap FRAME:X:Y`. Run `tests/windows_window_smoke.py` with
the bundled `python.exe` for the native message test.

The rendering update avoids a second client-index copy and caches exact index
contents with their validated maximum. Reusing mesh topology avoids repeated
Python index scans; changed indices are revalidated. The cache is limited to
2 MiB and 256 entries. This reduces CPU work without changing scene detail.
The reported 20+ to 7.6 FPS drop in another Sim's house is not yet verified fixed.
