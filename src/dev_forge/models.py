"""Shared immutable models and domain errors."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

XML_CATALOG_SETTING_TOKEN = "__DEV_FORGE_XML_CATALOG__"


class PackagerError(RuntimeError):
    """A user-facing bundle construction error."""


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
    replace_extensions: bool = False
    resources: tuple[tuple[str, Path], ...] = ()
    profile_resources: tuple[ProfileResource, ...] = ()


@dataclass(frozen=True)
class ExtensionRelease:
    extension_id: str
    version: str
    engine: str
    target_platform: str | None
    download_url: str
    sha256: str | None = None
