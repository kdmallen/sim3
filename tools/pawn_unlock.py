"""Use the game's completed-goal count to unlock the Pawn Shop at 15."""
import struct

from fast_services import ArmCode


class PawnUnlock:
    ADDRESS = 0x62000000
    GETTER = 0x11894
    COMPLETED_COUNT = 0x11B7C
    UNLOCK = 0x15208
    REQUIRED = 15

    def __init__(self, runtime):
        signatures = {
            self.GETTER: bytes.fromhex("b804d0e51eff2fe1"),
            self.COMPLETED_COUNT: bytes.fromhex("7c209fe57c309fe54d1ea0e34fcea0e3"),
            self.UNLOCK: bytes.fromhex("0130a0e3b834c0e5d5f9ffea"),
        }
        self.enabled = runtime.image.base == 0x4A000000 and all(
            runtime.image.code[offset:offset + len(value)] == value
            for offset, value in signatures.items())
        if not self.enabled:
            return
        code = ArmCode()
        code.emit(0xE92D4010)  # push {r4, lr}; preserve 8-byte stack alignment
        code.emit(0xE1A04000)  # mov r4, r0 (application/settings object)
        code.emit(0xE5D404B8)  # ldrb r0, [r4, #0x4b8] (existing unlock)
        code.emit(0xE3500000)  # cmp r0, #0
        code.branch(1, "done")
        code.emit(0xE1A00004)  # mov r0, r4
        code.load(3, runtime.image.base + self.COMPLETED_COUNT)
        code.emit(0xE12FFF33)  # blx r3 (original completed count, deduplicates bits)
        code.emit(0xE3500000 | self.REQUIRED)  # cmp r0, #15
        code.emit(0xB3A00000)  # movlt r0, #0
        code.branch(11, "done")
        code.emit(0xE1A00004)  # mov r0, r4
        code.load(3, runtime.image.base + self.UNLOCK)
        code.emit(0xE12FFF33)  # original unlock sets flag and saves settings
        code.emit(0xE3A00001)  # mov r0, #1
        code.labels["done"] = len(code.words)
        code.emit(0xE8BD8010)  # pop {r4, pc}
        runtime.cpu.mem_map(self.ADDRESS, 4096)
        runtime.cpu.mem_write(self.ADDRESS, code.finish())
        # Eight-byte absolute jump replaces the fingerprinted getter only.
        runtime.cpu.mem_write(runtime.image.base + self.GETTER,
                              struct.pack("<2I", 0xE51FF004, self.ADDRESS))

    def report(self):
        return {"enabled": self.enabled, "required_completed_goals": self.REQUIRED}
