"""ARM runtime and bounded command-line startup diagnostic.

Unknown platform functions stop execution. No Android code runs on the host CPU,
and guest filesystem access is limited to supplied assets and isolated saves.
The original S3E image is mapped at its preferred link address. A rendered title
screen does not establish that full gameplay works.
"""
import argparse
import bisect
import collections
import json
import lzma
from pathlib import Path
import struct
import time
import uuid
import zipfile
import zlib

from unicorn import Uc, UcError, UC_ARCH_ARM, UC_MODE_ARM, UC_HOOK_CODE, UC_QUERY_TIMEOUT
from unicorn.arm_const import (
    UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3,
    UC_ARM_REG_SP, UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_CPSR,
)
from graphics import Graphics, GraphicsUnsupported, FrameLimitReached
from jni_bridge import JniBridge, JniUnsupported
from filesystem import FileSystem, FileUnsupported
from thread_bridge import ThreadBridge, ThreadUnsupported
from input_device import InputDevice


class UnsupportedImport(RuntimeError):
    pass


def read_icf(text):
    """Read global and Android settings, excluding device-specific overrides.

    This narrow parser covers the inspected APK's configuration; it is not
    intended as a general implementation of the SDK's configuration language.
    """
    values = {}
    active = True
    section = "s3e"
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("{"):
            active = line in ("{}", "{OS=ANDROID}", "{ID=ANY}", "{CLASS=ANY}")
        elif line.startswith("[") and line.endswith("]"):
            section = line[1:-1].casefold()
        elif active and "=" in line:
            key, value = line.split("=", 1)
            values[(section, key.strip().casefold())] = value.strip().strip('"')
    return values


class Image:
    def __init__(self, data):
        if data.startswith(b"\x5d"):
            data = lzma.decompress(data, format=lzma.FORMAT_ALONE,
                                   memlimit=128 * 1024 * 1024)
        if data[:4] != b"XE3U":
            raise ValueError("Expected an XE3U S3E module")
        self.data = data
        self.header_bytes, _, self.code_offset, self.code_bytes, self.memory_bytes = (
            struct.unpack_from("<5I", data, 12))
        self.base = struct.unpack_from("<I", data, 52)[0]
        if self.header_bytes != 76:
            raise ValueError("Only the inspected 1.6.11 header is supported")
        if self.code_offset + self.code_bytes != len(data):
            raise ValueError("Unexpected code extent")
        if not self.code_bytes <= self.memory_bytes <= 16 * 1024 * 1024:
            raise ValueError("Unexpected module memory size")
        self.code = data[self.code_offset:]
        self.imports = []
        self.references = []
        self.relocations = []
        offset = self.header_bytes
        while offset < self.code_offset:
            kind, length = struct.unpack_from("<II", data, offset)
            if length < 8 or offset + length > self.code_offset:
                raise ValueError("Invalid module section bounds")
            if kind == 0:
                count = struct.unpack_from("<H", data, offset + 8)[0]
                cursor = offset + 10
                for _ in range(count):
                    end = data.index(0, cursor, offset + length)
                    self.imports.append(data[cursor:end].decode("ascii"))
                    cursor = end + 1
            elif kind in (1, 2):
                count = struct.unpack_from("<I", data, offset + 8)[0]
                if offset + 12 + count * 4 > offset + length:
                    raise ValueError("Invalid relocation section")
                if kind == 2 and count:
                    raise ValueError("Unimplemented type-2 relocations")
                self.relocations.extend(struct.unpack_from(
                    "<" + "I" * count, data, offset + 12))
            elif kind == 4:
                count = struct.unpack_from("<I", data, offset + 8)[0]
                if offset + 12 + count * 6 > offset + length:
                    raise ValueError("Invalid import reference section")
                for i in range(count):
                    high, low, index = struct.unpack_from("<3H", data, offset + 12 + i * 6)
                    self.references.append((high << 16 | low, index))
            else:
                raise ValueError(f"Unimplemented module section: {kind}")
            offset += length
        if offset != self.code_offset:
            raise ValueError("Module sections do not meet code boundary")
        for target in self.relocations:
            if target + 4 > self.code_bytes:
                raise ValueError("Relocation outside module")
        for target, index in self.references:
            if target + 4 > self.code_bytes or index >= len(self.imports):
                raise ValueError("Import reference outside module/table")


