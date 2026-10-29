from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

LAYERS = ("template", "compose", "ci", "k8s", "code")
SEVERITIES = ("error", "warn", "info")


@dataclass
class Ref:
    """One place where an environment variable is defined or used."""

    name: str
    layer: str
    role: str  # "define" or "use"
    file: str
    line: int
    kind: str = "literal"  # literal, passthrough, ref, secret, configmap, field, interp, ctx
    value: str | None = None
    scope: str = ""
    required: bool = False  # use only: no default, must come from outside
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def where(self) -> str:
        return f"{self.file}:{self.line}"


@dataclass
class Finding:
    rule: str
    severity: str
    name: str
    message: str
    locations: list[str] = field(default_factory=list)
    hint: str = ""

    def to_dict(self) -> dict:
        return {
            "rule": self.rule,
            "severity": self.severity,
            "name": self.name,
            "message": self.message,
            "locations": self.locations,
            "hint": self.hint,
        }


@dataclass
class KubeObject:
    kind: str  # ConfigMap or Secret
    name: str
    file: str
    keys: dict[str, tuple[int, Any]] = field(default_factory=dict)


@dataclass
class Project:
    root: str
    refs: list[Ref] = field(default_factory=list)
    objects: dict[tuple[str, str], KubeObject] = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)
    files: dict[str, list[str]] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)
    mentions: set[str] = field(default_factory=set)

    def add_file(self, layer: str, path: str) -> None:
        self.files.setdefault(layer, []).append(path)


NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
PLACEHOLDER_RE = re.compile(
    r"^(<.*>|your[_-].*|change[_-]?me.*|replace[_-]?me.*|x{3,}|\*{3,}|\.\.\.|todo|example.*|secret|password|"
    r"\$\{.*\}|\$\(.*\)|\{\{.*\}\})$",
    re.IGNORECASE,
)


def is_placeholder(value: str | None, name: str | None = None) -> bool:
    if value is None or value.strip() == "":
        return True
    value = value.strip()
    if PLACEHOLDER_RE.match(value) or "placeholder" in value.lower():
        return True
    return bool(name) and value.upper().startswith(name.upper()) and bool(re.fullmatch(r"[A-Z0-9_]+", value))
