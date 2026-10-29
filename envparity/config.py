from __future__ import annotations

import fnmatch
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

BUILTIN_PROVIDED = [
    "PATH", "HOME", "USER", "PWD", "HOSTNAME", "SHELL", "TERM", "LANG", "TZ", "CI", "COMPOSE_*", "DOCKER_*", "GITHUB_*",
    "RUNNER_*", "KUBERNETES_*", "NODE_ENV",
]


@dataclass
class Config:
    ignore: list[str] = field(default_factory=list)
    provided: list[str] = field(default_factory=lambda: list(BUILTIN_PROVIDED))
    rules: dict[str, str] = field(default_factory=dict)
    exclude: list[str] = field(default_factory=list)

    def ignored(self, name: str) -> bool:
        return any(fnmatch.fnmatchcase(name, p) for p in self.ignore)

    def is_provided(self, name: str) -> bool:
        return any(fnmatch.fnmatchcase(name, p) for p in self.provided)


def load(root: Path, explicit: Path | None) -> Config:
    path = explicit or root / ".envparity.toml"
    config = Config()
    if not path.is_file():
        if explicit:
            raise SystemExit(f"envparity: config file not found: {path}")
        return config
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError) as exc:
        raise SystemExit(f"envparity: cannot read {path}: {exc}")
    config.ignore = _str_list(data.get("ignore"), path, "ignore")
    config.provided += _str_list(data.get("provided"), path, "provided")
    config.exclude = _str_list(data.get("exclude"), path, "exclude")
    rules = data.get("rules", {})
    if not isinstance(rules, dict):
        raise SystemExit(f"envparity: [rules] in {path} must be a table")
    for rule, level in rules.items():
        if level not in ("error", "warn", "info", "off"):
            raise SystemExit(f"envparity: rule '{rule}' in {path}: level must be error, warn, info or off")
        config.rules[rule] = level
    return config


def _str_list(value, path: Path, key: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise SystemExit(f"envparity: '{key}' in {path} must be a list of strings")
    return value
