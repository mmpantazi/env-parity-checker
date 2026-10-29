from __future__ import annotations

import json
from collections import Counter, defaultdict

from .checks import LAYER_LABEL
from .model import LAYERS, Finding, Project

COLOUR = {"error": "\033[31m", "warn": "\033[33m", "info": "\033[36m"}
RESET = "\033[0m"


def summary(findings: list[Finding]) -> dict[str, int]:
    counts = Counter(f.severity for f in findings)
    return {s: counts.get(s, 0) for s in ("error", "warn", "info")}


def text(project: Project, findings: list[Finding], colour: bool = False) -> str:
    lines = []
    seen = ", ".join(f"{LAYER_LABEL[l]} ({len(project.files[l])})" for l in LAYERS if project.files.get(l))
    lines.append(f"Checked: {seen or 'nothing found'}")
    if project.skipped:
        lines.append(f"Skipped {len(project.skipped)} templated (Helm) file(s): {', '.join(project.skipped[:3])}"
                     + (" ..." if len(project.skipped) > 3 else "") + "; render them with `helm template` into a folder and scan that.")
    lines.append("")
    for f in findings:
        tag = f"{f.severity.upper():5}"
        if colour:
            tag = f"{COLOUR[f.severity]}{tag}{RESET}"
        lines.append(f"{tag} {f.rule:<14} {f.message}")
        if f.locations:
            lines.append(f"      at {', '.join(f.locations)}")
        if f.hint:
            lines.append(f"      fix: {f.hint}")
    counts = summary(findings)
    lines.append("")
    lines.append(f"{counts['error']} error(s), {counts['warn']} warning(s), {counts['info']} note(s)")
    return "\n".join(lines)


def matrix(project: Project) -> str:
    rows: dict[str, set[str]] = defaultdict(set)
    for ref in project.refs:
        if ref.name:
            rows[ref.name].add(ref.layer)
    layers = [l for l in LAYERS if project.files.get(l)]
    if not rows:
        return "No variables found."
    width = max(len(n) for n in rows)
    head = "VARIABLE".ljust(width) + "  " + "  ".join(f"{l[:8]:<8}" for l in layers)
    body = ["  ".join([n.ljust(width)] + [("yes" if l in rows[n] else "-").ljust(8) for l in layers]) for n in sorted(rows)]
    return "\n".join([head, *body])


def as_json(project: Project, findings: list[Finding]) -> str:
    return json.dumps({
        "files": project.files,
        "skipped": project.skipped,
        "summary": summary(findings),
        "findings": [f.to_dict() for f in findings],
    }, indent=2)


def github(findings: list[Finding]) -> str:
    out = []
    for f in findings:
        level = {"error": "error", "warn": "warning", "info": "notice"}[f.severity]
        file, line = "", ""
        if f.locations:
            file, _, line = f.locations[0].split(" -> ")[0].rpartition(":")
        message = f"{f.rule}: {f.message}".replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        props = f"file={file},line={line}," if file else ""
        out.append(f"::{level} {props}title=envparity {f.rule}::{message}")
    return "\n".join(out)
