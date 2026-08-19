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
class ProfileSettings:
    name: str
    source: Path | None
    use_default: bool = False


@dataclass(frozen=True)
class ProfileResource:
    name: str
    kind: str
    source: Path | None
    use_default: bool = False


@dataclass(frozen=True)
class Config:
    version: str
    package: str
    arch: str
    extensions: tuple[str, ...]
    settings: Path | None
    output_dir: Path
    extension_profiles: tuple[tuple[str, tuple[str, ...]], ...] = ()
    profile_settings: tuple[ProfileSettings, ...] = ()
    install_mode: str = "merge"
    resources: tuple[tuple[str, Path], ...] = ()
    profile_resources: tuple[ProfileResource, ...] = ()


@dataclass(frozen=True)
class ExtensionRelease:
    extension_id: str
    version: str
    engine: str
    target_platform: str | None
    download_url: str


def user_data_root() -> Path:
    system = platform.system()
    if system == "Windows":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return (Path(appdata) / "Code/User").resolve()
        return (Path.home() / "AppData/Roaming/Code/User").resolve()
    if system == "Darwin":
        return (Path.home() / "Library/Application Support/Code/User").resolve()
    config_home = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return (config_home / "Code/User").resolve()


def _profile_root(profile_name: str | None = None) -> Path | None:
    root = user_data_root()
    if profile_name is None:
        return root

    storage = root / "globalStorage/storage.json"
    if not storage.is_file():
        return None
    try:
        metadata = json.loads(storage.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    for profile in metadata.get("userDataProfiles", []):
        if not isinstance(profile, dict) or profile.get("name") != profile_name:
            continue
        location = profile.get("location")
        if not isinstance(location, str) or not location:
            return None
        return (root / "profiles" / location).resolve()
    return None


def find_resource(kind: str, profile_name: str | None = None) -> Path | None:
    filenames = {
        "settings": "settings.json",
        "keybindings": "keybindings.json",
        "snippets": "snippets",
        "tasks": "tasks.json",
        "mcp": "mcp.json",
    }
    if kind not in filenames:
        raise ValueError(f"未知的 VS Code Profile 资源: {kind}")
    root = _profile_root(profile_name)
    if root is None:
        return None
    candidate = root / filenames[kind]
    expected = candidate.is_dir() if kind == "snippets" else candidate.is_file()
    return candidate.resolve() if expected else None


def find_settings(profile_name: str | None = None) -> Path | None:
    return find_resource("settings", profile_name)


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

    install_value = raw.get("install", {})
    if not isinstance(install_value, dict):
        raise PackagerError("install 必须是对象")
    unknown_install_keys = set(install_value) - {"mode"}
    if unknown_install_keys:
        names = "、".join(sorted(unknown_install_keys))
        raise PackagerError(f"install 包含未知配置项: {names}")
    install_mode = install_value.get("mode", "merge")
    if install_mode not in {"merge", "replace"}:
        raise PackagerError("install.mode 只能是 merge 或 replace")

    extensions_value = raw.get("extensions", [])
    extension_profiles: list[tuple[str, tuple[str, ...]]] = []
    if isinstance(extensions_value, list):
        extensions = _parse_extension_ids(extensions_value, "extensions")
    elif isinstance(extensions_value, dict):
        unknown_keys = set(extensions_value) - {"default", "profiles"}
        if unknown_keys:
            names = "、".join(sorted(unknown_keys))
            raise PackagerError(f"extensions 包含未知配置项: {names}")
        extensions = _parse_extension_ids(extensions_value.get("default", []), "extensions.default")
        profiles_value = extensions_value.get("profiles", {})
        if not isinstance(profiles_value, dict):
            raise PackagerError("extensions.profiles 必须是以 Profile 名称为键的对象")
        seen_profile_names: set[str] = set()
        for name, profile_extensions in profiles_value.items():
            if not isinstance(name, str) or not name.strip():
                raise PackagerError("extensions.profiles 的 Profile 名称不能为空")
            name = name.strip()
            normalized_name = name.casefold()
            if normalized_name == "default":
                raise PackagerError("Default 是保留名称；通用插件请配置到 extensions.default")
            if normalized_name in seen_profile_names:
                raise PackagerError(f"Profile 名称不能仅有大小写差异: {name}")
            seen_profile_names.add(normalized_name)
            parsed_extensions = _parse_extension_ids(
                profile_extensions, f"extensions.profiles.{name}"
            )
            parsed_extensions = [item for item in parsed_extensions if item not in extensions]
            extension_profiles.append((name, tuple(parsed_extensions)))
    else:
        raise PackagerError("extensions 必须是字符串数组或包含 default/profiles 的对象")

    settings_value = raw.get("settings", "auto")
    profile_settings: list[ProfileSettings] = []
    if isinstance(settings_value, str):
        default_settings_value = settings_override or settings_value
        settings = _resolve_settings_source(path.parent, default_settings_value, "settings")
    elif isinstance(settings_value, dict):
        unknown_keys = set(settings_value) - {"default", "profiles"}
        if unknown_keys:
            names = "、".join(sorted(unknown_keys))
            raise PackagerError(f"settings 包含未知配置项: {names}")
        default_settings_value = settings_override or settings_value.get("default", "auto")
        settings = _resolve_settings_source(path.parent, default_settings_value, "settings.default")
        profile_settings_value = settings_value.get("profiles", {})
        if not isinstance(profile_settings_value, dict):
            raise PackagerError("settings.profiles 必须是以 Profile 名称为键的对象")
        extension_profile_names = {name.casefold(): name for name, _ in extension_profiles}
        seen_settings_profiles: set[str] = set()
        for configured_name, profile_value in profile_settings_value.items():
            if not isinstance(configured_name, str) or not configured_name.strip():
                raise PackagerError("settings.profiles 的 Profile 名称不能为空")
            normalized_name = configured_name.strip().casefold()
            if normalized_name in seen_settings_profiles:
                raise PackagerError(f"settings Profile 名称不能仅有大小写差异: {configured_name}")
            seen_settings_profiles.add(normalized_name)
            if normalized_name not in extension_profile_names:
                raise PackagerError(
                    f"settings.profiles.{configured_name} 未在 extensions.profiles 中声明"
                )
            profile_name = extension_profile_names[normalized_name]
            if isinstance(profile_value, str):
                source = _resolve_settings_source(
                    path.parent,
                    profile_value,
                    f"settings.profiles.{profile_name}",
                    profile_name,
                )
                profile_settings.append(ProfileSettings(profile_name, source))
            elif isinstance(profile_value, dict) and profile_value == {"use_default": True}:
                profile_settings.append(ProfileSettings(profile_name, None, True))
            else:
                raise PackagerError(
                    f"settings.profiles.{profile_name} 必须是路径、auto 或 {{\"use_default\": true}}"
                )
    else:
        raise PackagerError("settings 必须是路径字符串或包含 default/profiles 的对象")

    resource_kinds = {"keybindings", "snippets", "tasks", "mcp"}
    resources_value = raw.get("resources", {})
    if not isinstance(resources_value, dict):
        raise PackagerError("resources 必须是包含 default/profiles 的对象")
    unknown_resource_keys = set(resources_value) - {"default", "profiles"}
    if unknown_resource_keys:
        names = "、".join(sorted(unknown_resource_keys))
        raise PackagerError(f"resources 包含未知配置项: {names}")

    default_resources_value = resources_value.get("default", {})
    if not isinstance(default_resources_value, dict):
        raise PackagerError("resources.default 必须是以资源名称为键的对象")
    unknown_default_resources = set(default_resources_value) - resource_kinds
    if unknown_default_resources:
        names = "、".join(sorted(unknown_default_resources))
        raise PackagerError(f"resources.default 包含未知资源: {names}")
    resources: list[tuple[str, Path]] = []
    for kind, value in default_resources_value.items():
        source = _resolve_resource_source(path.parent, value, f"resources.default.{kind}", kind)
        if source is not None:
            resources.append((kind, source))

    profile_resources_value = resources_value.get("profiles", {})
    if not isinstance(profile_resources_value, dict):
        raise PackagerError("resources.profiles 必须是以 Profile 名称为键的对象")
    extension_profile_names = {name.casefold(): name for name, _ in extension_profiles}
    seen_resource_profiles: set[str] = set()
    profile_resources: list[ProfileResource] = []
    for configured_name, profile_value in profile_resources_value.items():
        if not isinstance(configured_name, str) or not configured_name.strip():
            raise PackagerError("resources.profiles 的 Profile 名称不能为空")
        normalized_name = configured_name.strip().casefold()
        if normalized_name in seen_resource_profiles:
            raise PackagerError(f"resources Profile 名称不能仅有大小写差异: {configured_name}")
        seen_resource_profiles.add(normalized_name)
        if normalized_name not in extension_profile_names:
            raise PackagerError(
                f"resources.profiles.{configured_name} 未在 extensions.profiles 中声明"
            )
        profile_name = extension_profile_names[normalized_name]
        if not isinstance(profile_value, dict):
            raise PackagerError(f"resources.profiles.{profile_name} 必须是对象")
        unknown_profile_resources = set(profile_value) - resource_kinds
        if unknown_profile_resources:
            names = "、".join(sorted(unknown_profile_resources))
            raise PackagerError(f"resources.profiles.{profile_name} 包含未知资源: {names}")
        for kind, value in profile_value.items():
            if isinstance(value, dict) and value == {"use_default": True}:
                profile_resources.append(ProfileResource(profile_name, kind, None, True))
                continue
            source = _resolve_resource_source(
                path.parent,
                value,
                f"resources.profiles.{profile_name}.{kind}",
                kind,
                profile_name,
            )
            if source is not None:
                profile_resources.append(ProfileResource(profile_name, kind, source))

    output_value = output_override or raw.get("output_dir", "dist")
    output_dir = _relative_to(path.parent, output_value)
    return Config(
        version,
        package,
        arch,
        tuple(extensions),
        settings,
        output_dir,
        tuple(extension_profiles),
        tuple(profile_settings),
        install_mode,
        tuple(resources),
        tuple(profile_resources),
    )


def _parse_extension_ids(value: Any, field: str) -> list[str]:
    extension_pattern = r"[A-Za-z0-9_-]+\.[A-Za-z0-9_.-]+"
    if not isinstance(value, list) or not all(
        isinstance(item, str) and re.fullmatch(extension_pattern, item) for item in value
    ):
        raise PackagerError(f"{field} 必须是 publisher.name 格式的字符串数组")
    return list(dict.fromkeys(item.lower() for item in value))


def _resolve_settings_source(
    base: Path,
    value: Any,
    field: str,
    profile_name: str | None = None,
) -> Path | None:
    if not isinstance(value, str):
        raise PackagerError(f"{field} 必须是文件路径或 auto")
    if value == "auto":
        return find_settings(profile_name)
    settings = _relative_to(base, value)
    if not settings.is_file():
        raise PackagerError(f"{field} 指定的 settings.json 不存在: {settings}")
    return settings


def _resolve_resource_source(
    base: Path,
    value: Any,
    field: str,
    kind: str,
    profile_name: str | None = None,
) -> Path | None:
    if not isinstance(value, str):
        raise PackagerError(f"{field} 必须是路径或 auto")
    if value == "auto":
        return find_resource(kind, profile_name)
    source = _relative_to(base, value)
    valid = source.is_dir() if kind == "snippets" else source.is_file()
    if not valid:
        expected = "目录" if kind == "snippets" else "文件"
        raise PackagerError(f"{field} 指定的{expected}不存在: {source}")
    return source


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


def _copy_or_create_empty_settings(source: Path | None, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if source is None:
        target.write_text("{}\n", encoding="utf-8")
    else:
        shutil.copy2(source, target)


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


def _powershell_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _powershell_array(paths: list[str], indent: str = "") -> str:
    if not paths:
        return "@()"
    windows_paths = (path.replace("/", "\\") for path in paths)
    values = ",\n".join(f"{indent}    {_powershell_literal(path)}" for path in windows_paths)
    return f"@(\n{values}\n{indent})"


def _powershell_values(values: list[str], indent: str = "") -> str:
    if not values:
        return "@()"
    rendered = ",\n".join(f"{indent}    {_powershell_literal(value)}" for value in values)
    return f"@(\n{rendered}\n{indent})"


def _powershell_profile_map(profiles: list[tuple[str, list[str]]]) -> str:
    if not profiles:
        return "[ordered]@{}"
    entries = []
    for name, paths in profiles:
        entries.append(f"    {_powershell_literal(name)} = {_powershell_array(paths, '    ')}")
    return "[ordered]@{\n" + "\n".join(entries) + "\n}"


def _powershell_settings_map(profiles: list[tuple[str, str]]) -> str:
    if not profiles:
        return "[ordered]@{}"
    entries = []
    for name, path in profiles:
        windows_path = path.replace("/", "\\")
        entries.append(f"    {_powershell_literal(name)} = {_powershell_literal(windows_path)}")
    return "[ordered]@{\n" + "\n".join(entries) + "\n}"


def _powershell_string_map(values: list[tuple[str, str]], indent: str = "") -> str:
    if not values:
        return "[ordered]@{}"
    entries = []
    for key, value in values:
        windows_value = value.replace("/", "\\")
        entries.append(
            f"{indent}    {_powershell_literal(key)} = {_powershell_literal(windows_value)}"
        )
    return "[ordered]@{\n" + "\n".join(entries) + f"\n{indent}}}"


def _powershell_nested_values_map(profiles: list[tuple[str, list[str]]]) -> str:
    if not profiles:
        return "[ordered]@{}"
    entries = [
        f"    {_powershell_literal(name)} = {_powershell_values(values, '    ')}"
        for name, values in profiles
    ]
    return "[ordered]@{\n" + "\n".join(entries) + "\n}"


def _powershell_nested_string_map(
    profiles: list[tuple[str, list[tuple[str, str]]]],
) -> str:
    if not profiles:
        return "[ordered]@{}"
    entries = [
        f"    {_powershell_literal(name)} = {_powershell_string_map(values, '    ')}"
        for name, values in profiles
    ]
    return "[ordered]@{\n" + "\n".join(entries) + "\n}"


def _write_support_files(root: Path, config: Config, manifest: dict[str, Any]) -> None:
    (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    readme = f"""VS Code {config.version} Windows 离线安装包

1. 在 PowerShell 中运行 .\\install.ps1。
2. 默认安装模式是 {config.install_mode}；可使用 -Mode merge 或 -Mode replace 临时覆盖。
3. merge 保留本机已有扩展；replace 会删除当前用户原有的扩展目录和各 Profile 的扩展清单。
4. 脚本先检查离线文件并确认 VS Code 已关闭，再按选择的模式处理扩展。
5. 脚本按打包时的 packager.jsonc 配置创建 Profile：
   - 通用扩展安装到 Default 和所有已配置 Profile；
   - Profile 专属扩展仅安装到对应 Profile。
6. settings.json 仅在目标不存在时复制；使用 -ForceSettings 可覆盖。
7. keybindings、snippets、tasks、MCP 默认保留已有文件；使用 -ForceResources 可覆盖。

详细版本和 SHA-256 校验值见 manifest.json。
"""
    (root / "README.txt").write_text(readme, encoding="utf-8")
    installer = manifest["vscode"]["file"].replace("/", "\\")
    archive_literal = "$true" if config.package == "archive" else "$false"
    files_by_id = {extension["id"]: extension["file"] for extension in manifest["extensions"]}
    common_extensions = _powershell_array([files_by_id[item] for item in config.extensions])
    profile_extensions = _powershell_profile_map([
        (name, [files_by_id[item] for item in extensions])
        for name, extensions in config.extension_profiles
    ])
    settings_manifest = manifest["settings"]
    default_settings_path = settings_manifest["default"]["file"].replace("/", "\\")
    shared_settings_profiles = _powershell_values([
        name
        for name, value in settings_manifest["profiles"].items()
        if value.get("use_default") is True
    ])
    profile_settings = _powershell_settings_map([
        (name, value["file"])
        for name, value in settings_manifest["profiles"].items()
        if "file" in value
    ])
    resources_manifest = manifest["resources"]

    def resource_path(value: dict[str, Any]) -> str:
        return value.get("file") or value["directory"]

    default_resources = _powershell_string_map([
        (kind, resource_path(value))
        for kind, value in resources_manifest["default"].items()
    ])
    shared_profile_resources = _powershell_nested_values_map([
        (
            name,
            [kind for kind, value in values.items() if value.get("use_default") is True],
        )
        for name, values in resources_manifest["profiles"].items()
        if any(value.get("use_default") is True for value in values.values())
    ])
    profile_resources = _powershell_nested_string_map([
        (
            name,
            [
                (kind, resource_path(value))
                for kind, value in values.items()
                if value.get("use_default") is not True
            ],
        )
        for name, values in resources_manifest["profiles"].items()
        if any(value.get("use_default") is not True for value in values.values())
    ])
    default_install_mode = _powershell_literal(config.install_mode)
    ps1 = f"""param(
    [ValidateSet('merge', 'replace')]
    [string]$Mode = {default_install_mode},
    [switch]$ForceSettings,
    [switch]$ForceResources
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
Set-StrictMode -Version 2.0

$Root = $PSScriptRoot
$Installer = Join-Path $Root '{installer}'
$ArchiveMode = {archive_literal}
$ArchiveTarget = Join-Path $Root 'vscode\\app'
$CodePath = $null
$UserDataRoot = Join-Path $env:APPDATA 'Code\\User'
$ExtensionsDir = Join-Path $Root 'extensions'
$CommonExtensions = {common_extensions}
$ProfileExtensions = {profile_extensions}
$DefaultSettingsSource = Join-Path $Root '{default_settings_path}'
$SharedSettingsProfiles = {shared_settings_profiles}
$ProfileSettings = {profile_settings}
$DefaultResources = {default_resources}
$SharedProfileResources = {shared_profile_resources}
$ProfileResources = {profile_resources}

if (-not (Test-Path -LiteralPath $Installer -PathType Leaf)) {{
    throw "VS Code 安装文件不存在: $Installer"
}}
if (-not (Test-Path -LiteralPath $ExtensionsDir -PathType Container)) {{
    throw "扩展目录不存在: $ExtensionsDir"
}}

# 在删除现有扩展前检查清单中的所有输入文件，避免离线包不完整时破坏当前环境。
$RequiredExtensions = @($CommonExtensions)
foreach ($ProfileName in $ProfileExtensions.Keys) {{
    $RequiredExtensions += @($ProfileExtensions[$ProfileName])
}}
foreach ($Extension in @($RequiredExtensions | Sort-Object -Unique)) {{
    $ExtensionPath = Join-Path $Root $Extension
    if (-not (Test-Path -LiteralPath $ExtensionPath -PathType Leaf)) {{
        throw "扩展文件不存在: $ExtensionPath"
    }}
}}
$RequiredSettingsFiles = @($DefaultSettingsSource)
foreach ($ProfileName in $ProfileSettings.Keys) {{
    $RequiredSettingsFiles += Join-Path $Root $ProfileSettings[$ProfileName]
}}
foreach ($SettingsPath in @($RequiredSettingsFiles | Sort-Object -Unique)) {{
    if (-not (Test-Path -LiteralPath $SettingsPath -PathType Leaf)) {{
        throw "配置文件不存在: $SettingsPath"
    }}
}}
foreach ($ResourceName in $DefaultResources.Keys) {{
    $ResourcePath = Join-Path $Root $DefaultResources[$ResourceName]
    $PathType = if ($ResourceName -eq 'snippets') {{ 'Container' }} else {{ 'Leaf' }}
    if (-not (Test-Path -LiteralPath $ResourcePath -PathType $PathType)) {{
        throw "Profile 资源不存在: $ResourcePath"
    }}
}}
foreach ($ProfileName in $ProfileResources.Keys) {{
    foreach ($ResourceName in $ProfileResources[$ProfileName].Keys) {{
        $ResourcePath = Join-Path $Root $ProfileResources[$ProfileName][$ResourceName]
        $PathType = if ($ResourceName -eq 'snippets') {{ 'Container' }} else {{ 'Leaf' }}
        if (-not (Test-Path -LiteralPath $ResourcePath -PathType $PathType)) {{
            throw "Profile 资源不存在: $ResourcePath"
        }}
    }}
}}

$RunningCodeProcesses = @(Get-Process -Name 'Code' -ErrorAction SilentlyContinue)
if ($RunningCodeProcesses.Count -gt 0) {{
    throw '检测到 VS Code 正在运行。请关闭所有 VS Code 窗口，并从独立 PowerShell 重新运行安装脚本。'
}}

if ($Mode -eq 'replace') {{
    Write-Warning 'replace 模式会删除本机现有扩展；如果已启用 Settings Sync，请先关闭 Extensions 和 Profiles 同步。'
    $UserExtensionsDirs = @(
        (Join-Path ([Environment]::GetFolderPath('UserProfile')) '.vscode\\extensions')
    )
    if ($env:VSCODE_EXTENSIONS) {{
        $UserExtensionsDirs += [Environment]::ExpandEnvironmentVariables($env:VSCODE_EXTENSIONS)
    }}
    if ($ArchiveMode) {{
        $UserExtensionsDirs += Join-Path $ArchiveTarget 'data\\extensions'
    }}
    foreach ($UserExtensionsDir in @($UserExtensionsDirs | Sort-Object -Unique)) {{
        if (Test-Path -LiteralPath $UserExtensionsDir) {{
            Write-Host "正在删除当前用户的 VS Code 扩展目录: $UserExtensionsDir"
            Remove-Item -LiteralPath $UserExtensionsDir -Recurse -Force
        }}
    }}

    # 删除物理扩展目录后也必须清理现有 Profile 的扩展清单。
    $ProfileExtensionStateFiles = @()
    $LegacyDefaultExtensionState = Join-Path $UserDataRoot 'extensions.json'
    if (Test-Path -LiteralPath $LegacyDefaultExtensionState -PathType Leaf) {{
        $ProfileExtensionStateFiles += $LegacyDefaultExtensionState
    }}
    $ProfilesRoot = Join-Path $UserDataRoot 'profiles'
    if (Test-Path -LiteralPath $ProfilesRoot -PathType Container) {{
        $ProfileExtensionStateFiles += @(
            Get-ChildItem -LiteralPath $ProfilesRoot -Filter 'extensions.json' -File -Recurse |
                Select-Object -ExpandProperty FullName
        )
    }}
    foreach ($ExtensionStateFile in @($ProfileExtensionStateFiles | Sort-Object -Unique)) {{
        Write-Host "正在清理 VS Code Profile 扩展清单: $ExtensionStateFile"
        Remove-Item -LiteralPath $ExtensionStateFile -Force
    }}
}} else {{
    Write-Host 'merge 模式：保留本机现有扩展，只安装或更新离线包清单中的扩展。'
}}

if ($ArchiveMode) {{
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

function Test-ProfileAvailable {{
    param([Parameter(Mandatory = $true)][string]$Name)

    $PreviousErrorActionPreference = $ErrorActionPreference
    try {{
        # Windows PowerShell 5 会把原生命令 stderr 转为 NativeCommandError。
        # Profile 不存在是这里的预期探测结果，因此临时允许命令返回退出码 1。
        $ErrorActionPreference = 'Continue'
        & $CodePath '--profile' $Name '--list-extensions' 2>$null |
            Out-Null
        $ProfileExitCode = $LASTEXITCODE
    }} finally {{
        $ErrorActionPreference = $PreviousErrorActionPreference
    }}
    return ($ProfileExitCode -eq 0)
}}

function Ensure-Profile {{
    param([Parameter(Mandatory = $true)][string]$Name)

    if (Test-ProfileAvailable -Name $Name) {{
        Write-Host "Profile 已存在: $Name"
        return
    }}

    $BootstrapFolder = Join-Path ([IO.Path]::GetTempPath()) (
        'dev-forge-profile-' + [Guid]::NewGuid().ToString('N')
    )
    New-Item -ItemType Directory -Path $BootstrapFolder -Force | Out-Null
    $Created = $false
    try {{
        Write-Host "正在创建 Profile: $Name..."
        & $CodePath '--profile' $Name '--new-window' $BootstrapFolder |
            Out-Null

        for ($Attempt = 0; $Attempt -lt 80; $Attempt++) {{
            Start-Sleep -Milliseconds 250
            if (Test-ProfileAvailable -Name $Name) {{
                $Created = $true
                break
            }}
        }}
    }} finally {{
        $BootstrapProcesses = @(Get-Process -Name 'Code' -ErrorAction SilentlyContinue)
        foreach ($Process in $BootstrapProcesses) {{
            if ($Process.MainWindowHandle -ne 0) {{ $null = $Process.CloseMainWindow() }}
        }}
        Start-Sleep -Milliseconds 500
        @(Get-Process -Name 'Code' -ErrorAction SilentlyContinue) |
            Stop-Process -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $BootstrapFolder -Recurse -Force -ErrorAction SilentlyContinue
    }}

    if (-not $Created) {{
        throw "VS Code 未能在 20 秒内创建 Profile: $Name"
    }}

    if (-not (Test-ProfileAvailable -Name $Name)) {{
        throw "关闭创建窗口后 VS Code 无法识别 Profile: $Name"
    }}
    Write-Host "已创建 Profile: $Name"
}}

$InstalledExtensionPaths = @{{}}

function Stop-ResidualCodeProcesses {{
    $Processes = @(Get-Process -Name 'Code' -ErrorAction SilentlyContinue)
    if ($Processes.Count -eq 0) {{ return }}

    Write-Host '正在关闭扩展安装产生的残留 VS Code 进程...'
    $Processes | Stop-Process -Force -ErrorAction SilentlyContinue
    for ($Attempt = 0; $Attempt -lt 20; $Attempt++) {{
        if (@(Get-Process -Name 'Code' -ErrorAction SilentlyContinue).Count -eq 0) {{ return }}
        Start-Sleep -Milliseconds 250
    }}
    throw '无法关闭扩展安装产生的 VS Code 进程，请重新运行安装脚本。'
}}

function Install-ProfileExtension {{
    param(
        [Parameter(Mandatory = $true)][string]$RelativePath,
        [string]$Profile
    )

    $ExtensionPath = Join-Path $Root $RelativePath
    if (-not (Test-Path -LiteralPath $ExtensionPath -PathType Leaf)) {{
        throw "扩展文件不存在: $ExtensionPath"
    }}

    $ProfileLabel = if ($Profile) {{ $Profile }} else {{ 'Default' }}
    $ExtensionFile = Split-Path $ExtensionPath -Leaf
    $ExtensionKey = [IO.Path]::GetFullPath($ExtensionPath).ToLowerInvariant()
    $IsRepeatedInstall = $InstalledExtensionPaths.ContainsKey($ExtensionKey)
    $Arguments = @('--install-extension', $ExtensionPath, '--force')
    if ($Profile) {{ $Arguments += @('--profile', $Profile) }}

    for ($Attempt = 1; $Attempt -le 2; $Attempt++) {{
        # 本地 VSIX 安装到另一个 Profile 时，VS Code 会重新处理同一个物理目录。
        # Oracle 等包含原生模块的扩展可能仍被上一次 CLI 进程占用，因此先清理残留进程。
        if ($IsRepeatedInstall -or ($Attempt -gt 1)) {{
            Stop-ResidualCodeProcesses
        }}

        Write-Host "正在向 $ProfileLabel Profile 安装扩展 $ExtensionFile..."
        & $CodePath @Arguments
        $ExtensionExitCode = $LASTEXITCODE
        if ($ExtensionExitCode -eq 0) {{
            $InstalledExtensionPaths[$ExtensionKey] = $true
            return
        }}
        if ($Attempt -lt 2) {{
            Write-Warning "扩展安装失败，将在清理 VS Code 进程后重试: $ExtensionFile，退出码: $ExtensionExitCode"
            Start-Sleep -Milliseconds 500
        }}
    }}
    throw "扩展安装失败: $ExtensionFile，Profile: $ProfileLabel，退出码: $ExtensionExitCode"
}}

# Profile 的扩展集合相互独立。通用扩展需要同时登记到每个 Profile，
# 才能在切换后继续使用。
foreach ($ProfileName in $ProfileExtensions.Keys) {{
    Ensure-Profile -Name $ProfileName
}}
foreach ($Extension in $CommonExtensions) {{
    Install-ProfileExtension -RelativePath $Extension
}}
foreach ($ProfileName in $ProfileExtensions.Keys) {{
    foreach ($Extension in @($CommonExtensions) + @($ProfileExtensions[$ProfileName])) {{
        Install-ProfileExtension -RelativePath $Extension -Profile $ProfileName
    }}
}}

function Copy-SettingsFile {{
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Target,
        [Parameter(Mandatory = $true)][string]$Label
    )

    if (-not (Test-Path -LiteralPath $Source -PathType Leaf)) {{
        throw "$Label 配置文件不存在: $Source"
    }}
    if ((-not (Test-Path -LiteralPath $Target -PathType Leaf)) -or $ForceSettings) {{
        New-Item -ItemType Directory -Path (Split-Path $Target) -Force | Out-Null
        Copy-Item -LiteralPath $Source -Destination $Target -Force
        Write-Host "$Label settings.json 已恢复。"
    }} else {{
        Write-Warning "$Label settings.json 已存在，未覆盖。使用 -ForceSettings 可覆盖。"
    }}
}}

function Get-ProfileResourceTarget {{
    param(
        [Parameter(Mandatory = $true)][string]$Base,
        [Parameter(Mandatory = $true)][string]$ResourceName
    )
    $Filename = switch ($ResourceName) {{
        'keybindings' {{ 'keybindings.json' }}
        'snippets' {{ 'snippets' }}
        'tasks' {{ 'tasks.json' }}
        'mcp' {{ 'mcp.json' }}
        default {{ throw "未知的 Profile 资源: $ResourceName" }}
    }}
    return Join-Path $Base $Filename
}}

function Copy-ProfileResource {{
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Target,
        [Parameter(Mandatory = $true)][string]$Label,
        [Parameter(Mandatory = $true)][bool]$Directory
    )

    if ($Directory) {{
        if (-not (Test-Path -LiteralPath $Source -PathType Container)) {{
            throw "$Label 资源目录不存在: $Source"
        }}
        if ((Test-Path -LiteralPath $Target) -and
            (-not (Test-Path -LiteralPath $Target -PathType Container))) {{
            if (-not $ForceResources) {{
                Write-Warning "$Label 目标已存在且不是目录，未覆盖。使用 -ForceResources 可覆盖。"
                return
            }}
            Remove-Item -LiteralPath $Target -Force
        }}
        New-Item -ItemType Directory -Path $Target -Force | Out-Null
        foreach ($SourceFile in @(Get-ChildItem -LiteralPath $Source -File -Recurse)) {{
            $RelativePath = $SourceFile.FullName.Substring($Source.Length).TrimStart('\\')
            $TargetFile = Join-Path $Target $RelativePath
            if ((-not (Test-Path -LiteralPath $TargetFile -PathType Leaf)) -or $ForceResources) {{
                New-Item -ItemType Directory -Path (Split-Path $TargetFile) -Force | Out-Null
                Copy-Item -LiteralPath $SourceFile.FullName -Destination $TargetFile -Force
            }} else {{
                Write-Warning "$Label 文件已存在，未覆盖: $RelativePath"
            }}
        }}
        Write-Host "$Label 资源目录已合并。"
        return
    }}

    if (-not (Test-Path -LiteralPath $Source -PathType Leaf)) {{
        throw "$Label 资源文件不存在: $Source"
    }}
    if ((-not (Test-Path -LiteralPath $Target -PathType Leaf)) -or $ForceResources) {{
        New-Item -ItemType Directory -Path (Split-Path $Target) -Force | Out-Null
        Copy-Item -LiteralPath $Source -Destination $Target -Force
        Write-Host "$Label 资源已恢复。"
    }} else {{
        Write-Warning "$Label 已存在，未覆盖。使用 -ForceResources 可覆盖。"
    }}
}}

Copy-SettingsFile `
    -Source $DefaultSettingsSource `
    -Target (Join-Path $UserDataRoot 'settings.json') `
    -Label 'Default'

foreach ($ResourceName in $DefaultResources.Keys) {{
    $Source = Join-Path $Root $DefaultResources[$ResourceName]
    $Target = Get-ProfileResourceTarget -Base $UserDataRoot -ResourceName $ResourceName
    Copy-ProfileResource `
        -Source $Source `
        -Target $Target `
        -Label "Default $ResourceName" `
        -Directory ($ResourceName -eq 'snippets')
}}

if (($SharedSettingsProfiles.Count -gt 0) -or
    ($ProfileSettings.Count -gt 0) -or
    ($SharedProfileResources.Count -gt 0) -or
    ($ProfileResources.Count -gt 0)) {{
    $StoragePath = Join-Path $UserDataRoot 'globalStorage\\storage.json'
    if (-not (Test-Path -LiteralPath $StoragePath -PathType Leaf)) {{
        throw "未找到 VS Code Profile 元数据: $StoragePath"
    }}
    $Storage = Get-Content -LiteralPath $StoragePath -Raw | ConvertFrom-Json

    function Get-ProfileMetadata {{
        param([Parameter(Mandatory = $true)][string]$Name)
        $Result = @($Storage.userDataProfiles | Where-Object {{ $_.name -eq $Name }}) | Select-Object -First 1
        if (-not $Result) {{ throw "安装扩展后仍未找到 Profile: $Name" }}
        return $Result
    }}

    function Set-ProfileResourceInheritance {{
        param(
            [Parameter(Mandatory = $true)]$ProfileInfo,
            [Parameter(Mandatory = $true)][string]$ResourceName,
            [Parameter(Mandatory = $true)][bool]$UseDefault
        )
        if ((-not $ProfileInfo.PSObject.Properties['useDefaultFlags']) -or
            ($null -eq $ProfileInfo.useDefaultFlags)) {{
            $ProfileInfo | Add-Member -NotePropertyName 'useDefaultFlags' -NotePropertyValue ([pscustomobject]@{{}}) -Force
        }}
        if ($ProfileInfo.useDefaultFlags.PSObject.Properties[$ResourceName]) {{
            $ProfileInfo.useDefaultFlags.$ResourceName = $UseDefault
        }} else {{
            $ProfileInfo.useDefaultFlags | Add-Member -NotePropertyName $ResourceName -NotePropertyValue $UseDefault
        }}
    }}

    foreach ($ProfileName in $SharedSettingsProfiles) {{
        $ProfileInfo = Get-ProfileMetadata -Name $ProfileName
        Set-ProfileResourceInheritance -ProfileInfo $ProfileInfo -ResourceName 'settings' -UseDefault $true
        Write-Host "$ProfileName Profile 已设置为共享 Default settings.json。"
    }}

    foreach ($ProfileName in $ProfileSettings.Keys) {{
        $ProfileInfo = Get-ProfileMetadata -Name $ProfileName
        Set-ProfileResourceInheritance -ProfileInfo $ProfileInfo -ResourceName 'settings' -UseDefault $false
        $Source = Join-Path $Root $ProfileSettings[$ProfileName]
        $Target = Join-Path $UserDataRoot "profiles\\$($ProfileInfo.location)\\settings.json"
        Copy-SettingsFile -Source $Source -Target $Target -Label $ProfileName
    }}

    foreach ($ProfileName in $SharedProfileResources.Keys) {{
        $ProfileInfo = Get-ProfileMetadata -Name $ProfileName
        foreach ($ResourceName in $SharedProfileResources[$ProfileName]) {{
            Set-ProfileResourceInheritance `
                -ProfileInfo $ProfileInfo `
                -ResourceName $ResourceName `
                -UseDefault $true
            Write-Host "$ProfileName Profile 已设置为共享 Default $ResourceName。"
        }}
    }}

    foreach ($ProfileName in $ProfileResources.Keys) {{
        $ProfileInfo = Get-ProfileMetadata -Name $ProfileName
        $ProfileRoot = Join-Path $UserDataRoot "profiles\\$($ProfileInfo.location)"
        foreach ($ResourceName in $ProfileResources[$ProfileName].Keys) {{
            Set-ProfileResourceInheritance `
                -ProfileInfo $ProfileInfo `
                -ResourceName $ResourceName `
                -UseDefault $false
            $Source = Join-Path $Root $ProfileResources[$ProfileName][$ResourceName]
            $Target = Get-ProfileResourceTarget -Base $ProfileRoot -ResourceName $ResourceName
            Copy-ProfileResource `
                -Source $Source `
                -Target $Target `
                -Label "$ProfileName $ResourceName" `
                -Directory ($ResourceName -eq 'snippets')
        }}
    }}

    # Windows PowerShell 5 的 Set-Content -Encoding UTF8 会写入 BOM，
    # VS Code 可能因此无法解析 storage.json。显式写入无 BOM UTF-8。
    $StorageJson = $Storage | ConvertTo-Json -Depth 100
    $Utf8WithoutBom = New-Object System.Text.UTF8Encoding($false)
    [IO.File]::WriteAllText($StoragePath, $StorageJson, $Utf8WithoutBom)
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
        all_extensions = list(dict.fromkeys(
            config.extensions
            + tuple(
                extension_id
                for _, profile_extensions in config.extension_profiles
                for extension_id in profile_extensions
            )
        ))
        for extension_id in all_extensions:
            progress(f"解析 {extension_id}...")
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

        profile_indexes = {
            name: index
            for index, (name, _) in enumerate(config.extension_profiles, start=1)
        }
        settings_target = root / "user-data" / "default" / "settings.json"
        _copy_or_create_empty_settings(config.settings, settings_target)
        profile_settings_entries: dict[str, dict[str, Any]] = {}
        for profile_setting in config.profile_settings:
            if profile_setting.use_default:
                profile_settings_entries[profile_setting.name] = {"use_default": True}
                continue
            index = profile_indexes[profile_setting.name]
            profile_target = root / "user-data" / "profiles" / f"profile-{index}" / "settings.json"
            _copy_or_create_empty_settings(profile_setting.source, profile_target)
            profile_settings_entries[profile_setting.name] = {
                "file": profile_target.relative_to(root).as_posix(),
                "sha256": sha256_file(profile_target),
            }

        resource_names = {
            "keybindings": "keybindings.json",
            "snippets": "snippets",
            "tasks": "tasks.json",
            "mcp": "mcp.json",
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
            "install": {"mode": config.install_mode},
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
        staging_parent.rmdir()
        progress("创建 ZIP 压缩包...")
        with zipfile.ZipFile(partial_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for item in sorted(final_root.rglob("*")):
                archive_name = Path(bundle_name) / item.relative_to(final_root)
                if item.is_dir():
                    archive.writestr(archive_name.as_posix().rstrip("/") + "/", "")
                elif item.is_file():
                    archive.write(item, archive_name)
        partial_zip.replace(final_zip)
        if archive_only:
            shutil.rmtree(final_root)
        return final_zip
    except Exception:
        partial_zip.unlink(missing_ok=True)
        shutil.rmtree(staging_parent, ignore_errors=True)
        raise
