# Windows downloads

[Download Sims3-Performance-Update.zip](https://github.com/kdmallen/sim3/blob/main/downloads/Sims3-Performance-Update.zip)
(54,942 bytes). Click **Download raw file**.
Close the game, copy all ZIP contents beside **Sims3.exe**, replace files and
merge tools folders, then restart. Saves remain in AppData. This is cumulative:
sound, background play, smooth F4/F6 tilt, and earlier fixes are included.
Earlier patch URLs also supply this latest update.

**F8 cycles 1x / 2x / 4x / 6x / 8x / 10x / 12x game speed**, displayed in the title. Restarting resets
speed to 1x. Actual acceleration depends on CPU headroom. Menus and animations
also accelerate; return to 1x for minigames. Audio and input remain at real speed.
F3 shows FPS. F1 shows controls. F4/F6 smooth tilt and F5 shake are unchanged.

Rendering reuses driver vertex-pointer descriptions while checking bounds on
each draw. The pinned vertex data remains live, so animations update normally.
Scalar ARM register writes reuse native call buffers; graphics and allocation
services take shorter dispatch paths. A native free(NULL) helper avoids a host
callback for a no-op. Non-null frees retain allocator checks.

35 ABI/rendering tests pass on Linux and the packaged Windows runtime under
Wine, including live vertex rewrites, buffer transitions, timer continuity and
64-bit returns, and checked allocator fallback. Physical Windows FPS and full
crowded-house gameplay remain unverified. See research/performance-update.json
for development evidence. The earlier single town-map comparison at 1x measured
14.81 versus 14.88 FPS: no meaningful average improvement, so the reported
town-map drop is **not confirmed fixed**. The earlier repeated-fishing freeze remains unverified.

[Complete Sims3-Windows-test.zip](https://github.com/kdmallen/sim3/blob/main/downloads/Sims3-Windows-test.zip)
includes the update. Click **Download raw file**, extract fully, and run Sims3.exe.
No separate emulator or Python install is needed. The full ZIP uses Git LFS;
automatic repository source ZIPs can contain pointers. EA's agreement remains
for you to review and accept.

SHA256 full: `3ed733c46b7988266be48d7a976b6333dbfdde6f73e267ea7769b9d8c12e0a42`

SHA256 patch (and earlier aliases): `a64e44cd21267f28556372a9a6478718b82ea16769322fa472ddd5c1f8dbbfc7`
