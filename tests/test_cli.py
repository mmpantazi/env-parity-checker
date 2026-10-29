import json
import shutil
from pathlib import Path

from envparity.cli import main

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "shop"


def test_example_fails_with_errors(capsys):
    assert main([str(EXAMPLE)]) == 1
    out = capsys.readouterr().out
    assert "k8s-unquoted" in out and "DATABSE_URL" in out


def test_json_output_and_fail_on_never(capsys):
    assert main([str(EXAMPLE), "--format", "json", "--fail-on", "never"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["summary"]["error"] == 5
    assert {"rule", "severity", "name", "message", "locations", "hint"} <= set(data["findings"][0])


def test_github_format(capsys):
    main([str(EXAMPLE), "--format", "github", "--min-severity", "error"])
    lines = capsys.readouterr().out.splitlines()
    assert lines and all(l.startswith("::error file=") for l in lines)


def test_min_severity_hides_notes(capsys):
    main([str(EXAMPLE), "--min-severity", "warn"])
    assert "INFO" not in capsys.readouterr().out


def test_clean_project_exits_zero(tmp_path, capsys):
    (tmp_path / ".env.example").write_text("PORT=3000\n")
    (tmp_path / "docker-compose.yml").write_text("services:\n  a:\n    image: x\n    environment:\n      PORT: '3000'\n")
    assert main([str(tmp_path), "--fail-on", "warn", "--no-code"]) == 0


def test_bad_path_and_bad_config(tmp_path, capsys):
    assert main([str(tmp_path / "nope")]) == 2
    (tmp_path / ".envparity.toml").write_text('[rules]\nmissing = "loud"\n')
    try:
        main([str(tmp_path)])
        raised = False
    except SystemExit as exc:
        raised = "level must be" in str(exc)
    assert raised


def test_matrix(tmp_path, capsys):
    (tmp_path / ".env.example").write_text("PORT=3000\n")
    main([str(tmp_path), "--matrix"])
    assert "VARIABLE" in capsys.readouterr().out
