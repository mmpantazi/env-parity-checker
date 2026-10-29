from __future__ import annotations

import fnmatch
import os
import re
from pathlib import Path

from . import parsers
from .model import Finding, Project, Ref

SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", "vendor", ".tox", "target", ".next",
             ".terraform", ".mypy_cache", ".pytest_cache", "site-packages"}
TEMPLATE_RE = re.compile(r"^((\.?env)(\.[\w-]+)*\.(example|sample|template|dist|defaults)|(example|sample)\.env)$", re.IGNORECASE)
COMPOSE_RE = re.compile(r"^(docker-)?compose([.-][\w.-]+)?\.ya?ml$", re.IGNORECASE)
CODE_EXT = {".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".go", ".rb", ".rs", ".php", ".java", ".kt", ".cs", ".vue", ".svelte"}
MAX_CODE_BYTES = 1_000_000
MENTION_EXT = {".json", ".sh", ".bash", ".toml", ".ini", ".cfg", ".conf", ".tf", ".properties", ".service", ".mk"}
MENTION_NAMES = {"dockerfile", "makefile", "procfile"}
CAPS_WORD = re.compile(r"\b[A-Z][A-Z0-9_]{2,}\b")

CODE_PATTERNS = [
    re.compile(r"process\.env\.([A-Za-z_][A-Za-z0-9_]*)"),
    re.compile(r"process\.env\[\s*[\"']([A-Za-z_][A-Za-z0-9_]*)[\"']\s*\]"),
    re.compile(r"import\.meta\.env\.([A-Za-z_][A-Za-z0-9_]*)"),
    re.compile(r"os\.environ(?:\.get)?\s*[\[(]\s*[\"']([A-Za-z_][A-Za-z0-9_]*)[\"']"),
    re.compile(r"os\.(?:getenv|Getenv|LookupEnv)\s*\(\s*[\"']([A-Za-z_][A-Za-z0-9_]*)[\"']"),
    re.compile(r"\bENV(?:\.fetch)?\s*[\[(]\s*[\"']([A-Za-z_][A-Za-z0-9_]*)[\"']"),
    re.compile(r"\b(?:getenv|env)\s*\(\s*[\"']([A-Za-z_][A-Za-z0-9_]*)[\"']"),
    re.compile(r"env::var(?:_os)?\s*\(\s*[\"']([A-Za-z_][A-Za-z0-9_]*)[\"']"),
    re.compile(r"System\.getenv\s*\(\s*[\"']([A-Za-z_][A-Za-z0-9_]*)[\"']"),
    re.compile(r"Environment\.GetEnvironmentVariable\s*\(\s*[\"']([A-Za-z_][A-Za-z0-9_]*)[\"']"),
]
DESTRUCTURE = re.compile(r"\{([^}]*)\}\s*=\s*process\.env")


def _read(path: Path) -> str | None:
    try:
        return path.read_bytes().decode("utf-8", errors="replace")
    except OSError:
        return None


def _excluded(rel: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(rel, p) or fnmatch.fnmatch(rel, p + "/*") for p in patterns)


def walk(root: Path, exclude: list[str]):
    for current, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not _excluded(_rel(root, Path(current) / d), exclude))
        for name in sorted(files):
            path = Path(current) / name
            rel = _rel(root, path)
            if not _excluded(rel, exclude):
                yield path, rel


def _rel(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def scan_project(root: Path, exclude: list[str] | None = None, code: bool = True) -> Project:
    exclude = exclude or []
    project = Project(root=str(root))

    def read_sibling(rel: str, target: str) -> str | None:
        base = (root / rel).parent
        candidate = (base / target).resolve()
        try:
            candidate.relative_to(root.resolve())
        except ValueError:
            return None
        return _read(candidate) if candidate.is_file() else None

    for path, rel in walk(root, exclude):
        name = path.name
        suffix = path.suffix.lower()
        if TEMPLATE_RE.match(name):
            text = _read(path)
            if text is not None:
                parsers.parse_template(project, rel, text)
        elif suffix in (".yml", ".yaml"):
            text = _read(path)
            if text is None:
                continue
            if COMPOSE_RE.match(name):
                parsers.parse_compose(project, rel, text, read_sibling)
            elif ".github/workflows/" in "/" + rel:
                parsers.parse_workflow(project, rel, text)
            elif re.search(r"^apiVersion:", text, re.M) and re.search(r"^kind:", text, re.M):
                if "{{" in text:
                    project.skipped.append(rel)
                else:
                    parsers.parse_kube(project, rel, text, read_sibling)
        elif code and suffix in CODE_EXT:
            _scan_code(project, path, rel)
        elif code and (suffix in MENTION_EXT or name.lower() in MENTION_NAMES or name.lower().startswith("dockerfile")):
            text = _read(path)
            if text is not None and len(text) <= MAX_CODE_BYTES:
                project.mentions.update(CAPS_WORD.findall(text))
    return project


def _scan_code(project: Project, path: Path, rel: str) -> None:
    try:
        if path.stat().st_size > MAX_CODE_BYTES:
            return
    except OSError:
        return
    text = _read(path)
    if text is None:
        return
    project.add_file("code", rel)
    for number, line in enumerate(text.splitlines(), 1):
        names = [m.group(1) for pattern in CODE_PATTERNS for m in pattern.finditer(line)]
        for m in DESTRUCTURE.finditer(line):
            names += [part.split(":")[0].split("=")[0].strip() for part in m.group(1).split(",") if part.strip()]
        for name in names:
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
                project.refs.append(Ref(name, "code", "use", rel, number, "interp", scope="code"))
