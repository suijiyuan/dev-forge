from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import tempfile
import time
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen
import zipfile

from .semver import Version, satisfies


GALLERY_QUERY_URL = "https://marketplace.visualstudio.com/_apis/public/gallery/extensionquery"


class PackagerError(RuntimeError):
    pass


@dataclass(frozen=True)
class Config:
    version: str
    package: str
    arch: str
    extensions: tuple[str, ...]
    settings: Path
    output_dir: Path


@dataclass(frozen=True)
class ExtensionRelease:
    extension_id: str
    version: str
    engine: str
    target_platform: str | None
    download_url: str


def find_settings() -> Path:
    candidates: list[Path]
    system = platform.system()
    if system == "Windows":
        appdata = os.environ.get("APPDATA")
        candidates = [Path(appdata) / "Code/User/settings.json"] if appdata else []
    elif system == "Darwin":
        candidates = [Path.home() / "Library/Application Support/Code/User/settings.json"]
    else:
        config_home = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        candidates = [config_home / "Code/User/settings.json"]
    for path in candidates:
        if path.is_file():
            return path.resolve()
    shown = ", ".join(str(item) for item in candidates) or "系统默认路径"
    raise PackagerError(f"未找到当前用户的 settings.json（已检查: {shown}），请用 --settings 指定")


def _strip_json_comments(content: str) -> str:
    """Remove JSONC line/block comments while preserving strings and line numbers."""
    result: list[str] = []
    index = 0
    in_string = False
    escaped = False
    while index < len(content):
        char = content[index]
        following = content[index + 1] if index + 1 < len(content) else ""
        if in_string:
            result.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
            result.append(char)
            index += 1
            continue
        if char == "/" and following == "/":
            result.extend("  ")
            index += 2
            while index < len(content) and content[index] not in "\r\n":
                result.append(" ")
                index += 1
            continue
        if char == "/" and following == "*":
            result.extend("  ")
            index += 2
            while index < len(content):
                if content[index:index + 2] == "*/":
                    result.extend("  ")
                    index += 2
                    break
                result.append(content[index] if content[index] in "\r\n" else " ")
                index += 1
            else:
                raise PackagerError("配置文件包含未闭合的 /* 注释")
            continue
        result.append(char)
        index += 1
    return "".join(result)


