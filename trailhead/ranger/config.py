"""Hub configuration: one JSON file per account, ``config_dir("ranger")/hubs/<account>.json``.

Reading refuses an absent or malformed file with :class:`HubConfigError`; writing
is atomic (temp file in the same directory, then rename).
"""

from __future__ import annotations

import json
import os
import re
import tomllib
from dataclasses import asdict, dataclass

from trailhead.launch_profile import hub_config_path, hub_home
from trailhead.paths import config_dir, ensure_dir

DEFAULT_LAUNCH_ACCOUNT = "default"

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_ACCOUNT_FILE = re.compile(r"([a-z0-9][a-z0-9-]*)\.json")


class HubConfigError(ValueError):
    """A hub config is absent, malformed, or conflicts with the requested values."""


@dataclass(frozen=True)
class HubConfig:
    name: str
    account: str
    launch_account: str


def validate_name(name: str) -> str:
    if not name.strip():
        raise HubConfigError("hub name must not be empty")
    if _CONTROL_CHARS.search(name):
        raise HubConfigError("hub name must not contain control characters")
    return name


def validate_launch_account(value: str) -> str:
    if _CONTROL_CHARS.search(value):
        raise HubConfigError("launch account must not contain control characters")
    if value == DEFAULT_LAUNCH_ACCOUNT or value.startswith("/") or value == "~" or value.startswith("~/"):
        return value
    raise HubConfigError(
        f"launch account {value!r} must be 'default' or an absolute or ~-prefixed path"
    )


def read_hub_config(
    account: str, *, env: dict[str, str] | None = None, platform: str | None = None
) -> HubConfig:
    path = hub_config_path(account, env=env, platform=platform)
    try:
        text = path.read_text()
    except FileNotFoundError:
        raise HubConfigError(f"no hub is configured for account {account!r} ({path} is absent)")
    except OSError as exc:
        raise HubConfigError(f"{path}: cannot read hub config: {exc}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise HubConfigError(f"{path}: malformed hub config ({exc})") from exc
    fields = ("name", "account", "launch_account")
    if not isinstance(data, dict) or any(not isinstance(data.get(k), str) for k in fields):
        raise HubConfigError(
            f"{path}: malformed hub config (expected a JSON object with string "
            "name, account and launch_account)"
        )
    return HubConfig(name=data["name"], account=data["account"], launch_account=data["launch_account"])


def list_hub_accounts(
    *, env: dict[str, str] | None = None, platform: str | None = None
) -> list[str]:
    hubs = hub_config_path("a", env=env, platform=platform).parent
    if not hubs.is_dir():
        return []
    return sorted(m.group(1) for p in hubs.iterdir() if (m := _ACCOUNT_FILE.fullmatch(p.name)))


def write_hub_config(
    config: HubConfig, *, env: dict[str, str] | None = None, platform: str | None = None
) -> bool:
    """Write *config* once; return True if it wrote, False if an identical file exists.

    Creates the hub home (0700). Raises :class:`HubConfigError` naming the file
    when one exists that is malformed or holds different values.
    """
    validate_name(config.name)
    validate_launch_account(config.launch_account)
    path = hub_config_path(config.account, env=env, platform=platform)
    if path.exists():
        existing = read_hub_config(config.account, env=env, platform=platform)
        if existing == config:
            ensure_dir(hub_home(config.account, env=env, platform=platform))
            return False
        raise HubConfigError(
            f"{path} already configures account {config.account!r} with different "
            "values; edit or delete that file to change it"
        )
    ensure_dir(hub_home(config.account, env=env, platform=platform))
    ensure_dir(path.parent)
    tmp = path.with_name(f".{path.name}.tmp")
    try:
        tmp.write_text(json.dumps(asdict(config), indent=2) + "\n")
        os.replace(tmp, path)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        raise HubConfigError(f"{path}: cannot write hub config: {exc}") from exc
    return True


def camp_declared_launch_accounts(
    *, env: dict[str, str] | None = None, platform: str | None = None
) -> set[str]:
    """Launch accounts camp groups declare (``[launch] account``), ``~`` expanded.

    Read from camp's own config location; a group file that cannot be parsed
    declares nothing.
    """
    groups = config_dir("camp", env=env, platform=platform) / "groups"
    home = (env if env is not None else os.environ).get("HOME", "")
    declared: set[str] = set()
    if not groups.is_dir():
        return declared
    for toml_file in sorted(groups.glob("*.toml")):
        try:
            account = tomllib.loads(toml_file.read_text()).get("launch", {}).get("account")
        except (OSError, tomllib.TOMLDecodeError, AttributeError):
            continue
        if isinstance(account, str):
            declared.add(expand_home(account, home))
    return declared


def expand_home(value: str, home: str) -> str:
    if value == "~" or value.startswith("~/"):
        return home + value[1:]
    return value
