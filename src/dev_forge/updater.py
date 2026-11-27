"""Automatic VS Code stable version and extension lock-file updater."""

from __future__ import annotations

import json
import re
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .bundle import collect_extension_ids, vscode_download, vscode_platform
from .config import _strip_json_comments, load_config
from .manifest import load_extension_lock, write_extension_lock
from .marketplace import download_file, query_extension, validate_vsix
from .models import ExtensionRelease, PackagerError
from .semver import Version

VSCODE_RELEASES_URL = "https://update.code.visualstudio.com/api/releases/stable"
VSCODE_UPDATE_URL_TEMPLATE = (
    "https://update.code.visualstudio.com/api/update/{platform}/stable/latest"
)
_EXACT_SEMVER_RE = re.compile(r"\d+\.\d+\.\d+")


@dataclass(frozen=True)
class ExtensionChange:
    """Version change summary for a single extension."""

    extension_id: str
    previous_version: str | None
    new_version: str


@dataclass(frozen=True)
class UpdateResult:
    """Outcome of checking and applying VS Code and extension updates."""

    changed: bool
    vscode_changed: bool
    lock_changed: bool
    previous_version: str
    target_version: str
    extension_changes: tuple[ExtensionChange, ...]


def _is_exact_semver(value: str) -> bool:
    return isinstance(value, str) and _EXACT_SEMVER_RE.fullmatch(value) is not None


def _fetch_json_get(url: str, retries: int = 3) -> Any:
    """GET JSON from an upstream endpoint, returning None on HTTP 204."""
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "dev-forge/0.1",
        },
    )
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            with urlopen(request, timeout=30) as response:
                if response.status == 204:
                    return None
                raw = response.read().decode("utf-8").strip()
                if not raw:
                    return None
                return json.loads(raw)
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt + 1 < retries:
                time.sleep(2**attempt)
    raise PackagerError(f"查询 VS Code 最新版本失败 ({url}): {last_error}") from last_error


def check_url_available(url: str, retries: int = 2) -> bool:
    """Verify that a download URL responds successfully without downloading the file."""
    for method, headers in (
        ("HEAD", {"User-Agent": "dev-forge/0.1"}),
        ("GET", {"User-Agent": "dev-forge/0.1", "Range": "bytes=0-0"}),
    ):
        for attempt in range(retries):
            request = Request(url, method=method, headers=headers)
            try:
                with urlopen(request, timeout=30) as response:
                    if 200 <= response.status < 300:
                        return True
            except HTTPError as exc:
                if method == "HEAD" and exc.code in {403, 405}:
                    break
                if attempt + 1 < retries and exc.code >= 500:
                    time.sleep(2**attempt)
                    continue
                if method == "GET" or exc.code == 404:
                    return False
            except (URLError, TimeoutError):
                if attempt + 1 < retries:
                    time.sleep(2**attempt)
    return False


def fetch_latest_vscode_version(
    package: str,
    arch: str,
    json_getter: Callable[[str], Any] = _fetch_json_get,
    url_checker: Callable[[str], bool] = check_url_available,
) -> str:
    """Query Microsoft's official update service for the latest stable VS Code version."""
    platform_name = vscode_platform(package, arch)
    candidate: str | None = None

    update_info = json_getter(VSCODE_UPDATE_URL_TEMPLATE.format(platform=platform_name))
    if isinstance(update_info, dict):
        product_version = update_info.get("productVersion")
        if isinstance(product_version, str) and _is_exact_semver(product_version):
            candidate = product_version

    if candidate is None:
        releases = json_getter(VSCODE_RELEASES_URL)
        if isinstance(releases, list):
            valid_versions = [
                item for item in releases if isinstance(item, str) and _is_exact_semver(item)
            ]
            if valid_versions:
                candidate = max(valid_versions, key=Version.parse)

    if candidate is None:
        raise PackagerError("无法从微软官方服务解析最新的 VS Code 稳定版本")

    download_url, _ = vscode_download(candidate, package, arch)
    if not url_checker(download_url):
        raise PackagerError(f"VS Code {candidate} 安装包尚未就绪: {download_url}")
    return candidate


def _read_json_string_end(text: str, start: int) -> int:
    """Return index right after the closing quote of a JSON string starting at `start`."""
    index = start + 1
    escaped = False
    while index < len(text):
        char = text[index]
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == '"':
            return index + 1
        index += 1
    raise PackagerError("配置文件包含未闭合的字符串")


def _find_vscode_version_span(stripped: str) -> tuple[int, int]:
    """Locate the exact `"..."` token span of top-level `vscode.version` in stripped JSON."""
    index = 0
    length = len(stripped)
    depth = 0
    in_vscode = False
    vscode_depth = -1
    pending_key: str | None = None
    expecting_value = False

    while index < length:
        char = stripped[index]
        if char.isspace():
            index += 1
            continue
        if char == '"':
            end = _read_json_string_end(stripped, index)
            value = json.loads(stripped[index:end])
            if expecting_value:
                if in_vscode and depth == vscode_depth and pending_key == "version":
                    return index, end
                pending_key = None
                expecting_value = False
            elif depth == 1 or (in_vscode and depth == vscode_depth):
                pending_key = value
            index = end
            continue
        if char == ":":
            if pending_key is not None:
                expecting_value = True
            index += 1
            continue
        if char == "{":
            depth += 1
            if expecting_value and depth == 2 and pending_key == "vscode":
                in_vscode = True
                vscode_depth = depth
            pending_key = None
            expecting_value = False
            index += 1
            continue
        if char == "}":
            if in_vscode and depth == vscode_depth:
                in_vscode = False
                vscode_depth = -1
            depth -= 1
            pending_key = None
            expecting_value = False
            index += 1
            continue
        if char in {"[", "]", ","}:
            if char != "[":
                pending_key = None
            expecting_value = False
            index += 1
            continue
        # Primitive value (number, true, false, null)
        while index < length and stripped[index] not in ',}] \t\r\n':
            index += 1
        pending_key = None
        expecting_value = False

    raise PackagerError("未在配置中找到 vscode.version 字段")


