"""Exercise native text entry through the original ARM OSReadString wrapper."""
import ctypes as C
from pathlib import Path
import struct
import sys
import threading
import time
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from boot_trace import Runtime, Image
from win32_window import Win32Window
from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_SP, UC_ARM_REG_LR


def main():
    root = Path(__file__).resolve().parents[1]
    with zipfile.ZipFile(root / "game/game.apk") as apk:
        image = Image(apk.read("assets/The Sims 3.s3e"))
    runtime = Runtime(image)
    window = Win32Window(runtime)
    errors = []
    def answer(cancel=False):
        try:
            find = window.bind(window.user, "FindWindowW", C.c_void_p, C.c_wchar_p, C.c_wchar_p)
            get_item = window.bind(window.user, "GetDlgItem", C.c_void_p, C.c_void_p, C.c_int32)
            send = window.bind(window.user, "SendMessageW", C.c_ssize_t, C.c_void_p,
                               C.c_uint32, C.c_size_t, C.c_ssize_t)
            deadline = time.monotonic() + 8
            handle = None
            edit = None
            visible = window.bind(window.user, "IsWindowVisible", C.c_int32, C.c_void_p)
            while time.monotonic() < deadline:
                handle = find("#32770", "The Sims 3 — Enter text")
                edit = get_item(handle, 101) if handle else None
                if handle and edit and visible(handle):
                    break
                time.sleep(0.02)
            assert handle and edit and visible(handle), "Native text dialog did not finish opening"
            if not cancel:
                for character in "Zoë Sim":
                    send(edit, 0x0102, ord(character), 0)
            send(handle, 0x0111, 2 if cancel else 1, 0)
        except Exception as exc:
            errors.append(exc)
    try:
        prompt = runtime.allocate(32)
        runtime.cpu.mem_write(prompt, b"Name Your Sim\0")
        for cancel in (False, True):
            runtime.cpu.reg_write(UC_ARM_REG_R0, prompt)
            runtime.cpu.reg_write(UC_ARM_REG_R1, 0)
            runtime.cpu.reg_write(UC_ARM_REG_SP, runtime.STACK + runtime.STACK_BYTES - 16)
            runtime.cpu.reg_write(UC_ARM_REG_LR, runtime.API + 0xFFFC)
            worker = threading.Thread(target=answer, args=(cancel,), daemon=True)
            worker.start()
            runtime.cpu.emu_start(image.base + 0x188D64, runtime.API + 0xFFFC,
                                  timeout=10_000_000, count=100000)
            worker.join(1)
            if errors:
                raise errors[0]
            result = runtime.cpu.reg_read(UC_ARM_REG_R0)
            print("text result", cancel, hex(result), ascii(runtime.string(result)) if result else None,
                  "requests", runtime.text_input.requests, flush=True)
            assert result == 0 if cancel else runtime.string(result) == "Zoë Sim"
        print("PASS: original ARM text wrapper, typed Unicode name, and native cancellation")
    finally:
        runtime.filesystem.close()
        window.close()


if __name__ == "__main__":
    main()
