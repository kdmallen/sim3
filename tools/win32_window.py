"""Native Windows window and event pump; no external GUI runtime is required."""
import ctypes as C
import os


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
                        for key in tuple(self.runtime.input.keys):
                            self.runtime.input.key(key, False)
                        for button in tuple(self.runtime.input.buttons):
                            self.runtime.input.button(button, False)
            if message == 0x0014:
                return 1  # Avoid erasing an EGL-rendered frame.
            if message == 0x000F and self.status:
                self.paint_status(handle)
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
