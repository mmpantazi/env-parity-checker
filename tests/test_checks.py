from conftest import rules

TEMPLATE = {".env.example": "PORT=3000\nDATABASE_URL=postgres://u:p@localhost:5432/db\n"}


def test_compose_interpolation_missing_from_template(project):
    f = project({**TEMPLATE, "docker-compose.yml": "services:\n  a:\n    image: x\n    ports: ['${HTTP_PORT}:80']\n"})
    assert ("missing", "HTTP_PORT") in rules(f)


def test_compose_default_and_required_forms_are_not_missing(project):
    f = project({**TEMPLATE, "docker-compose.yml": "services:\n  a:\n    image: ${IMG:-nginx}\n    command: ${CMD:?set CMD} ${X-y}\n"})
    assert ("missing", "IMG") not in rules(f)
    assert ("missing", "X") not in rules(f)
    assert ("missing", "CMD") in rules(f)


def test_compose_escaped_dollar_is_ignored(project):
    f = project({**TEMPLATE, "docker-compose.yml": "services:\n  a:\n    image: x\n    command: echo $$HOME $${FOO}\n"})
    assert ("missing", "FOO") not in rules(f)


def test_compose_passthrough_needs_template(project):
    f = project({**TEMPLATE, "docker-compose.yml": "services:\n  a:\n    image: x\n    environment:\n      - TOKEN\n"})
    assert ("missing", "TOKEN") in rules(f)


def test_undeclared_in_compose_environment(project):
    f = project({**TEMPLATE, "docker-compose.yml": "services:\n  a:\n    image: x\n    environment:\n      NEW_FLAG: 'on'\n"})
    assert ("undeclared", "NEW_FLAG") in rules(f)


def test_no_template_gives_single_note(project):
    f = project({"docker-compose.yml": "services:\n  a:\n    image: x\n    environment:\n      A: '1'\n"})
    assert rules(f, "no-template") == [("no-template", "")]
    assert not rules(f, "undeclared")


def test_unused_needs_code_scan(project):
    files = {**TEMPLATE, "app.py": "import os\nos.environ['PORT']\n"}
    assert ("unused", "DATABASE_URL") in rules(project(files))


def test_unused_skipped_without_code(project):
    assert not rules(project(TEMPLATE, code=False), "unused")


def test_template_interpolation_counts_as_use(project):
    files = {".env.example": "HOST=db\nURL=postgres://${HOST}/x\n", "app.py": "os.getenv('URL')\n"}
    assert ("unused", "HOST") not in rules(project(files))


def test_code_patterns(project):
    code = "process.env.A_ONE\nprocess.env['B_TWO']\nimport.meta.env.VITE_C\nos.environ.get('D_FOUR')\nos.Getenv(\"E_FIVE\")\nconst {F_SIX, G_SEVEN: g} = process.env\n"
    names = {n for r, n in rules(project({**TEMPLATE, "x.js": code}), "code-undeclared")}
    assert names == {"A_ONE", "B_TWO", "VITE_C", "D_FOUR", "E_FIVE", "F_SIX", "G_SEVEN"}


def test_skips_node_modules(project):
    f = project({**TEMPLATE, "node_modules/x/index.js": "process.env.HIDDEN_ONE\n"})
    assert ("code-undeclared", "HIDDEN_ONE") not in rules(f)


def test_inconsistent_values_and_url_host_ignored(project):
    files = {
        ".env.example": "PORT=3000\nDB=postgres://localhost:5432/app\nMODE=dev\n",
        "docker-compose.yml": "services:\n  a:\n    image: x\n    environment:\n      PORT: '3001'\n      DB: postgres://db:5432/app\n      MODE: dev\n",
    }
    got = rules(project(files), "inconsistent")
    assert got == [("inconsistent", "PORT")]


def test_placeholder_values_do_not_conflict(project):
    files = {".env.example": "API_KEY=changeme\n", "docker-compose.yml": "services:\n  a:\n    image: x\n    environment:\n      API_KEY: abc123xyz\n"}
    assert not rules(project(files), "inconsistent")


def test_ci_difference_is_only_a_note(project):
    files = {".env.example": "PORT=3000\n", ".github/workflows/t.yml": "on: push\nenv:\n  PORT: 4000\njobs: {}\n"}
    f = [x for x in project(files) if x.rule == "inconsistent"]
    assert f and f[0].severity == "info"


def test_ci_env_context_missing(project):
    wf = "on: push\njobs:\n  a:\n    runs-on: x\n    steps:\n      - run: echo ${{ env.NOPE }} ${{ env.OK }} ${{ secrets.S }}\n        env:\n          OK: 1\n"
    f = project({**TEMPLATE, ".github/workflows/t.yml": wf})
    assert ("missing", "NOPE") in rules(f)
    assert [x.severity for x in f if x.rule == "missing"] == ["warn"]
    assert ("missing", "OK") not in rules(f)
    assert ("missing", "S") not in rules(f)


