"""Regression checks across the guest/host ABI, independent of game assets."""
import ctypes
from pathlib import Path
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from boot_trace import Runtime
from unicorn.arm_const import (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2,
                               UC_ARM_REG_R3, UC_ARM_REG_SP, UC_ARM_REG_LR, UC_ARM_REG_PC)


class RuntimeServices(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        apk = self.root / "synthetic.apk"
        obb = self.root / "synthetic.obb"
        with zipfile.ZipFile(apk, "w") as archive:
            archive.writestr("assets/config.txt", b"test configuration")
        with zipfile.ZipFile(obb, "w") as archive:
            archive.writestr("assets/LowRes/example.bin", b"0123456789")
        image = SimpleNamespace(base=0x4A000000, memory_bytes=4096,
                                code=b"\x1e\xff\x2f\xe1", imports=[], references=[])
        self.runtime = Runtime(image, apk=apk, obb=obb, saves=self.root / "saves")

    def tearDown(self):
        if self.runtime.graphics:
            self.runtime.graphics.close()
        self.runtime.filesystem.close()
        self.temporary.cleanup()

    def data(self, value):
        if isinstance(value, str):
            value = value.encode() + b"\0"
        pointer = self.runtime.allocate(len(value))
        self.runtime.cpu.mem_write(pointer, value)
        return pointer

    def call(self, name, *arguments, address=None, return_address=None):
        cpu = self.runtime.cpu
        registers = (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3)
        for index, register in enumerate(registers):
            cpu.reg_write(register, arguments[index] & 0xFFFFFFFF if index < len(arguments) else 0)
        if len(arguments) > 4:
            cpu.mem_write(cpu.reg_read(UC_ARM_REG_SP), struct.pack(
                "<" + "I" * (len(arguments) - 4), *[a & 0xFFFFFFFF for a in arguments[4:]]))
        end = return_address or self.runtime.image.base
        cpu.reg_write(UC_ARM_REG_LR, end)
        cpu.emu_start(address or self.runtime.import_trap(name), end, count=100)
        return cpu.reg_read(UC_ARM_REG_R0)

    def test_zip_mount_normalizes_windows_separators_and_reads(self):
        self.assertEqual(self.call("vfs_slot_2", self.data("/game/content.obb"),
                                  self.data("Zipmount://"), 0), 0)
        handle = self.call("s3eFileOpen", self.data(r"Zipmount:\\assets\LowRes\example.bin"), self.data("rb"))
        self.assertNotEqual(handle, 0)
        self.assertEqual(self.call("s3eFileSeek", handle, 3, 0), 0)
        output = self.data(bytes(4))
        self.assertEqual(self.call("s3eFileRead", output, 2, 2, handle), 2)
        self.assertEqual(bytes(self.runtime.cpu.mem_read(output, 4)), b"3456")
        self.assertEqual(self.call("s3eFileClose", handle), 0)
        self.assertEqual(self.call("s3eFileClose", 0), 1)

    def test_save_roundtrip_and_parent_path_rejection(self):
        name, mode = self.data("ram://slot.dat"), self.data("wb")
        handle = self.call("s3eFileOpen", name, mode)
        self.assertNotEqual(handle, 0)
        payload = b"saved state\x00\xff"
        self.assertEqual(self.call("s3eFileWrite", self.data(payload), 1, len(payload), handle), len(payload))
        self.call("s3eFileClose", handle)
        handle = self.call("s3eFileOpen", name, self.data("rb"))
        output = self.data(bytes(len(payload)))
        self.assertEqual(self.call("s3eFileRead", output, 1, len(payload), handle), len(payload))
        self.assertEqual(bytes(self.runtime.cpu.mem_read(output, len(payload))), payload)
        self.assertEqual((self.root / "saves/slot.dat").read_bytes(), payload)
        with self.assertRaises(ValueError):
            self.call("s3eFileOpen", self.data("ram://../escape.dat"), mode)
        self.assertFalse((self.root / "escape.dat").exists())

    def test_file_metadata_returns_both_arm_result_registers(self):
        target = self.root / "saves/metadata.dat"
        target.write_bytes(b"file")
        path = self.data("/saves/metadata.dat")
        self.assertEqual(self.call("s3eFileGetFileInt", path, 1), 0)  # File, not directory.
        self.assertEqual(self.call("s3eFileGetFileInt", path, 4), 4)
        self.assertEqual(self.runtime.cpu.reg_read(UC_ARM_REG_R1), 0)
        low = self.call("s3eFileGetFileInt", path, 5)
        high = self.runtime.cpu.reg_read(UC_ARM_REG_R1)
        self.assertEqual(low | high << 32, target.stat().st_mtime_ns // 1_000_000)

    def test_jni_vm_getenv_and_game_save_path(self):
        vm = self.call("s3eAndroidJNIGetVM")
        table = struct.unpack("<I", self.runtime.cpu.mem_read(vm, 4))[0]
        get_env = struct.unpack("<I", self.runtime.cpu.mem_read(table + 6 * 4, 4))[0]
        output = self.data(bytes(4))
        self.assertEqual(self.call(None, vm, output, 0x00010006, address=get_env), 0)
        env = struct.unpack("<I", self.runtime.cpu.mem_read(output, 4))[0]
        clazz = self.call("jni_FindClass", env, self.data("com/ea/thesims3/MainActivity"))
        field = self.call("jni_GetStaticFieldID", env, clazz, self.data("m_SaveDataPath"),
                          self.data("Ljava/lang/String;"))
        string = self.call("jni_GetStaticObjectField", env, clazz, field)
        chars = self.call("jni_GetStringUTFChars", env, string, 0)
        self.assertEqual(self.runtime.string(chars), "/saves")

    def test_mutex_ownership_and_recursive_depth(self):
        mutex = self.call("thread_slot_9")
        self.assertEqual(self.call("thread_slot_12", mutex, 0), 0)
        self.assertEqual(self.call("thread_slot_12", mutex, 0), 1)
        self.assertEqual(self.call("thread_slot_13", mutex), 0)
        self.assertEqual(self.call("thread_slot_13", mutex), 1)
        recursive = self.call("thread_slot_10")
        self.assertEqual(self.call("thread_slot_12", recursive, 0), 0)
        self.assertEqual(self.call("thread_slot_12", recursive, 0), 0)
        self.assertEqual(self.call("thread_slot_13", recursive), 0)
        self.assertEqual(self.call("thread_slot_13", recursive), 0)
        self.assertEqual(self.call("thread_slot_11", recursive), 0)

    def test_pointer_event_runs_registered_arm_callback_with_coordinates(self):
        # ARM callback: copy event x/y into user data, return zero via LR.
        function = self.runtime.image.base + 0x100
        code = struct.pack("<6I", 0xE5902008, 0xE5812000, 0xE590200C,
                           0xE5812004, 0xE3A00000, 0xE12FFF1E)
        self.runtime.cpu.mem_write(function, code)
        output = self.data(bytes(8))
        self.assertEqual(self.call("s3ePointerRegister", 0, function, output), 0)
        self.runtime.input.button(0, True, 239, 143)
        self.call("s3ePointerUpdate")
        self.assertEqual(struct.unpack("<2i", self.runtime.cpu.mem_read(output, 8)), (239, 143))
        self.assertEqual(self.runtime.callback_returns, 1)
        self.assertEqual(self.runtime.callback_stack, [])

    def test_headless_audio_reports_unavailable_and_never_claims_playback(self):
        self.assertEqual(self.call("s3eAudioGetInt", 6), 0)
        self.assertEqual(self.call("s3eAudioPlay", self.data("missing.mp3"), 1), 1)
        self.assertEqual(self.call("s3eSoundGetFreeChannel"), 0xFFFFFFFF)
        self.assertEqual(self.call("s3eSurfaceGetInt", 11), 0)

    def test_guest_heap_reuses_coalesced_blocks_and_preserves_realloc_data(self):
        first = self.call("s3eMallocBase", 32)
        second = self.call("s3eMallocBase", 64)
        payload = bytes(range(64))
        self.runtime.cpu.mem_write(second, payload)
        self.call("s3eFreeBase", first)
        half = self.call("s3eMallocBase", 16)
        other_half = self.call("s3eMallocBase", 16)
        self.assertEqual((half, other_half), (first, first + 16))
        self.call("s3eFreeBase", half)
        self.call("s3eFreeBase", other_half)
        self.assertEqual(self.call("s3eMallocBase", 32), first)
        grown = self.call("s3eReallocBase", second, 96)
        self.assertNotEqual(grown, 0)
        self.assertEqual(bytes(self.runtime.cpu.mem_read(grown, 64)), payload)
        self.assertEqual(self.call("s3eReallocBase", grown, self.runtime.HEAP_BYTES + 1), 0)
        self.assertEqual(bytes(self.runtime.cpu.mem_read(grown, 64)), payload)
        self.assertIn(grown, self.runtime.allocations)

    def test_api_return_is_committed_before_a_cpu_slice_can_resume(self):
        cpu = self.runtime.cpu
        trap = self.runtime.import_trap("s3eMallocBase")
        cpu.reg_write(UC_ARM_REG_R0, 64)
        cpu.reg_write(UC_ARM_REG_LR, self.runtime.image.base)
        cpu.emu_start(trap, self.runtime.image.base, count=1)
        self.assertEqual(cpu.reg_read(UC_ARM_REG_PC), self.runtime.image.base)
        self.assertEqual(self.runtime.counts["s3eMallocBase"], 1)

    def test_real_gles_shader_and_guest_vertex_pointer_render_red_pixel(self):
        def shader(type_, source):
            handle = self.call("glCreateShader", type_)
            self.call("glShaderSource", handle, 1, self.data(struct.pack("<I", self.data(source))), 0)
            self.call("glCompileShader", handle)
            status = self.data(bytes(4))
            self.call("glGetShaderiv", handle, 0x8B81, status)
            self.assertEqual(struct.unpack("<I", self.runtime.cpu.mem_read(status, 4))[0], 1)
            return handle
        vertex = shader(0x8B31, "attribute vec2 a_position; void main(){gl_Position=vec4(a_position,0.0,1.0);}")
        fragment = shader(0x8B30, "precision mediump float; void main(){gl_FragColor=vec4(1.0,0.0,0.0,1.0);}")
        program = self.call("glCreateProgram")
        self.call("glAttachShader", program, vertex)
        self.call("glAttachShader", program, fragment)
        self.call("glLinkProgram", program)
        self.call("glUseProgram", program)
        self.call("glViewport", 0, 0, 800, 480)
        self.call("glClearColor", 0, 0, 0, 0x3F800000)
        self.call("glClear", 0x4000)
        location = self.call("glGetAttribLocation", program, self.data("a_position"))
        self.assertLess(location, 16)
        self.call("glEnableVertexAttribArray", location)
        self.call("glVertexAttribPointer", location, 2, 0x1406, 0, 0,
                  self.data(struct.pack("<6f", -1, -1, 1, -1, 0, 1)))
        self.call("glDrawArrays", 4, 0, 3)
        pixel = self.data(bytes(4))
        self.call("glReadPixels", 400, 240, 1, 1, 0x1908, 0x1401, pixel)
        self.assertEqual(bytes(self.runtime.cpu.mem_read(pixel, 4)), b"\xff\x00\x00\xff")
        self.assertEqual(self.call("glGetError"), 0)

    def test_legacy_bitmap_filter_fix_is_narrow_and_updates_real_texture(self):
        output = self.data(bytes(4))
        self.call("glGenTextures", 1, output)
        texture = struct.unpack("<I", self.runtime.cpu.mem_read(output, 4))[0]
        self.call("glBindTexture", 0x0DE1, texture)
        # A regular invalid target must retain the standard GL_INVALID_ENUM.
        self.call("glTexParameteri", texture, 0x2800, 0x2600)
        self.assertEqual(self.call("glGetError"), 0x0500)
        caller = self.runtime.image.base + 0x154304
        self.runtime.cpu.mem_map(caller & ~0xFFF, 4096)
        self.runtime.cpu.reg_write(UC_ARM_REG_SP, self.runtime.STACK + self.runtime.STACK_BYTES - 128)
        self.runtime.cpu.mem_write(self.runtime.cpu.reg_read(UC_ARM_REG_SP) + 20,
                                   struct.pack("<I", self.runtime.image.base + 0x120314))
        self.call("glTexParameteri", texture, 0x2800, 0x2600, return_address=caller)
        self.assertEqual(self.call("glGetError"), 0)
        self.call("glGetTexParameteriv", 0x0DE1, 0x2800, output)
        self.assertEqual(struct.unpack("<I", self.runtime.cpu.mem_read(output, 4))[0], 0x2600)
        self.assertEqual(self.runtime.graphics.texture_parameter_compatibility_calls, 1)


if __name__ == "__main__":
    unittest.main()