def update_config_vscode_version(config_path: Path, new_version: str) -> bool:
    """Update `vscode.version` in a JSONC file while preserving comments and formatting."""
    if not _is_exact_semver(new_version):
        raise PackagerError(f"无效的 VS Code 版本号: {new_version}")

    original = config_path.read_text(encoding="utf-8")
    stripped = _strip_json_comments(original)
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise PackagerError(f"无法解析配置 {config_path}: {exc}") from exc

    current = str(parsed.get("vscode", {}).get("version", ""))
    if current == new_version:
        return False

    start, end = _find_vscode_version_span(stripped)
    updated = f"{original[:start]}{json.dumps(new_version)}{original[end:]}"
    temporary = config_path.with_suffix(config_path.suffix + ".part")
    temporary.write_text(updated, encoding="utf-8", newline="")
    temporary.replace(config_path)
    return True


def _read_existing_versions(lock_file: Path) -> dict[str, str]:
    """Best-effort read of extension versions from an existing lock file for reporting."""
    if not lock_file.is_file():
        return {}
    try:
        data = json.loads(lock_file.read_text(encoding="utf-8"))
        entries = data.get("extensions", [])
        return {
            entry["id"].lower(): entry["version"]
            for entry in entries
            if isinstance(entry, dict)
            and isinstance(entry.get("id"), str)
            and isinstance(entry.get("version"), str)
        }
    except (OSError, json.JSONDecodeError, TypeError):
        return {}


def _release_identity(release: ExtensionRelease) -> tuple[str, str, str | None, str]:
    return (
        release.version,
        release.engine,
        release.target_platform or None,
        release.download_url,
    )


def auto_update(
    config_path: Path,
    lock_file: Path,
    *,
    check_vscode: bool = True,
    vscode_version_fetcher: Callable[[str, str], str] = fetch_latest_vscode_version,
    extension_query: Callable[[str, str, str], ExtensionRelease] = query_extension,
    downloader: Callable[[str, Path], str] = download_file,
    extension_validator: Callable[[Path, ExtensionRelease], None] = validate_vsix,
    progress: Callable[[str], None] = print,
) -> UpdateResult:
    """Check for new VS Code / extension versions and update config and lock file atomically."""
    config = load_config(config_path)
    all_extensions = collect_extension_ids(config)

    target_version = config.version
    vscode_changed = False
    if check_vscode:
        progress("检查 VS Code 最新正式版本...")
        latest_version = vscode_version_fetcher(config.package, config.arch)
        if Version.parse(latest_version) > Version.parse(config.version):
            target_version = latest_version
            vscode_changed = True
            progress(f"发现 VS Code 新版本: {config.version} -> {target_version}")
        else:
            progress(f"VS Code 已是最新稳定版本: {config.version}")

    resolved_releases: list[ExtensionRelease] = []
    for extension_id in all_extensions:
        progress(f"解析扩展 {extension_id} (目标 VS Code {target_version})...")
        resolved_releases.append(
            extension_query(extension_id, target_version, config.arch)
        )

    existing_locked: dict[str, ExtensionRelease] | None = None
    if lock_file.is_file():
        try:
            existing_locked = load_extension_lock(
                lock_file, target_version, config.arch, all_extensions
            )
        except PackagerError:
            existing_locked = None

    previous_versions = _read_existing_versions(lock_file)
    extension_changes: list[ExtensionChange] = []
    lock_changed = existing_locked is None

    for release in resolved_releases:
        old_version = previous_versions.get(release.extension_id)
        if old_version != release.version:
            extension_changes.append(
                ExtensionChange(release.extension_id, old_version, release.version)
            )
        if existing_locked is not None:
            old_release = existing_locked[release.extension_id]
            if _release_identity(release) != _release_identity(old_release):
                lock_changed = True

    if not vscode_changed and not lock_changed:
        progress("VS Code 与全部扩展均无更新。")
        return UpdateResult(
            changed=False,
            vscode_changed=False,
            lock_changed=False,
            previous_version=config.version,
            target_version=target_version,
            extension_changes=(),
        )

    verified_releases: list[ExtensionRelease] = []
    with tempfile.TemporaryDirectory(prefix="dev-forge-lock-") as temp_dir:
        temp_root = Path(temp_dir)
        for index, release in enumerate(resolved_releases):
            progress(f"下载并校验扩展 {release.extension_id} {release.version}...")
            vsix_path = temp_root / f"{index}.vsix"
            digest = downloader(release.download_url, vsix_path)
            extension_validator(vsix_path, release)
            verified_releases.append(replace(release, sha256=digest.lower()))

    write_extension_lock(lock_file, target_version, config.arch, verified_releases)
    if vscode_changed:
        update_config_vscode_version(config_path, target_version)

    return UpdateResult(
        changed=True,
        vscode_changed=vscode_changed,
        lock_changed=True,
        previous_version=config.version,
        target_version=target_version,
        extension_changes=tuple(extension_changes),
    )
