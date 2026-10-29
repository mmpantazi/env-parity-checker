from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import __version__, checks, config as config_mod, report, scan

FAIL_LEVELS = {"error": ("error",), "warn": ("error", "warn"), "info": ("error", "warn", "info"), "never": ()}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="envparity", description="Check environment variable parity across .env.example, "
                                "docker-compose, GitHub Actions workflows and Kubernetes manifests.")
    p.add_argument("path", nargs="?", default=".", help="project root (default: current directory)")
    p.add_argument("--format", choices=("text", "json", "github"), default="text")
    p.add_argument("--fail-on", choices=tuple(FAIL_LEVELS), default="error", help="lowest severity that gives exit code 1 (default: error)")
    p.add_argument("--min-severity", choices=("error", "warn", "info"), default="info", help="hide findings below this level")
    p.add_argument("--no-code", action="store_true", help="do not scan source code for env reads")
    p.add_argument("--suggest", action="store_true", help="print NAME=value lines to append to .env.example and exit")
    p.add_argument("--baseline", type=Path, help="hide findings already listed in this baseline file")
    p.add_argument("--write-baseline", type=Path, metavar="FILE", help="record current findings as accepted and exit 0")
    p.add_argument("--matrix", action="store_true", help="print which layer defines which variable")
    p.add_argument("--config", type=Path, help="path to .envparity.toml")
    p.add_argument("--version", action="version", version=f"envparity {__version__}")
    return p


def _key(finding) -> str:
    return f"{finding.rule}|{finding.name or finding.message}"


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.path)
    if not root.is_dir():
        print(f"envparity: not a directory: {root}", file=sys.stderr)
        return 2
    root = root.resolve()
    try:
        config = config_mod.load(root, args.config)
        known = set(json.loads(args.baseline.read_text(encoding="utf-8"))) if args.baseline else set()
    except (config_mod.ConfigError, OSError, ValueError) as exc:
        print(exc if isinstance(exc, config_mod.ConfigError) else f"envparity: cannot read baseline: {exc}", file=sys.stderr)
        return 2
    project = scan.scan_project(root, config.exclude, code=not args.no_code)
    findings = checks.run(project, config, code_scanned=not args.no_code)
    if args.write_baseline:
        args.write_baseline.write_text(json.dumps(sorted({_key(f) for f in findings}), indent=2) + "\n", encoding="utf-8")
        print(f"envparity: wrote {len(findings)} finding(s) to {args.write_baseline}")
        return 0
    hidden = [f for f in findings if _key(f) in known]
    findings = [f for f in findings if _key(f) not in known]
    allowed = {"error": ("error",), "warn": ("error", "warn"), "info": ("error", "warn", "info")}[args.min_severity]
    shown = [f for f in findings if f.severity in allowed]
    try:
        if args.suggest:
            print(report.suggest(project, findings))
        elif args.format == "json":
            print(report.as_json(project, shown))
        elif args.format == "github":
            print(report.github(shown))
        else:
            if args.matrix:
                print(report.matrix(project), "\n", sep="")
            colour = sys.stdout.isatty() and "NO_COLOR" not in os.environ
            print(report.text(project, shown, colour=colour, hidden=len(hidden)))
        sys.stdout.flush()
    except BrokenPipeError:
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
    return 1 if any(f.severity in FAIL_LEVELS[args.fail_on] for f in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
