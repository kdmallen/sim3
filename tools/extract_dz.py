"""Extract and validate the inspected Sims 3 1.6.11 DTRZ resource archives."""
import argparse
import hashlib
import json
import lzma
from pathlib import Path, PurePosixPath
import struct


def extract(source, destination, progress=None):
    data = source.read_bytes()
    if data[:4] != b"DTRZ":
        raise ValueError("Not a DTRZ archive")
    file_count, directory_count = struct.unpack_from("<HH", data, 4)
    if not 0 < file_count < 10000 or not 0 < directory_count < 1000:
        raise ValueError("Unexpected resource counts")
    cursor = 8

    def string():
        nonlocal cursor
        end = data.index(0, cursor)
        result = data[cursor:end].decode("utf-8")
        cursor = end + 1
        return result

    root = string()
    names = [string() for _ in range(file_count)]
    directories = [root] + [string() for _ in range(directory_count - 1)]
    descriptors = [struct.unpack_from("<3H", data, cursor + i * 6)
                   for i in range(file_count)]
    cursor += file_count * 6
    blocks, metadata_count = struct.unpack_from("<HH", data, cursor)
    cursor += 4
    if blocks != 1 or metadata_count != file_count:
        raise ValueError("Unsupported archive block layout")
    metadata = [struct.unpack_from("<4I", data, cursor + i * 16)
                for i in range(metadata_count)]
    cursor += metadata_count * 16
    if cursor != metadata[0][0]:
        raise ValueError("Resource metadata/data boundary mismatch")
    if sum(item[1] for item in metadata) > 512 * 1024 * 1024:
        raise ValueError("Archive exceeds extraction budget")
    destination.mkdir(parents=True, exist_ok=False)
    inventory = []
    paths = set()
    for i, (directory, name_index, external) in enumerate(descriptors):
        if directory >= len(directories) or name_index >= len(names) or external != 65535:
            raise ValueError("Unsupported resource descriptor")
        logical_name = "/".join(filter(None, (directories[directory], names[name_index])))
        logical_name = logical_name.replace("\\", "/")
        relative = PurePosixPath(logical_name)
        if (relative.is_absolute() or ".." in relative.parts or ":" in logical_name
                or not logical_name or logical_name in paths):
            raise ValueError("Unsafe/duplicate resource path")
        paths.add(logical_name)
        offset, expected, repeated_size, flags = metadata[i]
        end = metadata[i + 1][0] if i + 1 < file_count else len(data)
        if not cursor <= offset < end <= len(data) or expected != repeated_size:
            raise ValueError("Invalid resource extent/size")
        if flags != 0x200 or expected > 32 * 1024 * 1024:
            raise ValueError("Unsupported compression flags or oversized resource")
        compressed = data[offset:end]
        # A declared size limits decompression before allocating output memory.
        if len(compressed) < 13 or int.from_bytes(compressed[5:13], "little") != expected:
            raise ValueError("LZMA header length mismatch")
        decoder = lzma.LZMADecompressor(format=lzma.FORMAT_ALONE,
                                       memlimit=64 * 1024 * 1024)
        unpacked = decoder.decompress(compressed, max_length=expected + 1)
        if len(unpacked) != expected or not decoder.eof or decoder.unused_data:
            raise ValueError("Resource LZMA stream/size mismatch")
        target = destination.joinpath(*relative.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as output:
            output.write(unpacked)
        inventory.append({"path": logical_name, "bytes": expected,
                          "compressed_bytes": len(compressed),
                          "sha256": hashlib.sha256(unpacked).hexdigest()})
        if progress:
            progress(i + 1, file_count)
    (destination / "inventory.json").write_text(json.dumps(inventory, indent=2) + "\n")
    return {"source": str(source), "files": len(inventory),
            "uncompressed_bytes": sum(item["bytes"] for item in inventory),
            "destination": str(destination)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    print(json.dumps(extract(args.source, args.destination), indent=2))


if __name__ == "__main__":
    main()
