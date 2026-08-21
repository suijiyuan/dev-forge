"""Compatibility facade for the original public API."""

from .bundle import build_bundle, vscode_download
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

__all__ = [
    "XML_CATALOG_SETTING_TOKEN",
    "Config",
    "ExtensionRelease",
    "PackagerError",
    "ProfileResource",
    "ProfileSettings",
    "_request_json",
    "build_bundle",
    "download_file",
    "find_resource",
    "find_settings",
    "load_config",
    "load_extension_lock",
    "query_extension",
    "sha256_file",
    "user_data_root",
    "validate_vsix",
    "vscode_download",
    "write_extension_lock",
]
