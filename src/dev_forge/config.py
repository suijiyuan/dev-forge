"""Configuration parsing and local VS Code resource discovery."""

from __future__ import annotations

import json
import os
import platform
import re
from pathlib import Path
from typing import Any

from .models import Config, PackagerError, ProfileResource, ProfileSettings
from .semver import Version


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
        "xml": "xml",
    }
    if kind not in filenames:
        raise ValueError(f"未知的 VS Code Profile 资源: {kind}")
    root = _profile_root(profile_name)
    if root is None:
        return None
    candidate = root / filenames[kind]
    expected = (
        candidate.is_dir() if kind in {"snippets", "xml"} else candidate.is_file()
    )
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
                if content[index : index + 2] == "*/":
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


def load_config(
    path: Path, settings_override: str | None = None, output_override: str | None = None
) -> Config:
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
    legacy_install_mode = install_value.get("mode", "merge")
    if legacy_install_mode not in {"merge", "replace"}:
        raise PackagerError("install.mode 只能是 merge 或 replace")
    replace_extensions = legacy_install_mode == "replace"

    extensions_value = raw.get("extensions", [])
    extension_profiles: list[tuple[str, tuple[str, ...]]] = []
    if isinstance(extensions_value, list):
        extensions = _parse_extension_ids(extensions_value, "extensions")
    elif isinstance(extensions_value, dict):
        unknown_keys = set(extensions_value) - {"default", "profiles"}
        if unknown_keys:
            names = "、".join(sorted(unknown_keys))
            raise PackagerError(f"extensions 包含未知配置项: {names}")
        extensions = _parse_extension_ids(
            extensions_value.get("default", []), "extensions.default"
        )
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
                raise PackagerError(
                    "Default 是保留名称；通用插件请配置到 extensions.default"
                )
            if normalized_name in seen_profile_names:
                raise PackagerError(f"Profile 名称不能仅有大小写差异: {name}")
            seen_profile_names.add(normalized_name)
            parsed_extensions = _parse_extension_ids(
                profile_extensions, f"extensions.profiles.{name}"
            )
            parsed_extensions = [
                item for item in parsed_extensions if item not in extensions
            ]
            extension_profiles.append((name, tuple(parsed_extensions)))
    else:
        raise PackagerError("extensions 必须是字符串数组或包含 default/profiles 的对象")

    settings_value = raw.get("settings", "auto")
    profile_settings: list[ProfileSettings] = []
    if isinstance(settings_value, str):
        default_settings_value = settings_override or settings_value
        settings = _resolve_settings_source(
            path.parent, default_settings_value, "settings"
        )
    elif isinstance(settings_value, dict):
        unknown_keys = set(settings_value) - {"default", "profiles"}
        if unknown_keys:
            names = "、".join(sorted(unknown_keys))
            raise PackagerError(f"settings 包含未知配置项: {names}")
        default_settings_value = settings_override or settings_value.get(
            "default", "auto"
        )
        settings = _resolve_settings_source(
            path.parent, default_settings_value, "settings.default"
        )
        profile_settings_value = settings_value.get("profiles", {})
        if not isinstance(profile_settings_value, dict):
            raise PackagerError("settings.profiles 必须是以 Profile 名称为键的对象")
        extension_profile_names = {
            name.casefold(): name for name, _ in extension_profiles
        }
        seen_settings_profiles: set[str] = set()
        for configured_name, profile_value in profile_settings_value.items():
            if not isinstance(configured_name, str) or not configured_name.strip():
                raise PackagerError("settings.profiles 的 Profile 名称不能为空")
            normalized_name = configured_name.strip().casefold()
            if normalized_name in seen_settings_profiles:
                raise PackagerError(
                    f"settings Profile 名称不能仅有大小写差异: {configured_name}"
                )
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
            elif isinstance(profile_value, dict) and profile_value == {
                "use_default": True
            }:
                profile_settings.append(ProfileSettings(profile_name, None, True))
            else:
                raise PackagerError(
                    f'settings.profiles.{profile_name} 必须是路径、auto 或 {{"use_default": true}}'
                )
    else:
        raise PackagerError("settings 必须是路径字符串或包含 default/profiles 的对象")

    default_resource_kinds = {"keybindings", "snippets", "tasks", "mcp", "xml"}
    profile_resource_kinds = {"keybindings", "snippets", "tasks", "mcp"}
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
    unknown_default_resources = set(default_resources_value) - default_resource_kinds
    if unknown_default_resources:
        names = "、".join(sorted(unknown_default_resources))
        raise PackagerError(f"resources.default 包含未知资源: {names}")
    resources: list[tuple[str, Path]] = []
    for kind, value in default_resources_value.items():
        source = _resolve_resource_source(
            path.parent, value, f"resources.default.{kind}", kind
        )
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
            raise PackagerError(
                f"resources Profile 名称不能仅有大小写差异: {configured_name}"
            )
        seen_resource_profiles.add(normalized_name)
        if normalized_name not in extension_profile_names:
            raise PackagerError(
                f"resources.profiles.{configured_name} 未在 extensions.profiles 中声明"
            )
        profile_name = extension_profile_names[normalized_name]
        if not isinstance(profile_value, dict):
            raise PackagerError(f"resources.profiles.{profile_name} 必须是对象")
        unknown_profile_resources = set(profile_value) - profile_resource_kinds
        if unknown_profile_resources:
            names = "、".join(sorted(unknown_profile_resources))
            raise PackagerError(
                f"resources.profiles.{profile_name} 包含未知资源: {names}"
            )
        for kind, value in profile_value.items():
            if isinstance(value, dict) and value == {"use_default": True}:
                profile_resources.append(
                    ProfileResource(profile_name, kind, None, True)
                )
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
        replace_extensions,
        tuple(resources),
        tuple(profile_resources),
    )


def _parse_extension_ids(value: Any, field: str) -> list[str]:
    extension_pattern = r"[A-Za-z0-9_-]+\.[A-Za-z0-9_.-]+"
    if not isinstance(value, list) or not all(
        isinstance(item, str) and re.fullmatch(extension_pattern, item)
        for item in value
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
        source = find_resource(kind, profile_name)
        if source is None:
            return None
    else:
        source = _relative_to(base, value)
    valid = source.is_dir() if kind in {"snippets", "xml"} else source.is_file()
    if not valid:
        expected = "目录" if kind in {"snippets", "xml"} else "文件"
        raise PackagerError(f"{field} 指定的{expected}不存在: {source}")
    if kind == "xml" and not (source / "catalog.xml").is_file():
        raise PackagerError(f"{field} 指定的目录缺少 catalog.xml: {source}")
    return source


def _relative_to(base: Path, value: str) -> Path:
    result = Path(value).expanduser()
    return (base / result).resolve() if not result.is_absolute() else result.resolve()
