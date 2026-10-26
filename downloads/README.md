# Windows downloads

[Download Sims3-Stability-Update.zip](https://github.com/kdmallen/sim3/blob/main/downloads/Sims3-Stability-Update.zip)
(53,608 bytes). On the file page click **Download raw file**.

1. Close the game.
2. Extract all update files into the folder containing your existing **Sims3.exe**.
3. Replace matching files, merge the `tools` folders, and start **Sims3.exe** again.

This cumulative update includes sound, naming, zoom, and previous performance
changes. It applies to all earlier complete builds here. Saves remain in
`%LOCALAPPDATA%\Sims3Mobile`. The small ZIP needs the complete build to run.
The earlier controls and speed/sound ZIP URLs also supply this current update.

For a fresh install, [download Sims3-Windows-test.zip](https://github.com/kdmallen/sim3/blob/main/downloads/Sims3-Windows-test.zip),
click **Download raw file**, extract the whole ZIP, and run **Sims3.exe**.
Keep adjacent files together. No separate emulator or Python install is needed.
The full ZIP is 139.7 MiB and uses Git LFS; GitHub's automatic
repository source ZIP may contain only an LFS pointer.

## Controls and stability changes

- **Hold F4 / F6:** tilt the screen left / right.
- **F5:** existing shake input, unchanged.
- **F7:** the previous F6 upward jerk.
- **Mouse:** click/drag for touch, wheel for zoom. Type normally in name prompts.
- **F1:** controls help. **F3:** FPS. **Escape:** back. Arrow keys/WASD still tilt.

The game keeps running when another application has focus; held input is released
on focus loss. Its own menus keep their normal pause behavior. GPU buffer/texture
state cleanup is corrected, callback deadlines return control to Windows, and
host driver errors are preserved in reports. Periodic diagnostics and previous
session logs survive restarts under the save directory above.

28 runtime tests pass on Linux and the packaged Windows runtime under Wine.
Native checks cover focus, controls, the original ARM landscape conversion,
typing, and window close. The clock advanced while another window had focus.
Extended play rendered 14,920 frames and included sleep/nap, meals, and a town
transition with no error or failed allocation; shutdown completed normally.
The full four-sleep/job/social/job-change sequence was not completed. The earlier
silent app exit was not reproduced here, and the user reports that the prior
sound update no longer crashes. Repeated fishing freezes remain unverified.
Full gameplay and physical Windows performance remain unverified here.
EA's original agreement remains for you to review and accept.

SHA256, complete ZIP: `c0645388a467ce36e095785388abed19de523d9051665c14b4b50753590b95bc`

SHA256, update ZIP (including old URL aliases): `4c0a66ce5c24077606180aff7b96667a9a17ed23edff474ffaa4592a6b27459e`
