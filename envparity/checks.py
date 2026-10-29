from __future__ import annotations

import json
import re
from collections import defaultdict
from urllib.parse import urlsplit

from .config import Config
from .model import Finding, Project, Ref, is_placeholder

SECRET_NAME = re.compile(r"(SECRET|PASSWORD|PASSWD|TOKEN|API_?KEY|PRIVATE_KEY|ACCESS_KEY|CREDENTIAL)", re.I)
SECRET_NOT = re.compile(r"(_FILE|_PATH|_NAME|_ENABLED|_LENGTH|_TTL|_URL|_URI|_HEADER|_TYPE)$", re.I)
BOOL_NAME = re.compile(r"(_ENABLED|_DISABLED|_DEBUG|^DEBUG$|^(ENABLE|DISABLE|USE|IS|HAS|ALLOW)_)", re.I)
NUM_NAME = re.compile(r"(_TIMEOUT|_TTL|_SECONDS|_MS|_MINUTES|_RETRIES|_LIMIT|_COUNT|_SIZE|_WORKERS|^MAX_|_MAX$|^MIN_|_MIN$)", re.I)
URL_NAME = re.compile(r"(_URL|_URI|_DSN|_ENDPOINT)$", re.I)
BOOL_WORDS = {"true", "false", "1", "0", "yes", "no", "on", "off"}
TRUTHY = {"true", "1", "yes", "on"}
DEPLOY = ("compose", "k8s")
LAYER_LABEL = {"template": ".env.example", "compose": "docker-compose", "ci": "GitHub Actions", "k8s": "Kubernetes", "code": "code"}


def run(project: Project, config: Config, code_scanned: bool) -> list[Finding]:
    findings = list(project.findings)
    opaque_k8s = _expand_k8s(project, findings)
    refs = [r for r in project.refs if not config.ignored(r.name)]
    findings = [f for f in findings if not f.name or not config.ignored(f.name)]
    by_name: dict[str, list[Ref]] = defaultdict(list)
    for ref in refs:
        by_name[ref.name].append(ref)
    context = _Context(project, config, by_name, opaque_k8s, code_scanned)
    for check in (_missing, _undeclared, _unused, _layer_gaps, _inconsistent, _types, _typos, _secrets):
        findings.extend(check(context))
    return _apply_rules(findings, config)


class _Context:
    def __init__(self, project, config, by_name, opaque_k8s, code_scanned):
        self.project, self.config, self.by_name = project, config, by_name
        self.opaque_k8s, self.code_scanned = opaque_k8s, code_scanned
        self.template_names = {n for n, rs in by_name.items() if any(r.layer == "template" for r in rs)}
        self.compose_covers_template = any(f.rule == "env-file-missing" for f in project.findings)
        self.has = lambda layer: bool(project.files.get(layer))

    def defines(self, name: str, layer: str | None = None) -> list[Ref]:
        return [r for r in self.by_name.get(name, []) if r.role == "define" and (layer is None or r.layer == layer)]

    def external(self, name: str) -> bool:
        return self.config.is_provided(name)


def _expand_k8s(project: Project, findings: list[Finding]) -> bool:
    opaque = False
    expanded: list[Ref] = []
    reported: set[tuple[str, str]] = set()
    for ref in project.refs:
        if ref.layer != "k8s":
            expanded.append(ref)
            continue
        target = ref.extra.get("object")
        if not target:
            expanded.append(ref)
            continue
        obj = project.objects.get(target)
        kind_name, obj_name = target
        if obj is None and target not in reported:
            reported.add(target)
            findings.append(Finding(
                "external-ref", "info", "", f"{ref.scope} reads {kind_name} '{obj_name}', which is not in the repo, so its keys cannot be checked",
                [ref.where], "Fine if it is created by another pipeline (sealed-secrets, external-secrets, terraform)."))
        if ref.kind == "envfrom":
            if obj is None:
                opaque = True
                continue
            for key, (_, value) in obj.keys.items():
                text = value if isinstance(value, str) and kind_name == "ConfigMap" else None
                expanded.append(Ref(ref.extra["prefix"] + key, "k8s", "define", ref.file, ref.line,
                                    "configmap" if kind_name == "ConfigMap" else "secret", text, ref.scope))
            continue
        key = ref.extra["key"]
        if obj is not None:
            if key not in obj.keys:
                if not ref.extra.get("optional"):
                    findings.append(Finding(
                        "k8s-missing-key", "error", ref.name,
                        f"{ref.scope}: {ref.name} reads key '{key}' of {kind_name} '{obj_name}' but {obj.file} does not define it; "
                        "the pod will fail with CreateContainerConfigError",
                        [ref.where], f"Add '{key}' to {obj_name} or fix the key name."))
            elif kind_name == "ConfigMap" and isinstance(obj.keys[key][1], str):
                ref.value = obj.keys[key][1]
        expanded.append(ref)
    project.refs = expanded
    return opaque


