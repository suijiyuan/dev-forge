"""Extension lock-file parsing and deterministic serialization."""

from __future__ import annotations

import json
import re
from pathlib import Path

from .models import ExtensionRelease, PackagerError


def load_extension_lock(
    path: Path,
    vscode_version: str,
    arch: str,
    extension_ids: list[str],
) -> dict[str, ExtensionRelease]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PackagerError(f"无法读取扩展锁文件 {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise PackagerError(f"扩展锁文件顶层必须是对象: {path}")
    if data.get("schema_version") != 2:
        raise PackagerError(f"扩展锁文件 schema_version 必须为 2: {path}")
    target = data.get("target", {})
    if target != {"vscode_version": vscode_version, "arch": arch}:
        raise PackagerError(
            f"扩展锁文件目标 {target} 与当前 VS Code {vscode_version}/{arch} 不一致"
        )
    raw_entries = data.get("extensions")
    if not isinstance(raw_entries, list):
        raise PackagerError(f"扩展锁文件 extensions 必须是数组: {path}")
    releases: dict[str, ExtensionRelease] = {}
    try:
        for entry in raw_entries:
            extension_id = entry["id"].lower()
            if not all(
                isinstance(entry[field], str) and entry[field]
                for field in (
                    "id",
                    "version",
                    "engine",
                    "target_platform",
                    "download_url",
                    "sha256",
                )
            ):
                raise PackagerError(f"扩展锁文件条目包含空值或非字符串: {path}")
            if extension_id in releases:
                raise PackagerError(f"扩展锁文件包含重复 ID: {extension_id}")
            digest = entry["sha256"].lower()
            if re.fullmatch(r"[0-9a-f]{64}", digest) is None:
                raise PackagerError(f"扩展锁文件 SHA-256 无效: {extension_id}")
            releases[extension_id] = ExtensionRelease(
                extension_id,
                entry["version"],
                entry["engine"],
                None
                if entry["target_platform"] == "universal"
                else entry["target_platform"],
                entry["download_url"],
                digest,
            )
    except (KeyError, TypeError, AttributeError) as exc:
        raise PackagerError(f"扩展锁文件条目格式无效: {path}") from exc
    expected = set(extension_ids)
    actual = set(releases)
    if actual != expected:
        missing = ", ".join(sorted(expected - actual)) or "无"
        extra = ", ".join(sorted(actual - expected)) or "无"
        raise PackagerError(f"扩展锁文件与配置不一致；缺少: {missing}；多余: {extra}")
    return releases


def write_extension_lock(
    path: Path,
    vscode_version: str,
    arch: str,
    releases: list[ExtensionRelease],
) -> None:
    invalid_hashes = [
        release.extension_id
        for release in releases
        if not isinstance(release.sha256, str)
        or re.fullmatch(r"[0-9a-fA-F]{64}", release.sha256) is None
    ]
    if invalid_hashes:
        raise PackagerError(
            "无法写入 SHA-256 无效的扩展锁条目: " + ", ".join(invalid_hashes)
        )
    payload = {
        "schema_version": 2,
        "target": {"vscode_version": vscode_version, "arch": arch},
        "extensions": [
            {
                "id": release.extension_id,
                "version": release.version,
                "engine": release.engine,
                "target_platform": release.target_platform or "universal",
                "download_url": release.download_url,
                "sha256": release.sha256.lower(),
            }
            for release in releases
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    temporary.replace(path)
