"""Check F4/F6 through the inspected game's original ARM landscape adapter."""
import argparse
from pathlib import Path
import struct
import sys
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from boot_trace import Image, Runtime
from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R6, UC_ARM_REG_R8, UC_ARM_REG_SP


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apk", type=Path, default=Path(__file__).resolve().parents[1] / "game/game.apk")
    args = parser.parse_args()
    with zipfile.ZipFile(args.apk) as archive:
        image = Image(archive.read("assets/The Sims 3.s3e"))
    runtime = Runtime(image)
    try:
        runtime.input.accelerometer_available = runtime.input.accelerometer_active = True
        tick = [0.0]
        runtime.input.clock = lambda: tick[0]
        instance = runtime.allocate(36)
        scene = runtime.allocate(0x900)
        runtime.cpu.mem_write(instance, struct.pack("<9I", 0, 0, 0x3F800000, 0, 0, 0, 0, 0, 0))
        stack = runtime.STACK + runtime.STACK_BYTES - 256

        def pull_delta(vector):
            runtime.cpu.reg_write(UC_ARM_REG_SP, stack)
            runtime.cpu.reg_write(UC_ARM_REG_R6, scene)
            runtime.cpu.mem_write(stack + 4, struct.pack("<3f", *vector))
            runtime.cpu.mem_write(scene + 0x814, struct.pack("<2f", 1, 0))
            # Original fishing pull detector: |length(a) - previous_length|.
            runtime.cpu.emu_start(image.base + 0xA5010, image.base + 0xA5080, count=200000)
            return struct.unpack("<f", runtime.cpu.mem_read(scene + 0x818, 4))[0]

        old_pull = pull_delta((1, 0, 1))
        assert old_pull > 0.4, old_pull
        largest_pull = 0
        for key, sign in ((115, 1), (117, -1)):
            runtime.input.key(key, True)
            tick[0] += 2
            runtime.cpu.reg_write(UC_ARM_REG_SP, runtime.STACK + runtime.STACK_BYTES - 256)
            runtime.cpu.reg_write(UC_ARM_REG_R8, instance)
            # The actual main-loop code reads SDK axes, converts milli-g to
            # floats, and stores landscape X/Y/Z at instance + 12.
            runtime.cpu.emu_start(image.base + 0x1216EC, image.base + 0x1217E8, count=10000)
            x, y, z = struct.unpack("<3f", runtime.cpu.mem_read(instance + 12, 12))
            assert abs(x) < 1e-6 and abs(y - sign * 0.5) < 1e-6 and abs(z - 0.866) < 1e-6, (key, x, y, z)
            delta = pull_delta((x, y, z))
            largest_pull = max(largest_pull, delta)
            assert delta < 0.002, delta
            # The original steering block normalizes filtered landscape Y.
            runtime.cpu.reg_write(UC_ARM_REG_SP, stack)
            runtime.cpu.reg_write(UC_ARM_REG_R0, instance)
            runtime.cpu.emu_start(image.base + 0xA5520, image.base + 0xA55BC, count=200000)
            steering = struct.unpack("<f", runtime.cpu.mem_read(stack + 8, 4))[0]
            assert sign * steering > 0.49, steering
            runtime.input.key(key, False)
        print(f"PASS: original fishing steering responds both ways; pull detector old={old_pull:.6f}g, smooth={largest_pull:.6f}g")
    finally:
        runtime.filesystem.close()


if __name__ == "__main__":
    main()