def test_ci_github_env_file_suppresses_missing(project):
    wf = "on: push\njobs:\n  a:\n    runs-on: x\n    steps:\n      - run: echo LATE=1 >> $GITHUB_ENV\n      - run: echo ${{ env.LATE }}\n"
    assert ("missing", "LATE") not in rules(project({**TEMPLATE, ".github/workflows/t.yml": wf}))


def test_type_and_name_based_checks(project):
    files = {".env.example": "DB_PORT=abc\nAPI_URL=localhost:3000\nDEBUG=maybe\nCACHE_TTL=forever\nOK_PORT=8080\n"}
    got = {n for _, n in rules(project(files), "bad-type")}
    assert got == {"DB_PORT", "API_URL", "DEBUG", "CACHE_TTL"}


def test_type_mismatch_between_layers(project):
    files = {".env.example": "RETRIES=3\n", "docker-compose.yml": "services:\n  a:\n    image: x\n    environment:\n      RETRIES: many\n"}
    assert ("type-mismatch", "RETRIES") in rules(project(files))


def test_bool_spelling(project):
    files = {".env.example": "FEATURE_ENABLED=true\n", "docker-compose.yml": "services:\n  a:\n    image: x\n    environment:\n      FEATURE_ENABLED: '1'\n"}
    got = rules(project(files))
    assert ("bool-spelling", "FEATURE_ENABLED") in got
    assert ("inconsistent", "FEATURE_ENABLED") not in got


def test_typo_detection(project):
    files = {".env.example": "DATABASE_URL=postgres://x/y\n", "docker-compose.yml": "services:\n  a:\n    image: x\n    environment:\n      DATABSE_URL: postgres://x/y\n"}
    got = rules(project(files))
    assert ("possible-typo", "DATABSE_URL") in got
    assert ("undeclared", "DATABSE_URL") not in got


def test_typo_ignores_numbered_names(project):
    files = {".env.example": "WORKER_1_URL=http://a\nWORKER_2_URL=http://b\n", "x.py": "os.getenv('WORKER_1_URL')\n"}
    assert not rules(project(files), "possible-typo")


def test_secret_in_plain(project):
    files = {".env.example": "DB_PASSWORD=\n", "k8s/d.yaml": K8S_PLAIN_SECRET}
    got = project(files)
    assert ("secret-in-plain", "DB_PASSWORD") in rules(got)


K8S_PLAIN_SECRET = """\
apiVersion: apps/v1
kind: Deployment
metadata: {name: a}
spec:
  template:
    spec:
      containers:
        - name: a
          env:
            - {name: DB_PASSWORD, value: hunter2}
"""


def test_duplicate_in_template_and_k8s(project):
    k8s = K8S_PLAIN_SECRET + "            - {name: DB_PASSWORD, value: other}\n"
    f = project({".env.example": "A=1\nA=2\n", "k8s/d.yaml": k8s})
    assert {n for _, n in rules(f, "duplicate")} == {"A", "DB_PASSWORD"}


def test_config_ignore_provided_and_rules_off(project):
    files = {**TEMPLATE, "docker-compose.yml": "services:\n  a:\n    image: ${REG}/x\n    environment:\n      NOISE_ONE: '1'\n      LEGACY: '2'\n"}
    cfg = 'ignore = ["NOISE_*"]\nprovided = ["REG"]\n[rules]\nundeclared = "off"\n'
    got = rules(project(files, cfg))
    assert not any(n in ("REG", "NOISE_ONE", "LEGACY") for _, n in got)


def test_invalid_yaml_is_a_finding_not_a_crash(project):
    f = project({**TEMPLATE, "docker-compose.yml": "services: [\n  a: {\n"})
    assert rules(f, "parse-error")


def test_empty_project(project):
    assert project({"README.md": "hi"}) == []


def test_non_utf8_files_do_not_crash(project):
    root = project.root
    (root / ".env.example").write_bytes(b"CAF\xc9=1\nOK=2\n")
    project({"x.py": "pass\n"})


def test_substitution_placeholders_are_not_literal_secrets(project):
    k8s = K8S_PLAIN_SECRET.replace("hunter2", "DB_PASSWORD_VAL")
    assert not rules(project({"k8s/d.yaml": k8s}), "secret-in-plain")


