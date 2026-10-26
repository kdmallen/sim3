"""Read-only game archives and an isolated save directory for the guest."""
import io
import datetime
from pathlib import Path, PurePosixPath
import struct
import zipfile


class FileUnsupported(RuntimeError):
    pass


class FileSystem:
    CONTENT = "/game/content.obb"

    def __init__(self, runtime, apk=None, obb=None, resources=None, saves=None):
        self.runtime = runtime
        self.apk_path, self.obb_path = apk, obb
        self.apk = zipfile.ZipFile(apk) if apk else None
        self.obb = zipfile.ZipFile(obb) if obb else None
        self.resources = resources
        self.saves = saves
        if saves is not None:
            saves.mkdir(parents=True, exist_ok=True)
        self.mounts = {}
        self.files = {}
        self.last_error = 0
        self.last_error_text = ""
        self.opened_paths = []
        self.user_filesystems = []
        self.resolution = "LowRes"

    def safe_relative(self, name):
        name = name.replace("\\", "/")
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or ":" in name or not name:
            raise ValueError("Unsafe guest file path")
        return path

    def normalize(self, path):
        path = path.replace("\\", "/")
        if ":/" in path:
            scheme, rest = path.split(":", 1)
            path = scheme + "://" + rest.lstrip("/")
        return path

    def error(self, message):
        self.last_error = 1
        self.last_error_text = str(message)

    def file_object(self, handle):
        if handle not in self.files:
            raise ValueError("Unknown guest file handle")
        return self.files[handle]

    def new_file(self, file, path, writable=False):
        handle = self.runtime.allocate(16)
        if not handle:
            raise RuntimeError("Unable to allocate guest file handle")
        self.files[handle] = {"stream": file, "path": path, "writable": writable}
        self.opened_paths.append(path)
        return handle

    def archive_entry(self, path):
        path = self.normalize(path)
        for prefix, archive in self.mounts.items():
            if path.startswith(prefix):
                entry = str(self.safe_relative(path[len(prefix):]))
                if entry in archive.namelist():
                    return archive, entry
                return None
        relative = path.lstrip("/")
        for prefix in ("rom://", "rom:/", "./"):
            if relative.startswith(prefix):
                relative = relative[len(prefix):]
                break
        if ":" not in relative and not relative.startswith("/"):
            relative = str(self.safe_relative(relative))
            for archive in (self.apk, self.obb):
                if archive:
                    for entry in ("assets/" + relative, relative,
                                  "assets/" + self.resolution + "/" + relative):
                        if entry in archive.namelist():
                            return archive, entry
        return None

    def save_path(self, path, default=False):
        path = self.normalize(path)
        if path in ("/saves", "/saves/", "ram://"):
            if self.saves is None:
                raise FileUnsupported("No save directory is configured")
            return self.saves.resolve()
        for prefix in ("ram://", "ram:/", "/saves/"):
            if path.startswith(prefix):
                if self.saves is None:
                    raise FileUnsupported("No save directory is configured")
                relative = self.safe_relative(path[len(prefix):])
                target = self.saves.joinpath(*relative.parts).resolve()
                if not target.is_relative_to(self.saves.resolve()):
                    raise ValueError("Save path escapes its root")
                return target
        if default and not path.startswith("/") and ":" not in path:
            if self.saves is None:
                raise FileUnsupported("No save directory is configured")
            relative = self.safe_relative(path)
            target = self.saves.joinpath(*relative.parts).resolve()
            if not target.is_relative_to(self.saves.resolve()):
                raise ValueError("Save path escapes its root")
            return target
        return None

    def resource_path(self, path):
        path = self.normalize(path).lstrip("/")
        if not self.user_filesystems or self.resources is None or ":" in path or path.startswith("/"):
            return None
        relative = self.safe_relative(path)
        root = (self.resources / self.resolution).resolve()
        candidate = root.joinpath(*relative.parts).resolve()
        if not candidate.is_relative_to(root):
            raise ValueError("Resource path escapes its root")
        return candidate if candidate.exists() else None

    def read_path(self, path):
        target = self.save_path(path)
        if target is not None:
            return target.read_bytes()
        match = self.archive_entry(path)
        if match:
            archive, entry = match
            if archive.getinfo(entry).file_size > 64 * 1024 * 1024:
                raise ValueError("Game file exceeds transfer budget")
            if entry.endswith("/res.dz"):
                self.resolution = "HighRes" if "/HighRes/" in entry else "LowRes"
            return archive.read(entry)
        resource = self.resource_path(path)
        if resource is not None:
            return resource.read_bytes()
        target = self.save_path(path, default=True)
        if target is not None and target.is_file():
            return target.read_bytes()
        raise FileNotFoundError(path)

    def dispatch(self, name):
        a = self.runtime.argument
        cpu = self.runtime.cpu
        record = self.runtime.trace[-1]
        if name == "s3eFileAddUserFileSys":
            callbacks = struct.unpack("<16I", cpu.mem_read(a(0), 64))
            if not all(callbacks[index] for index in range(9)) or len(self.user_filesystems) >= 4:
                self.error("Invalid/excessive user filesystem registration")
                return 1
            if self.resources is None or not (self.resources / self.resolution / "inventory.json").is_file():
                raise FileUnsupported("Validated extracted DTRZ resources are required for the native resource backend")
            self.user_filesystems.append(callbacks)
            record.update({"callbacks": list(callbacks), "backend": "read_only_extracted_DTRZ"})
            return 0
        if name == "vfs_slot_0":
            return self.last_error
        if name == "vfs_slot_1":
            return self.runtime.get_graphics().guest_string(self.last_error_text.encode())
        if name == "vfs_slot_2":
            source, prefix = self.runtime.string(a(0)), self.runtime.string(a(1))
            record.update({"archive": source, "mount": prefix})
            if source != self.CONTENT or self.obb is None:
                self.error("The matching OBB was not supplied for " + source)
                return 1
            if not prefix or a(2) != 0:
                raise FileUnsupported("Unsupported VFS mount flags/prefix")
            self.mounts[prefix] = self.obb
            return 0
        if name == "vfs_slot_3":
            prefix = self.runtime.string(a(0))
            if prefix not in self.mounts:
                self.error("Unknown VFS mount")
                return 1
            del self.mounts[prefix]
            return 0
        if name == "s3eFileCheckExists":
            path = self.runtime.string(a(0))
            record["path"] = path
            target = self.save_path(path, default=True)
            return int((target is not None and target.exists()) or self.archive_entry(path) is not None
                       or self.resource_path(path) is not None)
        if name == "s3eFileGetFileInt" and a(1) in (0, 1, 2, 3, 4, 5):
            path, property_ = self.runtime.string(a(0)), a(1)
            record.update({"path": path, "property": property_})
            target = self.save_path(path, default=True)
            resource = self.resource_path(path)
            archive = self.archive_entry(path)
            exists = (target is not None and target.exists()) or resource is not None or archive is not None
            directory = ((target is not None and target.is_dir()) or
                         (resource is not None and resource.is_dir()) or
                         (archive is not None and archive[0].getinfo(archive[1]).is_dir()))
            if property_ == 0:
                result = int(exists)
            elif property_ == 1:
                result = int(directory)
            elif property_ == 2:
                result = int(exists and not (target is not None and target.exists()))
            elif property_ == 3:
                result = int(exists)
            elif property_ == 5 and exists:
                if target is not None and target.exists():
                    result = target.stat().st_mtime_ns // 1_000_000
                elif resource is not None:
                    result = resource.stat().st_mtime_ns // 1_000_000
                else:
                    date = archive[0].getinfo(archive[1]).date_time
                    result = int(datetime.datetime(*date, tzinfo=datetime.timezone.utc).timestamp() * 1000)
            elif exists and not directory:
                result = (target.stat().st_size if target is not None and target.is_file() else
                          resource.stat().st_size if resource is not None else
                          archive[0].getinfo(archive[1]).file_size)
            else:
                result = -1
                self.error("File size requested for missing file/directory")
            record["result"] = result
            return result
        if name == "s3eFileOpen":
            path, mode = self.runtime.string(a(0)), self.runtime.string(a(1))
            record.update({"path": path, "mode": mode})
            if mode not in ("r", "rb", "w", "wb", "a", "ab", "r+", "r+b", "rb+", "w+b", "wb+"):
                raise FileUnsupported("Unsupported file mode: " + mode)
            writable = any(char in mode for char in "wa+")
            try:
                if writable:
                    target = self.save_path(path, default=True)
                    if target is None:
                        raise PermissionError("Writes are restricted to guest save storage")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    stream = target.open(mode if "b" in mode else mode + "b")
                else:
                    stream = io.BytesIO(self.read_path(path))
                return self.new_file(stream, path, writable)
            except (OSError, KeyError) as exc:
                self.error(exc)
                record["error"] = str(exc)
                return 0
        if name == "s3eFileOpenFromMemory":
            if a(1) > 64 * 1024 * 1024:
                raise ValueError("Memory file exceeds budget")
            return self.new_file(io.BytesIO(bytes(cpu.mem_read(a(0), a(1)))), "<memory>")
        if name == "s3eFileClose":
            if a(0) not in self.files:
                self.error("Invalid/null file passed to close")
                return 1
            self.file_object(a(0))["stream"].close()
            del self.files[a(0)]
            self.runtime.release(a(0))
            return 0
        if name in ("s3eFileRead", "s3eFileWrite"):
            pointer, size, count, handle = (a(index) for index in range(4))
            file = self.file_object(handle)
            if size * count > 64 * 1024 * 1024:
                raise ValueError("File transfer exceeds budget")
            if not size or not count:
                return 0
            if name == "s3eFileRead":
                content = file["stream"].read(size * count)
                if content:
                    cpu.mem_write(pointer, content)
                return len(content) // size
            if not file["writable"]:
                self.error("Read-only guest file")
                return 0
            return file["stream"].write(bytes(cpu.mem_read(pointer, size * count))) // size
        if name == "s3eFileSeek":
            offset = a(1) - (1 << 32) if a(1) & 0x80000000 else a(1)
            try:
                self.file_object(a(0))["stream"].seek(offset, a(2))
                return 0
            except (OSError, ValueError) as exc:
                self.error(exc)
                return 1
        if name == "s3eFileTell":
            return self.file_object(a(0))["stream"].tell()
        if name == "s3eFileGetSize":
            stream = self.file_object(a(0))["stream"]
            position = stream.tell()
            stream.seek(0, 2)
            length = stream.tell()
            stream.seek(position)
            return length
        if name == "s3eFileGetChar":
            content = self.file_object(a(0))["stream"].read(1)
            return content[0] if content else 0xFFFFFFFF
        if name == "s3eFileGetError":
            return self.last_error
        if name == "s3eFileMakeDirectory":
            path = self.runtime.string(a(0))
            target = self.save_path(path)
            if target is None:
                self.error("Directories are restricted to save storage")
                return 1
            target.mkdir(parents=True, exist_ok=True)
            return 0
        raise FileUnsupported("File interface not implemented: " + name)

    def close(self):
        for file in self.files.values():
            file["stream"].close()
        for archive in (self.apk, self.obb):
            if archive:
                archive.close()
