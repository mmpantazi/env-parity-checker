from conftest import rules

HEAD = "apiVersion: apps/v1\nkind: Deployment\nmetadata: {name: web}\nspec:\n  template:\n    spec:\n      containers:\n        - name: web\n"


def test_unquoted_number_and_bool_are_errors(project):
    k8s = HEAD + "          env:\n            - {name: PORT, value: 8080}\n            - {name: FLAG, value: yes}\n            - {name: OK, value: '1'}\n"
    got = rules(project({".env.example": "PORT=1\nFLAG=1\nOK=1\n", "k.yaml": k8s}), "k8s-unquoted")
    assert got == [("k8s-unquoted", "FLAG"), ("k8s-unquoted", "PORT")]


def test_configmap_unquoted_value(project):
    cm = "apiVersion: v1\nkind: ConfigMap\nmetadata: {name: c}\ndata:\n  RETRIES: 3\n  NAME: x\n"
    assert rules(project({"cm.yaml": cm}), "k8s-unquoted") == [("k8s-unquoted", "RETRIES")]


def test_forward_reference_and_escape(project):
    k8s = HEAD + "          env:\n            - {name: A, value: '$(B)'}\n            - {name: B, value: x}\n            - {name: C, value: '$(B)'}\n            - {name: D, value: '$$(Z)'}\n"
    got = rules(project({".env.example": "A=1\nB=1\nC=1\nD=1\n", "k.yaml": k8s}), "k8s-forward-ref")
    assert got == [("k8s-forward-ref", "A")]


def test_secret_key_ref_missing_key_in_repo_secret(project):
    k8s = HEAD + "          env:\n            - name: TOKEN\n              valueFrom: {secretKeyRef: {name: s, key: tok}}\n"
    secret = "apiVersion: v1\nkind: Secret\nmetadata: {name: s}\nstringData:\n  token: abc\n"
    got = project({".env.example": "TOKEN=\n", "k.yaml": k8s + "---\n" + secret})
    assert ("k8s-missing-key", "TOKEN") in rules(got)


def test_optional_key_ref_is_fine(project):
    k8s = HEAD + "          env:\n            - name: TOKEN\n              valueFrom: {configMapKeyRef: {name: s, key: tok, optional: true}}\n"
    cm = "apiVersion: v1\nkind: ConfigMap\nmetadata: {name: s}\ndata: {other: x}\n"
    assert not rules(project({".env.example": "TOKEN=\n", "k.yaml": k8s + "---\n" + cm}), "k8s-missing-key")


def test_external_secret_is_info_only(project):
    k8s = HEAD + "          env:\n            - name: TOKEN\n              valueFrom: {secretKeyRef: {name: ext, key: t}}\n"
    f = project({".env.example": "TOKEN=\n", "k.yaml": k8s})
    assert [x.severity for x in f if x.rule == "external-ref"] == ["info"]
    assert not [x for x in f if x.severity == "error"]


def test_envfrom_expands_configmap_keys(project):
    k8s = HEAD + "          envFrom:\n            - configMapRef: {name: cfg}\n            - secretRef: {name: unknown}\n"
    cm = "apiVersion: v1\nkind: ConfigMap\nmetadata: {name: cfg}\ndata: {LOG_LEVEL: info, STRAY: x}\n"
    f = project({".env.example": "LOG_LEVEL=info\n", "k.yaml": k8s + "---\n" + cm, "app.py": "os.getenv('LOG_LEVEL')\n"})
    got = rules(f)
    assert ("undeclared", "STRAY") in got
    assert ("layer-gap", "LOG_LEVEL") not in got and ("template-gap", "LOG_LEVEL") not in got


def test_opaque_envfrom_suppresses_k8s_gaps(project):
    k8s = HEAD + "          envFrom:\n            - secretRef: {name: unknown}\n"
    f = project({".env.example": "A=1\n", "k.yaml": k8s, "docker-compose.yml": "services:\n  s:\n    image: x\n    environment:\n      A: '1'\n"})
    assert not rules(f, "layer-gap")


def test_cronjob_and_init_containers(project):
    k8s = ("apiVersion: batch/v1\nkind: CronJob\nmetadata: {name: j}\nspec:\n  jobTemplate:\n    spec:\n      template:\n        spec:\n"
           "          initContainers:\n            - name: i\n              env: [{name: INIT_ONLY, value: '1'}]\n"
           "          containers:\n            - name: c\n              env: [{name: JOB_ONLY, value: '1'}]\n")
    got = {n for _, n in rules(project({".env.example": "A=1\n", "j.yaml": k8s}), "undeclared")}
    assert got == {"INIT_ONLY", "JOB_ONLY"}


def test_kustomize_generators(project):
    kust = ("apiVersion: kustomize.config.k8s.io/v1beta1\nkind: Kustomization\nconfigMapGenerator:\n  - name: cfg\n    literals: [MODE=prod]\n"
            "    envs: [cfg.env]\n")
    k8s = HEAD + "          env:\n            - name: M\n              valueFrom: {configMapKeyRef: {name: cfg, key: MODE}}\n            - name: X\n              valueFrom: {configMapKeyRef: {name: cfg, key: EXTRA}}\n"
    f = project({".env.example": "M=1\nX=1\n", "kustomization.yaml": kust, "cfg.env": "EXTRA=1\n", "d.yaml": k8s})
    assert not rules(f, "k8s-missing-key")


def test_helm_templates_are_skipped_with_note(project):
    tpl = "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: {{ .Release.Name }}\n"
    f = project({".env.example": "A=1\n", "chart/templates/cm.yaml": tpl})
    assert f == [] or not rules(f, "parse-error")


HELM = """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ include "x.fullname" . }}
spec:
  template:
    spec:
      containers:
        - name: web
          env:
            {{- if .Values.debug }}
            - name: DEBUG
              value: "true"
            {{- else }}
            - name: DEBUG
              value: "false"
            {{- end }}
            - name: PORT
              value: {{ .Values.port | quote }}
            - name: RETRIES
              value: 3
            - name: DB
              valueFrom:
                secretKeyRef:
                  name: {{ .Release.Name }}-db
                  key: url
"""


def test_helm_template_best_effort(project):
    f = project({".env.example": "DEBUG=true\nPORT=1\nRETRIES=3\nDB=x\n", "chart/templates/d.yaml": HELM})
    got = rules(f)
    assert ("k8s-unquoted", "RETRIES") in got
    assert not rules(f, "duplicate") and not rules(f, "external-ref")
    assert not [x for x in f if x.rule == "inconsistent"]


def test_helm_with_include_blocks_makes_k8s_opaque(project):
    tpl = HELM.replace("{{- if .Values.debug }}", "{{- include \"x.env\" . | nindent 12 }}")
    f = project({".env.example": "ONLY_IN_TEMPLATE=1\n", "docker-compose.yml": "services:\n  a:\n    image: x\n    environment:\n      ONLY_IN_TEMPLATE: '1'\n",
                 "chart/templates/d.yaml": tpl})
    assert not rules(f, "layer-gap")
