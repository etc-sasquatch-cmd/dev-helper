"""Build the portable Windows x64 application folder from the project environment."""

import argparse
import platform
import struct
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--distpath", type=Path, default=root / "dist")
    options = parser.parse_args()
    if platform.system() != "Windows" or platform.machine() != "AMD64" or struct.calcsize("P") != 8:
        parser.error("Build with x64 Python on Windows.")
    dist = options.distpath.resolve()
    if not dist.is_relative_to(root):
        parser.error("Choose an output folder inside the repository.")
    if (dist / "FocusDeck").exists():
        parser.error("FocusDeck already exists there. Choose a new output folder.")

    try:
        from PyInstaller.__main__ import run
    except ImportError as error:
        parser.error(f"Install the portable build dependencies first ({error}).")

    work = root / "build" / "portable"
    work.mkdir(parents=True, exist_ok=True)
    run(
        [
            "--name=FocusDeck",
            "--onedir",
            "--windowed",
            "--noupx",
            f"--distpath={dist}",
            f"--workpath={work}",
            f"--specpath={work}",
            f"--paths={root / 'src'}",
            "--collect-data=nicegui",
            "--copy-metadata=nicegui",
            f"--add-data={root / 'src/focusdeck/db/migrations'}:focusdeck/db/migrations",
            f"--add-data={root / 'src/focusdeck/music/youtube_player.js'}:focusdeck/music",
            str(Path(__file__).with_name("launch.py")),
        ]
    )


if __name__ == "__main__":
    main()
