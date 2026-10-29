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
    assert data["summary"]["error"] == 4
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
    assert main([str(tmp_path)]) == 2
    assert "level must be" in capsys.readouterr().err
    (tmp_path / ".envparity.toml").write_text('[rules]\nmisssing = "off"\n')
    assert main([str(tmp_path)]) == 2
    assert "unknown rule 'misssing'" in capsys.readouterr().err


def test_matrix(tmp_path, capsys):
    (tmp_path / ".env.example").write_text("PORT=3000\n")
    main([str(tmp_path), "--matrix"])
    assert "VARIABLE" in capsys.readouterr().out


def test_baseline_roundtrip(tmp_path, capsys):
    base = tmp_path / "base.json"
    assert main([str(EXAMPLE), "--write-baseline", str(base)]) == 0
    assert main([str(EXAMPLE), "--baseline", str(base)]) == 0
    assert "hidden by the baseline" in capsys.readouterr().out


def test_suggest_lists_undocumented_variables(capsys):
    main([str(EXAMPLE), "--suggest"])
    lines = capsys.readouterr().out.splitlines()
    assert "STRIPE_KEY=" in lines and "SENTRY_DSN=" in lines
    assert not any(l.startswith("JWT_SECRET") for l in lines)


def test_unreadable_baseline_is_usage_error(tmp_path, capsys):
    assert main([str(EXAMPLE), "--baseline", str(tmp_path / "missing.json")]) == 2


def test_empty_directory_is_not_a_silent_pass(tmp_path, capsys):
    assert main([str(tmp_path)]) == 0
    assert "Nothing to check" in capsys.readouterr().out


def test_repeated_rules_drop_fix_hints_after_a_few(tmp_path, capsys):
    (tmp_path / ".env.example").write_text("A=1\n")
    env = "".join(f"      V{i}: 'x'\n" for i in range(12))
    (tmp_path / "docker-compose.yml").write_text("services:\n  a:\n    image: x\n    environment:\n" + env)
    main([str(tmp_path), "--no-code"])
    out = capsys.readouterr().out
    assert out.count("fix:") == 5 and "fix hints omitted" in out
