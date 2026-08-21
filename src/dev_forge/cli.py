from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .core import PackagerError, build_bundle, load_config


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Dev Forge：创建 Windows VS Code 离线环境压缩包"
    )
    result.add_argument(
        "--config",
        type=Path,
        default=Path("packager.jsonc"),
        help="配置文件（默认: packager.jsonc）",
    )
    result.add_argument("--settings", help="覆盖 settings.json 路径")
    result.add_argument("--output-dir", help="覆盖输出目录")
    result.add_argument("--archive-only", action="store_true", help="成功后仅保留 ZIP")
    result.add_argument(
        "--lock-file",
        type=Path,
        help="扩展锁文件（默认: 配置文件同目录/packager.lock.json）",
    )
    lock_group = result.add_mutually_exclusive_group()
    lock_group.add_argument(
        "--update-lock",
        action="store_true",
        help="重新解析 Marketplace 并更新扩展锁文件",
    )
    lock_group.add_argument(
        "--no-lock",
        action="store_true",
        help="不读写扩展锁文件（非可复现兼容模式）",
    )
    lock_group.add_argument(
        "--locked",
        action="store_true",
        help="要求锁文件存在且与配置完全一致",
    )
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        config_path = args.config.expanduser().resolve()
        config = load_config(config_path, args.settings, args.output_dir)
        lock_file = (
            None
            if args.no_lock
            else (
                args.lock_file.expanduser().resolve()
                if args.lock_file
                else config_path.with_name("packager.lock.json")
            )
        )
        archive = build_bundle(
            config,
            args.archive_only,
            lock_file=lock_file,
            update_lock=args.update_lock,
            locked=args.locked,
        )
        print(f"完成: {archive}")
        return 0
    except (PackagerError, OSError) as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1
