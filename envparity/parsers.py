from __future__ import annotations

import re
from pathlib import PurePosixPath

import yaml

from . import yamlx
from .model import HELM_TOKEN, NAME_RE, Finding, KubeObject, Project, Ref

POD_KINDS = {"Pod", "Deployment", "StatefulSet", "DaemonSet", "Job", "CronJob", "ReplicaSet", "ReplicationController"}
DOTENV_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_.-]*)\s*(?:=\s*(.*?))?\s*$")
INTERP = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?:(:?[-?+])([^}]*))?\}|\$([A-Za-z_][A-Za-z0-9_]*)")
CI_CONTEXT = re.compile(r"\$\{\{\s*(env|vars|secrets)\.([A-Za-z_][A-Za-z0-9_-]*)")
KUBE_REF = re.compile(r"(?<!\$)\$\(([A-Za-z_][A-Za-z0-9_.-]*)\)")


def parse_dotenv_text(text: str) -> list[tuple[str, str | None, int]]:
    out = []
    for number, raw in enumerate(text.splitlines(), 1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        match = DOTENV_LINE.match(raw)
        if not match:
            continue
        value = match.group(2)
        if value is not None:
            value = _unquote(value)
        out.append((match.group(1), value, number))
    return out


def _unquote(value: str) -> str:
    value = value.strip()
    if value[:1] in "\"'" and len(value) > 1:
        end = value.find(value[0], 1)
        return value[1:end] if end > 0 else value[1:]
    return re.split(r"\s+#", value, maxsplit=1)[0].strip()


def _text(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def parse_template(project: Project, rel: str, text: str) -> None:
    project.add_file("template", rel)
    seen: dict[str, int] = {}
    for name, value, line in parse_dotenv_text(text):
        if name in seen:
            project.findings.append(Finding(
                "duplicate", "warn", name, f"defined twice in {rel} (lines {seen[name]} and {line}); the last one wins",
                [f"{rel}:{seen[name]}", f"{rel}:{line}"]))
        seen[name] = line
        project.refs.append(Ref(name, "template", "define", rel, line, "literal", value or ""))


def _interpolations(project: Project, rel: str, text: str, layer: str = "compose") -> None:
    for number, raw in enumerate(text.splitlines(), 1):
        if raw.lstrip().startswith("#"):
            continue
        line = re.split(r"\s+#", raw, maxsplit=1)[0].replace("$$", "\0\0")
        for match in INTERP.finditer(line):
            name = match.group(1) or match.group(4)
            op, arg = match.group(2), match.group(3)
            required = op is None or (op.endswith("?"))
            if op and op.endswith("-"):
                required = False
            if op and op.endswith("+"):
                required = False
            project.refs.append(Ref(name, layer, "use", rel, number, "interp", scope="${}", required=required,
                                    extra={"op": op, "default": arg}))


def parse_compose(project: Project, rel: str, text: str, read_sibling) -> None:
    project.add_file("compose", rel)
    try:
        docs = yamlx.load_all(text)
    except yaml.YAMLError as exc:
        project.findings.append(Finding("parse-error", "warn", "", f"{rel}: invalid YAML ({_first_line(exc)})", [rel]))
        return
    _interpolations(project, rel, text)
    for doc in docs:
        services = doc.get("services") if isinstance(doc, dict) else None
        if not isinstance(services, dict):
            continue
        for service, spec in services.items():
            if not isinstance(spec, dict):
                continue
            env_files = spec.get("env_file")
            if isinstance(env_files, (str, dict)):
                env_files = [env_files]
            for item in env_files or []:
                path = item.get("path") if isinstance(item, dict) else item
                if not isinstance(path, str):
                    continue
                content = read_sibling(rel, path)
                if content is None:
                    project.findings.append(Finding(
                        "env-file-missing", "info", "", f"service '{service}' reads env_file {path} which is not in the repo",
                        [f"{rel}:{yamlx.line_of(spec, 'env_file')}"],
                        "That is normal for a real .env; make sure the matching .env.example documents its keys."))
                    continue
                for name, value, line in parse_dotenv_text(content):
                    project.refs.append(Ref(name, "compose", "define", f"{rel} -> {path}", line, "literal", value or "",
                                            f"service {service}"))
            env = spec.get("environment")
            line0 = yamlx.line_of(spec, "environment")
            if isinstance(env, dict):
                for key, value in env.items():
                    _compose_define(project, rel, str(key), value, service, yamlx.line_of(env, key, line0))
            elif isinstance(env, list):
                seen: dict[str, int] = {}
                for index, entry in enumerate(env):
                    line = yamlx.line_of(env, index, line0)
                    if not isinstance(entry, str):
                        continue
                    key, sep, value = entry.partition("=")
                    if key in seen:
                        project.findings.append(Finding(
                            "duplicate", "warn", key, f"service '{service}' sets {key} twice (lines {seen[key]} and {line})",
                            [f"{rel}:{seen[key]}", f"{rel}:{line}"]))
                    seen[key] = line
                    _compose_define(project, rel, key, value if sep else None, service, line)


def _compose_define(project: Project, rel: str, name: str, value, service: str, line: int) -> None:
    if not NAME_RE.match(name):
        return
    if value is None:
        project.refs.append(Ref(name, "compose", "define", rel, line, "passthrough", None, f"service {service}"))
        project.refs.append(Ref(name, "compose", "use", rel, line, "interp", scope="passthrough", required=True,
                                extra={"op": None, "default": None}))
        return
    text = _text(value)
    kind = "ref" if INTERP.search(text.replace("$$", "")) else "literal"
    project.refs.append(Ref(name, "compose", "define", rel, line, kind, None if kind == "ref" else text, f"service {service}"))


def parse_workflow(project: Project, rel: str, text: str) -> None:
    project.add_file("ci", rel)
    try:
        docs = yamlx.load_all(text)
    except yaml.YAMLError as exc:
        project.findings.append(Finding("parse-error", "warn", "", f"{rel}: invalid YAML ({_first_line(exc)})", [rel]))
        return
    for doc in docs:
        if isinstance(doc, dict):
            _walk_env(project, rel, doc, "workflow")
    defined = {r.name.lower() for r in project.refs if r.file == rel and r.role == "define"}
    sets_env_file = "GITHUB_ENV" in text
    for number, raw in enumerate(text.splitlines(), 1):
        if raw.lstrip().startswith("#"):
            continue
        for match in CI_CONTEXT.finditer(raw):
            ctx, name = match.groups()
            if ctx == "env":
                project.refs.append(Ref(name, "ci", "use", rel, number, "interp", scope="env context",
                                        required=name.lower() not in defined and not (sets_env_file and re.search(rf"\b{name}=", text)),
                                        extra={"op": None, "default": None}))
            else:
                project.refs.append(Ref(name, "ci", "use", rel, number, "ctx", scope=ctx,
                                        extra={"external": ctx}))


def _walk_env(project: Project, rel: str, node, scope: str) -> None:
    if isinstance(node, dict):
        env = node.get("env")
        if isinstance(env, dict):
            for key, value in env.items():
                text = _text(value)
                is_ctx = text is not None and "${{" in text
                project.refs.append(Ref(str(key), "ci", "define", rel, yamlx.line_of(env, key), "ref" if is_ctx else "literal",
                                        None if is_ctx else text, scope))
        for key, value in node.items():
            if key != "env":
                _walk_env(project, rel, value, str(node.get("name", key)) if key in ("jobs", "steps") else scope)
    elif isinstance(node, list):
        for item in node:
            _walk_env(project, rel, item, scope)


def _first_line(exc: Exception) -> str:
    return str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__


HELM_DIRECTIVE = re.compile(r"^\s*\{\{-?.*-?\}\}\s*$")
HELM_OPAQUE = re.compile(r"\b(include|toYaml|range|tpl)\b")


def _helm_to_yaml(text: str) -> tuple[str, bool]:
    """Best effort: blank out whole-line directives and replace inline {{ }} with a token."""
    lines, opaque = [], False
    for line in text.splitlines():
        if HELM_DIRECTIVE.match(line):
            opaque = opaque or bool(HELM_OPAQUE.search(line))
            lines.append("")
        else:
            lines.append(re.sub(r"\{\{.*?\}\}", HELM_TOKEN, line))
    return "\n".join(lines), opaque


def parse_kube(project: Project, rel: str, text: str, read_sibling, helm: bool = False) -> bool:
    if helm:
        text, opaque = _helm_to_yaml(text)
        project.k8s_opaque = project.k8s_opaque or opaque
    try:
        docs = yamlx.load_all(text)
    except yaml.YAMLError as exc:
        if not helm:
            project.add_file("k8s", rel)
            project.findings.append(Finding("parse-error", "warn", "", f"{rel}: invalid YAML ({_first_line(exc)})", [rel]))
        return False
    project.add_file("k8s", rel)
    before = len(project.findings)
    _parse_kube_docs(project, rel, docs, read_sibling)
    if helm:
        project.findings[before:] = [f for f in project.findings[before:] if f.rule != "duplicate"]
    return True


def _parse_kube_docs(project: Project, rel: str, docs: list, read_sibling) -> None:
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        kind = doc.get("kind")
        meta = doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {}
        name = str(meta.get("name", ""))
        if kind in ("ConfigMap", "Secret"):
            _register_object(project, rel, doc, kind, name)
        elif kind == "Kustomization":
            _kustomization(project, rel, doc, read_sibling)
        elif kind in POD_KINDS:
            spec = _pod_spec(doc, kind)
            for section in ("initContainers", "containers"):
                for container in spec.get(section) or []:
                    if isinstance(container, dict):
                        _container(project, rel, kind, name, container)


def _pod_spec(doc: dict, kind: str) -> dict:
    spec = doc.get("spec") or {}
    if kind == "Pod":
        return spec if isinstance(spec, dict) else {}
    if kind == "CronJob":
        spec = ((spec.get("jobTemplate") or {}).get("spec")) or {}
    template = (spec.get("template") or {}) if isinstance(spec, dict) else {}
    inner = template.get("spec") if isinstance(template, dict) else None
    return inner if isinstance(inner, dict) else {}


def _register_object(project: Project, rel: str, doc: dict, kind: str, name: str) -> None:
    if HELM_TOKEN in name:
        return
    obj = project.objects.setdefault((kind, name), KubeObject(kind, name, rel))
    for section in ("data", "stringData", "binaryData"):
        data = doc.get(section)
        if not isinstance(data, dict):
            continue
        for key, value in data.items():
            line = yamlx.line_of(data, key)
            obj.keys[str(key)] = (line, value)
            if kind == "ConfigMap" and section == "data" and not isinstance(value, str) and value is not None:
                project.findings.append(Finding(
                    "k8s-unquoted", "error", str(key),
                    f"ConfigMap {name}: value {value!r} is {_yaml_type(value)}, not a string; kubectl apply will reject it",
                    [f"{rel}:{line}"], f'Quote it: {key}: "{_text(value)}"'))


def _yaml_type(value) -> str:
    return {bool: "a YAML boolean", int: "a YAML number", float: "a YAML number"}.get(type(value), type(value).__name__)


def _kustomization(project: Project, rel: str, doc: dict, read_sibling) -> None:
    for generator, kind in (("configMapGenerator", "ConfigMap"), ("secretGenerator", "Secret")):
        for entry in doc.get(generator) or []:
            if not isinstance(entry, dict):
                continue
            obj = project.objects.setdefault((kind, str(entry.get("name", ""))), KubeObject(kind, str(entry.get("name", "")), rel))
            for literal in entry.get("literals") or []:
                key, _, value = str(literal).partition("=")
                obj.keys[key] = (yamlx.line_of(entry, "literals"), value)
            for env_file in entry.get("envs") or []:
                content = read_sibling(rel, str(env_file))
                if content is not None:
                    for key, value, line in parse_dotenv_text(content):
                        obj.keys[key] = (line, value)
            if entry.get("files"):
                for item in entry["files"]:
                    key = str(item).split("=", 1)[0]
                    obj.keys[PurePosixPath(key).name if "=" not in str(item) else key] = (yamlx.line_of(entry, "files"), None)


def _container(project: Project, rel: str, kind: str, workload: str, container: dict) -> None:
    scope = f"{kind}/{workload}/{container.get('name', '?')}"
    env = container.get("env")
    defined_so_far: set[str] = set()
    seen: dict[str, int] = {}
    for index, item in enumerate(env or []):
        if not isinstance(item, dict) or "name" not in item:
            continue
        name = str(item["name"])
        if HELM_TOKEN in name:
            continue
        line = yamlx.line_of(env, index)
        if name in seen:
            project.findings.append(Finding(
                "duplicate", "warn", name, f"{scope} sets {name} twice (lines {seen[name]} and {line}); the last one wins",
                [f"{rel}:{seen[name]}", f"{rel}:{line}"]))
        seen[name] = line
        if "value" in item:
            value = item["value"]
            if value is not None and not isinstance(value, str):
                project.findings.append(Finding(
                    "k8s-unquoted", "error", name,
                    f"{scope}: env value {value!r} is {_yaml_type(value)}, not a string; the API server rejects it",
                    [f"{rel}:{line}"], f'Quote it: value: "{_text(value)}"'))
            text = _text(value) if value is not None else ""
            for match in KUBE_REF.finditer(text):
                ref = match.group(1)
                if ref not in defined_so_far:
                    project.findings.append(Finding(
                        "k8s-forward-ref", "error", name,
                        f"{scope}: {name} uses $({ref}) but {ref} is not defined earlier in env, so it stays the literal text "
                        f"'$({ref})' and the pod starts anyway",
                        [f"{rel}:{line}"], f"Define {ref} before {name}, or escape with $$({ref})."))
            templated = HELM_TOKEN in text
            project.refs.append(Ref(name, "k8s", "define", rel, line, "ref" if templated else "literal", None if templated else text, scope))
        elif isinstance(item.get("valueFrom"), dict):
            _value_from(project, rel, name, item["valueFrom"], scope, line)
        defined_so_far.add(name)
    for entry in container.get("envFrom") or []:
        if not isinstance(entry, dict):
            continue
        prefix = str(entry.get("prefix", ""))
        for ref_key, kind_name in (("configMapRef", "ConfigMap"), ("secretRef", "Secret")):
            ref = entry.get(ref_key)
            if isinstance(ref, dict) and "name" in ref and HELM_TOKEN in str(ref["name"]):
                project.k8s_opaque = True
            elif isinstance(ref, dict) and "name" in ref:
                project.refs.append(Ref(prefix, "k8s", "define", rel, yamlx.line_of(container, "envFrom"), "envfrom",
                                        scope=scope, extra={"object": (kind_name, str(ref["name"])), "prefix": prefix,
                                                            "optional": bool(ref.get("optional"))}))


def _value_from(project: Project, rel: str, name: str, source: dict, scope: str, line: int) -> None:
    for key, kind_name, kind in (("configMapKeyRef", "ConfigMap", "configmap"), ("secretKeyRef", "Secret", "secret")):
        ref = source.get(key)
        if isinstance(ref, dict) and HELM_TOKEN in str(ref.get("name", "")) + str(ref.get("key", "")):
            project.refs.append(Ref(name, "k8s", "define", rel, line, "field", None, scope))
            return
        if isinstance(ref, dict):
            project.refs.append(Ref(name, "k8s", "define", rel, line, kind, None, scope,
                                    extra={"object": (kind_name, str(ref.get("name", ""))), "key": str(ref.get("key", "")),
                                           "optional": bool(ref.get("optional"))}))
            return
    project.refs.append(Ref(name, "k8s", "define", rel, line, "field", None, scope))
