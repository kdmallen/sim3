"""Native Windows window and event pump; no external GUI runtime is required."""
import ctypes as C
import os
import struct
import time


class Point(C.Structure):
    _fields_ = [("x", C.c_int32), ("y", C.c_int32)]


class Rect(C.Structure):
    _fields_ = [(name, C.c_int32) for name in ("left", "top", "right", "bottom")]


class Message(C.Structure):
    _fields_ = [("hwnd", C.c_void_p), ("message", C.c_uint32),
                ("wParam", C.c_size_t), ("lParam", C.c_ssize_t),
                ("time", C.c_uint32), ("point", Point), ("private", C.c_uint32)]


class Paint(C.Structure):
    _fields_ = [("hdc", C.c_void_p), ("erase", C.c_int32), ("rect", Rect),
                ("restore", C.c_int32), ("update", C.c_int32), ("reserved", C.c_byte * 32)]


class Win32Window:
    def __init__(self, runtime, title="The Sims 3 Mobile"):
        if os.name != "nt":
            raise RuntimeError("The native game window requires Windows")
        self.runtime = runtime
        self.user = C.WinDLL("user32", use_last_error=True)
        self.kernel = C.WinDLL("kernel32", use_last_error=True)
        self.gdi = C.WinDLL("gdi32", use_last_error=True)
        self.functions = {}
        self.closed = False
        self.minimized = False
        self.active = True
        self.timer_period = False
        self.show_fps = False
        self.title_updated = 0
        self.callback_error = None
        self.status = "Preparing game…"
        self.handle = None
        self.class_name = "Sims3MobileCompatibilityWindow"
        procedure = C.WINFUNCTYPE(C.c_ssize_t, C.c_void_p, C.c_uint32, C.c_size_t, C.c_ssize_t)
        self.procedure = procedure(self._procedure)

        class WindowClass(C.Structure):
            _fields_ = [("style", C.c_uint32), ("procedure", procedure),
                        ("class_extra", C.c_int32), ("window_extra", C.c_int32),
                        ("instance", C.c_void_p), ("icon", C.c_void_p),
                        ("cursor", C.c_void_p), ("background", C.c_void_p),
                        ("menu", C.c_wchar_p), ("name", C.c_wchar_p)]

        self.instance = self.bind(self.kernel, "GetModuleHandleW", C.c_void_p, C.c_wchar_p)(None)
        self.bind(self.user, "SetProcessDPIAware", C.c_int32)()
        cursor = self.bind(self.user, "LoadCursorW", C.c_void_p, C.c_void_p, C.c_void_p)(None, 32512)
        window_class = WindowClass(3, self.procedure, 0, 0, self.instance, None,
                                    cursor, None, None, self.class_name)
        register = self.bind(self.user, "RegisterClassW", C.c_uint16, C.POINTER(WindowClass))
        if not register(C.byref(window_class)):
            raise C.WinError(C.get_last_error())
        style = 0x00CA0000  # Caption, system menu, minimize; fixed client dimensions.
        dimensions = Rect(0, 0, runtime.surface_width, runtime.surface_height)
        self.bind(self.user, "AdjustWindowRect", C.c_int32, C.POINTER(Rect), C.c_uint32, C.c_int32)(
            C.byref(dimensions), style, 0)
        create = self.bind(self.user, "CreateWindowExW", C.c_void_p, C.c_uint32,
                           C.c_wchar_p, C.c_wchar_p, C.c_uint32, C.c_int32,
                           C.c_int32, C.c_int32, C.c_int32, C.c_void_p,
                           C.c_void_p, C.c_void_p, C.c_void_p)
        self.handle = create(0, self.class_name, title, style, -2147483648, -2147483648,
                             dimensions.right - dimensions.left, dimensions.bottom - dimensions.top,
                             None, None, self.instance, None)
        if not self.handle:
            raise C.WinError(C.get_last_error())
        runtime.window = self
        runtime.input.accelerometer_available = True
        self.multimedia = C.WinDLL("winmm", use_last_error=True)
        self.timer_period = self.bind(self.multimedia, "timeBeginPeriod", C.c_uint32, C.c_uint32)(1) == 0
        self.bind(self.user, "ShowWindow", C.c_int32, C.c_void_p, C.c_int32)(self.handle, 5)
        self.bind(self.user, "UpdateWindow", C.c_int32, C.c_void_p)(self.handle)

    def bind(self, library, name, result, *arguments):
        key = (library._name, name)
        if key not in self.functions:
            function = getattr(library, name)
            function.restype = result
            function.argtypes = list(arguments)
            self.functions[key] = function
        return self.functions[key]

    def _procedure(self, handle, message, wparam, lparam):
        try:
            if message == 0x0010:  # WM_CLOSE: keep EGL's window alive until cleanup.
                self.runtime.quit_requested = True
                return 0
            if message == 0x0002:
                self.closed = True
                self.runtime.quit_requested = True
                return 0
            if message == 0x0005:
                self.minimized = wparam == 1
            if message == 0x001C:
                active = bool(wparam)
                if self.active != active:
                    self.runtime.input.device_events.append((1 if active else 0, None))
                    self.active = active
                    if not active:
                        for key in tuple(self.runtime.input.held_keys):
                            self.runtime.input.key(key, False)
                        for button in tuple(self.runtime.input.buttons):
                            self.runtime.input.button(button, False)
            if message == 0x0014:
                return 1  # Avoid erasing an EGL-rendered frame.
            if message == 0x000F and self.status:
                self.paint_status(handle)
                return 0
            if message == 0x020A:  # WM_MOUSEWHEEL uses screen coordinates.
                position = Point(C.c_int16(lparam & 0xFFFF).value, C.c_int16((lparam >> 16) & 0xFFFF).value)
                self.bind(self.user, "ScreenToClient", C.c_int32, C.c_void_p, C.POINTER(Point))(
                    handle, C.byref(position))
                self.runtime.input.wheel(C.c_int16((wparam >> 16) & 0xFFFF).value, position.x, position.y)
                return 0
            if message in (0x0200, 0x0201, 0x0202, 0x0204, 0x0205, 0x0207, 0x0208):
                x, y = C.c_int16(lparam & 0xFFFF).value, C.c_int16((lparam >> 16) & 0xFFFF).value
                if message == 0x0200:
                    self.runtime.input.motion(x, y)
                else:
                    button = 0 if message in (0x0201, 0x0202) else 1 if message in (0x0204, 0x0205) else 2
                    pressed = message in (0x0201, 0x0204, 0x0207)
                    self.runtime.input.button(button, pressed, x, y)
                    if pressed:
                        self.bind(self.user, "SetCapture", C.c_void_p, C.c_void_p)(handle)
                    else:
                        self.bind(self.user, "ReleaseCapture", C.c_int32)()
                return 0
            if message in (0x0100, 0x0101):
                if message == 0x0100 and wparam == 114 and not lparam & (1 << 30):
                    self.show_fps = not self.show_fps
                    self.title_updated = 0
                    self.update_frame_rate(0)
                    return 0
                if message == 0x0100 and wparam == 112 and not lparam & (1 << 30):
                    self.show_controls()
                    return 0
                if message == 0x0101 or not lparam & (1 << 30):
                    self.runtime.input.key(wparam, message == 0x0100)
                return 0
            if message == 0x0102:
                self.runtime.input.character(wparam)
                return 0
        except Exception as exc:
            self.callback_error = repr(exc)
            self.runtime.quit_requested = True
        return self.bind(self.user, "DefWindowProcW", C.c_ssize_t, C.c_void_p,
                         C.c_uint32, C.c_size_t, C.c_ssize_t)(handle, message, wparam, lparam)

    def paint_status(self, handle):
        paint = Paint()
        context = self.bind(self.user, "BeginPaint", C.c_void_p, C.c_void_p,
                            C.POINTER(Paint))(handle, C.byref(paint))
        rectangle = Rect()
        self.bind(self.user, "GetClientRect", C.c_int32, C.c_void_p, C.POINTER(Rect))(handle, C.byref(rectangle))
        brush = self.bind(self.gdi, "GetStockObject", C.c_void_p, C.c_int32)(4)
        self.bind(self.user, "FillRect", C.c_int32, C.c_void_p, C.POINTER(Rect), C.c_void_p)(
            context, C.byref(rectangle), brush)
        self.bind(self.gdi, "SetTextColor", C.c_uint32, C.c_void_p, C.c_uint32)(context, 0xFFFFFF)
        self.bind(self.gdi, "SetBkMode", C.c_int32, C.c_void_p, C.c_int32)(context, 1)
        self.bind(self.user, "DrawTextW", C.c_int32, C.c_void_p, C.c_wchar_p,
                  C.c_int32, C.POINTER(Rect), C.c_uint32)(context, self.status, -1, C.byref(rectangle), 0x25)
        self.bind(self.user, "EndPaint", C.c_int32, C.c_void_p, C.POINTER(Paint))(handle, C.byref(paint))

    def set_status(self, text):
        self.status = text
        if self.handle:
            self.bind(self.user, "InvalidateRect", C.c_int32, C.c_void_p, C.c_void_p, C.c_int32)(self.handle, None, 0)
            self.bind(self.user, "UpdateWindow", C.c_int32, C.c_void_p)(self.handle)
        self.pump()

    def read_text(self, prompt, initial=""):
        """Native modal edit control, including Enter, Escape and clipboard."""
        encode = lambda value: value.encode("utf-16le") + b"\0\0"
        style = 0x80000000 | 0x00C00000 | 0x00080000 | 0x80 | 0x40 | 0x800
        template = bytearray(struct.pack("<IIHhhhh", style, 0, 4, 0, 0, 300, 104))
        template += b"\0\0\0\0" + encode("The Sims 3 — Enter text")
        template += struct.pack("<H", 9) + encode("Segoe UI")
        def control(style, x, y, width, height, identifier, kind, text):
            template.extend(b"\0" * (-len(template) % 4))
            template.extend(struct.pack("<IIhhhhH", style, 0, x, y, width, height, identifier))
            template.extend(struct.pack("<HH", 0xFFFF, kind) + encode(text) + b"\0\0")
        control(0x50000000, 10, 8, 280, 24, 100, 0x82, prompt)
        control(0x50810080, 10, 36, 280, 16, 101, 0x81, initial)
        control(0x50010001, 180, 76, 52, 16, 1, 0x80, "OK")
        control(0x50010000, 238, 76, 52, 16, 2, 0x80, "Cancel")
        answer = [None]
        errors = []
        end = self.bind(self.user, "EndDialog", C.c_int32, C.c_void_p, C.c_ssize_t)
        procedure_type = C.WINFUNCTYPE(C.c_ssize_t, C.c_void_p, C.c_uint32, C.c_size_t, C.c_ssize_t)
        def procedure(handle, message, wparam, lparam):
            try:
                if message == 0x0110:  # WM_INITDIALOG
                    edit = self.bind(self.user, "GetDlgItem", C.c_void_p, C.c_void_p, C.c_int32)(handle, 101)
                    self.bind(self.user, "SetFocus", C.c_void_p, C.c_void_p)(edit)
                    self.bind(self.user, "SendMessageW", C.c_ssize_t, C.c_void_p,
                              C.c_uint32, C.c_size_t, C.c_ssize_t)(edit, 0x00C5, 1023, 0)
                    self.bind(self.user, "SendMessageW", C.c_ssize_t, C.c_void_p,
                              C.c_uint32, C.c_size_t, C.c_ssize_t)(edit, 0x00B1, 0, -1)
                    return 0
                if message == 0x0111 and wparam & 0xFFFF in (1, 2):
                    if wparam & 0xFFFF == 1:
                        edit = self.bind(self.user, "GetDlgItem", C.c_void_p, C.c_void_p, C.c_int32)(handle, 101)
                        length = self.bind(self.user, "GetWindowTextLengthW", C.c_int32, C.c_void_p)(edit)
                        if length > 4096:
                            raise ValueError("Text entry exceeds the supported length")
                        buffer = C.create_unicode_buffer(length + 1)
                        self.bind(self.user, "GetWindowTextW", C.c_int32, C.c_void_p,
                                  C.c_wchar_p, C.c_int32)(edit, buffer, length + 1)
                        answer[0] = buffer.value
                    end(handle, 1 if answer[0] is not None else 0)
                    return 1
                if message == 0x0010:
                    end(handle, 0)
                    return 1
            except Exception as exc:
                errors.append(exc)
                end(handle, -1)
                return 1
            return 0
        callback = procedure_type(procedure)
        data = C.create_string_buffer(bytes(template))
        result = self.bind(self.user, "DialogBoxIndirectParamW", C.c_ssize_t, C.c_void_p,
                           C.c_void_p, C.c_void_p, procedure_type, C.c_ssize_t)(
            self.instance, C.cast(data, C.c_void_p), self.handle, callback, 0)
        if errors:
            raise errors[0]
        if result == -1:
            raise C.WinError(C.get_last_error())
        if self.runtime.graphics:
            self.runtime.graphics.reset_frame_timing()
        return answer[0]

    def show_controls(self):
        self.bind(self.user, "MessageBoxW", C.c_int32, C.c_void_p, C.c_wchar_p,
                  C.c_wchar_p, C.c_uint32)(self.handle,
            "Mouse: click and drag for touch controls and camera panning.\n"
            "Mouse wheel: camera zoom using the game's right-hand slider.\n"
            "Ctrl + wheel over the slider's middle: align zoom if its position differs.\n"
            "Arrow keys / WASD: tilt the phone.\n"
            "F5: shake the phone (hold for continuous shaking).\n"
            "F6: quick upward jerk for fishing.\n"
            "F3: show or hide frame rate.\n"
            "Escape: back.\n\n"
            "Name prompts: type normally; Enter confirms, Escape cancels.\n"
            "Ctrl+C / Ctrl+V work in the text prompt.", "The Sims 3 — Controls", 0)
        if self.runtime.graphics:
            self.runtime.graphics.reset_frame_timing()

    def update_frame_rate(self, fps):
        now = time.monotonic()
        if now - self.title_updated < 1:
            return
        if self.show_fps or not self.title_updated:
            title = f"The Sims 3 Mobile — {fps:.1f} FPS" if self.show_fps else "The Sims 3 Mobile"
            self.bind(self.user, "SetWindowTextW", C.c_int32, C.c_void_p, C.c_wchar_p)(self.handle, title)
        self.title_updated = now

    def pump(self):
        message = Message()
        peek = self.bind(self.user, "PeekMessageW", C.c_int32, C.POINTER(Message),
                          C.c_void_p, C.c_uint32, C.c_uint32, C.c_uint32)
        translate = self.bind(self.user, "TranslateMessage", C.c_int32, C.POINTER(Message))
        dispatch = self.bind(self.user, "DispatchMessageW", C.c_ssize_t, C.POINTER(Message))
        for _ in range(512):
            if not peek(C.byref(message), None, 0, 0, 1):
                break
            if message.message == 0x0012:
                self.runtime.quit_requested = True
                break
            translate(C.byref(message))
            dispatch(C.byref(message))
        if self.callback_error:
            raise RuntimeError("Window event callback failed: " + self.callback_error)

    def close(self):
        if self.handle and not self.closed:
            self.bind(self.user, "DestroyWindow", C.c_int32, C.c_void_p)(self.handle)
        self.bind(self.user, "UnregisterClassW", C.c_int32, C.c_wchar_p, C.c_void_p)(self.class_name, self.instance)
        if self.timer_period:
            self.bind(self.multimedia, "timeEndPeriod", C.c_uint32, C.c_uint32)(1)
            self.timer_period = False
