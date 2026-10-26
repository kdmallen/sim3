"""Cross-link the small Windows entry point with GNU gcc and ld."""
import argparse
from pathlib import Path
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python-dll", type=Path, required=True)
    parser.add_argument("--windows-libs", type=Path, required=True,
                        help="Directory containing PE kernel32.dll and shell32.dll (e.g. Wine's exports)")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source = Path(__file__).with_name("windows_launcher.c")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        obj = str(Path(directory) / "launcher.o")
        subprocess.run(["gcc", "-c", "-Os", "-mabi=ms", "-fshort-wchar", "-fno-stack-protector",
                        "-fno-asynchronous-unwind-tables", "-fno-ident", "-ffreestanding",
                        str(source), "-o", obj], check=True)
        subprocess.run(["ld", "-m", "i386pep", "--subsystem", "windows",
                        "--major-subsystem-version", "6", "--minor-subsystem-version", "1",
                        "--entry", "entry", obj, str(args.python_dll),
                        str(args.windows_libs / "kernel32.dll"), str(args.windows_libs / "shell32.dll"),
                        "-o", str(args.output)], check=True)


if __name__ == "__main__":
    main()
