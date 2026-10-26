"""Pinned guest RAM with checked host views for synchronous graphics transfers."""
import ctypes as C
import mmap


class GuestMemory:
    def __init__(self, cpu):
        self.cpu = cpu
        self.regions = []

    def map(self, address, size):
        storage = mmap.mmap(-1, size)
        owner = (C.c_ubyte * size).from_buffer(storage)
        host = C.addressof(owner)
        self.cpu.mem_map_ptr(address, size, 7, host)
        self.regions.append((address, size, host, storage, owner))

    def pointer(self, address, size):
        if not 0 <= size <= 64 * 1024 * 1024:
            raise ValueError("Guest transfer exceeds memory budget")
        for base, length, host, _, _ in self.regions:
            offset = address - base
            if 0 <= offset < length and size <= length - offset:
                return C.c_void_p(host + offset)
        raise ValueError("Host transfer points outside mapped guest RAM")

    def string(self, address, limit=4096):
        for base, length, host, storage, _ in self.regions:
            offset = address - base
            if 0 <= offset < length:
                end = storage.find(b"\0", offset, min(offset + limit, length))
                if end < 0:
                    raise ValueError("Unterminated guest string")
                return storage[offset:end].decode("utf-8", errors="replace")
        raise ValueError("String points outside mapped guest RAM")


class ArmRegisters:
    """Reuse batch buffers with the checksum-pinned Unicorn 2.1.4 C ABI."""
    def __init__(self, cpu, registers):
        from unicorn.unicorn_py3.unicorn import uclib
        self.cpu = cpu
        self.function = uclib.uc_reg_read_batch
        self.identifiers = (C.c_int * len(registers))(*registers)
        self.values = (C.c_uint64 * len(registers))()
        self.pointers = (C.c_void_p * len(registers))(*(
            C.addressof(self.values) + index * C.sizeof(C.c_uint64)
            for index in range(len(registers))))

    def read(self):
        from unicorn import UcError
        status = self.function(self.cpu._uch, self.identifiers, self.pointers, len(self.values))
        if status:
            raise UcError(status)
        return tuple(self.values)
