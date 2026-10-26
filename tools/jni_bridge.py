"""Small, explicit JNI adapter for game-owned platform services.

This is not an Android/Java virtual machine. Unknown classes, fields or calls
stop the probe. Objects are retained for the bounded research run.
"""
import json
from pathlib import Path
import struct


class JniUnsupported(RuntimeError):
    pass


class JniBridge:
    MAIN = "com/ea/thesims3/MainActivity"

    def __init__(self, runtime):
        self.runtime = runtime
        self.objects = {}
        self.next_object = 0x72000001
        self.classes = {}
        self.fields = {}
        self.utf_buffers = {}
        self.exception = 0
        layout = json.loads((Path(__file__).resolve().parents[1] /
                             "research/jni-interface.json").read_text())["slots"]
        slots = max(layout.values()) + 1
        functions = [0] * slots
        for name, index in layout.items():
            functions[index] = runtime.import_trap("jni_" + name)
        table = runtime.allocate(slots * 4)
        runtime.cpu.mem_write(table, struct.pack("<" + "I" * slots, *functions))
        self.env = runtime.allocate(4)
        runtime.cpu.mem_write(self.env, struct.pack("<I", table))
        vm_functions = [0, 0, 0] + [runtime.import_trap("jvm_" + name) for name in (
            "DestroyJavaVM", "AttachCurrentThread", "DetachCurrentThread", "GetEnv",
            "AttachCurrentThreadAsDaemon")]
        vm_table = runtime.allocate(len(vm_functions) * 4)
        runtime.cpu.mem_write(vm_table, struct.pack("<8I", *vm_functions))
        self.vm = runtime.allocate(4)
        runtime.cpu.mem_write(self.vm, struct.pack("<I", vm_table))
        self.attached = True
        self.data_path = self.object("string", "/game/content.obb")
        self.save_path = self.object("string", "/saves")
        self.boolean_fields = {"mIsUserMusicPlaying": False, "mEnableMinimize": False}

    def object(self, kind, value):
        handle = self.next_object
        self.next_object += 1
        self.objects[handle] = (kind, value)
        return handle

    def value(self, handle, expected=None):
        if handle not in self.objects:
            raise ValueError("Unknown JNI object handle")
        kind, value = self.objects[handle]
        if expected is not None and kind != expected:
            raise ValueError(f"Expected JNI {expected}, got {kind}")
        return value

    def dispatch(self, name):
        a = self.runtime.argument
        if name.startswith("jvm_"):
            if a(0) != self.vm:
                raise ValueError("Unknown JavaVM interface pointer")
            if name in ("jvm_AttachCurrentThread", "jvm_AttachCurrentThreadAsDaemon"):
                self.attached = True
                self.runtime.cpu.mem_write(a(1), struct.pack("<I", self.env))
                return 0
            if name == "jvm_DetachCurrentThread":
                self.attached = False
                return 0
            if name == "jvm_GetEnv":
                if a(2) not in (0x00010002, 0x00010004, 0x00010006):
                    return 0xFFFFFFFD  # JNI_EVERSION
                if not self.attached:
                    return 0xFFFFFFFE  # JNI_EDETACHED
                self.runtime.cpu.mem_write(a(1), struct.pack("<I", self.env))
                return 0
            raise JniUnsupported("JavaVM interface call not implemented: " + name)
        if a(0) != self.env:
            raise ValueError("Unknown JNIEnv pointer")
        record = self.runtime.trace[-1]
        if name == "jni_GetVersion":
            return 0x00010006
        if name == "jni_FindClass":
            class_name = self.runtime.string(a(1))
            record["class"] = class_name
            if class_name != self.MAIN:
                raise JniUnsupported("JNI class not implemented: " + class_name)
            if class_name not in self.classes:
                self.classes[class_name] = self.object("class", class_name)
            return self.classes[class_name]
        if name == "jni_GetStaticFieldID":
            class_name = self.value(a(1), "class")
            field, signature = self.runtime.string(a(2)), self.runtime.string(a(3))
            record.update({"class": class_name, "field": field, "signature": signature})
            supported = ((field in ("m_SDCardAbsolutePath", "m_SaveDataPath") and signature == "Ljava/lang/String;")
                         or (field in self.boolean_fields and signature == "Z"))
            if class_name != self.MAIN or not supported:
                raise JniUnsupported(f"JNI static field not implemented: {class_name}.{field} {signature}")
            key = (class_name, field, signature)
            if key not in self.fields:
                self.fields[key] = self.object("field", key)
            return self.fields[key]
        if name == "jni_GetStaticObjectField":
            class_name = self.value(a(1), "class")
            field_class, field, signature = self.value(a(2), "field")
            if field_class != class_name:
                raise ValueError("JNI field/class mismatch")
            if field == "m_SDCardAbsolutePath":
                return self.data_path
            if field == "m_SaveDataPath":
                return self.save_path
        if name in ("jni_GetStaticBooleanField", "jni_SetStaticBooleanField"):
            class_name = self.value(a(1), "class")
            field_class, field, signature = self.value(a(2), "field")
            if field_class != class_name or signature != "Z" or field not in self.boolean_fields:
                raise ValueError("JNI boolean field/class mismatch")
            if name == "jni_SetStaticBooleanField":
                self.boolean_fields[field] = bool(a(3))
                return None
            return int(self.boolean_fields[field])
        if name == "jni_NewStringUTF":
            return self.object("string", self.runtime.string(a(1)))
        if name in ("jni_GetStringUTFLength", "jni_GetStringLength"):
            value = self.value(a(1), "string")
            return len(value.encode("utf-8")) if name == "jni_GetStringUTFLength" else len(value.encode("utf-16le")) // 2
        if name == "jni_GetStringUTFChars":
            value = self.value(a(1), "string")
            if a(1) not in self.utf_buffers:
                encoded = value.encode() + b"\0"
                pointer = self.runtime.allocate(len(encoded))
                self.runtime.cpu.mem_write(pointer, encoded)
                self.utf_buffers[a(1)] = pointer
            if a(2):
                self.runtime.cpu.mem_write(a(2), b"\1")
            return self.utf_buffers[a(1)]
        if name == "jni_ReleaseStringUTFChars":
            self.value(a(1), "string")
            if self.utf_buffers.get(a(1)) != a(2):
                raise ValueError("JNI UTF buffer/object mismatch")
            return None
        if name == "jni_NewGlobalRef":
            if a(1):
                self.value(a(1))
            return a(1)
        if name in ("jni_DeleteGlobalRef", "jni_DeleteLocalRef"):
            if a(1):
                self.value(a(1))
            return None
        if name == "jni_IsSameObject":
            return int(a(1) == a(2))
        if name in ("jni_ExceptionOccurred", "jni_ExceptionCheck"):
            return int(bool(self.exception)) if name == "jni_ExceptionCheck" else self.exception
        if name == "jni_ExceptionClear":
            self.exception = 0
            return None
        raise JniUnsupported("JNI method not implemented: " + name)
