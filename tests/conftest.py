import textwrap
from pathlib import Path

import pytest

from envparity import checks, config, scan


@pytest.fixture
def project(tmp_path):
    def build(files: dict[str, str], cfg: str | None = None, code: bool = True):
        for rel, text in files.items():
            path = tmp_path / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(textwrap.dedent(text), encoding="utf-8")
        if cfg is not None:
            (tmp_path / ".envparity.toml").write_text(textwrap.dedent(cfg), encoding="utf-8")
        conf = config.load(tmp_path, None)
        proj = scan.scan_project(tmp_path, conf.exclude, code=code)
        return checks.run(proj, conf, code_scanned=code)

    build.root = tmp_path
    return build


def rules(findings, rule=None):
    return [(f.rule, f.name) for f in findings if rule is None or f.rule == rule]
