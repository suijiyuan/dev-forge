"""Render a network-free installer for PowerShell parser validation."""

from __future__ import annotations

import sys
import tempfile
import zipfile
from pathlib import Path

from dev_forge.core import Config, build_bundle


def main() -> None:
    target = Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory() as temp:
        output_dir = Path(temp) / "out"

        def fake_download(_url: str, destination: Path) -> str:
            destination.write_bytes(b"x")
            return "2d711642b726b04401627ca9fbac32f5c8530fb1903cc4db02258717921a4881"

        archive_path = build_bundle(
            Config("1.134.0", "user", "x64", (), None, output_dir),
            downloader=fake_download,
            progress=lambda _message: None,
        )
        with zipfile.ZipFile(archive_path) as archive:
            installer = next(
                name for name in archive.namelist() if name.endswith("/install.ps1")
            )
            target.write_bytes(archive.read(installer))


if __name__ == "__main__":
    main()
