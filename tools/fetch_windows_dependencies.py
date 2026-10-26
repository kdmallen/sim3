"""Fetch the pinned Windows dependencies with TLS and hash verification."""
import argparse
import concurrent.futures
import hashlib
import json
from pathlib import Path
import shutil
import tarfile
import urllib.request


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=root / "local-data/windows-deps")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    records = json.loads((root / "research/windows-dependencies.json").read_text())
    def fetch(record):
        target = args.output / record["filename"]
        def valid(path):
            if not path.is_file():
                return False
            with path.open("rb") as stream:
                return hashlib.file_digest(stream, record["algorithm"]).hexdigest() == record["expected_hash"]
        if not valid(target):
            temporary = target.with_name(target.name + ".download")
            try:
                with urllib.request.urlopen(record["url"], timeout=60) as source, temporary.open("wb") as output:
                    shutil.copyfileobj(source, output)
                if not valid(temporary):
                    raise ValueError("Dependency checksum mismatch: " + target.name)
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
        print("Verified", target.name, flush=True)
        return dict(record, path=str(target), hash_verified=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        verified = list(pool.map(fetch, records))
    by_name = {record["name"]: record for record in verified}
    (args.output / "verified-downloads.json").write_text(json.dumps(
        [by_name["python"], by_name["unicorn"]], indent=2))
    (args.output / "angle-download.json").write_text(json.dumps(by_name["angle"], indent=2))
    source = by_name["unicorn_source"]
    (args.output / "unicorn-source-download.json").write_text(json.dumps(
        {"url": source["url"], "path": source["path"], "sha256": source["expected_hash"]}, indent=2))
    with tarfile.open(source["path"]) as archive:
        for name in ("COPYING", "COPYING.LIB", "LICENSE"):
            data = archive.extractfile("unicorn-2.1.4/src/qemu/" + name).read()
            (args.output / ("unicorn-" + name)).write_bytes(data)


if __name__ == "__main__":
    main()