def _missing(ctx: _Context):
    for name, refs in sorted(ctx.by_name.items()):
        if ctx.external(name):
            continue
        for ref in refs:
            if ref.role != "use" or ref.kind != "interp" or not ref.required or ref.layer == "code":
                continue
            if ref.layer == "compose" and (name in ctx.template_names or ctx.defines(name, "template")):
                continue
            if ref.layer == "ci" and ctx.defines(name, "ci"):
                continue
            if ref.layer == "compose":
                what = "passed through from the host" if ref.scope == "passthrough" else "interpolated"
                msg = (f"{name} is {what} in docker-compose with no default but is not documented in .env.example; "
                       "a fresh checkout starts with an empty value")
                hint = f"Add {name}= to .env.example, or give it a default: ${{{name}:-value}}, or require it: ${{{name}:?message}}."
            else:
                msg = f"${{{{ env.{name} }}}} is read but no env: block in this workflow sets it"
                hint = f"Set {name} under env: at workflow, job or step level."
            yield Finding("missing", "error", name, msg, [ref.where], hint)
            break


def _undeclared(ctx: _Context):
    if not ctx.project.files.get("template"):
        if any(ctx.project.files.get(layer) for layer in ("compose", "k8s", "ci")):
            yield Finding("no-template", "info", "", "no .env.example (or .env.sample/.env.template) found; "
                          "undeclared-variable checks are skipped", [], "Create one so new developers know what to set.")
        return
    for name, refs in sorted(ctx.by_name.items()):
        if name in ctx.template_names or ctx.external(name):
            continue
        defs = [r for r in refs if r.role == "define" and r.kind not in ("field",)]
        if not defs:
            continue
        layers = sorted({r.layer for r in defs})
        severity = "info" if layers == ["ci"] else "warn"
        yield Finding("undeclared", severity, name,
                      f"{name} is set in {_join(layers)} but is not documented in .env.example",
                      _locs(defs), f"Add {name}= to .env.example (or list it in ignore in .envparity.toml if it is not app configuration).")
    if ctx.code_scanned:
        for name, refs in sorted(ctx.by_name.items()):
            if name in ctx.template_names or ctx.external(name) or ctx.defines(name):
                continue
            uses = [r for r in refs if r.layer == "code"]
            if uses and not any(r.layer == "compose" and r.role == "use" for r in refs):
                yield Finding("code-undeclared", "warn", name, f"{name} is read by the code but is in no config file at all",
                              _locs(uses), f"Add {name}= to .env.example and to the deploy manifests that need it.")


def _unused(ctx: _Context):
    if not ctx.code_scanned or not ctx.project.files.get("code"):
        return
    template_text = " ".join(r.value or "" for r in ctx.project.refs if r.layer == "template")
    for name in sorted(ctx.template_names):
        refs = ctx.by_name[name]
        if any(r.layer != "template" for r in refs) or re.search(rf"\$\{{?{re.escape(name)}\b", template_text):
            continue
        yield Finding("unused", "warn", name, f"{name} is in .env.example but no code, compose file, workflow or manifest uses it",
                      _locs([r for r in refs if r.layer == "template"]), "Remove it, or the next person will keep configuring something dead.")


