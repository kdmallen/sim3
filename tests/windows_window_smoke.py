"""Run with the bundled Windows Python to exercise actual Win32 messages."""
import ctypes as C
from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from input_device import InputDevice
from win32_window import Win32Window


def main():
    runtime = SimpleNamespace(surface_width=800, surface_height=480,
                              quit_requested=False, callbacks={})
    runtime.input = InputDevice(runtime)
    window = Win32Window(runtime)
    try:
        send = window.bind(window.user, "SendMessageW", C.c_ssize_t, C.c_void_p,
                           C.c_uint32, C.c_size_t, C.c_ssize_t)
        position = 143 << 16 | 239
        send(window.handle, 0x0201, 1, position)
        assert runtime.input.buttons == {0}
        assert (runtime.input.x, runtime.input.y) == (239, 143)
        send(window.handle, 0x0202, 0, position)
        assert runtime.input.buttons == set()
        send(window.handle, 0x0100, 39, 0)
        runtime.input.poll_keyboard()
        runtime.input.accelerometer_active = True
        assert runtime.input.acceleration("X") == 1000
        send(window.handle, 0x0101, 39, 0)
        runtime.input.poll_keyboard()
        assert runtime.input.acceleration("X") == 0
        tick = [0.0]
        runtime.input.clock = lambda: tick[0]
        send(window.handle, 0x0100, 116, 0)
        first = runtime.input.acceleration("X")
        tick[0] += 0.08
        assert first * runtime.input.acceleration("X") < 0
        send(window.handle, 0x0101, 116, 0)
        send(window.handle, 0x0010, 0, 0)
        assert runtime.quit_requested
        assert not window.closed  # EGL cleanup must precede HWND destruction.
        print("PASS: native mouse, keyboard tilt, F5 shake, and orderly close")
    finally:
        window.close()
        assert window.closed


if __name__ == "__main__":
    main()
