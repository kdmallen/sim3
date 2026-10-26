"""Exercise real WinMM PCM and the original MP3 via the packaged Windows Python."""
import math
from pathlib import Path
import struct
import sys
import tempfile
import time
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from audio import WindowsAudio


def wait_for(condition):
    deadline = time.monotonic() + 10
    while not condition() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert condition(), "Timed out waiting for native audio"


def main():
    root = Path(__file__).resolve().parents[1]
    device = WindowsAudio()
    assert device.available, "Native validation requires a configured audio output device"
    try:
        data = struct.pack("<" + "h" * 11025, *[
            round(8000 * math.sin(2 * math.pi * 440 * index / 22050))
            for index in range(11025)])
        assert device.play_effect(0, data, 22050, 2, 128), device.last_error
        assert device.play_effect(1, data, 22050, 0, 64), device.last_error
        assert device.busy(1)
        device.set_effect_volume(1, 32)
        time.sleep(1.3)
        assert not device.busy(0), "Finite effect repeats did not finish"
        assert device.busy(1), "Infinite effect loop stopped prematurely"
        assert device.stop_effect(1)
        assert not device.busy(1)
        device.start_music_worker()
        with tempfile.TemporaryDirectory() as temporary:
            music = Path(temporary) / "original-menu.mp3"
            with zipfile.ZipFile(root / "game/content.obb") as archive:
                music.write_bytes(archive.read("assets/audio/bgm_menu_1.mp3"))
            assert device.play_music(str(music), 0, 128), device.last_error
            wait_for(lambda: device.music_plays == 1)
            assert device.music_plays == 1, device.last_error
            assert device.music_status() == 1, device.last_error
            start = time.monotonic()
            for _ in range(1000):
                device.music_status()
            assert time.monotonic() - start < 0.2, "SDK music status blocked rendering"
            device.pause_music()
            wait_for(lambda: device.music_paused)
            assert device.music_status() == 2, device.last_error
            device.resume_music()
            wait_for(lambda: not device.music_paused)
            assert device.music_status() == 1, device.last_error
            device.set_music_volume(64)
            device.stop_music()
            wait_for(lambda: not device.music_open)
            assert device.music_status() == 0
        print("PASS: two PCM channels, finite/infinite repeats, volume, MP3 play/pause/resume/stop")
    finally:
        device.close()


if __name__ == "__main__":
    main()