def _layer_gaps(ctx: _Context):
    for name, refs in sorted(ctx.by_name.items()):
        if ctx.external(name):
            continue
        present = {r.layer for r in refs if r.role == "define" and r.layer in DEPLOY}
        if ctx.compose_covers_template and name in ctx.template_names:
            present.add("compose")
        if ctx.opaque_k8s:
            present.add("k8s")
        if present and present != set(DEPLOY):
            for layer in DEPLOY:
                if layer not in present and ctx.has(layer):
                    other = next(iter(present))
                    yield Finding("layer-gap", "warn", name,
                                  f"{name} is set in {LAYER_LABEL[other]} but not in {LAYER_LABEL[layer]}",
                                  _locs(ctx.defines(name, other)),
                                  f"Add {name} to {LAYER_LABEL[layer]} or the app will see it unset there.")
        elif not present and name in ctx.template_names and ctx.code_scanned:
            used = any(r.layer == "code" for r in refs)
            missing = [LAYER_LABEL[l] for l in DEPLOY if ctx.has(l) and not (l == "compose" and ctx.compose_covers_template)
                       and not (l == "k8s" and ctx.opaque_k8s)]
            if used and missing:
                yield Finding("template-gap", "info", name,
                              f"{name} is read by the code and in .env.example but is not set in {_join(missing)}",
                              _locs(ctx.defines(name, "template")), "Intentional if it has a code default; otherwise deploys run without it.")


def _literal(ref: Ref) -> str | None:
    if ref.role != "define" or ref.value is None or ref.kind not in ("literal", "configmap"):
        return None
    return None if is_placeholder(ref.value) else ref.value.strip()


def _normalise(value: str) -> tuple:
    if value.lower() in BOOL_WORDS and value.lower() not in ("1", "0"):
        return ("bool", value.lower() in TRUTHY)
    if "://" in value:
        parts = urlsplit(value)
        try:
            port = parts.port
        except ValueError:
            port = None
        return ("url", parts.scheme, port, parts.path.rstrip("/"))
    return ("str", value)


def _inconsistent(ctx: _Context):
    for name, refs in sorted(ctx.by_name.items()):
        if ctx.external(name):
            continue
        values: dict[str, list[tuple[tuple, Ref]]] = defaultdict(list)
        for ref in refs:
            literal = _literal(ref)
            if literal is not None and ref.layer != "code":
                values[ref.layer].append((_normalise(literal), ref))
        layers = sorted(values)
        worst, shown = None, None
        for i, a in enumerate(layers):
            for b in layers[i + 1:]:
                if not {v for v, _ in values[a]} & {v for v, _ in values[b]}:
                    severity = "info" if "ci" in (a, b) or ("template" in (a, b) and _is_url(values[a][0][0])) else "warn"
                    if worst != "warn":
                        worst, shown = severity, (a, b)
        if shown:
            a, b = shown
            ra, rb = values[a][0][1], values[b][0][1]
            yield Finding("inconsistent", worst, name,
                          f"{name} is {ra.value!r} in {LAYER_LABEL[a]} but {rb.value!r} in {LAYER_LABEL[b]}",
                          [ra.where, rb.where], "Pick one value, or make one layer read the other instead of repeating it.")


def _is_url(norm: tuple) -> bool:
    return norm[0] == "url"


def _kind_of(name: str, value: str) -> str:
    lowered = value.strip().lower()
    if BOOL_NAME.search(name) and lowered in BOOL_WORDS:
        return "bool"
    if re.fullmatch(r"[+-]?\d+(\.\d+)?", lowered):
        return "number"
    if lowered in ("true", "false"):
        return "bool"
    if re.match(r"^[a-z][a-z0-9+.-]*://", lowered):
        return "url"
    if lowered[:1] in "{[":
        try:
            json.loads(value)
            return "json"
        except ValueError:
            pass
    return "string"


def _types(ctx: _Context):
    for name, refs in sorted(ctx.by_name.items()):
        if ctx.external(name):
            continue
        literals = [(r, _literal(r)) for r in refs if r.layer != "code"]
        literals = [(r, v) for r, v in literals if v is not None]
        kinds: dict[str, Ref] = {}
        for ref, value in literals:
            kinds.setdefault(_kind_of(name, value), ref)
        if len(kinds) > 1:
            parts = [f"{k} in {LAYER_LABEL[r.layer]} ({r.value!r})" for k, r in kinds.items()]
            yield Finding("type-mismatch", "warn", name, f"{name} looks like different types: " + ", ".join(parts),
                          [r.where for r in kinds.values()], "One of them is probably a typo or a missing scheme/quote.")
        spellings = {v.lower() for _, v in literals if BOOL_NAME.search(name) and v.lower() in BOOL_WORDS}
        if len({s in TRUTHY for s in spellings}) == 1 and len(spellings) > 1:
            yield Finding("bool-spelling", "info", name, f"{name} is spelled {sorted(spellings)} in different places; "
                          "many apps only accept one of them", _locs([r for r, _ in literals]), "Use one spelling everywhere.")
        for ref, value in literals:
            problem = _name_problem(name, value)
            if problem:
                yield Finding("bad-type", "warn", name, f"{name}={value!r} in {LAYER_LABEL[ref.layer]}: {problem}", [ref.where])
                break


