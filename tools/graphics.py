"""EGL/GLES backend for offscreen diagnostics and native Windows presentation.

Guest pointers and handles are translated explicitly. A guest address is never
passed directly to the host graphics driver. Unknown pointer signatures stop.
"""
import ctypes as C
import ctypes.util
import os
from pathlib import Path
import re
import struct
import time
import zlib
from collections import deque


class GraphicsUnsupported(RuntimeError):
    pass


class FrameLimitReached(RuntimeError):
    pass


class Graphics:
    NONE = 0x3038
    TYPES = {
        "void": None, "GLvoid": None, "GLenum": C.c_uint,
        "GLuint": C.c_uint, "GLint": C.c_int, "GLsizei": C.c_int,
        "GLbitfield": C.c_uint, "GLboolean": C.c_ubyte,
        "GLbyte": C.c_byte, "GLubyte": C.c_ubyte,
        "GLshort": C.c_short, "GLushort": C.c_ushort,
        "GLfloat": C.c_float, "GLclampf": C.c_float,
        "GLfixed": C.c_int, "GLintptr": C.c_ssize_t,
        "GLsizeiptr": C.c_ssize_t, "GLchar": C.c_char,
    }

    def __init__(self, runtime):
        self.runtime = runtime
        self.windowed = runtime.window is not None
        if os.name == "nt":
            library = Path(__file__).resolve().parents[1] / "native/av_libglesv2.dll"
            self.library = C.WinDLL(str(library))
        else:
            cache = Path(__file__).resolve().parents[1] / "local-data/cache/mesa"
            cache.mkdir(parents=True, exist_ok=True)
            os.environ.setdefault("MESA_SHADER_CACHE_DIR", str(cache))
            library = ctypes.util.find_library("EGL")
            if not library:
                raise RuntimeError("No EGL library is available")
            self.library = C.CDLL(library)
        self.functions = {}
        self.call_plans = {}
        self.handles = {}
        self.reverse_handles = {}
        self.next_handle = 0x71000001
        self.strings = {}
        self.contexts = []
        self.surfaces = []
        self.frames = 0
        self.calls = 0
        self.unpack_alignment = 4
        self.pack_alignment = 4
        self.array_buffer = 0
        self.element_buffer = 0
        self.active_texture = 0x84C0
        self.texture_bindings = {}
        self.texture_parameter_compatibility_calls = 0
        self.vertex_arrays = {}
        self.enabled_arrays = set()
        self.buffer_data = {}
        self.draw_buffers = []
        self.draws = 0
        self.error_queue = []
        self.invalid_texture_call = None
        self.captures = []
        self.last_present_ns = 0
        self.frame_durations = deque(maxlen=180)
        self.gl_prototypes = self.read_prototypes()
        if os.name == "nt":
            self.display = self.egl_function("eglGetDisplay", C.c_void_p, C.c_void_p)(None)
        else:
            getproc = self.egl_function("eglGetProcAddress", C.c_void_p, C.c_char_p)
            address = getproc(b"eglGetPlatformDisplayEXT")
            if not address:
                raise RuntimeError("Surfaceless EGL is unavailable on this host")
            platform_display = C.CFUNCTYPE(C.c_void_p, C.c_uint, C.c_void_p,
                                          C.POINTER(C.c_int))(address)
            self.display = platform_display(0x31DD, None, None)
        major, minor = C.c_int(), C.c_int()
        initialize = self.egl_function("eglInitialize", C.c_uint, C.c_void_p,
                                       C.POINTER(C.c_int), C.POINTER(C.c_int))
        if not initialize(self.display, C.byref(major), C.byref(minor)):
            raise RuntimeError("Unable to initialize EGL graphics")
        self.egl_version = (major.value, minor.value)
        if not self.egl_function("eglBindAPI", C.c_uint, C.c_uint)(0x30A0):
            raise RuntimeError("EGL does not support OpenGL ES")
        attributes = (C.c_int * 17)(
            0x3033, 4 if self.windowed else 1, 0x3040, 4, 0x3024, 8, 0x3023, 8, 0x3022, 8,
            0x3021, 8, 0x3025, 16, 0x3026, 0, self.NONE)
        config = C.c_void_p()
        count = C.c_int()
        choose = self.egl_function("eglChooseConfig", C.c_uint, C.c_void_p,
                                  C.POINTER(C.c_int), C.POINTER(C.c_void_p),
                                  C.c_int, C.POINTER(C.c_int))
        if not choose(self.display, attributes, C.byref(config), 1, C.byref(count)) or not count.value:
            raise RuntimeError("No offscreen GLES2 configuration is available")
        self.config = config.value
        self.config_handle(self.config)
        self.bootstrap_surface = self.create_surface(self.config)
        self.bootstrap_context = self.create_context(self.config, None, [0x3098, 2, self.NONE])
        if not self.make_current(self.bootstrap_surface, self.bootstrap_surface, self.bootstrap_context):
            raise RuntimeError("Unable to activate GLES2 context")
        get_string = self.gl_function("glGetString", C.c_void_p, C.c_uint)
        self.info = {
            "egl_version": list(self.egl_version),
            "vendor": C.string_at(get_string(0x1F00)).decode(),
            "renderer": C.string_at(get_string(0x1F01)).decode(),
            "version": C.string_at(get_string(0x1F02)).decode(),
            "offscreen": not self.windowed,
            "guest_api": "OpenGL ES 2.0 core; optional extensions not exposed",
        }

    def read_prototypes(self):
        header = (Path(__file__).resolve().parents[1] / "vendor/khronos/gl2.h").read_text()
        result = {}
        for returns, name, parameters in re.findall(
                r"GL_APICALL\s+(.+?)\s*GL_APIENTRY\s+(\w+)\s*\(([^)]*)\);", header):
            arguments = []
            if parameters.strip() != "void":
                for parameter in parameters.split(","):
                    match = re.fullmatch(r"\s*(.+?)([A-Za-z_]\w*)\s*", parameter)
                    if not match:
                        raise ValueError("Unrecognized Khronos prototype")
                    arguments.append((match[1].strip(), match[2]))
            result[name] = (returns.strip(), arguments)
        if "glCreateShader" not in result or "glGetString" not in result:
            raise ValueError("No GLES declarations were parsed")
        return result

    def ctype(self, declaration):
        declaration = declaration.replace("const", "").strip()
        if "*" in declaration:
            return C.c_void_p
        if declaration not in self.TYPES:
            raise GraphicsUnsupported("Unknown GL type " + declaration)
        return self.TYPES[declaration]

    def egl_function(self, name, returns, *arguments):
        key = (name, returns, arguments)
        if key not in self.functions:
            function = getattr(self.library, name, None)
            if function is None and os.name == "nt":
                function = getattr(self.library, "EGL_" + name[3:])
            if function is None:
                raise GraphicsUnsupported("EGL function unavailable: " + name)
            function.restype = returns
            function.argtypes = list(arguments)
            self.functions[key] = function
        return self.functions[key]

    def gl_function(self, name, returns, *arguments):
        key = (name, returns, arguments)
        if key not in self.functions:
            address = self.egl_function("eglGetProcAddress", C.c_void_p, C.c_char_p)(name.encode())
            if not address:
                raise GraphicsUnsupported("Host GL function is unavailable: " + name)
            convention = C.WINFUNCTYPE if os.name == "nt" else C.CFUNCTYPE
            self.functions[key] = convention(returns, *arguments)(address)
        return self.functions[key]

    def handle(self, real):
        if not real:
            return 0
        if real not in self.reverse_handles:
            self.handles[self.next_handle] = real
            self.reverse_handles[real] = self.next_handle
            self.next_handle += 1
        return self.reverse_handles[real]

    def real(self, handle):
        if not handle:
            return None
        if handle not in self.handles:
            raise ValueError("Unknown guest EGL handle")
        return self.handles[handle]

    def config_handle(self, real):
        # The original SDK uses the EGL_CONFIG_ID queried from its context as
        # an opaque config handle. Preserve that integer representation while
        # translating it to the host driver's pointer-sized EGLConfig.
        value = C.c_int()
        function = self.egl_function("eglGetConfigAttrib", C.c_uint, C.c_void_p,
                                    C.c_void_p, C.c_int, C.POINTER(C.c_int))
        if not function(self.display, real, 0x3028, C.byref(value)):
            raise RuntimeError("Unable to identify EGL configuration")
        self.handles[value.value] = real
        self.reverse_handles[real] = value.value
        return value.value

    def guest_string(self, value):
        if value not in self.strings:
            pointer = self.runtime.allocate(len(value) + 1)
            if not pointer:
                raise RuntimeError("Guest string allocation failed")
            self.runtime.cpu.mem_write(pointer, value + b"\0")
            self.strings[value] = pointer
        return self.strings[value]

    def attribute_list(self, pointer):
        if not pointer:
            return [self.NONE]
        result = []
        for index in range(128):
            key = struct.unpack("<i", self.runtime.cpu.mem_read(pointer + index * 8, 4))[0]
            result.append(key)
            if key == self.NONE:
                return result
            result.append(struct.unpack("<i", self.runtime.cpu.mem_read(pointer + index * 8 + 4, 4))[0])
        raise ValueError("Unterminated EGL attribute list")

    def write_int(self, pointer, value):
        if pointer:
            self.runtime.cpu.mem_write(pointer, struct.pack("<I", value & 0xFFFFFFFF))

    def create_surface(self, config):
        if self.windowed:
            # A native window may have only one EGL surface at a time. The
            # guest uses the context already bootstrapped by this adapter.
            if self.surfaces:
                return self.surfaces[0]
            function = self.egl_function("eglCreateWindowSurface", C.c_void_p,
                                        C.c_void_p, C.c_void_p, C.c_void_p, C.POINTER(C.c_int))
            surface = function(self.display, config, self.runtime.window.handle, None)
            if surface:
                self.surfaces.append(surface)
            return surface
        attributes = (C.c_int * 5)(0x3057, self.runtime.surface_width,
                                  0x3056, self.runtime.surface_height, self.NONE)
        function = self.egl_function("eglCreatePbufferSurface", C.c_void_p,
                                    C.c_void_p, C.c_void_p, C.POINTER(C.c_int))
        surface = function(self.display, config, attributes)
        if surface:
            self.surfaces.append(surface)
        return surface

    def create_context(self, config, share, attributes):
        array = (C.c_int * len(attributes))(*attributes)
        function = self.egl_function("eglCreateContext", C.c_void_p, C.c_void_p,
                                    C.c_void_p, C.c_void_p, C.POINTER(C.c_int))
        context = function(self.display, config, share, array)
        if context:
            self.contexts.append(context)
        return context

    def make_current(self, draw, read, context):
        return self.egl_function("eglMakeCurrent", C.c_uint, C.c_void_p,
                                 C.c_void_p, C.c_void_p, C.c_void_p)(self.display, draw, read, context)

    def egl_call(self, name):
        a = self.runtime.argument
        if name == "eglGetDisplay":
            return self.handle(self.display)
        if name == "eglInitialize":
            self.real(a(0))
            self.write_int(a(1), self.egl_version[0])
            self.write_int(a(2), self.egl_version[1])
            return 1
        if name == "eglBindAPI":
            return self.egl_function(name, C.c_uint, C.c_uint)(a(0))
        if name in ("eglChooseConfig", "eglGetConfigs"):
            choose = name == "eglChooseConfig"
            display = self.real(a(0))
            pointer, maximum, number = (a(2), a(3), a(4)) if choose else (a(1), a(2), a(3))
            if maximum > 4096:
                raise ValueError("Excessive EGL configuration count")
            array = (C.c_void_p * max(maximum, 1))()
            count = C.c_int()
            if choose:
                attributes = self.attribute_list(a(1))
                for index in range(0, len(attributes) - 1, 2):
                    if attributes[index] == 0x3033:
                        attributes[index + 1] = 4 if self.windowed else 1
                attrs = (C.c_int * len(attributes))(*attributes)
                function = self.egl_function(name, C.c_uint, C.c_void_p, C.POINTER(C.c_int),
                                            C.POINTER(C.c_void_p), C.c_int, C.POINTER(C.c_int))
                success = function(display, attrs, array if pointer else None, maximum, C.byref(count))
            else:
                function = self.egl_function(name, C.c_uint, C.c_void_p, C.POINTER(C.c_void_p),
                                            C.c_int, C.POINTER(C.c_int))
                success = function(display, array if pointer else None, maximum, C.byref(count))
            self.write_int(number, count.value)
            if success and pointer:
                for index in range(min(maximum, count.value)):
                    self.write_int(pointer + index * 4, self.config_handle(array[index]))
            return success
        if name == "eglGetConfigAttrib":
            value = C.c_int()
            function = self.egl_function(name, C.c_uint, C.c_void_p, C.c_void_p,
                                        C.c_int, C.POINTER(C.c_int))
            result = function(self.real(a(0)), self.real(a(1)), a(2), C.byref(value))
            self.write_int(a(3), value.value)
            return result
        if name == "eglCreateContext":
            return self.handle(self.create_context(self.real(a(1)), self.real(a(2)),
                                                   self.attribute_list(a(3))))
        if name == "eglCreateWindowSurface":
            return self.handle(self.create_surface(self.real(a(1))))
        if name == "eglMakeCurrent":
            return self.make_current(self.real(a(1)), self.real(a(2)), self.real(a(3)))
        if name in ("eglGetCurrentContext", "eglGetCurrentDisplay"):
            return self.handle(self.egl_function(name, C.c_void_p)())
        if name == "eglGetCurrentSurface":
            return self.handle(self.egl_function(name, C.c_void_p, C.c_int)(a(0)))
        if name == "eglGetError":
            return self.egl_function(name, C.c_uint)()
        if name == "eglQueryString":
            value = self.egl_function(name, C.c_void_p, C.c_void_p, C.c_int)(self.real(a(0)), a(1))
            return self.guest_string(C.string_at(value)) if value else 0
        if name == "eglQueryContext":
            value = C.c_int()
            result = self.egl_function(name, C.c_uint, C.c_void_p, C.c_void_p,
                                       C.c_int, C.POINTER(C.c_int))(
                self.real(a(0)), self.real(a(1)), a(2), C.byref(value))
            self.write_int(a(3), value.value)
            return result
        if name == "eglGetProcAddress":
            requested = self.runtime.string(a(0))
            self.runtime.trace[-1]["requested"] = requested
            if requested in self.gl_prototypes or requested.startswith("egl"):
                return self.runtime.import_trap(requested)
            return 0
        if name == "eglSwapBuffers":
            # Read the back buffer before the swap on double-buffered windows.
            self.capture_frame(self.frames + 1)
            result = self.egl_function(name, C.c_uint, C.c_void_p, C.c_void_p)(
                self.real(a(0)), self.real(a(1)))
            if result:
                self.present()
            return result
        if name in ("eglDestroySurface", "eglDestroyContext"):
            value = self.real(a(1))
            result = self.egl_function(name, C.c_uint, C.c_void_p, C.c_void_p)(self.real(a(0)), value)
            if result:
                collection = self.surfaces if name.endswith("Surface") else self.contexts
                if value in collection:
                    collection.remove(value)
            return result
        if name == "eglTerminate":
            return self.egl_function(name, C.c_uint, C.c_void_p)(self.real(a(0)))
        raise GraphicsUnsupported("Unimplemented EGL call: " + name)

    def gl_call(self, name):
        if name not in self.call_plans:
            if name not in self.gl_prototypes:
                raise GraphicsUnsupported("GL declaration not implemented: " + name)
            returns, declarations = self.gl_prototypes[name]
            types = [self.ctype(declaration) for declaration, _ in declarations]
            function = self.gl_function(name, self.ctype(returns), *types)
            self.call_plans[name] = declarations, types, function
        declarations, types, function = self.call_plans[name]
        raw = [self.runtime.argument(index) for index in range(len(types))]
        if name == "glTexParameteri" and raw[0] not in (0x0DE1, 0x8513) and self.invalid_texture_call is None:
            from unicorn.arm_const import UC_ARM_REG_SP
            stack = self.runtime.cpu.reg_read(UC_ARM_REG_SP)
            end = next((end + 1 for begin, end, _ in self.runtime.cpu.mem_regions()
                        if begin <= stack <= end), stack)
            count = min(16, (end - stack) // 4)
            self.invalid_texture_call = {"args": raw, "frame": self.frames,
                                         "caller": self.runtime.trace[-1]["caller"],
                                         "stack_words": list(struct.unpack("<" + "I" * count,
                                                                          self.runtime.cpu.mem_read(stack, count * 4)))}
        if (name == "glTexParameteri" and raw[0] not in (0x0DE1, 0x8513)
                and self.runtime.trace[-1]["caller"] == 0x154304):
            # This game build's bitmap renderer passes a bound texture name to
            # the SDK's target-based filter wrapper (call sites 0x120314/324).
            # Translate only that recovered legacy call pattern. Other invalid
            # GL enums must still reach the driver and retain normal GL errors.
            from unicorn.arm_const import UC_ARM_REG_SP
            parent = struct.unpack("<I", self.runtime.cpu.mem_read(
                self.runtime.cpu.reg_read(UC_ARM_REG_SP) + 20, 4))[0] - self.runtime.image.base
            if (parent in (0x120314, 0x120324)
                    and raw[0] != 0 and self.texture_bindings.get((self.active_texture, 0x0DE1)) == raw[0]):
                self.runtime.trace[-1]["legacy_texture_name"] = raw[0]
                raw[0] = 0x0DE1
                self.texture_parameter_compatibility_calls += 1
        if name == "glGetString":
            # The host driver offers ES 3.2, while this bridge implements ES 2.0
            # core. Do not advertise extension entry points we cannot marshal.
            guest_strings = {0x1F02: b"OpenGL ES 2.0 Sims3 compatibility",
                             0x8B8C: b"OpenGL ES GLSL ES 1.00",
                             0x1F03: b""}
            if raw[0] in guest_strings:
                return self.guest_string(guest_strings[raw[0]])
            value = function(*raw)
            return self.guest_string(C.string_at(value)) if value else 0
        if name == "glGetError" and self.error_queue:
            return self.error_queue.pop(0)
        if name == "glVertexAttribPointer":
            index, size, type_, normalized, stride, pointer = raw
            if index >= 16 or not 1 <= size <= 4:
                raise ValueError("Invalid vertex attribute dimensions")
            self.vertex_arrays[index] = {"size": size, "type": type_, "normalized": normalized,
                                         "stride": stride, "pointer": pointer, "buffer": self.array_buffer}
            if self.array_buffer:
                function(*raw[:-1], C.c_void_p(pointer))
            return None
        if name in ("glDrawArrays", "glDrawElements"):
            self.draw_buffers = []
            if name == "glDrawArrays":
                first, count = raw[1:3]
                maximum = first + count - 1
                pointer = None
            else:
                count, type_, address = raw[1:4]
                element_bytes = {0x1401: 1, 0x1403: 2, 0x1405: 4}.get(type_)
                if element_bytes is None:
                    raise GraphicsUnsupported("Unsupported index element type")
                if self.element_buffer:
                    content = self.buffer_data.get(self.element_buffer)
                    if content is None or address + count * element_bytes > len(content):
                        raise ValueError("Unknown/out-of-bounds guest index buffer")
                    indices = content[address:address + count * element_bytes]
                    pointer = C.c_void_p(address)
                else:
                    indices = bytes(self.runtime.cpu.mem_read(address, count * element_bytes)) if count else b""
                    buffer = C.create_string_buffer(indices)
                    self.draw_buffers.append(buffer)
                    pointer = C.cast(buffer, C.c_void_p)
                maximum = max((value[0] for value in struct.iter_unpack(
                    {1: "<B", 2: "<H", 4: "<I"}[element_bytes], indices)), default=-1)
            if count > 10_000_000 or maximum > 10_000_000:
                raise ValueError("Draw exceeds vertex/index budget")
            self.prepare_arrays(maximum)
            if name == "glDrawElements":
                function(*raw[:-1], pointer)
            else:
                function(*raw)
            self.draws += 1
            if self.runtime.frame_limit is not None:
                error = self.gl_function("glGetError", C.c_uint)()
                if error:
                    if error not in self.error_queue:
                        self.error_queue.append(error)
                    self.runtime.trace[-1]["gl_error"] = error
            return None
        if name in ("glGetIntegerv", "glGetFloatv", "glGetBooleanv"):
            sizes = {0x0B70: 2, 0x846D: 2, 0x846E: 2, 0x0D3A: 2,
                     0x8005: 4, 0x0C22: 4, 0x0C23: 4, 0x0C10: 4, 0x0BA2: 4}
            count = sizes.get(raw[0], 1)
            dynamic = {0x86A3: 0x86A2, 0x8DF8: 0x8DF9, 0x87FF: 0x87FE}
            if raw[0] in dynamic:
                length = C.c_int()
                self.gl_function("glGetIntegerv", None, C.c_uint, C.c_void_p)(
                    dynamic[raw[0]], C.byref(length))
                count = length.value
            if not 0 <= count <= 4096:
                raise ValueError("Excessive GL query result length")
            scalar = C.c_int if name == "glGetIntegerv" else C.c_float if name == "glGetFloatv" else C.c_ubyte
            output = (scalar * 4096)()
            function(raw[0], C.cast(output, C.c_void_p))
            if name == "glGetIntegerv":
                # Bound the virtual device to the ES2 texture-unit contract.
                # Older SDK state caches are not sized for Mesa's 192 units.
                ceilings = {0x8872: 8, 0x8B4D: 8, 0x8B4C: 0}
                if raw[0] in ceilings:
                    output[0] = min(output[0], ceilings[raw[0]])
            self.runtime.cpu.mem_write(raw[1], bytes(output)[:count * C.sizeof(scalar)])
            self.runtime.trace[-1]["output"] = list(output[:count])
            return None
        if name in ("glGetShaderiv", "glGetProgramiv", "glGetBufferParameteriv",
                    "glGetRenderbufferParameteriv", "glGetFramebufferAttachmentParameteriv",
                    "glGetTexParameteriv", "glGetTexParameterfv",
                    "glGetVertexAttribiv", "glGetVertexAttribfv"):
            count = 4 if name.startswith("glGetVertexAttrib") and raw[1] == 0x8626 else 1
            scalar = C.c_float if name.endswith("fv") else C.c_int
            output = (scalar * 16)()
            function(*raw[:-1], C.cast(output, C.c_void_p))
            self.runtime.cpu.mem_write(raw[-1], bytes(output)[:count * C.sizeof(scalar)])
            self.runtime.trace[-1]["output"] = list(output[:count])
            return None
        if name == "glGetShaderPrecisionFormat":
            limits = (C.c_int * 2)()
            precision = C.c_int()
            function(raw[0], raw[1], C.cast(limits, C.c_void_p), C.byref(precision))
            self.runtime.cpu.mem_write(raw[2], bytes(limits))
            self.write_int(raw[3], precision.value)
            return None
        if name in ("glGenTextures", "glGenBuffers", "glGenFramebuffers", "glGenRenderbuffers"):
            count = raw[0]
            if count > 65536:
                raise ValueError("Excessive GL object count")
            output = (C.c_uint * count)()
            function(count, C.cast(output, C.c_void_p))
            self.runtime.cpu.mem_write(raw[1], bytes(output))
            return None
        if name in ("glDeleteTextures", "glDeleteBuffers", "glDeleteFramebuffers", "glDeleteRenderbuffers"):
            buffer = self.input_buffer(raw[1], raw[0] * 4)
            function(raw[0], buffer)
            deleted = {value[0] for value in struct.iter_unpack("<I", buffer.raw)}
            if name == "glDeleteBuffers":
                # GLES detaches a deleted buffer from current bindings and
                # vertex attributes. Stale index bindings would make a guest
                # offset look like a host pointer to the next glDrawElements.
                if self.array_buffer in deleted:
                    self.array_buffer = 0
                if self.element_buffer in deleted:
                    self.element_buffer = 0
                for vertex in self.vertex_arrays.values():
                    if vertex["buffer"] in deleted:
                        vertex["buffer"] = 0
                for identifier in deleted:
                    self.buffer_data.pop(identifier, None)
            elif name == "glDeleteTextures":
                for binding, identifier in self.texture_bindings.items():
                    if identifier in deleted:
                        self.texture_bindings[binding] = 0
            return None
        if name in ("glGetUniformLocation", "glGetAttribLocation", "glBindAttribLocation"):
            text = self.runtime.string(raw[-1])
            buffer = C.create_string_buffer(text.encode())
            result = function(*raw[:-1], C.cast(buffer, C.c_void_p))
            self.runtime.trace[-1]["name"] = text
            self.runtime.trace[-1]["result"] = result
            return result
        if name == "glShaderSource":
            shader, count, strings_pointer, lengths_pointer = raw
            if count > 1024:
                raise ValueError("Excessive shader source fragments")
            pointers = struct.unpack("<" + "I" * count,
                                     self.runtime.cpu.mem_read(strings_pointer, count * 4))
            lengths = (struct.unpack("<" + "i" * count,
                                     self.runtime.cpu.mem_read(lengths_pointer, count * 4))
                       if lengths_pointer else [-1] * count)
            buffers = []
            for pointer, length in zip(pointers, lengths):
                if length < 0:
                    content = self.runtime.string(pointer).encode()
                else:
                    if length > 1024 * 1024:
                        raise ValueError("Excessive shader source size")
                    content = bytes(self.runtime.cpu.mem_read(pointer, length))
                buffers.append(C.create_string_buffer(content))
            pointers = (C.c_void_p * count)(*[C.cast(buffer, C.c_void_p).value for buffer in buffers])
            sizes = (C.c_int * count)(*[len(buffer.raw) - 1 for buffer in buffers])
            function(shader, count, C.cast(pointers, C.c_void_p), C.cast(sizes, C.c_void_p))
            self.runtime.trace[-1]["source"] = b"".join(buffer.raw[:-1] for buffer in buffers).decode(errors="replace")
            return None
        if name in ("glGetShaderInfoLog", "glGetProgramInfoLog", "glGetShaderSource"):
            maximum = raw[1]
            if maximum > 1024 * 1024:
                raise ValueError("Excessive shader log size")
            buffer = C.create_string_buffer(max(maximum, 1))
            length = C.c_int()
            function(raw[0], maximum, C.byref(length), C.cast(buffer, C.c_void_p))
            self.write_int(raw[2], length.value)
            if raw[3] and maximum:
                self.runtime.cpu.mem_write(raw[3], buffer.raw[:min(maximum, length.value + 1)])
            self.runtime.trace[-1]["text"] = buffer.value.decode(errors="replace")
            return None
        if name in ("glGetActiveUniform", "glGetActiveAttrib"):
            maximum = raw[2]
            if maximum > 1024 * 1024:
                raise ValueError("Excessive GL symbol-name buffer size")
            length, size, type_ = C.c_int(), C.c_int(), C.c_uint()
            buffer = C.create_string_buffer(max(maximum, 1))
            function(raw[0], raw[1], maximum, C.byref(length), C.byref(size),
                     C.byref(type_), C.cast(buffer, C.c_void_p))
            self.write_int(raw[3], length.value)
            self.write_int(raw[4], size.value)
            self.write_int(raw[5], type_.value)
            if raw[6] and maximum:
                self.runtime.cpu.mem_write(raw[6], buffer.raw[:min(maximum, length.value + 1)])
            self.runtime.trace[-1]["name"] = buffer.value.decode(errors="replace")
            return None
        if name in ("glBufferData", "glBufferSubData"):
            position = 2 if name == "glBufferData" else 3
            length = raw[position - 1]
            args = raw[:]
            buffer = self.input_buffer(raw[position], length) if raw[position] else None
            args[position] = buffer
            function(*args)
            bound = self.array_buffer if raw[0] == 0x8892 else self.element_buffer if raw[0] == 0x8893 else 0
            if bound:
                if name == "glBufferData":
                    self.buffer_data[bound] = bytearray(buffer.raw[:length] if buffer is not None else bytes(length))
                else:
                    if bound not in self.buffer_data or raw[1] + length > len(self.buffer_data[bound]):
                        raise ValueError("Buffer update exceeds allocated GPU storage")
                    self.buffer_data[bound][raw[1]:raw[1] + length] = buffer.raw[:length]
            return None
        vertex = re.fullmatch(r"glVertexAttrib([1-4])fv", name)
        if vertex:
            buffer = self.input_buffer(raw[1], int(vertex[1]) * 4)
            function(raw[0], buffer)
            return None
        uniform = re.fullmatch(r"glUniform([1-4])([if])v", name)
        matrix = re.fullmatch(r"glUniformMatrix([2-4])fv", name)
        if uniform or matrix:
            factor = int(uniform[1]) if uniform else int(matrix[1]) ** 2
            buffer = self.input_buffer(raw[-1], raw[1] * factor * 4)
            function(*raw[:-1], buffer)
            return None
        if name in ("glTexImage2D", "glTexSubImage2D", "glReadPixels"):
            if name == "glTexImage2D":
                width, height, format_, type_, pointer = raw[3], raw[4], raw[6], raw[7], raw[8]
            elif name == "glTexSubImage2D":
                width, height, format_, type_, pointer = raw[4], raw[5], raw[6], raw[7], raw[8]
            else:
                width, height, format_, type_, pointer = raw[2], raw[3], raw[4], raw[5], raw[6]
            length = self.pixel_bytes(width, height, format_, type_,
                                      self.pack_alignment if name == "glReadPixels" else self.unpack_alignment)
            if name == "glReadPixels":
                buffer = C.create_string_buffer(length)
                function(*raw[:-1], C.cast(buffer, C.c_void_p))
                self.runtime.cpu.mem_write(pointer, buffer.raw)
            else:
                buffer = self.input_buffer(pointer, length) if pointer else None
                function(*raw[:-1], buffer)
            return None
        if name in ("glCompressedTexImage2D", "glCompressedTexSubImage2D"):
            buffer = self.input_buffer(raw[-1], raw[-2]) if raw[-1] else None
            function(*raw[:-1], buffer)
            return None
        if any("*" in declaration for declaration, _ in declarations):
            raise GraphicsUnsupported("GL pointer marshalling not implemented: " + name)
        args = []
        for value, ctype in zip(raw, types):
            if ctype == C.c_float:
                value = struct.unpack("<f", struct.pack("<I", value))[0]
            elif ctype in (C.c_int, C.c_ssize_t) and value & 0x80000000:
                value -= 1 << 32
            args.append(value)
        result = function(*args)
        if name == "glPixelStorei":
            if raw[0] == 0x0CF5:
                self.unpack_alignment = raw[1]
            elif raw[0] == 0x0D05:
                self.pack_alignment = raw[1]
        if name == "glBindBuffer":
            if raw[0] == 0x8892:
                self.array_buffer = raw[1]
            elif raw[0] == 0x8893:
                self.element_buffer = raw[1]
        if name == "glActiveTexture":
            self.active_texture = raw[0]
        elif name == "glBindTexture":
            self.texture_bindings[(self.active_texture, raw[0])] = raw[1]
        if name == "glEnableVertexAttribArray":
            self.enabled_arrays.add(raw[0])
        elif name == "glDisableVertexAttribArray":
            self.enabled_arrays.discard(raw[0])
        return result

    def prepare_arrays(self, maximum):
        if maximum < 0:
            return
        bind = self.gl_function("glBindBuffer", None, C.c_uint, C.c_uint)
        pointer_function = self.gl_function("glVertexAttribPointer", None, C.c_uint,
                                            C.c_int, C.c_uint, C.c_ubyte, C.c_int, C.c_void_p)
        for index in self.enabled_arrays:
            if index not in self.vertex_arrays:
                raise ValueError("Enabled vertex array has no defined pointer")
            vertex = self.vertex_arrays[index]
            if vertex["buffer"]:
                continue
            element = {0x1400: 1, 0x1401: 1, 0x1402: 2, 0x1403: 2,
                       0x1404: 4, 0x1405: 4, 0x1406: 4, 0x140C: 4}.get(vertex["type"])
            if element is None:
                raise GraphicsUnsupported("Unsupported client vertex type")
            stride = vertex["stride"] or vertex["size"] * element
            length = maximum * stride + vertex["size"] * element
            buffer = self.input_buffer(vertex["pointer"], length)
            self.draw_buffers.append(buffer)
            bind(0x8892, 0)
            pointer_function(index, vertex["size"], vertex["type"], vertex["normalized"],
                             vertex["stride"], C.cast(buffer, C.c_void_p))
        bind(0x8892, self.array_buffer)

    def present(self):
        # eglSwapBuffers submits the completed frame. A glFinish here forces
        # the CPU to wait for the entire GPU queue after every presentation.
        # Pbuffers do not wait for vertical refresh as the original display did.
        # Pace the virtual display at 60 Hz using real elapsed time.
        now = time.monotonic_ns()
        due = self.last_present_ns + 1_000_000_000 // 60
        if now < due:
            time.sleep((due - now) / 1_000_000_000)
        completed = time.monotonic_ns()
        if self.last_present_ns:
            self.frame_durations.append((completed - self.last_present_ns) / 1_000_000)
        self.last_present_ns = completed
        self.frames += 1
        self.runtime.input.on_frame(self.frames)
        if self.runtime.window:
            if self.runtime.window.status:
                self.runtime.window.set_status(None)
            self.runtime.window.pump()
            if self.runtime.window.show_fps:
                self.runtime.window.update_frame_rate(self.frame_stats()["recent_fps"])
        if self.runtime.frame_limit is not None and self.frames >= self.runtime.frame_limit:
            raise FrameLimitReached(f"Captured {self.frames} requested frame(s)")

    def frame_stats(self):
        values = sorted(self.frame_durations)
        return {"recent_fps": round(1000 * len(values) / sum(values), 2) if values and sum(values) else 0,
                "p95_frame_ms": round(values[int((len(values) - 1) * 0.95)], 2) if values else 0,
                "sample_frames": len(values)}

    def reset_frame_timing(self):
        self.last_present_ns = 0
        self.frame_durations.clear()

    def capture_frame(self, frame):
        if self.runtime.input.wheel_steps and self.runtime.input.zoom_gesture is None:
            self.sample_zoom_knob()
        limit = self.runtime.frame_limit
        capture = limit is not None and (frame <= 3 or frame % 30 == 0 or frame >= limit)
        if capture:
            width, height = self.runtime.surface_width, self.runtime.surface_height
            pixels = C.create_string_buffer(width * height * 4)
            self.gl_function("glReadPixels", None, C.c_int, C.c_int, C.c_int,
                             C.c_int, C.c_uint, C.c_uint, C.c_void_p)(
                0, 0, width, height, 0x1908, 0x1401, C.cast(pixels, C.c_void_p))
            raw = pixels.raw
            pitch = width * 4
            rows = b"".join(b"\0" + raw[row * pitch:(row + 1) * pitch]
                            for row in range(height - 1, -1, -1))
            def chunk(kind, data):
                return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
            png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
                   + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))
            destination = self.runtime.frames_directory / f"frame-{frame:04}.png"
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(png)
            self.captures.append({"frame": frame, "path": str(destination),
                                  "draw_calls": self.draws,
                                  "distinct_pixel_bytes": len(set(raw))})
            print(f"Captured frame {frame}: {destination}", flush=True)

    @staticmethod
    def locate_zoom_knob(raw, width, height, left, top):
        """Find the round blue slider thumb in a small, current HUD readback."""
        rows = []
        for y in range(height):
            xs = []
            offset = (height - y - 1) * width * 4
            for x in range(width):
                r, g, b = raw[offset + x * 4:offset + x * 4 + 3]
                if b > r + 12 and b > g + 8:
                    xs.append(x)
            rows.append(xs if 8 <= len(xs) <= 36 else [])
        groups, start = [], None
        for index in range(height + 1):
            valid = index < height and bool(rows[index])
            if valid and start is None:
                start = index
            elif not valid and start is not None:
                if 8 <= index - start <= 40 and max(map(len, rows[start:index])) >= 12:
                    groups.append((start, index))
                start = None
        if not groups:
            return None
        start, end = max(groups, key=lambda group: sum(map(len, rows[group[0]:group[1]])))
        xs = [x for row in rows[start:end] for x in row]
        return left + round(sum(xs) / len(xs)), top + (start + end - 1) // 2

    def sample_zoom_knob(self):
        width, height = self.runtime.surface_width, self.runtime.surface_height
        left, top, bottom = width - 48, round(height * 0.08), round(height * 0.52)
        pixels = C.create_string_buffer(48 * (bottom - top) * 4)
        self.gl_function("glReadPixels", None, C.c_int, C.c_int, C.c_int,
                         C.c_int, C.c_uint, C.c_uint, C.c_void_p)(
            left, height - bottom, 48, bottom - top, 0x1908, 0x1401, pixels)
        position = self.locate_zoom_knob(pixels.raw, 48, bottom - top, left, top)
        self.runtime.input.observe_zoom_knob(position)

    def input_buffer(self, pointer, length):
        if length == 0:
            return (C.c_char * 0)()
        address = self.runtime.memory.pointer(pointer, length)
        return (C.c_char * length).from_address(address.value)

    def pixel_bytes(self, width, height, format_, type_, alignment):
        if width > 16384 or height > 16384 or alignment not in (1, 2, 4, 8):
            raise ValueError("Invalid pixel transfer dimensions/alignment")
        if type_ in (0x8033, 0x8034, 0x8363):
            pixel_bytes = 2
        elif type_ == 0x1401:
            components = {0x1906: 1, 0x1907: 3, 0x1908: 4, 0x1909: 1, 0x190A: 2}
            if format_ not in components:
                raise GraphicsUnsupported(f"Unknown pixel format {format_:#x}")
            pixel_bytes = components[format_]
        else:
            raise GraphicsUnsupported(f"Unknown pixel type {type_:#x}")
        pitch = (width * pixel_bytes + alignment - 1) & ~(alignment - 1)
        size = 0 if not height else pitch * (height - 1) + width * pixel_bytes
        if size > 64 * 1024 * 1024:
            raise ValueError("Pixel transfer exceeds memory budget")
        return size

    def dispatch(self, name):
        self.calls += 1
        return self.egl_call(name) if name.startswith("egl") else self.gl_call(name)

    def close(self):
        if getattr(self, "closed", False):
            return
        self.closed = True
        self.make_current(None, None, None)
        for context in self.contexts:
            self.egl_function("eglDestroyContext", C.c_uint, C.c_void_p, C.c_void_p)(self.display, context)
        for surface in self.surfaces:
            self.egl_function("eglDestroySurface", C.c_uint, C.c_void_p, C.c_void_p)(self.display, surface)
        self.egl_function("eglTerminate", C.c_uint, C.c_void_p)(self.display)
