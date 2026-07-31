from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target-triple", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if not args.target_triple or any(
        character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
        for character in args.target_triple
    ):
        parser.error("target triple contains unsupported characters")

    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "ai-agent-worker.spec",
        ],
        cwd=ROOT,
        check=True,
    )
    extension = ".exe" if sys.platform == "win32" else ""
    source = ROOT / "dist" / f"ai-agent-worker{extension}"
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / f"ai-agent-worker-{args.target_triple}{extension}"
    shutil.copy2(source, destination)
    checksum = hashlib.sha256(destination.read_bytes()).hexdigest()
    checksum_path = destination.with_name(f"{destination.name}.sha256")
    checksum_path.write_text(
        f"{checksum}  {destination.name}\n",
        encoding="utf-8",
    )
    print(destination)
    print(checksum_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
