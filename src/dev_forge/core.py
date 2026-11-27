"""Compatibility facade for the original public API."""

from .bundle import build_bundle, collect_extension_ids, vscode_download, vscode_platform
from .config import find_resource, find_settings, load_config, user_data_root
from .manifest import load_extension_lock, write_extension_lock
from .marketplace import (
    download_file,
    query_extension,
    sha256_file,
    validate_vsix,
)
from .marketplace import (
    request_json as _request_json,
)
from .models import (
    XML_CATALOG_SETTING_TOKEN,
    Config,
    ExtensionRelease,
    PackagerError,
    ProfileResource,
    ProfileSettings,
)
from .updater import (
    ExtensionChange,
    UpdateResult,
    auto_update,
    fetch_latest_vscode_version,
    update_config_vscode_version,
)

__all__ = [
    "XML_CATALOG_SETTING_TOKEN",
    "Config",
    "ExtensionChange",
    "ExtensionRelease",
    "PackagerError",
    "ProfileResource",
    "ProfileSettings",
    "UpdateResult",
    "_request_json",
    "auto_update",
    "build_bundle",
    "collect_extension_ids",
    "download_file",
    "fetch_latest_vscode_version",
    "find_resource",
    "find_settings",
    "load_config",
    "load_extension_lock",
    "query_extension",
    "sha256_file",
    "update_config_vscode_version",
    "user_data_root",
    "validate_vsix",
    "vscode_download",
    "vscode_platform",
    "write_extension_lock",
]
