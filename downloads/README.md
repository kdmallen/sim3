# Windows downloads

[Download Sims3-Smooth-Tilt-Update.zip](https://github.com/kdmallen/sim3/blob/main/downloads/Sims3-Smooth-Tilt-Update.zip)
(53,324 bytes). Click **Download raw file** on the file page.

Close the game. Copy all ZIP contents into the folder containing **Sims3.exe**,
replace matching files, merge `tools` folders, then restart the game. Saves stay
in `%LOCALAPPDATA%\Sims3Mobile`. This cumulative update works with all earlier
complete builds and includes sound, background play, and the previous fixes.
Earlier controls/sound/stability patch URLs also supply this current update.

**Hold F4/F6** to gradually lean left/right; release to ease back to level.
The fishing steering axis is corrected. The gravity vector rotates at 1g,
capped at 30 degrees, avoiding the old false tug. **F5 is unchanged; F7** is
the upward jerk. Arrow keys/WASD retain their earlier behavior. Mouse clicks/drags
are touch controls, wheel zooms, F1 shows help, F3 shows FPS, Escape goes back.
Name prompts accept normal typing.

29 tests pass on Linux and the Windows runtime under Wine. Native keyboard and
original ARM fishing checks confirm steering both ways and a pull-detector result
of 0.000022g at maximum tilt, versus 0.414214g with the old vector. Full manual
fishing and the reported repeated-fishing freeze remain unverified.

For a fresh install, [download Sims3-Windows-test.zip](https://github.com/kdmallen/sim3/blob/main/downloads/Sims3-Windows-test.zip)
using **Download raw file**. Extract the whole portable ZIP and run **Sims3.exe**.
Keep adjacent files together. No separate emulator or Python install is needed.
The complete ZIP uses Git LFS; GitHub's repository source ZIP may contain an LFS
pointer. EA's original agreement remains for you to review and accept.

SHA256, full ZIP: `cb48555662e2d484d141c4978abd3f5d7acbc28ab04376c812f9f8cc599d53ca`

SHA256, update ZIP (and earlier aliases): `930b290437e4840a5fa7e6332459b737af534a27b262f3e31be12c2e85ddfd79`