class Runtime:
    API = 0x60000000
    STACK = 0x20000000
    STACK_BYTES = 1024 * 1024
    HEAP = 0x10000000
    HEAP_BYTES = 128 * 1024 * 1024

    def __init__(self, image, config=None, apk=None, obb=None, resources=None, saves=None,
                 frame_limit=1, frames_directory=None, taps=()):
        self.image = image
        self.api_names = list(image.imports)
        self.api_indices = {name: index for index, name in enumerate(self.api_names)}
        self.cpu = Uc(UC_ARCH_ARM, UC_MODE_ARM)
        rounded = (image.memory_bytes + 0xFFF) & ~0xFFF
        self.cpu.mem_map(image.base, rounded)
        self.cpu.mem_write(image.base, image.code)
        self.cpu.mem_map(self.STACK, self.STACK_BYTES)
        self.cpu.mem_map(self.HEAP, self.HEAP_BYTES)
        self.cpu.mem_map(self.API, 0x10000)
        self.cpu.mem_write(self.API, b"\x1e\xff\x2f\xe1" * (0x10000 // 4))
        for offset, index in image.references:
            self.cpu.mem_write(image.base + offset,
                               struct.pack("<I", self.API + index * 16))
        self.cpu.reg_write(UC_ARM_REG_CPSR, 0x10)
        self.cpu.reg_write(UC_ARM_REG_SP, self.STACK + self.STACK_BYTES - 16)
        self.cpu.reg_write(UC_ARM_REG_LR, self.API + 0xFFFC)
        self.cpu.hook_add(UC_HOOK_CODE, self.api_call,
                          begin=self.API, end=self.API + 0xFFFF)
        self.trace = collections.deque(maxlen=20000)
        self.trace_head = []
        self.api_call_count = 0
        self.counts = collections.Counter()
        self.status = "not_started"
        self.current_heap = 0
        self.heap_next = self.HEAP
        self.allocations = {}
        self.free_blocks = []
        self.user_memory_managers = {}
        self.config = config or {}
        self.empty_string = self.allocate(1)
        self.started_ns = time.monotonic_ns()
        self.callbacks = {}
        self.surface_width = 800
        self.surface_height = 480
        self.surface_format = 0x447  # Four-byte SDK pixel format, supported by the supplied loader.
        self.surface_pitch = self.surface_width * 4
        self.surface_buffer = self.allocate(self.surface_pitch * self.surface_height)
        self.graphics = None
        self.pointer_properties = {}
        self.keyboard_properties = {}
        self.device_flags = {}
        self.jni = None
        self.filesystem = FileSystem(self, apk, obb, resources, saves)
        self.threads = ThreadBridge(self)
        if frame_limit is not None and not 1 <= frame_limit <= 10000:
            raise ValueError("Invalid frame budget")
        self.frame_limit = frame_limit
        self.frames_directory = frames_directory or (Path(__file__).resolve().parents[1] /
                                                       "reports/frames" / uuid.uuid4().hex[:12])
        self.quit_requested = False
        self.window = None
        self.input = InputDevice(self, taps)
        self.callback_stack = []
        self.callback_returns = 0
        self.api_call_limit = 1_000_000

    def jump(self, address):
        flags = self.cpu.reg_read(UC_ARM_REG_CPSR)
        self.cpu.reg_write(UC_ARM_REG_CPSR, (flags & ~0x20) | (0x20 if address & 1 else 0))
        self.cpu.reg_write(UC_ARM_REG_PC, address & ~1)

    def invoke_callbacks(self, requests, result=0):
        if not requests:
            self.cpu.reg_write(UC_ARM_REG_R0, result)
            return
        state = {"remaining": collections.deque(requests),
                 "payloads": {request[1][0] for request in requests if request[1][0]},
                 "lr": self.cpu.reg_read(UC_ARM_REG_LR),
                 "sp": self.cpu.reg_read(UC_ARM_REG_SP), "result": result}
        self.callback_stack.append(state)
        self.next_callback()

    def next_callback(self):
        state = self.callback_stack[-1]
        if self.cpu.reg_read(UC_ARM_REG_SP) != state["sp"]:
            raise RuntimeError("Guest callback did not restore its stack")
        if state["remaining"]:
            function, arguments = state["remaining"].popleft()
            for index, register in enumerate((UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3)):
                self.cpu.reg_write(register, arguments[index] if index < len(arguments) else 0)
            self.cpu.reg_write(UC_ARM_REG_LR, self.import_trap("__resume_callback"))
            self.jump(function)
        else:
            self.callback_stack.pop()
            for pointer in state["payloads"]:
                self.release(pointer)
            self.cpu.reg_write(UC_ARM_REG_R0, state["result"])
            self.cpu.reg_write(UC_ARM_REG_LR, state["lr"])
            self.jump(state["lr"])

    def argument(self, index):
        if index < 4:
            return self.cpu.reg_read((UC_ARM_REG_R0, UC_ARM_REG_R1,
                                      UC_ARM_REG_R2, UC_ARM_REG_R3)[index])
        pointer = self.cpu.reg_read(UC_ARM_REG_SP) + (index - 4) * 4
        return struct.unpack("<I", self.cpu.mem_read(pointer, 4))[0]

    def import_trap(self, name):
        if name not in self.api_indices:
            if len(self.api_names) >= 4095:
                raise RuntimeError("Import trampoline budget exceeded")
            self.api_indices[name] = len(self.api_names)
            self.api_names.append(name)
        return self.API + self.api_indices[name] * 16

    def get_graphics(self):
        if self.graphics is None:
            self.graphics = Graphics(self)
        return self.graphics

    def allocate(self, size):
        # Bounded 16-byte-aligned allocator. Freed ranges coalesce and can be
        # reused during long scene loads; reused memory follows malloc semantics.
        size = max(size, 1)
        aligned = (size + 15) & ~15
        for index, (address, length) in enumerate(self.free_blocks):
            if length >= aligned:
                if length == aligned:
                    self.free_blocks.pop(index)
                else:
                    self.free_blocks[index] = (address + aligned, length - aligned)
                self.allocations[address] = size
                return address
        end = self.heap_next + aligned
        if end > self.HEAP + self.HEAP_BYTES:
            return 0
        address = self.heap_next
        self.heap_next = end
        self.allocations[address] = size
        return address

    def release_range(self, address, length):
        if not length:
            return
        index = bisect.bisect_left(self.free_blocks, (address, 0))
        if index and self.free_blocks[index - 1][0] + self.free_blocks[index - 1][1] == address:
            previous, previous_length = self.free_blocks.pop(index - 1)
            index -= 1
            address, length = previous, length + previous_length
        if index < len(self.free_blocks) and address + length == self.free_blocks[index][0]:
            _, following_length = self.free_blocks.pop(index)
            length += following_length
        self.free_blocks.insert(index, (address, length))

    def release(self, address):
        if not address:
            return
        if address not in self.allocations:
            raise ValueError("Free of an unknown guest allocation")
        size = self.allocations.pop(address)
        self.release_range(address, (size + 15) & ~15)

    def string(self, address):
        if address == 0:
            return None
        output = bytearray()
        for offset in range(4096):
            byte = self.cpu.mem_read(address + offset, 1)[0]
            if byte == 0:
                return output.decode("utf-8", errors="replace")
            output.append(byte)
        raise ValueError("Unterminated guest string")

    def api_call(self, cpu, address, size, _):
        # Complete the guest return in the hook itself. A CPU time slice can
        # end after a slow host service and before the trampoline's BX LR;
        # leaving PC there would execute the service twice on resume.
        self.api_dispatch(cpu, address, size, _)
        if cpu.reg_read(UC_ARM_REG_PC) == address:
            self.jump(cpu.reg_read(UC_ARM_REG_LR))

    def api_dispatch(self, cpu, address, size, _):
        index, within = divmod(address - self.API, 16)
        if within or index >= len(self.api_names):
            raise RuntimeError(f"Unexpected API address {address:#x}")
        name = self.api_names[index]
        if name == "__resume_callback":
            self.callback_returns += 1
            self.next_callback()
            return
        args = [cpu.reg_read(r) for r in
                (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3)]
        record = {"function": name, "args": args,
                  "caller": cpu.reg_read(UC_ARM_REG_LR) - self.image.base}
        self.trace.append(record)
        if len(self.trace_head) < 2000:
            self.trace_head.append(record)
        self.counts[name] += 1
        self.api_call_count += 1
        if self.api_call_limit is not None and self.api_call_count > self.api_call_limit:
            raise RuntimeError("API call budget exceeded")
        if name.startswith("thread_slot_"):
            try:
                result = self.threads.dispatch(name)
            except ThreadUnsupported as exc:
                self.status = "unsupported_import"
                raise UnsupportedImport(str(exc)) from exc
            if result is not None:
                cpu.reg_write(UC_ARM_REG_R0, result & 0xFFFFFFFF)
            return
        if name.startswith(("s3eFile", "vfs_slot_")):
            try:
                result = self.filesystem.dispatch(name)
            except FileUnsupported as exc:
                self.status = "unsupported_import"
                raise UnsupportedImport(str(exc)) from exc
            if result is not None:
                cpu.reg_write(UC_ARM_REG_R0, result & 0xFFFFFFFF)
                if name == "s3eFileGetFileInt":
                    cpu.reg_write(UC_ARM_REG_R1, (result >> 32) & 0xFFFFFFFF)
            return
        if name == "s3eAndroidJNIGetVM" or name.startswith(("jni_", "jvm_")):
            if self.jni is None:
                self.jni = JniBridge(self)
            try:
                result = self.jni.vm if name == "s3eAndroidJNIGetVM" else self.jni.dispatch(name)
            except JniUnsupported as exc:
                self.status = "unsupported_import"
                raise UnsupportedImport(str(exc)) from exc
            if result is not None:
                cpu.reg_write(UC_ARM_REG_R0, result)
            return
        if name in ("s3eDebugAssertShow", "s3eDebugErrorShow"):
            message = self.string(args[1])
            record["message"] = message
            raise RuntimeError("Guest assertion/error: " + message)
        if name.startswith(("gl", "egl")):
            try:
                result = self.get_graphics().dispatch(name)
            except GraphicsUnsupported as exc:
                self.status = "unsupported_import"
                raise UnsupportedImport(str(exc)) from exc
            if result is not None:
                cpu.reg_write(UC_ARM_REG_R0, int(result) & 0xFFFFFFFF)
            return
        if name == "s3eGLGetInt" and args[0] in (0, 2):
            self.get_graphics()
            cpu.reg_write(UC_ARM_REG_R0, 0x200 if args[0] == 0 else 1)
            return
        if name == "s3eGLGetNativeWindow":
            # A virtual native-window token, translated to a real pbuffer by
            # eglCreateWindowSurface in the offscreen development backend.
            self.get_graphics()
            cpu.reg_write(UC_ARM_REG_R0, 1)
            return
        if name == "s3eSurfaceShow":
            self.get_graphics().capture_frame(self.get_graphics().frames + 1)
            self.get_graphics().present()
            cpu.reg_write(UC_ARM_REG_R0, 0)
            return
        if name == "s3eDeviceYield":
            # The high bit is an SDK polling flag; the lower bits are delay ms.
            milliseconds = args[0] & 0x7FFFFFFF
            if milliseconds > 5000:
                raise UnsupportedImport("Device yield exceeds the bounded probe's wait budget")
            time.sleep(milliseconds / 1000)
            if self.window:
                self.window.pump()
            self.invoke_callbacks(self.input.poll_device() + self.input.poll_keyboard())
            return
        if name == "s3eDeviceCheckQuitRequest":
            cpu.reg_write(UC_ARM_REG_R0, int(self.quit_requested))
            return
        if name == "s3eDeviceRequestQuit":
            self.quit_requested = True
            cpu.reg_write(UC_ARM_REG_R0, 0)
            return
        if name == "s3eDeviceAbort":
            raise RuntimeError("Guest requested process abort; see preceding debug messages")
        if name == "s3eAudioGetInt" and args[0] <= 7:
            # Property 6 is audio availability. The original SDK returns zero
            # for all these queries when its audio capability is absent.
            record["result"] = "audio_device_unavailable"
            cpu.reg_write(UC_ARM_REG_R0, 0)
            return
        if name == "s3eSoundGetInt" and args[0] <= 7:
            value = 0 if args[0] in (3, 5) else 0xFFFFFFFF
            record["result"] = "audio_device_unavailable"
            cpu.reg_write(UC_ARM_REG_R0, value)
            return
        if name == "s3eAudioIsCodecSupported":
            cpu.reg_write(UC_ARM_REG_R0, 0)
            return
        if name in ("s3eAudioSetInt", "s3eAudioPlay", "s3eAudioStop", "s3eAudioPause", "s3eAudioResume",
                    "s3eSoundChannelSetInt", "s3eSoundChannelPlay", "s3eSoundChannelStop"):
            record["result"] = "error_audio_device_unavailable"
            cpu.reg_write(UC_ARM_REG_R0, 1)
            return
        if name == "s3eSoundGetFreeChannel":
            cpu.reg_write(UC_ARM_REG_R0, 0xFFFFFFFF)
            return
        if name in ("s3eAccelerometerStart", "s3eAccelerometerStop"):
            available = self.input.accelerometer_available
            self.input.accelerometer_active = available and name.endswith("Start")
            record["result"] = "virtual_sensor" if available else "sensor_unavailable"
            cpu.reg_write(UC_ARM_REG_R0, 0 if available else 1)
            return
        if name in ("s3eAccelerometerGetX", "s3eAccelerometerGetY", "s3eAccelerometerGetZ"):
            cpu.reg_write(UC_ARM_REG_R0, self.input.acceleration(name[-1]) & 0xFFFFFFFF)
            return
        if name == "s3eAccelerometerGetInt" and args[0] == 0:
            cpu.reg_write(UC_ARM_REG_R0, int(self.input.accelerometer_available))
            return
        if name in ("s3ePointerGetX", "s3ePointerGetY"):
            cpu.reg_write(UC_ARM_REG_R0, (self.input.x if name.endswith("X") else self.input.y) & 0xFFFFFFFF)
            return
        if name == "s3ePointerUpdate":
            if self.window:
                self.window.pump()
            self.invoke_callbacks(self.input.poll_pointer())
            return
        if name == "s3eKeyboardGetState":
            cpu.reg_write(UC_ARM_REG_R0, self.input.key_state(args[0]))
            return
        if name == "s3eCompressionDecomp" and self.argument(4) == 2:
            source, compressed_bytes, destination_pointer, size_pointer = args
            destination = struct.unpack("<I", cpu.mem_read(destination_pointer, 4))[0]
            capacity = struct.unpack("<I", cpu.mem_read(size_pointer, 4))[0]
            if not destination:
                raise UnsupportedImport("Compression output needs a guest allocator callback")
            if capacity > 64 * 1024 * 1024 or compressed_bytes > 64 * 1024 * 1024:
                raise ValueError("Compression transfer exceeds budget")
            decoder = zlib.decompressobj()
            content = decoder.decompress(bytes(cpu.mem_read(source, compressed_bytes)), capacity + 1)
            if not decoder.eof or decoder.unused_data or len(content) > capacity:
                raise ValueError("Incomplete/oversized zlib stream")
            cpu.mem_write(destination, content)
            cpu.mem_write(size_pointer, struct.pack("<I", len(content)))
            record["decoded_bytes"] = len(content)
            record["checksum_validated"] = True
            cpu.reg_write(UC_ARM_REG_R0, 0)
            return
        if name == "s3ePointerSetInt" and 5 <= args[0] <= 12:
            # SDK-owned pointer transform/state fields. Property 5 is byte-sized;
            # the remaining fields are full words in the original runtime.
            self.pointer_properties[args[0]] = args[1] & (0xFF if args[0] == 5 else 0xFFFFFFFF)
            cpu.reg_write(UC_ARM_REG_R0, 0)
            return
        if name == "s3eKeyboardSetInt" and args[0] in (4, 6):
            valid = args[0] != 4 or args[1] <= 1
            if valid:
                self.keyboard_properties[args[0]] = bool(args[1])
            cpu.reg_write(UC_ARM_REG_R0, 0 if valid else 1)
            return
        if name in ("s3eDebugOutputString", "s3eDebugTraceLine"):
            record["message"] = self.string(args[0])
            print(f"{name}: {record['message']}", flush=True)
            cpu.reg_write(UC_ARM_REG_R0, 0)
            return
        if name == "s3eExtGetHash":
            if args[0] == 3423291944 and args[2] == 80:
                functions = [self.import_trap("thread_slot_" + str(index)) for index in range(20)]
                cpu.mem_write(args[1], struct.pack("<20I", *functions))
                record["result"] = "provided_single_thread_sync_services"
                cpu.reg_write(UC_ARM_REG_R0, 0)
                return
            if args[0] == 2750204658 and args[2] == 4:
                cpu.mem_write(args[1], struct.pack("<I", self.import_trap("s3eAndroidJNIGetVM")))
                record["result"] = "provided_s3eAndroidJNI"
                cpu.reg_write(UC_ARM_REG_R0, 0)
                return
            if args[0] == 401902751 and args[2] == 16:
                functions = [self.import_trap("vfs_slot_" + str(index)) for index in range(4)]
                cpu.mem_write(args[1], struct.pack("<4I", *functions))
                record["result"] = "provided_vfs_research_interface"
                cpu.reg_write(UC_ARM_REG_R0, 0)
                return
            # The SDK returns S3E_RESULT_ERROR (1) when an extension is absent.
            # Leave the caller's output table untouched, matching that failure.
            record["result"] = "extension_not_available"
            cpu.reg_write(UC_ARM_REG_R0, 1)
            return
        if name == "s3eMemoryGetInt" and args[0] == 0:
            # SDK property 0 reads the selected heap index.
            cpu.reg_write(UC_ARM_REG_R0, self.current_heap)
            return
        if name == "s3eMemorySetInt" and args[0] == 0 and args[1] <= 7:
            self.current_heap = args[1]
            cpu.reg_write(UC_ARM_REG_R0, 0)
            return
        if name == "s3eMemorySetUserMemMgr":
            # Base allocation APIs bypass this table. Keep the registered
            # callback addresses for future platform-owned guest allocations.
            if not args[0]:
                self.user_memory_managers.pop(self.current_heap, None)
                result = 0
            else:
                callbacks = struct.unpack("<3I", cpu.mem_read(args[0], 12))
                result = 0 if all(callbacks) else 1
                if result == 0:
                    self.user_memory_managers[self.current_heap] = callbacks
            cpu.reg_write(UC_ARM_REG_R0, result)
            return
        if name in ("s3eConfigGetString", "s3eConfigGetInt"):
            section, key = self.string(args[0]), self.string(args[1])
            record["section"], record["key"] = section, key
            value = self.config.get(((section or "s3e").casefold(), key.casefold()))
            result = 1
            if value is not None and args[2]:
                if name == "s3eConfigGetString":
                    encoded = value.encode() + b"\0"
                    if len(encoded) > 128:
                        raise ValueError("ICF string exceeds SDK buffer limit")
                    cpu.mem_write(args[2], encoded)
                    result = 0
                else:
                    try:
                        number = int(value, 0)
                    except ValueError:
                        pass
                    else:
                        cpu.mem_write(args[2], struct.pack("<I", number & 0xFFFFFFFF))
                        result = 0
            record["result"] = result
            record["value"] = value
            cpu.reg_write(UC_ARM_REG_R0, result)
            return
        if name == "s3eDeviceGetString" and args[0] in (31, 36, 37):
            # Optional startup date/time text: the guest checks for an empty
            # result and has a fallback. No device text is available here.
            record["result"] = "property_not_available"
            cpu.reg_write(UC_ARM_REG_R0, self.empty_string)
            return
        if name == "s3eDeviceGetInt" and args[0] in (0, 1, 8):
            # Values verified against the supplied Android SDK runtime:
            # logical OS ANDROID = 18; ARMv5TEJ architecture = 4. These describe
            # the guest compatibility environment, not the host OS/CPU.
            value = {0: 18, 1: 0x3E80, 8: 4}[args[0]]
            record["result"] = value
            cpu.reg_write(UC_ARM_REG_R0, value)
            return
        if name == "s3eDeviceSetInt" and args[0] == 15:
            # The supplied SDK's 0x79a80 routine accepts only property 15,
            # stores its low byte in device state, and returns success. The
            # original game writes zero while returning from its entry point.
            self.device_flags[15] = args[1] & 0xFF
            cpu.reg_write(UC_ARM_REG_R0, 0)
            return
        if name in ("s3eTimerGetMs", "s3eTimerGetUTC"):
            value = ((time.monotonic_ns() - self.started_ns) // 1_000_000
                     if name == "s3eTimerGetMs" else time.time_ns() // 1_000_000)
            cpu.reg_write(UC_ARM_REG_R0, value & 0xFFFFFFFF)
            cpu.reg_write(UC_ARM_REG_R1, value >> 32)
            return
        if name == "s3eTimerGetLocaltimeOffset":
            # The research runtime deliberately uses UTC.
            cpu.reg_write(UC_ARM_REG_R0, 0)
            return
        if name == "s3eSurfaceGetInt" and args[0] in (0, 1, 2, 3, 4, 5, 11):
            # Virtual display dimensions for a headless startup probe. No
            # framebuffer or graphics context is created by this query.
            # Property 11 is the physical surface orientation (0..3), used
            # by IwGx's width/height rotation logic, not a DPI measurement.
            value = {0: self.surface_width, 1: self.surface_height,
                     2: self.surface_pitch, 3: self.surface_format,
                     4: self.surface_width, 5: self.surface_height, 11: 0}[args[0]]
            record["result"] = value
            cpu.reg_write(UC_ARM_REG_R0, value)
            return
        if name == "s3eSurfacePtr":
            cpu.reg_write(UC_ARM_REG_R0, self.surface_buffer)
            return
        if name == "s3eSurfaceSetup" and args[0] in (0x422, 0x426, 0x435, 0x447):
            pixel_format, pitch, buffer, rotation = args
            pixel_bytes = (pixel_format & 0x70) >> 4
            if rotation not in (0, 4):
                raise UnsupportedImport("Rotated software surfaces are not implemented")
            pitch = pitch or self.surface_width * pixel_bytes
            result = 1
            if pitch >= self.surface_width * pixel_bytes:
                required = pitch * self.surface_height
                if required > 32 * 1024 * 1024:
                    raise ValueError("Surface exceeds memory budget")
                buffer = buffer or self.allocate(required)
                if buffer:
                    cpu.mem_read(buffer, required)
                    self.surface_pitch = pitch
                    self.surface_format = pixel_format
                    self.surface_buffer = buffer
                    result = 0
            cpu.reg_write(UC_ARM_REG_R0, result)
            return
        families = ("s3eSurface", "s3eDevice", "s3ePointer", "s3eKeyboard", "s3eGL")
        for family in families:
            if name == family + "Register":
                event, callback, user = args[:3]
                key = (family, event, callback)
                result = 1 if not callback or key in self.callbacks else 0
                if result == 0:
                    self.callbacks[key] = user
                record["result"] = result
                cpu.reg_write(UC_ARM_REG_R0, result)
                return
            if name == family + "UnRegister":
                key = (family, args[0], args[1])
                result = 0 if key in self.callbacks else 1
                self.callbacks.pop(key, None)
                cpu.reg_write(UC_ARM_REG_R0, result)
                return
        if name == "s3eMallocBase":
            cpu.reg_write(UC_ARM_REG_R0, self.allocate(args[0]))
            return
        if name == "s3eFreeBase":
            self.release(args[0])
            return
        if name == "s3eReallocBase":
            pointer, size = args[:2]
            if pointer and pointer not in self.allocations:
                raise ValueError("Reallocation of an unknown guest allocation")
            if size == 0:
                self.release(pointer)
                result = 0
            elif pointer and size <= ((self.allocations[pointer] + 15) & ~15):
                capacity = (self.allocations[pointer] + 15) & ~15
                new_capacity = (size + 15) & ~15
                self.allocations[pointer] = size
                self.release_range(pointer + new_capacity, capacity - new_capacity)
                result = pointer
            else:
                result = self.allocate(size)
                if pointer and result:
                    count = min(size, self.allocations[pointer])
                    cpu.mem_write(result, bytes(cpu.mem_read(pointer, count)))
                    self.release(pointer)
            cpu.reg_write(UC_ARM_REG_R0, result)
            return
        self.status = "unsupported_import"
        strings = []
        for argument in args:
            try:
                strings.append(self.string(argument))
            except (UcError, ValueError):
                strings.append(None)
        record["argument_strings"] = strings
        raise UnsupportedImport(f"{name}({', '.join(hex(a) for a in args)})")

    def run(self, instructions=100_000_000, seconds=15):
        if not 0 < instructions <= 10_000_000_000 or not 0 < seconds <= 60:
            raise ValueError("Invalid bounded execution budget")
        error = None
        self.status = "running"
        try:
            self.cpu.emu_start(self.image.base, self.API + 0xFFFC,
                               timeout=int(seconds * 1_000_000), count=instructions)
            if self.cpu.reg_read(UC_ARM_REG_PC) == self.API + 0xFFFC:
                self.status = "entry_returned"
            elif self.cpu.query(UC_QUERY_TIMEOUT):
                self.status = "time_budget_exhausted"
            else:
                self.status = "instruction_budget_exhausted"
        except UnsupportedImport as exc:
            error = str(exc)
        except FrameLimitReached as exc:
            self.status = "frame_limit_reached"
            error = None
        except (UcError, RuntimeError, ValueError) as exc:
            self.status = "execution_error"
            error = str(exc)
        return self.finish(error)

    def run_interactive(self):
        """Run resumable CPU slices so the native window always gets events."""
        if self.window is None:
            raise RuntimeError("Interactive mode requires a native window")
        self.api_call_limit = None
        self.status = "running"
        address = self.image.base
        error = None
        try:
            while True:
                self.window.pump()
                if self.quit_requested:
                    self.status = "user_closed"
                    break
                self.cpu.emu_start(address, self.API + 0xFFFC, timeout=20_000)
                pc = self.cpu.reg_read(UC_ARM_REG_PC)
                if pc == self.API + 0xFFFC:
                    self.status = "entry_returned"
                    break
                address = pc | (1 if self.cpu.reg_read(UC_ARM_REG_CPSR) & 0x20 else 0)
                if self.window.minimized:
                    time.sleep(0.02)
        except UnsupportedImport as exc:
            self.status = "unsupported_import"
            error = str(exc)
        except FrameLimitReached:
            self.status = "frame_limit_reached"
        except (UcError, RuntimeError, ValueError) as exc:
            self.status = "execution_error"
            error = str(exc)
        return self.finish(error)

    def finish(self, error=None):
        head_ids = {id(record) for record in self.trace_head}
        trace = self.trace_head + [record for record in self.trace if id(record) not in head_ids]
        report = {"status": self.status, "error": error,
                "pc": self.cpu.reg_read(UC_ARM_REG_PC),
                "lr": self.cpu.reg_read(UC_ARM_REG_LR),
                "imports": len(self.image.imports),
                "import_references": len(self.image.references),
                "relocations": len(self.image.relocations),
                "api_counts": dict(self.counts), "trace": trace,
                "trace_omitted_calls": self.api_call_count - len(trace),
                "memory": {"heap_address_span": self.heap_next - self.HEAP,
                           "active_allocated_bytes": sum(self.allocations.values()),
                           "active_allocations": len(self.allocations)},
                "playable": False, "windows_executable_built": False}
        report["audio"] = {"available": False, "playback_tested": False,
                           "reason": "Audio playback backend is not implemented"}
        report["input"] = {"events_dispatched": self.input.events_dispatched,
                           "callbacks_completed": self.callback_returns,
                           "pointer": [self.input.x, self.input.y],
                           "registered_callbacks": [{"family": family, "event": event,
                                                     "function": function, "user": user}
                                                    for (family, event, function), user in self.callbacks.items()]}
        if self.graphics:
            report["graphics"] = dict(self.graphics.info, calls=self.graphics.calls,
                                      frames=self.graphics.frames, draws=self.graphics.draws,
                                      captures=self.graphics.captures,
                                      pending_gl_errors=self.graphics.error_queue,
                                      invalid_texture_call=self.graphics.invalid_texture_call,
                                      legacy_texture_filter_calls=self.graphics.texture_parameter_compatibility_calls)
            self.graphics.close()
        report["filesystem"] = {"opened": self.filesystem.opened_paths,
                                "mounts": list(self.filesystem.mounts)}
        self.filesystem.close()
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("module", type=Path, help="APK or original/decompressed S3E file")
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--instructions", type=int, default=100_000_000)
    parser.add_argument("--seconds", type=float, default=15)
    parser.add_argument("--obb", type=Path)
    parser.add_argument("--frames", type=int, default=1)
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--tap", action="append", default=[], metavar="FRAME:X:Y",
                        help="Inject a pointer press/release over two frames")
    parser.add_argument("--resources", type=Path,
                        default=Path(__file__).resolve().parents[1] / "local-data/resources")
    parser.add_argument("--saves", type=Path,
                        default=Path(__file__).resolve().parents[1] / "local-data/saves")
    args = parser.parse_args()
    config = {}
    if args.module.suffix.lower() == ".apk":
        with zipfile.ZipFile(args.module) as apk:
            data = apk.read("assets/The Sims 3.s3e")
            for name in ("assets/s3e.icf", "assets/app.icf"):
                if name in apk.namelist():
                    config.update(read_icf(apk.read(name).decode("utf-8")))
    else:
        data = args.module.read_bytes()
    taps = [tuple(int(value) for value in spec.split(":")) for spec in args.tap]
    if any(len(tap) != 3 or not 1 <= tap[0] <= 10000 for tap in taps):
        parser.error("Invalid --tap: expected FRAME:X:Y")
    report = Runtime(Image(data), config, args.module if args.module.suffix.lower() == ".apk" else None,
                     args.obb, args.resources, args.saves, args.frames, taps=taps).run(args.instructions, args.seconds)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    printed = ({k: v for k, v in report.items() if k != "trace"} if args.verbose else
               {k: report[k] for k in ("status", "error", "memory", "graphics", "filesystem") if k in report})
    print(json.dumps(printed, indent=2))
    return 0 if report["status"] in ("entry_returned", "frame_limit_reached") else 2


if __name__ == "__main__":
    raise SystemExit(main())
