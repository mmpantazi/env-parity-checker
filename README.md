# envparity

Finds missing, unused, mistyped and inconsistent environment variables across the places a small team keeps them:
`.env.example`, `docker-compose`, GitHub Actions workflows, Kubernetes manifests, and the code that reads them.
It runs offline, reads files only and exits non-zero when something would break a deploy.

```
$ envparity examples/shop
ERROR k8s-unquoted   Deployment/web/web: env value 8080 is a YAML number, not a string; the API server rejects it
      at k8s/deploy.yaml:12
      fix: Quote it: value: "8080"
ERROR missing        STRIPE_KEY is interpolated in docker-compose with no default but is not documented in .env.example; ...
WARN  possible-typo  DATABSE_URL (only in Kubernetes) looks like a typo of DATABASE_URL (...)
...
4 error(s), 18 warning(s), 4 note(s)
```

Existing tools compare one pair of files (`.env` vs `.env.example`) or explain how one variable resolves. envparity checks the whole chain at once and knows the traps of each layer.

## Install and run

Needs Python 3.11+.

```bash
pip install .            # from a checkout; installs PyYAML
envparity [PATH]         # PATH defaults to .
python -m envparity PATH # same, without installing the script
```

Options: `--format text|json|github`, `--fail-on error|warn|info|never` (default `error`), `--min-severity`,
`--no-code`, `--matrix` (which layer defines which variable), `--config FILE`, `NO_COLOR` is respected.
Exit code: 0 clean, 1 findings at or above `--fail-on`, 2 usage or config error.

Adopting it on an existing repo:

```bash
envparity --suggest >> .env.example                  # NAME=value lines for variables used but undocumented
envparity --write-baseline .envparity-baseline.json  # accept today's findings...
envparity --baseline .envparity-baseline.json        # ...and fail only on new ones
```

envparity is not on PyPI yet; install it from a checkout. In GitHub Actions, `--format github` turns findings into inline annotations:

```yaml
- run: pip install ./tools/envparity && envparity --format github   # path to a checkout or vendored copy
```

## What it checks

| Rule | Level | Meaning |
|---|---|---|
| `missing` | error | compose `${X}` with no default and not in `.env.example` (warn for pass-through `- X` and for a `${{ env.X }}` that no `env:` block in the workflow sets, since an action may set it) |
| `k8s-unquoted` | error | `value: 8080`, `value: yes` or a ConfigMap number: the API server rejects non-strings |
| `k8s-missing-key` | error | `configMapKeyRef`/`secretKeyRef` to a key that the ConfigMap/Secret in the repo does not define |
| `k8s-forward-ref` | error | `$(B)` used before `B` is defined in `env`; Kubernetes leaves the text as is and starts the pod |
| `undeclared` | warn | set in compose/k8s/CI but not documented in `.env.example` |
| `code-undeclared` | warn | read by code but in no config file |
| `unused` | warn | in `.env.example` but nothing reads it |
| `layer-gap` | warn | a documented variable is set in compose but not Kubernetes (or the reverse) |
| `inconsistent` | warn | different values in different layers (URL hosts are ignored on purpose) |
| `type-mismatch`, `bad-type` | warn | `*_PORT` not a port, `*_URL` without scheme, flags that are not booleans, number vs string across layers |
| `possible-typo` | warn | `DATABSE_URL` next to `DATABASE_URL` in a different layer |
| `secret-in-plain` | warn | `*_PASSWORD`/`*_TOKEN`/... with a literal value in k8s or CI |
| `committed-env` | warn | a `.env` tracked by git (it is then read as the template) |
| `duplicate`, `bool-spelling`, `template-gap`, `external-ref`, `env-file-missing`, `no-template` | info/warn | smaller notes |

Only one finding is shown per variable when rules overlap (`bad-type` beats `type-mismatch` beats `inconsistent`).

What it understands: `.env.example`/`.env.sample`/`.env.template`/`.env.dist`; compose `environment` (map or list), `env_file`,
`${VAR}`, `${VAR:-d}`, `${VAR:?e}` and `$$`; workflows' `env:` at every level plus `env.`, `vars.` and `secrets.` contexts;
Pod/Deployment/StatefulSet/DaemonSet/Job/CronJob `env`, `envFrom`, `valueFrom`, init containers, ConfigMap/Secret objects and
Kustomize generators; env reads in JS/TS, Python, Go, Ruby, Rust, PHP, Java, C#.
Helm templates are read best-effort (directive lines dropped, `{{ }}` replaced, values unknown); for exact results render with `helm template` and scan the output. Charts that build `env` with `include`/`toYaml` make Kubernetes gap checks stand down.
Secrets that are not in the repo are reported as a note, never as an error.

Pre-commit (local hook):

```yaml
- repo: local
  hooks:
    - id: envparity
      name: envparity
      entry: envparity --fail-on error
      language: system
      pass_filenames: false
```

## Configuration

Optional `.envparity.toml` in the project root:

```toml
ignore   = ["NODE_*", "CI_*"]      # variable names (globs) to skip everywhere
provided = ["HTTP_PROXY"]          # set by the host/runtime; never reported as missing
exclude  = ["examples/**"]         # paths to skip
[rules]
layer-gap = "off"                  # error | warn | info | off
```

## Limits

Static analysis: it cannot see values set in a cloud console, Helm values, or secrets created elsewhere. Code scanning is
regex based and misses computed names (`process.env[name]`). `unused` is only reported when source files were scanned.

## Develop

```bash
pip install -e '.[dev]' && pytest
```
