"""Offline bundle assembly and archive creation."""

from __future__ import annotations

import json
import shutil
import tempfile
import zipfile
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import _strip_json_comments
from .installer import _write_support_files
from .manifest import load_extension_lock, write_extension_lock
from .marketplace import download_file, query_extension, sha256_file, validate_vsix
from .models import XML_CATALOG_SETTING_TOKEN, Config, ExtensionRelease, PackagerError


def vscode_download(version: str, package: str, arch: str) -> tuple[str, str]:
    platform_name = {
        ("system", "x64"): "win32-x64",
        ("user", "x64"): "win32-x64-user",
        ("archive", "x64"): "win32-x64-archive",
        ("system", "arm64"): "win32-arm64",
        ("user", "arm64"): "win32-arm64-user",
        ("archive", "arm64"): "win32-arm64-archive",
    }[(package, arch)]
    suffix = ".zip" if package == "archive" else ".exe"
    label = (
        "VSCode"
        if package == "archive"
        else ("VSCodeUserSetup" if package == "user" else "VSCodeSetup")
    )
    filename = f"{label}-{arch}-{version}{suffix}"
    return (
        f"https://update.code.visualstudio.com/{version}/{platform_name}/stable",
        filename,
    )


def _copy_or_create_empty_settings(source: Path | None, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if source is None:
        target.write_text("{}\n", encoding="utf-8", newline="\n")
    else:
        shutil.copy2(source, target)


def _strip_json_trailing_commas(content: str) -> str:
    result = list(content)
    in_string = False
    escaped = False
    for index, char in enumerate(content):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            continue
        if char != ",":
            continue
        following = index + 1
        while following < len(content) and content[following].isspace():
            following += 1
        if following < len(content) and content[following] in "]}":
            result[index] = " "
    return "".join(result)


def _add_xml_catalog_setting(target: Path) -> None:
    try:
        settings = json.loads(
            _strip_json_trailing_commas(
                _strip_json_comments(target.read_text(encoding="utf-8"))
            )
        )
    except (OSError, json.JSONDecodeError, PackagerError) as exc:
        raise PackagerError(
            f"无法为 XML Catalog 更新 settings.json {target}: {exc}"
        ) from exc
    if not isinstance(settings, dict):
        raise PackagerError(f"settings.json 顶层必须是对象: {target}")
    catalogs = settings.get("xml.catalogs", [])
    if not isinstance(catalogs, list) or not all(
        isinstance(item, str) for item in catalogs
    ):
        raise PackagerError(
            f"settings.json 中的 xml.catalogs 必须是字符串数组: {target}"
        )
    if XML_CATALOG_SETTING_TOKEN not in catalogs:
        catalogs.append(XML_CATALOG_SETTING_TOKEN)
    settings["xml.catalogs"] = catalogs
    target.write_text(
        json.dumps(settings, ensure_ascii=False, indent=4) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _copy_resource(source: Path, target: Path, root: Path) -> dict[str, Any]:
    if source.is_dir():
        shutil.copytree(source, target)
        files = [
            {
                "file": item.relative_to(root).as_posix(),
                "sha256": sha256_file(item),
            }
            for item in sorted(target.rglob("*"))
            if item.is_file()
        ]
        return {
            "directory": target.relative_to(root).as_posix(),
            "files": files,
        }
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return {
        "file": target.relative_to(root).as_posix(),
        "sha256": sha256_file(target),
    }


def build_bundle(
    config: Config,
    archive_only: bool = False,
    downloader: Callable[[str, Path], str] = download_file,
    extension_query: Callable[[str, str, str], ExtensionRelease] = query_extension,
    extension_validator: Callable[[Path, ExtensionRelease], None] = validate_vsix,
    progress: Callable[[str], None] = print,
    lock_file: Path | None = None,
    update_lock: bool = False,
    locked: bool = False,
) -> Path:
    config.output_dir.mkdir(parents=True, exist_ok=True)
    bundle_name = f"dev-forge-{config.version}-win32-{config.arch}"
    final_root = config.output_dir / bundle_name
    final_zip = config.output_dir / f"{bundle_name}.zip"
    partial_zip = final_zip.with_suffix(final_zip.suffix + ".part")
    if final_root.exists() or final_zip.exists():
        raise PackagerError(f"输出已存在，请先移动或删除: {final_root} / {final_zip}")

    staging_parent = Path(
        tempfile.mkdtemp(prefix=f".{bundle_name}-", dir=config.output_dir)
    )
    root = staging_parent / bundle_name
    pending_lock = staging_parent / "packager.lock.next.json"
    try:
        (root / "vscode").mkdir(parents=True)
        (root / "extensions").mkdir()
        (root / "user-data").mkdir()

        vscode_url, vscode_name = vscode_download(
            config.version, config.package, config.arch
        )
        vscode_path = root / "vscode" / vscode_name
        progress(f"下载 VS Code {config.version}...")
        vscode_hash = downloader(vscode_url, vscode_path)

        extension_entries = []
        all_extensions = list(
            dict.fromkeys(
                config.extensions
                + tuple(
                    extension_id
                    for _, profile_extensions in config.extension_profiles
                    for extension_id in profile_extensions
                )
            )
        )
        if update_lock and lock_file is None:
            raise PackagerError("update_lock 需要指定 lock_file")
        if locked and lock_file is None:
            raise PackagerError("locked 模式需要指定 lock_file")
        locked_releases: dict[str, ExtensionRelease] | None = None
        if lock_file is not None and lock_file.is_file() and not update_lock:
            locked_releases = load_extension_lock(
                lock_file, config.version, config.arch, all_extensions
            )
        elif locked:
            raise PackagerError(f"扩展锁文件不存在: {lock_file}")

        resolved_releases: list[ExtensionRelease] = []
        for extension_id in all_extensions:
            if locked_releases is None:
                progress(f"解析 {extension_id}...")
                release = extension_query(extension_id, config.version, config.arch)
            else:
                release = locked_releases[extension_id]
                progress(f"使用锁定版本 {extension_id} {release.version}...")
            platform_suffix = (
                f"-{release.target_platform}"
                if release.target_platform not in (None, "", "universal")
                else ""
            )
            filename = f"{extension_id}-{release.version}{platform_suffix}.vsix"
            target = root / "extensions" / filename
            progress(f"下载 {extension_id} {release.version}...")
            digest = downloader(release.download_url, target)
            if release.sha256 is not None and digest.lower() != release.sha256:
                raise PackagerError(
                    f"扩展 {extension_id} SHA-256 与锁文件不一致；"
                    f"期望 {release.sha256}，实际 {digest.lower()}"
                )
            extension_validator(target, release)
            resolved_releases.append(replace(release, sha256=digest.lower()))
            extension_entries.append(
                {
                    "id": extension_id,
                    "version": release.version,
                    "engine": release.engine,
                    "target_platform": release.target_platform or "universal",
                    "file": target.relative_to(root).as_posix(),
                    "sha256": digest,
                    "source": release.download_url,
                }
            )

        if update_lock and lock_file is not None:
            write_extension_lock(
                pending_lock, config.version, config.arch, resolved_releases
            )

        profile_indexes = {
            name: index
            for index, (name, _) in enumerate(config.extension_profiles, start=1)
        }
        xml_catalog_enabled = any(kind == "xml" for kind, _ in config.resources)
        settings_target = root / "user-data" / "default" / "settings.json"
        _copy_or_create_empty_settings(config.settings, settings_target)
        if xml_catalog_enabled:
            _add_xml_catalog_setting(settings_target)
        profile_settings_entries: dict[str, dict[str, Any]] = {}
        for profile_setting in config.profile_settings:
            if profile_setting.use_default:
                profile_settings_entries[profile_setting.name] = {"use_default": True}
                continue
            index = profile_indexes[profile_setting.name]
            profile_target = (
                root / "user-data" / "profiles" / f"profile-{index}" / "settings.json"
            )
            _copy_or_create_empty_settings(profile_setting.source, profile_target)
            if xml_catalog_enabled:
                _add_xml_catalog_setting(profile_target)
            profile_settings_entries[profile_setting.name] = {
                "file": profile_target.relative_to(root).as_posix(),
                "sha256": sha256_file(profile_target),
            }

        resource_names = {
            "keybindings": "keybindings.json",
            "snippets": "snippets",
            "tasks": "tasks.json",
            "mcp": "mcp.json",
            "xml": "xml",
        }
        default_resource_entries: dict[str, dict[str, Any]] = {}
        for kind, source in config.resources:
            target = root / "user-data" / "default" / resource_names[kind]
            default_resource_entries[kind] = _copy_resource(source, target, root)
        profile_resource_entries: dict[str, dict[str, dict[str, Any]]] = {}
        for resource in config.profile_resources:
            profile_entry = profile_resource_entries.setdefault(resource.name, {})
            if resource.use_default:
                profile_entry[resource.kind] = {"use_default": True}
                continue
            index = profile_indexes[resource.name]
            target = (
                root
                / "user-data"
                / "profiles"
                / f"profile-{index}"
                / resource_names[resource.kind]
            )
            profile_entry[resource.kind] = _copy_resource(resource.source, target, root)
        manifest = {
            "schema_version": 4,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "install": {"mode": "replace" if config.replace_extensions else "merge"},
            "vscode": {
                "version": config.version,
                "package": config.package,
                "arch": config.arch,
                "file": vscode_path.relative_to(root).as_posix(),
                "sha256": vscode_hash,
                "source": vscode_url,
            },
            "extensions": extension_entries,
            "extension_profiles": {
                "default": list(config.extensions),
                "profiles": {
                    name: list(extensions)
                    for name, extensions in config.extension_profiles
                },
            },
            "settings": {
                "default": {
                    "file": settings_target.relative_to(root).as_posix(),
                    "sha256": sha256_file(settings_target),
                },
                "profiles": profile_settings_entries,
            },
            "resources": {
                "default": default_resource_entries,
                "profiles": profile_resource_entries,
            },
        }
        _write_support_files(root, config, manifest)
        root.replace(final_root)
        progress("创建 ZIP 压缩包...")
        with zipfile.ZipFile(
            partial_zip,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
        ) as archive:
            for item in sorted(final_root.rglob("*")):
                archive_name = Path(bundle_name) / item.relative_to(final_root)
                if item.is_dir():
                    archive.writestr(archive_name.as_posix().rstrip("/") + "/", "")
                elif item.is_file():
                    archive.write(item, archive_name)
        with zipfile.ZipFile(partial_zip) as archive:
            corrupt_member = archive.testzip()
            if corrupt_member is not None:
                raise PackagerError(f"ZIP 完整性校验失败: {corrupt_member}")
        partial_zip.replace(final_zip)
        if archive_only:
            shutil.rmtree(final_root)
        if update_lock and lock_file is not None:
            lock_file.parent.mkdir(parents=True, exist_ok=True)
            pending_lock.replace(lock_file)
        staging_parent.rmdir()
        return final_zip
    except Exception:
        partial_zip.unlink(missing_ok=True)
        final_zip.unlink(missing_ok=True)
        shutil.rmtree(staging_parent, ignore_errors=True)
        shutil.rmtree(final_root, ignore_errors=True)
        raise
