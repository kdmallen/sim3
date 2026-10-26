# Windows downloads

## Updating an existing copy

[Download Sims3-Speed-Sound-Update.zip](https://github.com/kdmallen/sim3/blob/main/downloads/Sims3-Speed-Sound-Update.zip)
(51,924 bytes). On the file page, click **Download raw file**.

1. Close the game.
2. Extract all update files into the folder containing your existing **Sims3.exe**.
3. Replace matching files, then start **Sims3.exe** again.

The update's `tools` folder must merge with the existing `tools` folder. This
cumulative update works with the original complete build or controls-2 and includes
the earlier typing/zoom fixes. Your controls stay the same. The small ZIP cannot
run by itself. Saves remain in `%LOCALAPPDATA%\Sims3Mobile`.
The prior `Sims3-Controls-Update.zip` URL also supplies this current update.

## Complete build

[Download Sims3-Windows-test.zip](https://github.com/kdmallen/sim3/blob/main/downloads/Sims3-Windows-test.zip)
using the file page's **Download raw file** button. This complete build includes
the speed and sound update. Extract the entire ZIP and double-click **Sims3.exe**.
Keep the adjacent folders and DLLs together. No separate emulator, Python
installation, or additional audio program is needed.

The full ZIP is about 139.7 MiB and uses Git LFS.
Download the ZIP file itself; GitHub's automatic repository source ZIP may
contain only an LFS pointer.

## What changed

Original MP3 music and PCM sound effects now play through Windows' built-in
services. Music control runs off the rendering thread. Graphics transfers and
SDK register/string reads do less repeated work. A 420-frame startup comparison
under Wine took 45.3 seconds for controls-2 and 41.3 seconds for this update, with
sound enabled. Recent p95 frame time in that sample decreased from 112.1 to
81.1 ms. This is a development measurement, not a Windows gameplay FPS promise.

The 23 runtime tests pass on Linux and the packaged Windows runtime under Wine.
Native tests cover overlapping effects, finite/infinite looping, volume, MP3
pause/resume/stop, text input, mouse/keyboard input, and normal window close.
The saved Alex profile loads into the house with music/effects and wheel zoom.
Full gameplay and sustained physical Windows performance remain unverified.
EA's original agreement remains for you to review and accept.

Type normally in name prompts; Enter confirms and Escape cancels. Mouse wheel
zooms. Arrow keys/WASD tilt. F5 shakes, F6 jerks upward, F1 shows controls, and F3
shows FPS. Ctrl+wheel over the slider's middle calibrates zoom if needed.

SHA256, complete ZIP: `47cba56eb400d411115b10fee15983ac854bc545b16ad831aaf664c93c5fb583`

SHA256, update ZIP (including legacy alias): `2ffdace6f85d98a5b624140246950de1691613ff848266ff10fd4940157f358b`
