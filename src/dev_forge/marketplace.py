"""Marketplace resolution, resilient downloads, and VSIX validation."""

from __future__ import annotations

import hashlib
import json
import time
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from .models import ExtensionRelease, PackagerError
from .semver import Version, satisfies

GALLERY_QUERY_URL = (
    "https://marketplace.visualstudio.com/_apis/public/gallery/extensionquery"
)


def request_json(
    url: str,
    body: dict[str, Any],
    retries: int = 3,
) -> dict[str, Any]:
    encoded = json.dumps(body).encode("utf-8")
    request = Request(
        url,
        data=encoded,
        method="POST",
        headers={
            "Accept": "application/json;api-version=7.2-preview.1",
            "Content-Type": "application/json",
            "User-Agent": "dev-forge/0.1",
        },
    )
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            with urlopen(request, timeout=45) as response:
                return json.load(response)
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt + 1 < retries:
                time.sleep(2**attempt)
    raise PackagerError(f"查询 Marketplace 失败: {last_error}") from last_error


def query_extension(
    extension_id: str,
    vscode_version: str,
    arch: str,
    request: Callable[[str, dict[str, Any]], dict[str, Any]] = request_json,
) -> ExtensionRelease:
    payload = {
        "filters": [
            {
                "criteria": [{"filterType": 7, "value": extension_id}],
                "pageNumber": 1,
                "pageSize": 1,
                "sortBy": 0,
                "sortOrder": 0,
            }
        ],
        "flags": 179,
    }
    data = request(GALLERY_QUERY_URL, payload)
    try:
        extension = data["results"][0]["extensions"][0]
    except (KeyError, IndexError, TypeError) as exc:
        raise PackagerError(f"Marketplace 中未找到扩展: {extension_id}") from exc

    wanted_platform = f"win32-{arch}"
    choices: list[tuple[Version, int, dict[str, Any], str]] = []
    for release in extension.get("versions", []):
        properties = {
            item.get("key"): item.get("value") for item in release.get("properties", [])
        }
        if (
            str(
                properties.get("Microsoft.VisualStudio.Code.PreRelease", "false")
            ).lower()
            == "true"
        ):
            continue
        engine = properties.get("Microsoft.VisualStudio.Code.Engine")
        if not engine or not satisfies(vscode_version, engine):
            continue
        target = release.get("targetPlatform")
        if target == wanted_platform:
            platform_rank = 2
        elif target in (None, "", "universal"):
            platform_rank = 1
        else:
            continue
        try:
            release_version = Version.parse(release["version"])
        except (KeyError, ValueError):
            continue
        choices.append((release_version, platform_rank, release, engine))

    if not choices:
        raise PackagerError(
            f"扩展 {extension_id} 没有与 VS Code {vscode_version}/{wanted_platform} "
            "兼容的稳定版本"
        )
    _, _, selected, engine = max(choices, key=lambda item: (item[0], item[1]))
    publisher, name = extension_id.split(".", 1)
    version = selected["version"]
    asset_url = next(
        (
            item.get("source")
            for item in selected.get("files", [])
            if item.get("assetType") == "Microsoft.VisualStudio.Services.VSIXPackage"
        ),
        None,
    )
    download_url = asset_url or (
        "https://marketplace.visualstudio.com/_apis/public/gallery/publishers/"
        f"{quote(publisher)}/vsextensions/{quote(name)}/{quote(version)}/vspackage"
    )
    return ExtensionRelease(
        extension_id,
        version,
        engine,
        selected.get("targetPlatform"),
        download_url,
    )


def download_file(url: str, destination: Path, retries: int = 3) -> str:
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")
    request = Request(url, headers={"User-Agent": "dev-forge/0.1"})
    last_error: Exception | None = None
    for attempt in range(retries):
        digest = hashlib.sha256()
        try:
            with urlopen(request, timeout=60) as response, partial.open("wb") as output:
                while chunk := response.read(1024 * 1024):
                    output.write(chunk)
                    digest.update(chunk)
            partial.replace(destination)
            return digest.hexdigest()
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            last_error = exc
            partial.unlink(missing_ok=True)
            if attempt + 1 < retries:
                time.sleep(2**attempt)
    raise PackagerError(f"下载失败 {url}: {last_error}")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def validate_vsix(path: Path, release: ExtensionRelease) -> None:
    try:
        with zipfile.ZipFile(path) as archive:
            package_name = next(
                (
                    name
                    for name in archive.namelist()
                    if name.lower() == "extension/package.json"
                ),
                None,
            )
            if package_name is None:
                raise PackagerError(f"VSIX 缺少 extension/package.json: {path}")
            package = json.loads(archive.read(package_name))
            corrupt_member = archive.testzip()
            if corrupt_member is not None:
                raise PackagerError(f"VSIX 包含损坏文件: {corrupt_member}")
    except (OSError, zipfile.BadZipFile, json.JSONDecodeError) as exc:
        raise PackagerError(f"VSIX 结构无效 {path}: {exc}") from exc
    actual_id = f"{package.get('publisher', '')}.{package.get('name', '')}".lower()
    if actual_id != release.extension_id.lower():
        raise PackagerError(
            f"VSIX ID 不一致: 期望 {release.extension_id}，实际 {actual_id or '缺失'}"
        )
    if package.get("version") != release.version:
        raise PackagerError(
            f"VSIX 版本不一致: {release.extension_id} 期望 {release.version}，"
            f"实际 {package.get('version') or '缺失'}"
        )
