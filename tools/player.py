"""Windows player for the inspected Sims 3 Mobile 1.6.11 assets."""
import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import traceback
import zipfile

from boot_trace import Image, Runtime, read_icf
from extract_dz import extract
from win32_window import Win32Window


APK_HASH = "5404b97d48681d190fa0e28e8571c55e3960f97654b8eb894dc2ece2d102a89b"
OBB_HASH = "371166624b179611c50f6e770e5f929e75832ccf116a9e619a52e3599a82056d"


def digest(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def prepare_resources(obb, cache, window):
    destination = cache / OBB_HASH
    marker = destination / "complete.json"
    if marker.is_file() and json.loads(marker.read_text()) == {"obb_sha256": OBB_HASH, "format": 1}:
        return destination
    cache.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix="preparing-", dir=cache))
    try:
        with zipfile.ZipFile(obb) as archive:
            for resolution in ("LowRes", "HighRes"):
                window.set_status("Preparing game files… This only runs the first time.")
                source = temporary / (resolution + ".dz")
                source.write_bytes(archive.read(f"assets/{resolution}/res.dz"))
                def progress(done, total):
                    if done % 20 == 0 or done == total:
                        window.set_status(f"Preparing game files… {resolution}: {done * 100 // total}%")
                        if window.runtime.quit_requested:
                            raise InterruptedError("Preparation cancelled")
                extract(source, temporary / resolution, progress=progress)
                source.unlink()
        (temporary / "complete.json").write_text(json.dumps({"obb_sha256": OBB_HASH, "format": 1}))
        # A previous interrupted cache is never reused. Saves live elsewhere.
        if destination.exists():
            shutil.rmtree(destination)
        temporary.rename(destination)
        return destination
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def main():
    if os.name != "nt":
        raise RuntimeError("This player requires 64-bit Windows")
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path)
    parser.add_argument("--frames", type=int, help="Diagnostic frame limit; omit for normal play")
    parser.add_argument("--tap", action="append", default=[])
    args = parser.parse_args()
    profile = args.profile or Path(os.environ["LOCALAPPDATA"]) / "Sims3Mobile"
    profile.mkdir(parents=True, exist_ok=True)
    apk, obb = root / "game/game.apk", root / "game/content.obb"
    config = {}
    with zipfile.ZipFile(apk) as archive:
        data = archive.read("assets/The Sims 3.s3e")
        for name in ("assets/s3e.icf", "assets/app.icf"):
            if name in archive.namelist():
                config.update(read_icf(archive.read(name).decode("utf-8")))
    taps = [tuple(map(int, value.split(":"))) for value in args.tap]
    runtime = Runtime(Image(data), config, apk, obb, saves=profile / "saves",
                      frame_limit=args.frames, taps=taps, frames_directory=profile / "frames")
    window = Win32Window(runtime)
    try:
        window.set_status("Checking game files…")
        if digest(apk) != APK_HASH or digest(obb) != OBB_HASH:
            raise ValueError("Game files are missing or damaged. Extract the complete download again.")
        runtime.filesystem.resources = prepare_resources(obb, profile / "cache", window)
        if runtime.quit_requested:
            return 0
        window.set_status("Starting The Sims 3…")
        report = runtime.run_interactive()
        report["windows_executable_built"] = True
        # Reaching a title screen is not a completed gameplay validation.
        (profile / "last-run.json").write_text(json.dumps(report, indent=2) + "\n")
        if report["error"]:
            raise RuntimeError(report["error"])
        return 0
    except InterruptedError:
        return 0
    finally:
        if runtime.graphics:
            runtime.graphics.close()
        runtime.filesystem.close()
        window.close()


def launch():
    """Keep a useful error log even when launched without a console."""
    directory = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Sims3Mobile"
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "player.log").open("w", encoding="utf-8", buffering=1) as log:
        sys.stdout = sys.stderr = log
        try:
            return main()
        except Exception as exc:
            traceback.print_exc()
            message = ("The Sims 3 stopped: " + str(exc) +
                       "\n\nDetails are saved in " + str(directory / "player.log"))
            ctypes.windll.user32.MessageBoxW(None, message, "The Sims 3 Mobile", 0x10)
            return 1


if __name__ == "__main__":
    raise SystemExit(launch())
