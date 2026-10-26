# Windows downloads

[Download Sims3-Render-Update.zip](https://github.com/kdmallen/sim3/blob/main/downloads/Sims3-Render-Update.zip)
(53,693 bytes). Click **Download raw file**.
Close the game, copy all ZIP contents beside **Sims3.exe**, replace files and
merge tools folders, then restart. Saves remain in AppData. This is cumulative:
sound, background play, smooth F4/F6 tilt, and earlier fixes are included.
Earlier patch URLs also supply this latest update.

The renderer now avoids an extra index-buffer copy and reuses validated index
bounds for identical mesh data. Changed data is revalidated; the cache is capped
at 2 MiB/256 entries. Scene detail, simulation, and controls are unchanged.
This targets CPU overhead in busy scenes. The reported 20+ to 7.6 FPS drop in
another Sim's house has not been remeasured on the user's PC.

31 tests pass on Linux and the Windows runtime under Wine. Tests include actual
pixel rendering, buffer deletion, modified index contents, and cache limits.
A repeated 8192-index microbenchmark improved substantially; that is a CPU bounds
processing measurement and does not predict total game FPS.

Hold F4/F6 for smooth tilt, release to level. F5 is unchanged; F7 jerks upward.
Mouse click/drag is touch, wheel zooms, F1 shows controls, F3 shows FPS.
Full gameplay and the prior repeated-fishing freeze remain unverified.

[Complete Sims3-Windows-test.zip](https://github.com/kdmallen/sim3/blob/main/downloads/Sims3-Windows-test.zip)
includes the update. Download the file itself with **Download raw file**, extract
fully, and run Sims3.exe. No separate emulator or Python install is needed.
The full ZIP uses Git LFS; automatic repository source ZIPs can contain pointers.
EA's original agreement remains for you to review and accept.

SHA256 full: `09809653c9844cb29f0c2d4ad15e17893cf2fcc5ec5fc69ef7a354e671529f4b`

SHA256 patch (and earlier aliases): `f8ebfa6f3e9ca6e6847ab05007de6def00040a918bbe1dd9f6e4f514556513ca`
