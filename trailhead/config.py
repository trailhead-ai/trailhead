"""Persisted configuration for the trailhead management tool.

Config file: config_dir("trailhead") / config.toml
The directory is created via ensure_dir(..., 0o700) on first write.

Hermeticity: all path resolution goes through paths.config_dir so the
TRAILHEAD_CONFIG_DIR env override redirects the path in tests.

TOML writer: stdlib only (no third-party deps). The config shape is small
and flat-ish; a purpose-built minimal emitter is used for writing.
"""

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from trailhead.paths import config_dir, ensure_dir

_DEFAULT_REGISTRY = "github.com/trailhead-ai"
_CONFIG_FILENAME = "config.toml"


# ---------------------------------------------------------------------------
# Error
# ---------------------------------------------------------------------------


class ConfigError(Exception):
    """Raised for config file parse errors or structural problems.

    Always cites the file path. Never exposes raw TOMLDecodeError.
    """


# ---------------------------------------------------------------------------
# Data structure
# ---------------------------------------------------------------------------


@dataclass
class TrailheadConfig:
    """Persisted trailhead configuration.

    Fields:
        registry:         Source registry base (default: github.com/trailhead-ai).
                          This is a default VALUE, never hardcoded into install/update logic.
        path_integration: Whether to manage a shim dir on PATH (default: True).
        preset:           The active preset name (default: "standard").
        capabilities:     Active runtime capability set per tool (default: {}).
                          Maps tool name → list of active capability names.
    """

    registry: str = _DEFAULT_REGISTRY
    path_integration: bool = True
    preset: str = "standard"
    capabilities: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# TOML serializer (stdlib only, deterministic)
# ---------------------------------------------------------------------------


def _serialize_toml(cfg: TrailheadConfig) -> str:
    """Serialize TrailheadConfig to a minimal deterministic TOML string."""
    lines = []
    lines.append(f'registry = {_toml_str(cfg.registry)}')
    lines.append(f'path_integration = {_toml_bool(cfg.path_integration)}')
    lines.append(f'preset = {_toml_str(cfg.preset)}')
    lines.append("")
    if cfg.capabilities:
        lines.append("[capabilities]")
        for tool, caps in sorted(cfg.capabilities.items()):
            sorted_caps = sorted(caps)
            lines.append(f'{_toml_key(tool)} = {_toml_list(sorted_caps)}')
    else:
        lines.append("[capabilities]")
    lines.append("")
    return "\n".join(lines)


def _toml_str(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _toml_bool(value: bool) -> str:
    return "true" if value else "false"


def _toml_key(key: str) -> str:
    if key.isidentifier() and "-" not in key:
        return key
    return _toml_str(key)


def _toml_list(items: list[str]) -> str:
    if not items:
        return "[]"
    inner = ", ".join(_toml_str(item) for item in items)
    return f"[{inner}]"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _config_path(env: dict[str, str] | None) -> Path:
    """Resolve the config file path using the env-injectable paths resolver."""
    kwargs: dict = {}
    if env is not None:
        kwargs["env"] = env
    return config_dir("trailhead", **kwargs) / _CONFIG_FILENAME


def load_config(*, env: dict[str, str] | None = None) -> TrailheadConfig:
    """Load config from TOML, returning defaults if the file is absent.

    Args:
        env: Environment dict override (for hermeticity in tests — pass
             {"TRAILHEAD_CONFIG_DIR": str(tmp_path)} to redirect the path).

    Returns:
        TrailheadConfig (defaults if the file doesn't exist).

    Raises:
        ConfigError: If the file exists but is malformed TOML. Always cites
                     the file path; never exposes raw TOMLDecodeError.
    """
    path = _config_path(env)
    if not path.exists():
        return TrailheadConfig()

    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(
            f"malformed TOML in {path}: {exc}"
        ) from exc

    return _from_dict(data)


def save_config(cfg: TrailheadConfig, *, env: dict[str, str] | None = None) -> None:
    """Serialize and write cfg to the config TOML file.

    Creates the config directory (0o700) if it doesn't exist.

    Args:
        cfg: Configuration to persist.
        env: Environment dict override (for hermeticity in tests).
    """
    path = _config_path(env)
    ensure_dir(path.parent, mode=0o700)
    path.write_text(_serialize_toml(cfg))


# ---------------------------------------------------------------------------
# Internal deserialization
# ---------------------------------------------------------------------------


def _from_dict(data: dict) -> TrailheadConfig:
    """Build a TrailheadConfig from a parsed TOML dict, using defaults for missing keys."""
    registry = data.get("registry", _DEFAULT_REGISTRY)
    path_integration = data.get("path_integration", True)
    preset = data.get("preset", "standard")
    raw_caps = data.get("capabilities", {})

    capabilities: dict[str, list[str]] = {}
    if isinstance(raw_caps, dict):
        for tool, caps in raw_caps.items():
            if isinstance(caps, list):
                capabilities[tool] = [str(c) for c in caps]

    return TrailheadConfig(
        registry=str(registry),
        path_integration=bool(path_integration),
        preset=str(preset),
        capabilities=capabilities,
    )
