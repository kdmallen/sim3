"""The inspected game's s3eOSReadString extension, backed by a host text prompt."""


class TextInputUnsupported(RuntimeError):
    pass


class TextInput:
    HASH = 2682513989

    def __init__(self, runtime):
        self.runtime = runtime
        self.buffer = 0
        self.requests = 0

    def dispatch(self, name):
        slot = int(name.removeprefix("osread_slot_"))
        if slot not in range(4):
            raise TextInputUnsupported("Unknown OSReadString slot")
        a = self.runtime.argument
        prompt = self.runtime.string(a(0)) or "Enter text"
        # Native registration at 0x26208 supplies [0x261f0, 0x26204] twice.
        # 0x261f0 inserts an empty default; 0x26204 forwards all three args.
        initial = (self.runtime.string(a(1)) or "") if slot & 1 else ""
        flags = a(2) if slot & 1 else a(1)
        if self.runtime.window is None:
            raise TextInputUnsupported("Text entry requires a native game window")
        value = self.runtime.window.read_text(prompt, initial)
        self.requests += 1
        record = self.runtime.trace[-1]
        record.update(operation="text_entry", flags=flags, cancelled=value is None)
        if self.buffer:
            self.runtime.release(self.buffer)
            self.buffer = 0
        if value is None:
            return 0
        encoded = value.encode("utf-8") + b"\0"
        self.buffer = self.runtime.allocate(len(encoded))
        if not self.buffer:
            raise RuntimeError("Text-entry buffer allocation failed")
        self.runtime.cpu.mem_write(self.buffer, encoded)
        return self.buffer
