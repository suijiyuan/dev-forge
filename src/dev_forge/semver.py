"""Small npm-style semver matcher for VS Code engine constraints.

It covers the forms used by extension manifests: comparators, whitespace AND,
``||``, hyphen ranges, x-ranges, caret and tilde ranges.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, order=True)
class Version:
    major: int
    minor: int = 0
    patch: int = 0

    @classmethod
    def parse(cls, value: str) -> Version:
        match = re.fullmatch(
            r"v?(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:[-+].*)?", value.strip()
        )
        if not match:
            raise ValueError(f"无效版本号: {value}")
        return cls(*(int(part or 0) for part in match.groups()))

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"


def _upper_for_caret(base: Version) -> Version:
    if base.major:
        return Version(base.major + 1, 0, 0)
    if base.minor:
        return Version(0, base.minor + 1, 0)
    return Version(0, 0, base.patch + 1)


def _test_comparator(version: Version, token: str) -> bool:
    token = token.strip()
    if not token or token in {"*", "x", "X"}:
        return True

    if token.startswith("^"):
        base = Version.parse(token[1:])
        return base <= version < _upper_for_caret(base)
    if token.startswith("~"):
        raw = token.lstrip("~> ")
        base = Version.parse(raw)
        parts = raw.split(".")
        upper = (
            Version(base.major + 1, 0, 0)
            if len(parts) == 1
            else Version(base.major, base.minor + 1, 0)
        )
        return base <= version < upper

    match = re.fullmatch(
        r"(>=|<=|>|<|=)?\s*v?(\d+|[xX*])(?:\.(\d+|[xX*]))?(?:\.(\d+|[xX*]))?(?:[-+].*)?",
        token,
    )
    if not match:
        raise ValueError(f"不支持的版本约束: {token}")
    operator, major, minor, patch = match.groups()
    pieces = (major, minor, patch)
    wildcard_at = next(
        (
            i
            for i, item in enumerate(pieces)
            if item is None or item.lower() == "x" or item == "*"
        ),
        None,
    )
    numeric = [int(item) if item and item.isdigit() else 0 for item in pieces]
    base = Version(*numeric)

    if wildcard_at is not None and not operator:
        if wildcard_at == 0:
            return True
        if wildcard_at == 1:
            return version.major == base.major
        return version.major == base.major and version.minor == base.minor

    operations = {
        None: lambda: version == base,
        "=": lambda: version == base,
        ">=": lambda: version >= base,
        "<=": lambda: version <= base,
        ">": lambda: version > base,
        "<": lambda: version < base,
    }
    return operations[operator]()


def satisfies(version_value: str, constraint: str) -> bool:
    """Return whether a stable version satisfies an npm-style constraint."""
    version = Version.parse(version_value)
    constraint = constraint.strip()
    if not constraint:
        return False

    for alternative in re.split(r"\s*\|\|\s*", constraint):
        hyphen = re.fullmatch(r"\s*(\S+)\s+-\s+(\S+)\s*", alternative)
        if hyphen:
            if (
                Version.parse(hyphen.group(1))
                <= version
                <= Version.parse(hyphen.group(2))
            ):
                return True
            continue
        tokens = [item for item in re.split(r"[\s,]+", alternative.strip()) if item]
        try:
            if tokens and all(_test_comparator(version, token) for token in tokens):
                return True
        except ValueError:
            continue
    return False
