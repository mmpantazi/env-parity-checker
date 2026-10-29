from __future__ import annotations

import argparse
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
    p.add_argument("--matrix", action="store_true", help="print which layer defines which variable")
    p.add_argument("--config", type=Path, help="path to .envparity.toml")
    p.add_argument("--version", action="version", version=f"envparity {__version__}")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.path)
    if not root.is_dir():
        print(f"envparity: not a directory: {root}", file=sys.stderr)
        return 2
    root = root.resolve()
    config = config_mod.load(root, args.config)
    project = scan.scan_project(root, config.exclude, code=not args.no_code)
    findings = checks.run(project, config, code_scanned=not args.no_code)
    allowed = {"error": ("error",), "warn": ("error", "warn"), "info": ("error", "warn", "info")}[args.min_severity]
    shown = [f for f in findings if f.severity in allowed]
    if args.format == "json":
        print(report.as_json(project, shown))
    elif args.format == "github":
        print(report.github(shown))
    else:
        if args.matrix:
            print(report.matrix(project), "\n", sep="")
        print(report.text(project, shown, colour=sys.stdout.isatty()))
    return 1 if any(f.severity in FAIL_LEVELS[args.fail_on] for f in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
