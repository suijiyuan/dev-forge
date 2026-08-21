"""PowerShell installer rendering."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import XML_CATALOG_SETTING_TOKEN, Config


def _powershell_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _powershell_array(paths: list[str], indent: str = "") -> str:
    if not paths:
        return "@()"
    windows_paths = (path.replace("/", "\\") for path in paths)
    values = ",\n".join(
        f"{indent}    {_powershell_literal(path)}" for path in windows_paths
    )
    return f"@(\n{values}\n{indent})"


def _powershell_values(values: list[str], indent: str = "") -> str:
    if not values:
        return "@()"
    rendered = ",\n".join(
        f"{indent}    {_powershell_literal(value)}" for value in values
    )
    return f"@(\n{rendered}\n{indent})"


def _powershell_profile_map(profiles: list[tuple[str, list[str]]]) -> str:
    if not profiles:
        return "[ordered]@{}"
    entries = []
    for name, paths in profiles:
        entries.append(
            f"    {_powershell_literal(name)} = {_powershell_array(paths, '    ')}"
        )
    return "[ordered]@{\n" + "\n".join(entries) + "\n}"


def _powershell_extension_metadata(entries: list[dict[str, Any]]) -> str:
    if not entries:
        return "[ordered]@{}"
    rendered = []
    for entry in entries:
        path = entry["file"].replace("/", "\\")
        rendered.append(
            f"    {_powershell_literal(path)} = @{{ "
            f"Id = {_powershell_literal(entry['id'])}; "
            f"Version = {_powershell_literal(entry['version'])} }}"
        )
    return "[ordered]@{\n" + "\n".join(rendered) + "\n}"


def _powershell_settings_map(profiles: list[tuple[str, str]]) -> str:
    if not profiles:
        return "[ordered]@{}"
    entries = []
    for name, path in profiles:
        windows_path = path.replace("/", "\\")
        entries.append(
            f"    {_powershell_literal(name)} = {_powershell_literal(windows_path)}"
        )
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


def _powershell_file_hash_map(values: list[tuple[str, str]]) -> str:
    if not values:
        return "[ordered]@{}"
    entries = []
    for path, digest in values:
        windows_path = path.replace("/", "\\")
        entries.append(
            f"    {_powershell_literal(windows_path)} = {_powershell_literal(digest.lower())}"
        )
    return "[ordered]@{\n" + "\n".join(entries) + "\n}"


def _manifest_file_hashes(manifest: dict[str, Any]) -> list[tuple[str, str]]:
    values = [(manifest["vscode"]["file"], manifest["vscode"]["sha256"])]
    values.extend((item["file"], item["sha256"]) for item in manifest["extensions"])
    values.append(
        (
            manifest["settings"]["default"]["file"],
            manifest["settings"]["default"]["sha256"],
        )
    )
    values.extend(
        (item["file"], item["sha256"])
        for item in manifest["settings"]["profiles"].values()
        if "file" in item
    )
    for item in manifest["resources"]["default"].values():
        if "file" in item:
            values.append((item["file"], item["sha256"]))
        else:
            values.extend((entry["file"], entry["sha256"]) for entry in item["files"])
    for resources in manifest["resources"]["profiles"].values():
        for item in resources.values():
            if item.get("use_default") is True:
                continue
            if "file" in item:
                values.append((item["file"], item["sha256"]))
            else:
                values.extend(
                    (entry["file"], entry["sha256"]) for entry in item["files"]
                )
    return values


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
    (root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    extension_behavior = (
        "旧版 install.mode=replace 使 -ReplaceExtensions 默认启用；可使用 "
        "-ReplaceExtensions:$false 临时保留本机已有扩展。"
        if config.replace_extensions
        else "默认保留本机已有扩展；使用 -ReplaceExtensions 可先清理并重建扩展。"
    )
    readme = f"""VS Code {config.version} Windows 离线安装包

1. 在 PowerShell 中运行 .\\install.ps1。
2. {extension_behavior}
3. -ReplaceExtensions 会删除当前用户原有的扩展目录和各 Profile 的扩展清单。
4. 脚本先检查离线文件并校验 SHA-256，再检查现有 VS Code 安装方式并确认 VS Code 已关闭，然后才按选择的模式处理扩展。
5. 脚本按打包时的 packager.jsonc 配置创建 Profile：
   - 通用扩展安装到 Default 和所有已配置 Profile；
   - Profile 专属扩展仅安装到对应 Profile。
