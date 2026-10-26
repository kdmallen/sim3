"""Small ARM implementations of frequently queried, deterministic SDK state.

These execute inside the same guest CPU. Unsupported properties branch back to
the ordinary checked host service; this does not replace any game logic.
"""
import struct


class ArmCode:
    def __init__(self):
        self.words = []
        self.labels = {}
        self.branches = []
        self.literals = []

    def emit(self, word):
        self.words.append(word)

    def load(self, register, value):
        self.literals.append((len(self.words), register, value))
        self.emit(0)

    def branch(self, condition, label):
        self.branches.append((len(self.words), condition, label))
        self.emit(0)

    def count(self, address):
        self.load(2, address)
        self.emit(0xE5923000)  # ldr r3, [r2]
        self.emit(0xE2833001)  # add r3, r3, #1
        self.emit(0xE5823000)  # str r3, [r2]

    def finish(self):
        for index, condition, label in self.branches:
            self.words[index] = condition << 28 | 0x0A000000 | ((self.labels[label] - index - 2) & 0xFFFFFF)
        for index, register, value in self.literals:
            distance = (len(self.words) - index - 2) * 4
            self.words[index] = 0xE59F0000 | register << 12 | distance
            self.emit(value)
        return struct.pack("<" + "I" * len(self.words), *self.words)


class FastServices:
    ADDRESS = 0x61000000
    NAMES = ("thread_slot_4", "s3eMemoryGetInt", "s3eMemorySetInt")

    def __init__(self, runtime):
        self.runtime = runtime
        self.entries = {}
        runtime.cpu.mem_map(self.ADDRESS, 4096)
        self.heap_address = self.ADDRESS + 0x800
        self.thread_address = self.ADDRESS + 0x804
        runtime.cpu.mem_write(self.heap_address, struct.pack("<I", runtime.current_heap))
        runtime.cpu.mem_write(self.thread_address, struct.pack("<I", runtime.threads.current_thread))
        for index, name in enumerate(self.NAMES):
            fallback = runtime.import_trap(name)
            code = ArmCode()
            if name != "thread_slot_4":
                code.emit(0xE3500000)  # cmp r0, #0
                code.branch(1, "fallback")
                if name == "s3eMemorySetInt":
                    code.emit(0xE3510007)  # cmp r1, #7
                    code.branch(8, "fallback")
            code.count(self.ADDRESS + 0x810 + index * 4)
            if name == "thread_slot_4":
                code.load(1, self.thread_address)
                code.emit(0xE5910000)  # ldr r0, [r1]
            elif name == "s3eMemoryGetInt":
                code.load(1, self.heap_address)
                code.emit(0xE5910000)  # ldr r0, [r1]
            else:
                code.load(2, self.heap_address)
                code.emit(0xE5821000)  # str r1, [r2]
                code.emit(0xE3A00000)  # mov r0, #0
            code.emit(0xE12FFF1E)  # bx lr
            code.labels["fallback"] = len(code.words)
            code.load(15, fallback)
            data = code.finish()
            if len(data) > 0x100:
                raise ValueError("Native SDK helper exceeds its slot")
            address = self.ADDRESS + index * 0x100
            runtime.cpu.mem_write(address, data)
            self.entries[name] = address

    @property
    def heap(self):
        return struct.unpack("<I", self.runtime.cpu.mem_read(self.heap_address, 4))[0]

    @heap.setter
    def heap(self, value):
        self.runtime.cpu.mem_write(self.heap_address, struct.pack("<I", value))

    def counts(self):
        values = struct.unpack("<3I", self.runtime.cpu.mem_read(self.ADDRESS + 0x810, 12))
        return dict(zip(self.NAMES, values))