def _name_problem(name: str, value: str) -> str | None:
    value = value.strip()
    if "${" in value or "$(" in value or "{{" in value:
        return None
    if name.upper().endswith("_PORT") or name.upper() == "PORT":
        if not (value.isdigit() and 0 < int(value) < 65536):
            return "a port should be a number from 1 to 65535"
    elif URL_NAME.search(name):
        if "://" not in value and not value.startswith(("/", "unix:")):
            return "a URL should include a scheme such as https://"
    elif BOOL_NAME.search(name):
        if value.lower() not in BOOL_WORDS:
            return "a flag should be true/false (or 1/0)"
    elif NUM_NAME.search(name):
        if not re.fullmatch(r"\d+(\.\d+)?\s*(ms|s|m|h|d|k|kb|mb|gb|m|g)?", value, re.I):
            return "expected a number (optionally with a unit)"
    return None


def _distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _typos(ctx: _Context):
    layers = {n: {r.layer for r in rs if r.layer != "code" or r.role == "use"} for n, rs in ctx.by_name.items()
              if not ctx.external(n)}
    names = sorted(layers)
    reported: set[str] = set()
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            if layers[a] & layers[b] or len(a) < 5 or len(b) < 5:
                continue
            limit = 1 if min(len(a), len(b)) < 9 else 2
            close = a.lower() == b.lower() or a.replace("_", "") == b.replace("_", "") or (
                abs(len(a) - len(b)) <= limit and _distance(a, b) <= limit and not _digits_only_diff(a, b))
            if not close:
                continue
            odd, norm = (a, b) if _weight(ctx, a) <= _weight(ctx, b) else (b, a)
            if odd in reported:
                continue
            reported.add(odd)
            yield Finding("possible-typo", "warn", odd, f"{odd} (only in {_join(sorted(layers[odd]))}) looks like a typo of {norm} "
                          f"(in {_join(sorted(layers[norm]))})", _locs(ctx.by_name[odd]) + _locs(ctx.by_name[norm])[:1],
                          f"Did you mean {norm}?")


def _weight(ctx: _Context, name: str) -> int:
    return len(ctx.by_name[name]) + (100 if name in ctx.template_names else 0)


def _digits_only_diff(a: str, b: str) -> bool:
    return len(a) == len(b) and all(x == y or (x.isdigit() and y.isdigit()) for x, y in zip(a, b))


def _secrets(ctx: _Context):
    severity = {"k8s": "warn", "ci": "warn", "compose": "info"}
    for name, refs in sorted(ctx.by_name.items()):
        if not SECRET_NAME.search(name) or SECRET_NOT.search(name):
            continue
        for ref in refs:
            literal = _literal(ref)
            if ref.layer in severity and literal is not None and ref.kind == "literal":
                hint = {"k8s": "Use secretKeyRef (or a Secret from your secret manager).",
                        "ci": "Use ${{ secrets." + name + " }}.", "compose": "Read it from .env: ${" + name + "}."}[ref.layer]
                yield Finding("secret-in-plain", severity[ref.layer], name,
                              f"{name} has a literal value in {LAYER_LABEL[ref.layer]}", [ref.where], hint)
                break


def _apply_rules(findings: list[Finding], config: Config) -> list[Finding]:
    out = []
    for finding in findings:
        level = config.rules.get(finding.rule, finding.severity)
        if level == "off":
            continue
        finding.severity = level
        out.append(finding)
    order = {"error": 0, "warn": 1, "info": 2}
    out.sort(key=lambda f: (order[f.severity], f.rule, f.name, f.locations))
    return out


def _locs(refs: list[Ref]) -> list[str]:
    seen = []
    for ref in refs:
        if ref.where not in seen:
            seen.append(ref.where)
    return seen[:4]


def _join(items: list[str]) -> str:
    labels = [LAYER_LABEL.get(i, i) for i in items]
    return " and ".join(labels) if len(labels) <= 2 else ", ".join(labels[:-1]) + " and " + labels[-1]
