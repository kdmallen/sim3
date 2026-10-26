"""Check F4/F6 through the inspected game's original ARM landscape adapter."""
import argparse
from pathlib import Path
import struct
import sys
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from boot_trace import Image, Runtime
from unicorn.arm_const import UC_ARM_REG_R8, UC_ARM_REG_SP


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apk", type=Path, default=Path(__file__).resolve().parents[1] / "game/game.apk")
    args = parser.parse_args()
    with zipfile.ZipFile(args.apk) as archive:
        image = Image(archive.read("assets/The Sims 3.s3e"))
    runtime = Runtime(image)
    try:
        runtime.input.accelerometer_available = runtime.input.accelerometer_active = True
        instance = runtime.allocate(36)
        runtime.cpu.mem_write(instance, struct.pack("<9I", 0, 0, 0x3F800000, 0, 0, 0, 0, 0, 0))
        for key, expected in ((115, -1.0), (117, 1.0)):
            runtime.input.key(key, True)
            runtime.cpu.reg_write(UC_ARM_REG_SP, runtime.STACK + runtime.STACK_BYTES - 256)
            runtime.cpu.reg_write(UC_ARM_REG_R8, instance)
            # The actual main-loop code reads SDK axes, converts milli-g to
            # floats, and stores landscape X/Y/Z at instance + 12.
            runtime.cpu.emu_start(image.base + 0x1216EC, image.base + 0x1217E8, count=10000)
            x, y, z = struct.unpack("<3f", runtime.cpu.mem_read(instance + 12, 12))
            assert (x, y, z) == (expected, 0.0, 1.0), (key, x, y, z)
            runtime.input.key(key, False)
        print("PASS: F4/F6 become -1/+1 on the original game's landscape X axis")
    finally:
        runtime.filesystem.close()


if __name__ == "__main__":
    main()