def load_config(path: Path, settings_override: str | None = None, output_override: str | None = None) -> Config:
    try:
        raw = json.loads(_strip_json_comments(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, PackagerError) as exc:
        raise PackagerError(f"无法读取配置 {path}: {exc}") from exc

    vscode = raw.get("vscode", {})
    version = str(vscode.get("version", ""))
    try:
        parsed = Version.parse(version)
    except ValueError as exc:
        raise PackagerError("vscode.version 必须是完整版本号，例如 1.95.3") from exc
    if version.count(".") != 2 or str(parsed) != version:
        raise PackagerError("vscode.version 必须是完整版本号，例如 1.95.3")

    package = vscode.get("package", "system")
    arch = vscode.get("arch", "x64")
    if package not in {"system", "user", "archive"}:
        raise PackagerError("vscode.package 只能是 system、user 或 archive")
    if arch not in {"x64", "arm64"}:
        raise PackagerError("vscode.arch 只能是 x64 或 arm64")

    extensions = raw.get("extensions", [])
    if not isinstance(extensions, list) or not all(re.fullmatch(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_.-]+", item or "") for item in extensions):
        raise PackagerError("extensions 必须是 publisher.name 格式的字符串数组")
    extensions = list(dict.fromkeys(item.lower() for item in extensions))

    settings_value = settings_override or raw.get("settings", "auto")
    settings = find_settings() if settings_value == "auto" else _relative_to(path.parent, settings_value)
    if not settings.is_file():
        raise PackagerError(f"settings.json 不存在: {settings}")

    output_value = output_override or raw.get("output_dir", "dist")
    output_dir = _relative_to(path.parent, output_value)
    return Config(version, package, arch, tuple(extensions), settings, output_dir)


def _relative_to(base: Path, value: str) -> Path:
    result = Path(value).expanduser()
    return (base / result).resolve() if not result.is_absolute() else result.resolve()


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
    label = "VSCode" if package == "archive" else ("VSCodeUserSetup" if package == "user" else "VSCodeSetup")
    filename = f"{label}-{arch}-{version}{suffix}"
    return f"https://update.code.visualstudio.com/{version}/{platform_name}/stable", filename


def _request_json(url: str, body: dict[str, Any]) -> dict[str, Any]:
    encoded = json.dumps(body).encode("utf-8")
    request = Request(url, data=encoded, method="POST", headers={
        "Accept": "application/json;api-version=7.2-preview.1",
        "Content-Type": "application/json",
        "User-Agent": "dev-forge/0.1",
    })
    try:
        with urlopen(request, timeout=45) as response:
            return json.load(response)
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise PackagerError(f"查询 Marketplace 失败: {exc}") from exc


def query_extension(extension_id: str, vscode_version: str, arch: str,
                    request_json: Callable[[str, dict[str, Any]], dict[str, Any]] = _request_json) -> ExtensionRelease:
    payload = {
        "filters": [{
            "criteria": [{"filterType": 7, "value": extension_id}],
            "pageNumber": 1,
            "pageSize": 1,
            "sortBy": 0,
            "sortOrder": 0,
        }],
        # versions + files + version properties + validated + asset URI
        "flags": 179,
    }
    data = request_json(GALLERY_QUERY_URL, payload)
    try:
        extension = data["results"][0]["extensions"][0]
    except (KeyError, IndexError, TypeError) as exc:
        raise PackagerError(f"Marketplace 中未找到扩展: {extension_id}") from exc

    wanted_platform = f"win32-{arch}"
    choices: list[tuple[Version, int, dict[str, Any], str]] = []
    for release in extension.get("versions", []):
        properties = {item.get("key"): item.get("value") for item in release.get("properties", [])}
        if str(properties.get("Microsoft.VisualStudio.Code.PreRelease", "false")).lower() == "true":
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
        raise PackagerError(f"扩展 {extension_id} 没有与 VS Code {vscode_version}/{wanted_platform} 兼容的稳定版本")
    _, _, selected, engine = max(choices, key=lambda item: (item[0], item[1]))
    publisher, name = extension_id.split(".", 1)
    version = selected["version"]
    asset_url = next((item.get("source") for item in selected.get("files", [])
                      if item.get("assetType") == "Microsoft.VisualStudio.Services.VSIXPackage"), None)
    download_url = asset_url or (
        "https://marketplace.visualstudio.com/_apis/public/gallery/publishers/"
        f"{quote(publisher)}/vsextensions/{quote(name)}/{quote(version)}/vspackage"
    )
    return ExtensionRelease(extension_id, version, engine, selected.get("targetPlatform"), download_url)


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
                time.sleep(2 ** attempt)
    raise PackagerError(f"下载失败 {url}: {last_error}")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _write_support_files(root: Path, config: Config, manifest: dict[str, Any]) -> None:
    (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    readme = f"""VS Code {config.version} Windows 离线安装包

1. 在 PowerShell 中运行 .\\install.ps1。
2. 脚本将安装 VS Code 和所有扩展。
3. settings.json 仅在目标不存在时复制；使用 -ForceSettings 可覆盖。

详细版本和 SHA-256 校验值见 manifest.json。
"""
    (root / "README.txt").write_text(readme, encoding="utf-8")
    installer = manifest["vscode"]["file"].replace("/", "\\")
    archive_literal = "$true" if config.package == "archive" else "$false"
    ps1 = f"""param([switch]$ForceSettings)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
Set-StrictMode -Version 2.0

$Root = $PSScriptRoot
$Installer = Join-Path $Root '{installer}'
$ArchiveMode = {archive_literal}
$CodePath = $null

if (-not (Test-Path -LiteralPath $Installer -PathType Leaf)) {{
    throw "VS Code 安装文件不存在: $Installer"
}}

if ($ArchiveMode) {{
    $ArchiveTarget = Join-Path $Root 'vscode\\app'
    Write-Host '正在解压 VS Code...'
    if (Test-Path -LiteralPath $ArchiveTarget) {{
        Write-Warning "解压目录已存在，继续使用: $ArchiveTarget"
    }} else {{
        Expand-Archive -LiteralPath $Installer -DestinationPath $ArchiveTarget
    }}
    $CodePath = Get-ChildItem -LiteralPath $ArchiveTarget -Filter 'code.cmd' -File -Recurse |
        Where-Object {{ $_.FullName -like '*\\bin\\code.cmd' }} |
        Select-Object -First 1 -ExpandProperty FullName
}} else {{
    Write-Host '正在安装 VS Code...'
    $InstallProcess = Start-Process -FilePath $Installer -ArgumentList '/VERYSILENT','/NORESTART','/MERGETASKS=!runcode' -Wait -PassThru
    if ($InstallProcess.ExitCode -ne 0) {{
        throw "VS Code 安装失败，退出码: $($InstallProcess.ExitCode)"
    }}
}}

if (-not $CodePath) {{
    $CodeCommand = Get-Command 'code.cmd' -ErrorAction SilentlyContinue
    if ($CodeCommand) {{ $CodePath = $CodeCommand.Source }}
}}
if (-not $CodePath) {{
    $Candidates = @()
    if ($env:LOCALAPPDATA) {{ $Candidates += Join-Path $env:LOCALAPPDATA 'Programs\\Microsoft VS Code\\bin\\code.cmd' }}
    if ($env:ProgramFiles) {{ $Candidates += Join-Path $env:ProgramFiles 'Microsoft VS Code\\bin\\code.cmd' }}
    $CodePath = $Candidates | Where-Object {{ Test-Path -LiteralPath $_ -PathType Leaf }} | Select-Object -First 1
}}
if (-not $CodePath) {{ throw '未找到 code.cmd；请确认 VS Code 已成功安装或解压。' }}

$ExtensionsDir = Join-Path $Root 'extensions'
if (-not (Test-Path -LiteralPath $ExtensionsDir -PathType Container)) {{
    throw "扩展目录不存在: $ExtensionsDir"
}}
$Extensions = @(Get-ChildItem -LiteralPath $ExtensionsDir -Filter '*.vsix' -File | Sort-Object Name)
foreach ($Extension in $Extensions) {{
    Write-Host "正在安装扩展 $($Extension.Name)..."
    & $CodePath '--install-extension' $Extension.FullName '--force'
    if ($LASTEXITCODE -ne 0) {{ throw "扩展安装失败: $($Extension.Name)，退出码: $LASTEXITCODE" }}
}}

$SettingsSource = Join-Path $Root 'user-data\\settings.json'
$SettingsTarget = Join-Path $env:APPDATA 'Code\\User\\settings.json'
if (-not (Test-Path -LiteralPath $SettingsSource -PathType Leaf)) {{
    throw "settings.json 不存在: $SettingsSource"
}}
if ((-not (Test-Path $SettingsTarget)) -or $ForceSettings) {{
    New-Item -ItemType Directory -Path (Split-Path $SettingsTarget) -Force | Out-Null
    Copy-Item -LiteralPath $SettingsSource -Destination $SettingsTarget -Force
    Write-Host 'settings.json 已恢复。'
}} else {{
    Write-Warning '目标 settings.json 已存在，未覆盖。使用 -ForceSettings 可覆盖。'
}}
Write-Host '完成。'
"""
    (root / "install.ps1").write_text(ps1, encoding="utf-8-sig")


def build_bundle(config: Config, archive_only: bool = False,
                 downloader: Callable[[str, Path], str] = download_file,
                 extension_query: Callable[[str, str, str], ExtensionRelease] = query_extension,
                 progress: Callable[[str], None] = print) -> Path:
    config.output_dir.mkdir(parents=True, exist_ok=True)
    bundle_name = f"dev-forge-{config.version}-win32-{config.arch}"
    final_root = config.output_dir / bundle_name
    final_zip = config.output_dir / f"{bundle_name}.zip"
    partial_zip = final_zip.with_suffix(final_zip.suffix + ".part")
    if final_root.exists() or final_zip.exists():
        raise PackagerError(f"输出已存在，请先移动或删除: {final_root} / {final_zip}")

    staging_parent = Path(tempfile.mkdtemp(prefix=f".{bundle_name}-", dir=config.output_dir))
    root = staging_parent / bundle_name
    try:
        (root / "vscode").mkdir(parents=True)
        (root / "extensions").mkdir()
        (root / "user-data").mkdir()

        vscode_url, vscode_name = vscode_download(config.version, config.package, config.arch)
        vscode_path = root / "vscode" / vscode_name
        progress(f"下载 VS Code {config.version}...")
        vscode_hash = downloader(vscode_url, vscode_path)

        extension_entries = []
        for extension_id in config.extensions:
            progress(f"解析扩展 {extension_id}...")
            release = extension_query(extension_id, config.version, config.arch)
            platform_suffix = f"-{release.target_platform}" if release.target_platform not in (None, "", "universal") else ""
            filename = f"{extension_id}-{release.version}{platform_suffix}.vsix"
            target = root / "extensions" / filename
            progress(f"下载 {extension_id} {release.version}...")
            digest = downloader(release.download_url, target)
            extension_entries.append({
                "id": extension_id,
                "version": release.version,
                "engine": release.engine,
                "target_platform": release.target_platform or "universal",
                "file": target.relative_to(root).as_posix(),
                "sha256": digest,
                "source": release.download_url,
            })

        settings_target = root / "user-data" / "settings.json"
        shutil.copy2(config.settings, settings_target)
        manifest = {
            "schema_version": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "vscode": {
                "version": config.version,
                "package": config.package,
                "arch": config.arch,
                "file": vscode_path.relative_to(root).as_posix(),
                "sha256": vscode_hash,
                "source": vscode_url,
            },
            "extensions": extension_entries,
            "settings": {
                "file": settings_target.relative_to(root).as_posix(),
                "sha256": sha256_file(settings_target),
            },
        }
        _write_support_files(root, config, manifest)
        root.replace(final_root)
        staging_parent.rmdir()
        progress("创建 ZIP 压缩包...")
        with zipfile.ZipFile(partial_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for item in sorted(final_root.rglob("*")):
                if item.is_file():
                    archive.write(item, Path(bundle_name) / item.relative_to(final_root))
        partial_zip.replace(final_zip)
        if archive_only:
            shutil.rmtree(final_root)
        return final_zip
    except Exception:
        partial_zip.unlink(missing_ok=True)
        shutil.rmtree(staging_parent, ignore_errors=True)
        raise
