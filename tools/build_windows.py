"""Assemble a portable Windows x64 test build from checksum-pinned inputs."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tarfile
import zipfile

from player import APK_HASH, OBB_HASH, digest

ROOT = Path(__file__).resolve().parents[1]


def build(apk, obb, dependencies, destination, launcher):
    if digest(apk) != APK_HASH or digest(obb) != OBB_HASH:
        raise ValueError("This build supports only the inspected 1.6.11 APK and OBB")
    if destination.exists():
        raise FileExistsError("Choose a new destination; existing builds are preserved")
    records = json.loads((dependencies / "verified-downloads.json").read_text())
    records.append(json.loads((dependencies / "angle-download.json").read_text()))
    for record in records:
        source = dependencies / Path(record["path"]).name
        actual = hashlib.new(record["algorithm"], source.read_bytes()).hexdigest()
        if actual != record["expected_hash"]:
            raise ValueError("Dependency checksum failed: " + source.name)
    destination.mkdir(parents=True)
    with tarfile.open(dependencies / Path(records[0]["path"]).name) as archive:
        for member in archive.getmembers():
            relative = Path(member.name).relative_to("python")
            if ".." in relative.parts or relative.is_absolute() or member.issym() or member.islnk():
                raise ValueError("Unexpected path in Python runtime")
            target = destination / relative
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(member) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)
    packages = destination / "Lib/site-packages"
    packages.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dependencies / Path(records[1]["path"]).name) as archive:
        for name in archive.namelist():
            path = Path(name)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError("Unexpected wheel path")
            if not name.endswith("/"):
                target = packages / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(name))
    native = destination / "native"
    native.mkdir()
    notices = destination / "licenses"
    notices.mkdir()
    with zipfile.ZipFile(dependencies / Path(records[2]["path"]).name) as archive:
        (native / "av_libglesv2.dll").write_bytes(archive.read("runtimes/win-x64/native/av_libglesv2.dll"))
        (notices / "ANGLE-LICENSE.txt").write_bytes(archive.read("LICENSE"))
    for name in ("COPYING", "COPYING.LIB", "LICENSE"):
        shutil.copyfile(dependencies / ("unicorn-" + name), notices / ("Unicorn-" + name + ".txt"))
    source_record = json.loads((dependencies / "unicorn-source-download.json").read_text())
    source = dependencies / Path(source_record["path"]).name
    if digest(source) != source_record["sha256"]:
        raise ValueError("Unicorn source checksum mismatch")
    shutil.copyfile(source, notices / "unicorn-2.1.4-source.tar.gz")
    (notices / "THIRD-PARTY.txt").write_text(
        "Python: PSF and bundled component licenses in ../LICENSE.txt\n"
        "Unicorn 2.1.4: GPLv2 engine; corresponding unmodified source included.\n"
        "ANGLE: BSD; full license in ANGLE-LICENSE.txt\n"
        "Khronos GLES declarations: MIT, notice in ../vendor/khronos/gl2.h\n"
        "Original game files supplied by the user; EA retains their copyrights.\n")
    (destination / "tools").mkdir()
    for source in list((ROOT / "tools").glob("*.py")) + [ROOT / "tools/windows_launcher.c"]:
        shutil.copyfile(source, destination / "tools" / source.name)
    for name in ("vendor/khronos/gl2.h", "research/jni-interface.json"):
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    (destination / "game").mkdir()
    shutil.copyfile(apk, destination / "game/game.apk")
    shutil.copyfile(obb, destination / "game/content.obb")
    shutil.copyfile(launcher, destination / "Sims3.exe")
    isolation = ".\ntools\nLib\nDLLs\nLib/site-packages\n"
    for name in ("python313._pth", "Sims3._pth"):
        (destination / name).write_text(isolation)
    (destination / "START HERE.txt").write_text(
        "THE SIMS 3 MOBILE - STABILITY AND CONTROLS UPDATE 4\n\n"
        "Extract the ENTIRE folder, then double-click Sims3.exe.\n"
        "Keep the other files beside it. No separate emulator or Python install is needed.\n"
        "Windows 11 x64 is the intended target.\n\n"
        "Click and drag with the mouse for touchscreen controls. Type using the keyboard.\n"
        "Mouse wheel zooms; Ctrl+wheel over the slider middle calibrates zoom.\n"
        "F4/F6 (hold) tilt the screen left/right; F5 shakes; F7 makes an upward jerk.\n"
        "Arrow keys / WASD also tilt. Switching windows keeps gameplay running.\n"
        "F1 shows controls; F3 toggles frame rate. Close the window to exit.\n"
        "The first launch prepares game files and may take a short while.\n"
        "Saves and logs: %LOCALAPPDATA%\\Sims3Mobile\\\n\n"
        "This is an experimental build. Naming, camera zoom, and basic save/load are verified.\n"
        "Original MP3 music and PCM sound effects use Windows' built-in audio services.\n"
        "Full gameplay and physical Windows performance need testing.\n"
        "EA's agreement screen is left for you to review and accept in the game.\n"
        "Name prompts accept normal keyboard typing; Enter confirms, Escape cancels.\n")
    (destination / "version.json").write_text(json.dumps({
        "version": "stability-4", "game_version": "1.6.11", "tests_passed": 28,
        "audio_implemented": True,
        "shake_money_cheat_confirmed": False,
    }, indent=2) + "\n")
    (destination / "dependency-sources.json").write_text(json.dumps(records + [source_record], indent=2))
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apk", type=Path, required=True)
    parser.add_argument("--obb", type=Path, required=True)
    parser.add_argument("--dependencies", type=Path, default=ROOT / "local-data/windows-deps")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--launcher", type=Path, required=True)
    args = parser.parse_args()
    print(build(args.apk, args.obb, args.dependencies, args.output, args.launcher))


if __name__ == "__main__":
    main()