6. settings.json 仅在目标不存在时复制；使用 -ForceSettings 可覆盖。
7. keybindings、snippets、tasks、MCP、XML Catalog 默认保留已有文件；使用 -ForceResources 可覆盖。
8. 已安装相同版本的 VS Code 默认跳过；Archive Mode 仅复用版本和架构完全匹配的目录，使用 -ForceVSCodeInstall 可强制替换。
9. 默认会跳过各 Profile 中已经达到离线清单版本的扩展。
10. 扩展包成员和依赖必须显式打包，安装时不会由 VS Code CLI 自动展开。
11. 所有外部进程均有超时限制，标准输出、错误输出、退出码和耗时写入脚本启动时显示的日志。
12. 用户目录之外的 VSCODE_EXTENSIONS 默认拒绝清理；确认后可显式使用 -AllowExternalExtensionsDirectory。

详细版本和 SHA-256 校验值见 manifest.json；校验失败时不会清理现有扩展。
"""
    (root / "README.txt").write_text(readme, encoding="utf-8")
    installer = manifest["vscode"]["file"].replace("/", "\\")
    archive_literal = "$true" if config.package == "archive" else "$false"
    files_by_id = {
        extension["id"]: extension["file"] for extension in manifest["extensions"]
    }
    common_extensions = _powershell_array(
        [files_by_id[item] for item in config.extensions]
    )
    profile_extensions = _powershell_profile_map(
        [
            (name, [files_by_id[item] for item in extensions])
            for name, extensions in config.extension_profiles
        ]
    )
    extension_metadata = _powershell_extension_metadata(manifest["extensions"])
    file_hashes = _powershell_file_hash_map(_manifest_file_hashes(manifest))
    settings_manifest = manifest["settings"]
    default_settings_path = settings_manifest["default"]["file"].replace("/", "\\")
    shared_settings_profiles = _powershell_values(
        [
            name
            for name, value in settings_manifest["profiles"].items()
            if value.get("use_default") is True
        ]
    )
    profile_settings = _powershell_settings_map(
        [
            (name, value["file"])
            for name, value in settings_manifest["profiles"].items()
            if "file" in value
        ]
    )
    resources_manifest = manifest["resources"]

    def resource_path(value: dict[str, Any]) -> str:
        return value.get("file") or value["directory"]

    default_resources = _powershell_string_map(
        [
            (kind, resource_path(value))
            for kind, value in resources_manifest["default"].items()
        ]
    )
    shared_profile_resources = _powershell_nested_values_map(
        [
            (
                name,
                [
                    kind
                    for kind, value in values.items()
                    if value.get("use_default") is True
                ],
            )
            for name, values in resources_manifest["profiles"].items()
            if any(value.get("use_default") is True for value in values.values())
        ]
    )
    profile_resources = _powershell_nested_string_map(
        [
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
        ]
    )
    replace_extensions_literal = "$true" if config.replace_extensions else "$false"
    template_path = Path(__file__).with_name("templates") / "install.ps1"
    ps1 = template_path.read_text(encoding="utf-8")
    replacements = {
        "@@REPLACE_EXTENSIONS_LITERAL@@": replace_extensions_literal,
        "@@INSTALLER_PATH@@": installer,
        "@@ARCHIVE_LITERAL@@": archive_literal,
        "@@TARGET_VERSION@@": _powershell_literal(config.version),
        "@@TARGET_ARCH@@": _powershell_literal(config.arch),
        "@@PACKAGE_KIND@@": _powershell_literal(config.package),
        "@@COMMON_EXTENSIONS@@": common_extensions,
        "@@PROFILE_EXTENSIONS@@": profile_extensions,
        "@@EXTENSION_METADATA@@": extension_metadata,
        "@@FILE_HASHES@@": file_hashes,
        "@@DEFAULT_SETTINGS_PATH@@": default_settings_path,
        "@@SHARED_SETTINGS_PROFILES@@": shared_settings_profiles,
        "@@PROFILE_SETTINGS@@": profile_settings,
        "@@DEFAULT_RESOURCES@@": default_resources,
        "@@SHARED_PROFILE_RESOURCES@@": shared_profile_resources,
        "@@PROFILE_RESOURCES@@": profile_resources,
        "@@XML_CATALOG_TOKEN@@": _powershell_literal(XML_CATALOG_SETTING_TOKEN),
    }
    for placeholder, value in replacements.items():
        if placeholder not in ps1:
            raise ValueError(f"PowerShell template is missing {placeholder}")
        ps1 = ps1.replace(placeholder, value)
    if "@@" in ps1:
        raise ValueError("PowerShell template contains unresolved placeholders")
    (root / "install.ps1").write_text(ps1, encoding="utf-8-sig")
