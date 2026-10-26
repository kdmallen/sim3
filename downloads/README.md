# Windows downloads

## Updating an existing copy

[Download Sims3-Controls-Update.zip](https://github.com/kdmallen/sim3/blob/main/downloads/Sims3-Controls-Update.zip)
(46,817 bytes). On the file page, click **Download raw file**.

1. Close the game.
2. Extract all update files into the folder containing your existing **Sims3.exe**.
3. Replace matching files, then start **Sims3.exe** again.

The update's `tools` folder must merge with the existing `tools` folder. The
update cannot run by itself. Saves remain in `%LOCALAPPDATA%\Sims3Mobile`.

## Complete build

[Download Sims3-Windows-test.zip](https://github.com/kdmallen/sim3/blob/main/downloads/Sims3-Windows-test.zip)
using the file page's **Download raw file** button. This complete build includes
the controls update. Extract the entire ZIP and double-click **Sims3.exe**.
Keep the adjacent folders and DLLs together. No separate emulator or Python
installation is needed.

The full ZIP is about 139.7 MiB and is stored using Git LFS.
Download the ZIP file itself; GitHub's automatic repository source ZIP may
contain only an LFS pointer.

## Controls and verification

Type normally in name prompts; Enter confirms and Escape cancels. Mouse wheel
zooms. Arrow keys/WASD tilt. F5 shakes the phone (hold to repeat). F6 gives an
upward jerk. F1 shows controls; F3 toggles FPS. Ctrl+wheel over the camera slider's
middle calibrates zoom if needed.

Naming, entering the house, basic save/reload, both zoom directions, normal
window close, and 20 runtime tests passed under Wine 10. Performance overhead was
reduced; physical Windows frame rate remains unverified. Audio is not implemented,
and full gameplay remains unverified. F5 supplies sensor input; the Gardening Tips
money cheat did not activate during our test. EA's original agreement remains for
you to review and accept.

SHA256, complete ZIP: `e743df16557e7b3354cde5f918741d80c8ac1383b5235bafb92daba12dad5a8e`

SHA256, update ZIP: `a1d6ebd6d4bf7c82fb0cbddd348bd7bcb40f8c36310e23dd672f748a2b41b2dd`