def test_ci_env_names_may_contain_hyphens_and_ignore_case(project):
    wf = "on: push\njobs:\n  a:\n    runs-on: x\n    steps:\n      - run: echo ${{ env.cache-name }} ${{ env.Mixed }}\n        env:\n          cache-name: x\n          MIXED: y\n"
    assert not rules(project({**TEMPLATE, ".github/workflows/t.yml": wf}), "missing")


def test_mention_in_config_file_counts_as_use(project):
    files = {".env.example": "PASSED_THROUGH=1\nDEAD_ONE=1\n", "turbo.json": '{"env": ["PASSED_THROUGH"]}', "app.py": "pass\n"}
    assert [n for _, n in rules(project(files), "unused")] == ["DEAD_ONE"]


def test_bom_and_crlf_in_template(project):
    root = project.root
    root.mkdir(exist_ok=True)
    (root / ".env.example").write_bytes(b"\xef\xbb\xbfPORT=1\r\nHOST=a\r\n")
    (root / "docker-compose.yml").write_bytes(b"services:\r\n  a:\r\n    image: x\r\n    environment:\r\n      - PORT=${PORT}\r\n")
    assert not rules(project({}), "missing")


def test_missing_replaces_undeclared_for_same_variable(project):
    f = project({**TEMPLATE, "docker-compose.yml": "services:\n  a:\n    image: x\n    environment:\n      NEW: ${NEW}\n"})
    assert ("missing", "NEW") in rules(f) and ("undeclared", "NEW") not in rules(f)


def test_multiline_quoted_value_does_not_leak_keys(project):
    f = project({".env.example": 'KEY="-----BEGIN\nINNER=oops\n-----END"\nLAST=1\n', "docker-compose.yml": "services:\n  a:\n    image: x\n    environment:\n      LAST: '1'\n      KEY: x\n"})
    assert ("undeclared", "INNER") not in rules(f)
    assert not rules(f, "undeclared")


def test_embedded_interpolation_is_not_compared_as_literal(project):
    files = {".env.example": "HOST=db\nNAME=app_${HOST}\n", "docker-compose.yml": "services:\n  a:\n    image: x\n    environment:\n      NAME: app_db\n      HOST: db\n"}
    assert not rules(project(files), "inconsistent")


def test_yaml_anchors_and_null_values_in_compose(project):
    compose = "x-env: &env\n  A_ONE: '1'\nservices:\n  a:\n    image: x\n    environment:\n      <<: *env\n      B_TWO:\n"
    got = rules(project({".env.example": "A_ONE=1\n", "docker-compose.yml": compose}))
    assert ("undeclared", "B_TWO") not in got or ("missing", "B_TWO") in got
    assert ("missing", "B_TWO") in got and ("undeclared", "A_ONE") not in got


def test_tracked_env_counts_as_template_and_is_flagged(project):
    import subprocess
    root = project.root
    (root / ".env").write_text("DB_HOST=x\n")
    (root / "docker-compose.yml").write_text("services:\n  a:\n    image: ${DB_HOST}\n")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", ".env"], cwd=root, check=True)
    got = rules(project({}))
    assert ("committed-env", "") in got and ("missing", "DB_HOST") not in got


def test_untracked_env_is_ignored(project):
    import subprocess
    root = project.root
    (root / ".env").write_text("DB_HOST=x\n")
    (root / "docker-compose.yml").write_text("services:\n  a:\n    image: ${DB_HOST}\n")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    got = rules(project({}))
    assert ("missing", "DB_HOST") in got and not rules(project({}), "committed-env")


def test_compose_custom_tags_do_not_break_parsing(project):
    compose = "services:\n  a:\n    image: x\n    ports: !reset []\n    environment: !override\n      NEW_VAR: '1'\n"
    f = project({".env.example": "A=1\n", "docker-compose.yml": compose})
    assert not rules(f, "parse-error") and ("undeclared", "NEW_VAR") in rules(f)


def test_compound_durations_are_valid_numbers(project):
    assert not rules(project({".env.example": "HEALTHCHECK_TIMEOUT=1m30s\nCACHE_TTL=500ms\nX_LIMIT=10mb\n"}), "bad-type")


def test_passthrough_without_template_entry_is_only_a_warning(project):
    f = project({**TEMPLATE, "docker-compose.yml": "services:\n  a:\n    image: x\n    environment:\n      - OPTIONAL_HOST\n      - X=${NEEDED}\n"})
    sev = {x.name: x.severity for x in f if x.rule == "missing"}
    assert sev == {"OPTIONAL_HOST": "warn", "NEEDED": "error"}


def test_system_variables_are_not_reported(project):
    assert not rules(project({**TEMPLATE, "x.py": "os.environ['LD_LIBRARY_PATH']\nos.getenv('HTTP_PROXY')\n"}), "code-undeclared")
