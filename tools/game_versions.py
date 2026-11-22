"""Supported Minecraft versions and their build/runtime profiles.

The launcher keeps one default line (Minecraft 26.1.2 / Java 25) and adds one
additional Fabric line (Minecraft 1.21.1 / Java 21). Any code that needs a
version-dependent decision should ask this module instead of hard-coding a
version string, so an unknown version fails loudly instead of silently using
the default.
"""
from __future__ import annotations

from dataclasses import dataclass

DEFAULT_VERSION = "26.1.2"
SUPPORTED_VERSIONS = ("26.1.2", "1.21.1")


@dataclass(frozen=True)
class VersionProfile:
    """Immutable facts about one supported Minecraft version.

    ``requires_remapping`` means the shipped classes are not in the runtime
    namespace yet: 1.21.1 must be remapped to Mojmap before use, while 26.1.2
    already ships Mojang-mapped classes.
    """

    version: str
    java_version: int
    runtime_namespace: str
    requires_remapping: bool
    render_roots: tuple[str, ...]


_PROFILES = {
    "26.1.2": VersionProfile("26.1.2", 25, "official", False,
        ("net/minecraft/client/renderer/RenderPipelines",)),
    "1.21.1": VersionProfile("1.21.1", 21, "official", True,
        ("com/mojang/blaze3d/systems/RenderSystem",
         "net/minecraft/client/renderer/GameRenderer",
         "net/minecraft/client/renderer/ShaderInstance")),
}


def get_profile(version: str = DEFAULT_VERSION) -> VersionProfile:
    """Return the profile for a supported version.

    Unsupported or malformed versions raise ``ValueError`` rather than falling
    back to the default, so a typo can never silently build another version.
    """
    if not isinstance(version, str):
        raise ValueError(f"Minecraft version must be text, got {type(version).__name__}")
    profile = _PROFILES.get(version)
    if profile is None:
        raise ValueError(
            f"Unsupported Minecraft version {version!r}; supported: "
            + ", ".join(SUPPORTED_VERSIONS)
        )
    return profile

