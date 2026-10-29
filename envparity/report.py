from __future__ import annotations

import json
from collections import Counter, defaultdict

from .checks import LAYER_LABEL
from .model import LAYERS, Finding, Project

COLOUR = {"error": "\033[31m", "warn": "\033[33m", "info": "\033[36m"}
RESET = "\033[0m"
COMPACT_AFTER = 5


def summary(findings: list[Finding]) -> dict[str, int]:
    counts = Counter(f.severity for f in findings)
    return {s: counts.get(s, 0) for s in ("error", "warn", "info")}


def text(project: Project, findings: list[Finding], colour: bool = False, hidden: int = 0) -> str:
    lines = []
    seen = ", ".join(f"{LAYER_LABEL[l]} ({len(project.files[l])})" for l in LAYERS if project.files.get(l))
    if not seen:
        return (f"Nothing to check under {project.root}: no .env.example, docker-compose, GitHub workflow or Kubernetes "
                "manifest found. Pass the project root as the first argument.")
    lines.append(f"Checked: {seen}")
    if project.helm:
        lines.append(f"Read {len(project.helm)} Helm template(s) on a best-effort basis (values are not rendered); "
                     "for exact results run `helm template` into a folder and scan that.")
    if project.skipped:
        lines.append(f"Skipped {len(project.skipped)} templated file(s) that are not valid YAML even after removing Helm "
                     f"directives: {', '.join(project.skipped[:3])}" + (" ..." if len(project.skipped) > 3 else ""))
    lines.append("")
    printed: Counter = Counter()
    for f in findings:
        tag = f"{f.severity.upper():5}"
        if colour:
            tag = f"{COLOUR[f.severity]}{tag}{RESET}"
        printed[f.rule] += 1
        lines.append(f"{tag} {f.rule:<14} {f.message}")
        if f.locations:
            lines.append(f"      at {', '.join(f.locations)}")
        if f.hint and printed[f.rule] <= COMPACT_AFTER:
            lines.append(f"      fix: {f.hint}")
        elif f.hint and printed[f.rule] == COMPACT_AFTER + 1:
            lines.append("      (fix hints omitted for the rest of this rule)")
    counts = summary(findings)
    lines.append("")
    by_rule = Counter(f.rule for f in findings)
    if len(by_rule) > 1:
        lines.append("By rule: " + ", ".join(f"{rule} {n}" for rule, n in by_rule.most_common()))
    lines.append(f"{counts['error']} error(s), {counts['warn']} warning(s), {counts['info']} note(s)"
                 + (f" ({hidden} known issue(s) hidden by the baseline)" if hidden else ""))
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


SUGGEST_RULES = {"missing", "undeclared", "code-undeclared"}


def suggest(project: Project, findings: list[Finding]) -> str:
    """Lines to append to .env.example for variables that are used but undocumented."""
    from .checks import SECRET_NAME

    names = sorted({f.name for f in findings if f.rule in SUGGEST_RULES and f.name})
    literal = {}
    for ref in project.refs:
        if ref.role == "define" and ref.kind == "literal" and ref.value and ref.name not in literal:
            literal[ref.name] = ref.value
    out = []
    for name in names:
        value = "" if SECRET_NAME.search(name) else literal.get(name, "")
        out.append(f"{name}={value}")
    return "\n".join(out)
