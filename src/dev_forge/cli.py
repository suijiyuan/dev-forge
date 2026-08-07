from __future__ import annotations

import argparse
from pathlib import Path
import sys

from .core import PackagerError, build_bundle, load_config


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Dev Forge：创建 Windows VS Code 离线环境压缩包")
    result.add_argument("--config", type=Path, default=Path("packager.jsonc"), help="配置文件（默认: packager.jsonc）")
    result.add_argument("--settings", help="覆盖 settings.json 路径")
    result.add_argument("--output-dir", help="覆盖输出目录")
    result.add_argument("--archive-only", action="store_true", help="成功后仅保留 ZIP")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        config_path = args.config.expanduser().resolve()
        config = load_config(config_path, args.settings, args.output_dir)
        archive = build_bundle(config, args.archive_only)
        print(f"完成: {archive}")
        return 0
    except (PackagerError, OSError) as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1
