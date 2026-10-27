# Windows downloads

[Download Sims3-Performance-Update.zip](https://github.com/kdmallen/sim3/blob/main/downloads/Sims3-Performance-Update.zip)
(58,235 bytes). Click **Download raw file**.
Close the game, copy all ZIP contents beside **Sims3.exe**, replace files and
merge tools folders, then restart. Saves remain in AppData. This is cumulative:
sound, background play, smooth F4/F6 tilt, and earlier fixes are included.
Earlier patch URLs also supply this latest update.

**Pawn Shop / criminal career:** unlock after **15 completed goals**, using
the same completed counter as the game's Goals Checklist. Existing progress
counts, duplicate completions across saved/current records count once, and
already unlocked careers stay unlocked. Discovered-only goals do not count.
The original unlock function persists the result through normal settings saves.

**Money cheat:** open **Help & About → Gardening Tips**, then press **F5**.
Each new press adds **1,000 Simoleons**, up to the original 999,999 balance cap,
and shows a confirmation. Holding F5 gives one reward. Save normally to retain
the balance. F5 retains phone shake in other screens.

**F8 cycles 1x / 2x / 4x / 6x / 8x / 10x / 12x game speed**, displayed in the title. Restarting resets
speed to 1x. Actual acceleration depends on CPU headroom. Menus and animations
also accelerate; return to 1x for minigames. Audio and input remain at real speed.
F3 shows FPS. F1 shows controls. F4/F6 smooth tilt and F5 shake are unchanged.

Rendering reuses driver vertex-pointer descriptions while checking bounds on
each draw. The pinned vertex data remains live, so animations update normally.
Scalar ARM register writes reuse native call buffers; graphics and allocation
services take shorter dispatch paths. A native free(NULL) helper avoids a host
callback for a no-op. Non-null frees retain allocator checks.

37 ABI/rendering tests and 12 original-ARM Pawn Shop cases pass on Linux and the packaged Windows runtime under
Wine, including live vertex rewrites, buffer transitions, timer continuity and
64-bit returns, and checked allocator fallback. Physical Windows FPS and full
crowded-house gameplay remain unverified. See research/performance-update.json
for prior performance evidence. The earlier town-map comparison measured
14.81 versus 14.88 FPS; that drop is not confirmed fixed. See research/pawn-unlock-update.json for the
15-goal unlock evidence. The earlier repeated-fishing freeze remains unverified.

[Complete Sims3-Windows-test.zip](https://github.com/kdmallen/sim3/blob/main/downloads/Sims3-Windows-test.zip)
includes the update. Click **Download raw file**, extract fully, and run Sims3.exe.
No separate emulator or Python install is needed. The full ZIP uses Git LFS;
automatic repository source ZIPs can contain pointers. EA's agreement remains
for you to review and accept.

SHA256 full: `0e4977c79ed5b340d66cbbbe3941b2c355d83a025fa7135f63a4b63b5274df6c`

SHA256 patch (and earlier aliases): `45750f16735bf6cc63c7049cd7cb32ae47eb6a7a727ecd84959371a5b424b858`
