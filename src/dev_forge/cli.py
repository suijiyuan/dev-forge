from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .core import PackagerError, UpdateResult, auto_update, build_bundle, load_config


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
    lock_group.add_argument(
        "--auto-update",
        action="store_true",
        help="自动检查 VS Code 稳定版与扩展更新并更新配置和锁文件（不构建压缩包）",
    )
    result.add_argument(
        "--no-vscode-update",
        action="store_true",
        help="配合 --auto-update 使用，仅更新扩展锁文件而不升级 VS Code 版本",
    )
    result.add_argument(
        "--github-output",
        type=Path,
        help="配合 --auto-update 使用，将更新结果写入 GitHub Actions GITHUB_OUTPUT 文件",
    )
    return result


def _commit_message_for(result: UpdateResult) -> str:
    if result.vscode_changed:
        return f"feat: update version to {result.target_version}"
    return "feat: update packager lock json"


def _write_github_output(path: Path, result: UpdateResult) -> None:
    lines = [
        f"changed={'true' if result.changed else 'false'}",
        f"vscode_changed={'true' if result.vscode_changed else 'false'}",
        f"lock_changed={'true' if result.lock_changed else 'false'}",
        f"previous_version={result.previous_version}",
        f"target_version={result.target_version}",
        f"commit_message={_commit_message_for(result)}",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write("\n".join(lines) + "\n")


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        config_path = args.config.expanduser().resolve()
        lock_file = (
            None
            if args.no_lock
            else (
                args.lock_file.expanduser().resolve()
                if args.lock_file
                else config_path.with_name("packager.lock.json")
            )
        )
        if args.auto_update:
            if lock_file is None:
                raise PackagerError("--auto-update 需要指定扩展锁文件")
            result = auto_update(
                config_path,
                lock_file,
                check_vscode=not args.no_vscode_update,
            )
            if args.github_output is not None:
                _write_github_output(args.github_output.expanduser(), result)
            if result.changed:
                for change in result.extension_changes:
                    old_label = change.previous_version or "新增"
                    print(
                        f"  扩展更新: {change.extension_id} {old_label} -> {change.new_version}"
                    )
                print(f"完成更新: {result.target_version}")
            else:
                print(f"无更新需要提交: {result.target_version}")
            return 0

        config = load_config(config_path, args.settings, args.output_dir)
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
