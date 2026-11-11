"""Command line for MAPLES.

    maples ui                 open the app in your browser (runs on this computer)
    maples run GENES ...      run one analysis in the terminal
    maples config             show where settings live and what is set
    maples config --set KEY=VALUE [KEY=VALUE ...]
"""
from __future__ import annotations

import argparse
import socket
import sys
import threading
import webbrowser

from maples import settings as user_settings


def _version() -> str:
    from maples.api import _maple_version

    return _maple_version()


def _free_port(host: str, start: int, tries: int = 20) -> int:
    for port in range(start, start + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind((host if host not in ("localhost",) else "127.0.0.1", port))
                return port
            except OSError:
                continue
    raise SystemExit(f"No free port found between {start} and {start + tries - 1}.")


def cmd_ui(args: argparse.Namespace) -> int:
    try:
        import uvicorn
    except ImportError:
        print("The app needs uvicorn: pip install uvicorn fastapi", file=sys.stderr)
        return 1
    from maples.web.server import app

    local = args.host in ("127.0.0.1", "localhost", "::1")
    if not local:
        app.state.extra_hosts = ["*"]
        print(
            f"Warning: listening on {args.host}. Anyone who can reach this address can use "
            "your saved API key. Only do this on a trusted network or inside a container.",
            file=sys.stderr,
        )
    port = _free_port(args.host, args.port)
    url = f"http://{'127.0.0.1' if local else args.host}:{port}"
    view = user_settings.public_view()
    print(f"MAPLES {_version()} is running at {url}")
    if not view["has_llm_key"]:
        print("No API key yet: the app will ask for one under Settings.")
    print("Press Ctrl+C to stop.")
    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=args.host, port=port, log_level="warning")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    from maples.api import annotate

    genes = " ".join(args.genes)
    result = annotate(genes, tissue=args.tissue, disease=args.disease, species=args.species)
    if not result.run_metadata.llm_used:
        print("No API key is set, so no papers were read. Run `maples config --set OPENAI_API_KEY=...`.", file=sys.stderr)

    print(f"\n{result.label}  (confidence: {result.confidence})")
    if result.rationale:
        print(result.rationale)
    if result.evidence:
        print(f"\n{len(result.evidence)} evidence rows:")
        for row in result.evidence[: args.top]:
            genes_str = ", ".join(row.matched_user_genes)
            print(f"  PMID {row.pmid} ({row.publication_year or '?'}): {row.celltype_label} [{genes_str}]")
            print(f"    \"{row.evidence_snippet[:240]}\"")
        if len(result.evidence) > args.top:
            print(f"  ... {len(result.evidence) - args.top} more (use --json to save everything)")
    for warning in result.warnings:
        print(f"Note: {warning}", file=sys.stderr)

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            fh.write(result.to_json())
        print(f"\nSaved full result to {args.json}")
    return 0


def cmd_config(args: argparse.Namespace) -> int:
    if args.set:
        values = {}
        for item in args.set:
            if "=" not in item:
                print(f"Expected KEY=VALUE, got: {item}", file=sys.stderr)
                return 2
            key, value = item.split("=", 1)
            key = key.strip().upper()
            if key not in user_settings.SETTING_KEYS:
                print(f"Unknown setting {key}. Allowed: {', '.join(user_settings.SETTING_KEYS)}", file=sys.stderr)
                return 2
            values[key] = value
        saved = user_settings.save_settings(values)
        user_settings.apply_to_env(saved, override=True)
        print(f"Saved to {user_settings.config_path()}")

    view = user_settings.public_view()
    print(f"Settings file: {view['config_path']}")
    print(f"Cache folder:  {user_settings.cache_dir()}")
    for key in user_settings.SETTING_KEYS:
        print(f"  {key:16} {view.get(key) or '(not set)'}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="maples",
        description="MAPLES: Mapping Analogous Programs from Literature Evidence across Species, organs and diseases.",
    )
    parser.add_argument("--version", action="store_true", help="print the version and exit")
    sub = parser.add_subparsers(dest="command")

    ui = sub.add_parser("ui", help="open the app in your browser")
    ui.add_argument("--host", default="127.0.0.1", help="address to listen on (default: 127.0.0.1, this computer only)")
    ui.add_argument("--port", type=int, default=8765, help="port (default: 8765; the next free one is used if taken)")
    ui.add_argument("--no-browser", action="store_true", help="do not open a browser window")
    ui.set_defaults(func=cmd_ui)

    run = sub.add_parser("run", help="run one analysis in the terminal")
    run.add_argument("genes", nargs="+", help="gene symbols, e.g. TP63 KRT17 CDKN2A")
    run.add_argument("--tissue")
    run.add_argument("--disease")
    run.add_argument("--species")
    run.add_argument("--json", metavar="PATH", help="save the full result as JSON")
    run.add_argument("--top", type=int, default=10, help="evidence rows to print (default: 10)")
    run.set_defaults(func=cmd_run)

    cfg = sub.add_parser("config", help="show or change saved settings")
    cfg.add_argument("--set", nargs="+", metavar="KEY=VALUE", help="save one or more settings")
    cfg.set_defaults(func=cmd_config)
    return parser


def main(argv: list[str] | None = None) -> int:
    user_settings.bootstrap()
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.version:
        print(_version())
        return 0
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
