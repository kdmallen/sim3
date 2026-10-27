"""Check the patched gate using the supplied game's original ARM goal counter."""
import argparse
import json
from pathlib import Path
import struct
import sys
import zipfile
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from boot_trace import Image, Runtime
from pawn_unlock import PawnUnlock
from unicorn import UC_HOOK_CODE
from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R4, UC_ARM_REG_LR, UC_ARM_REG_SP


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apk', type=Path, default=Path(__file__).resolve().parents[1] / 'game/game.apk')
    args = parser.parse_args()
    with zipfile.ZipFile(args.apk) as archive:
        runtime = Runtime(Image(archive.read('assets/The Sims 3.s3e')))
    try:
        gate = runtime.pawn_unlock
        assert gate.enabled
        application = runtime.allocate(0x520)
        saves = []
        # Only disk IO is intercepted: the original unlock's flag write executes.
        # Actual settings persistence is checked separately in a full game launch.
        def save(cpu, address, size, data):
            assert cpu.mem_read(application + 0x4B8, 1) == b'\x01'
            saves.append(True)
            runtime.jump(cpu.reg_read(UC_ARM_REG_LR))
        runtime.cpu.hook_add(UC_HOOK_CODE, save, begin=runtime.image.base + 0x1396C,
                             end=runtime.image.base + 0x1396C)
        cases = [
            ('zero', [], [], [], 0, 0),
            ('fourteen', list(range(14)), [], [], 0, 0),
            ('fifteen', list(range(15)), [], [], 0, 1),
            ('sixteen', list(range(16)), [], [], 0, 1),
            ('discovered_only', [], [], list(range(25)), 0, 0),
            ('duplicate_completions', list(range(10)), list(range(10)), [], 0, 0),
            ('combined_saved_and_current', list(range(10)), list(range(5, 15)), [], 0, 1),
            ('upper_bitset_boundary', list(range(120, 128)), list(range(7)), [], 0, 1),
            ('existing_unlock', [], [], [], 1, 1),
        ]
        results = []
        def bits(ids):
            return sum(1 << i for i in set(ids)).to_bytes(16, 'little')
        def call():
            stack = runtime.STACK + runtime.STACK_BYTES - 16
            runtime.cpu.reg_write(UC_ARM_REG_SP, stack)
            runtime.cpu.reg_write(UC_ARM_REG_R0, application)
            runtime.cpu.reg_write(UC_ARM_REG_R4, 0x12345678)
            runtime.cpu.reg_write(UC_ARM_REG_LR, runtime.API + 0xFFFC)
            runtime.cpu.emu_start(runtime.image.base + gate.GETTER, runtime.API + 0xFFFC, count=100000)
            assert runtime.cpu.reg_read(UC_ARM_REG_SP) == stack
            assert runtime.cpu.reg_read(UC_ARM_REG_R4) == 0x12345678
            return runtime.cpu.reg_read(UC_ARM_REG_R0)
        for name, saved, current, discovered, unlocked, expected in cases:
            runtime.cpu.mem_write(application, bytes(0x520))
            runtime.cpu.mem_write(application + 0x4B8, bytes([unlocked]))
            runtime.cpu.mem_write(application + 0x4D0, bits(saved))
            runtime.cpu.mem_write(application + 0x4F0, bits(current))
            runtime.cpu.mem_write(application + 0x4C0, bits(discovered))
            saves.clear()
            assert call() == expected, name
            assert len(saves) == int(expected and not unlocked), name
            assert call() == expected, name
            assert len(saves) == int(expected and not unlocked), name  # Save once.
            results.append({'case': name, 'unlocked': bool(expected), 'save_calls': len(saves)})
        for offset in (gate.GETTER, gate.COMPLETED_COUNT, gate.UNLOCK):
            code = bytearray(runtime.image.code)
            code[offset] ^= 1
            # No CPU is supplied: a rejected image must not try to patch memory.
            rejected = PawnUnlock(SimpleNamespace(image=SimpleNamespace(
                base=runtime.image.base, code=bytes(code))))
            assert not rejected.enabled
            results.append({'case': f'changed_signature_{offset:x}', 'rejected': True})
        print(json.dumps({'passed': len(results), 'cases': results}))
    finally:
        runtime.audio.close()
        runtime.filesystem.close()


if __name__ == '__main__':
    main()
