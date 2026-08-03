"""Bootstrap helper for apstra-mcp local setup.

Creates the local instance configuration and MCP client config files so users
can start quickly without manually editing multiple files.
"""

from __future__ import annotations

import argparse
import getpass
import importlib.util
import json
import os
import platform
import shlex
import socket
import stat
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml


RAG_PROVIDERS = ("ollama", "lmstudio", "openai_compatible")
UI_MODES = ("auto", "plain", "rich")

_UI_MODE = "plain"
_RICH_CONSOLE: Any = None
_RICH_PANEL: Any = None
_RICH_TABLE: Any = None
_RUN_STAMP = datetime.now().strftime("%Y%m%d-%H%M%S")


def _init_ui(mode: str) -> None:
    global _UI_MODE, _RICH_CONSOLE, _RICH_PANEL, _RICH_TABLE
    chosen = mode.lower().strip()
    if chosen not in UI_MODES:
        chosen = "auto"

    if chosen in {"auto", "rich"}:
        try:
            from rich.console import Console
            from rich.panel import Panel
            from rich.table import Table

            _RICH_CONSOLE = Console()
            _RICH_PANEL = Panel
            _RICH_TABLE = Table
            _UI_MODE = "rich"
            return
        except Exception:
            if chosen == "rich":
                print("[WARN] rich UI requested but 'rich' is not installed; falling back to plain output.")

    _UI_MODE = "plain"


def _supports_color() -> bool:
    if _UI_MODE == "rich":
        return False
    if os.getenv("NO_COLOR"):
        return False
    if os.getenv("TERM", "") == "dumb":
        return False
    return sys.stdout.isatty()


def _style(text: str, ansi_code: str) -> str:
    if not _supports_color():
        return text
    return f"\033[{ansi_code}m{text}\033[0m"


def _print_banner() -> None:
    if _UI_MODE == "rich":
        _RICH_CONSOLE.print(
            _RICH_PANEL(
                "[bold cyan]apstra-mcp setup wizard[/bold cyan]\n"
                "Guided setup for instance config, MCP config, and optional RAG index.",
                border_style="cyan",
            )
        )
        return

    line = "=" * 72
    print()
    print(_style(line, "36"))
    print(_style("apstra-mcp setup wizard", "1;36"))
    print("Guided setup for instance config, MCP config, and optional RAG index.")
    print(_style(line, "36"))


def _print_section(title: str) -> None:
    if _UI_MODE == "rich":
        _RICH_CONSOLE.rule(f"[bold blue]{title}[/bold blue]")
        return

    print()
    print(_style(f"[{title}]", "1;34"))


def _print_kv(key: str, value: str) -> None:
    if _UI_MODE == "rich":
        _RICH_CONSOLE.print(f"  • [bold]{key}[/bold]: {value}")
        return

    print(f"  - {key}: {value}")


def _print_ok(message: str) -> None:
    if _UI_MODE == "rich":
        _RICH_CONSOLE.print(f"[green][OK][/green] {message}")
        return

    print(_style(f"[OK] {message}", "32"))


def _print_warn(message: str) -> None:
    if _UI_MODE == "rich":
        _RICH_CONSOLE.print(f"[yellow][WARN][/yellow] {message}")
        return

    print(_style(f"[WARN] {message}", "33"))


def _print_step(current: int, total: int, message: str) -> None:
    prefix = f"[{current}/{total}]"
    if _UI_MODE == "rich":
        _RICH_CONSOLE.print(f"[bold cyan]{prefix}[/bold cyan] {message}")
        return
    print(_style(f"{prefix} {message}", "36"))


def _default_claude_config_path() -> Path:
    system = platform.system().lower()
    home = Path.home()
    if "darwin" in system:
        return home / "Library/Application Support/Claude/claude_desktop_config.json"
    if "windows" in system:
        appdata = os.getenv("APPDATA")
        if appdata:
            return Path(appdata) / "Claude/claude_desktop_config.json"
        return home / "AppData/Roaming/Claude/claude_desktop_config.json"
    return home / ".config/Claude/claude_desktop_config.json"


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _require(value: str | None, message: str, interactive: bool) -> str:
    if value and value.strip():
        return value.strip()
    if interactive:
        entered = input(message).strip()
        if entered:
            return entered
    raise SystemExit(message + " (required)")


def _arg_provided(flag: str) -> bool:
    return any(token == flag or token.startswith(flag + "=") for token in sys.argv[1:])


def _validate_http_url(raw: str, field_name: str) -> str:
    value = raw.strip()
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{field_name} must be a full http(s) URL (for example: https://apstra.example.com)")
    return value


def _ensure_valid_http_url(value: str, field_name: str, prompt: str, interactive: bool) -> str:
    current = value
    while True:
        try:
            return _validate_http_url(current, field_name)
        except ValueError as exc:
            if not interactive:
                raise SystemExit(str(exc))
            _print_warn(str(exc))
            current = input(prompt).strip()


def _ensure_existing_source_dir(
    workspace: Path,
    path_value: str,
    prompt: str,
    interactive: bool,
) -> str:
    current = path_value.strip()
    while True:
        resolved = _resolve_workspace_path(workspace, current)
        if resolved.exists() and resolved.is_dir():
            return current
        msg = f"RAG source directory not found: {resolved}"
        if not interactive:
            raise SystemExit(msg)
        _print_warn(msg)
        current = input(prompt).strip()


def _probe_endpoint(url: str) -> tuple[str, str]:
    parsed = urlparse(url)
    host = parsed.hostname
    if not host:
        return "FAIL", "invalid host"
    if parsed.scheme not in {"http", "https"}:
        return "FAIL", "unsupported scheme"

    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=3):
            return "PASS", f"reachable over TCP ({host}:{port})"
    except OSError as exc:
        return "WARN", f"unreachable over TCP ({host}:{port}): {exc}"


def _write_rollback_script(workspace: Path, backups: dict[Path, Path]) -> Path:
    rollback_dir = workspace / ".setup-backups"
    rollback_dir.mkdir(parents=True, exist_ok=True)
    script_path = rollback_dir / f"rollback-{_RUN_STAMP}.sh"

    lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        "",
        f"# Generated by setup_cli.py at {datetime.now().isoformat(timespec='seconds')}",
        "# Restores backups created during setup.",
        "",
    ]
    for target_path, backup_path in backups.items():
        lines.append(f"cp {shlex.quote(str(backup_path))} {shlex.quote(str(target_path))}")

    script_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(script_path, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    return script_path


def _prompt_yes_no(message: str, default: bool) -> bool:
    suffix = " [Y/n]: " if default else " [y/N]: "
    entered = input(message + suffix).strip().lower()
    if not entered:
        return default
    if entered in {"y", "yes"}:
        return True
    if entered in {"n", "no"}:
        return False
    print("Please answer 'y' or 'n'.")
    return _prompt_yes_no(message, default)


def _prompt_with_default(message: str, default: str) -> str:
    entered = input(f"{message} [{default}]: ").strip()
    return entered if entered else default


def _prompt_int_with_default(message: str, default: int, minimum: int = 0) -> int:
    entered = input(f"{message} [{default}]: ").strip()
    if not entered:
        return default
    try:
        parsed = int(entered)
    except ValueError:
        print("Please enter a valid integer.")
        return _prompt_int_with_default(message, default, minimum)
    if parsed < minimum:
        print(f"Please enter a value >= {minimum}.")
        return _prompt_int_with_default(message, default, minimum)
    return parsed


def _prompt_choice_with_default(message: str, choices: tuple[str, ...], default: str) -> str:
    choices_text = ", ".join(choices)
    entered = input(f"{message} ({choices_text}) [{default}]: ").strip().lower()
    if not entered:
        return default
    if entered in choices:
        return entered
    print(f"Please choose one of: {choices_text}")
    return _prompt_choice_with_default(message, choices, default)


def _backup_target(path: Path) -> Path:
    return path.with_suffix(path.suffix + f".bak.{_RUN_STAMP}")


def _collect_preflight(
    workspace: Path,
    host_url: str,
    check_endpoint: bool,
    enable_rag: bool,
    build_embeddings: bool,
    source_dir_raw: str,
) -> tuple[list[tuple[str, str, str]], bool, bool]:
    checks: list[tuple[str, str, str]] = []

    checks.append(("Workspace", "PASS", str(workspace)))

    if check_endpoint:
        status, details = _probe_endpoint(host_url)
        checks.append(("Apstra endpoint reachability", status, details))

    config_dir = workspace / "config"
    checks.append(
        (
            "Config directory",
            "PASS" if config_dir.exists() else "WARN",
            str(config_dir),
        )
    )

    if build_embeddings:
        build_script = workspace / "knowledge" / "build" / "build_index.py"
        checks.append(
            (
                "Embedding builder script",
                "PASS" if build_script.exists() else "FAIL",
                str(build_script),
            )
        )

        source_dir = Path(source_dir_raw)
        if not source_dir.is_absolute():
            source_dir = workspace / source_dir
        source_dir = source_dir.resolve()

        checks.append(
            (
                "PDF source directory",
                "PASS" if source_dir.exists() else "FAIL",
                str(source_dir),
            )
        )

        pdf_count = len(list(source_dir.glob("*.pdf"))) if source_dir.exists() else 0
        checks.append(
            (
                "PDF files found",
                "PASS" if pdf_count > 0 else "FAIL",
                str(pdf_count),
            )
        )

        pypdf_ok = importlib.util.find_spec("pypdf") is not None
        checks.append(
            (
                "Dependency: pypdf",
                "PASS" if pypdf_ok else "WARN",
                "installed" if pypdf_ok else "missing (install knowledge/build/requirements.txt)",
            )
        )

    if enable_rag and not build_embeddings:
        checks.append(("RAG mode", "PASS", "enabled (index build skipped)"))

    has_fail = any(status == "FAIL" for _, status, _ in checks)
    has_warn = any(status == "WARN" for _, status, _ in checks)
    return checks, has_fail, has_warn


def _print_preflight_review(checks: list[tuple[str, str, str]]) -> None:
    _print_section("Preflight review")

    if _UI_MODE == "rich":
        table = _RICH_TABLE(show_header=True, header_style="bold")
        table.add_column("Check")
        table.add_column("Status")
        table.add_column("Details")
        for check, status, details in checks:
            color = "green" if status == "PASS" else "yellow" if status == "WARN" else "red"
            table.add_row(check, f"[{color}]{status}[/{color}]", details)
        _RICH_CONSOLE.print(table)
        return

    for check, status, details in checks:
        marker = "OK" if status == "PASS" else "WARN" if status == "WARN" else "FAIL"
        print(f"  - [{marker}] {check}: {details}")


def _build_mcp_server_entry(workspace: Path, config_file: Path) -> dict[str, Any]:
    return {
        "command": "uvx",
        "args": ["--from", str(workspace.resolve()), "apstra-mcp"],
        "env": {
            "APSTRA_CONFIG_FILE": str(config_file.resolve()),
        },
    }


def _read_json_file(path: Path, default_obj: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return default_obj
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(loaded, dict):
        raise SystemExit(f"Expected top-level JSON object in {path}")
    return loaded


def _write_instances_yaml(
    config_path: Path,
    instance_name: str,
    host: str,
    username: str,
    password: str,
    ssl_verify: bool,
    rag_config: dict[str, Any] | None,
    overwrite: bool,
    interactive: bool,
    dry_run: bool,
) -> Path | None:
    if config_path.exists() and not overwrite:
        if interactive and _prompt_yes_no(
            f"{config_path} already exists. Overwrite it?",
            default=False,
        ):
            overwrite = True
        else:
            raise SystemExit(
                f"Refusing to overwrite existing file: {config_path}. "
                "Use --overwrite to replace it."
            )

    backup_path: Path | None = None
    if config_path.exists():
        backup_path = _backup_target(config_path)
        if not dry_run:
            backup_path = _backup_file(config_path)

    payload = {
        "instances": [
            {
                "name": instance_name,
                "host": host,
                "username": username,
                "password": password,
                "ssl_verify": ssl_verify,
            }
        ]
    }
    if rag_config:
        payload["rag"] = rag_config

    if dry_run:
        return backup_path

    _ensure_parent(config_path)
    config_path.write_text(
        yaml.safe_dump(payload, sort_keys=False),
        encoding="utf-8",
    )
    os.chmod(config_path, stat.S_IRUSR | stat.S_IWUSR)
    return backup_path


def _write_vscode_mcp_json(
    mcp_path: Path,
    server_name: str,
    server_entry: dict[str, Any],
    overwrite_server: bool,
    interactive: bool,
    dry_run: bool,
) -> Path | None:
    root = _read_json_file(mcp_path, {"servers": {}})
    servers = root.get("servers")
    if not isinstance(servers, dict):
        raise SystemExit(f"Expected 'servers' object in {mcp_path}")

    if server_name in servers and not overwrite_server:
        if interactive and _prompt_yes_no(
            f"Server '{server_name}' already exists in {mcp_path}. Replace it?",
            default=False,
        ):
            overwrite_server = True
        else:
            raise SystemExit(
                f"Server '{server_name}' already exists in {mcp_path}. "
                "Use --overwrite-mcp-server to replace it."
            )

    servers[server_name] = server_entry
    root["servers"] = servers

    backup_path: Path | None = None
    if mcp_path.exists():
        backup_path = _backup_target(mcp_path)
        if not dry_run:
            backup_path = _backup_file(mcp_path)

    if dry_run:
        return backup_path

    _ensure_parent(mcp_path)
    mcp_path.write_text(json.dumps(root, indent=2) + "\n", encoding="utf-8")
    return backup_path


def _backup_file(path: Path) -> Path:
    backup = _backup_target(path)
    backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    return backup


def _write_claude_desktop_config(
    claude_path: Path,
    server_name: str,
    server_entry: dict[str, Any],
    overwrite_server: bool,
    interactive: bool,
    dry_run: bool,
) -> Path | None:
    root = _read_json_file(claude_path, {"mcpServers": {}})
    servers = root.get("mcpServers")
    if not isinstance(servers, dict):
        raise SystemExit(f"Expected 'mcpServers' object in {claude_path}")

    if server_name in servers and not overwrite_server:
        if interactive and _prompt_yes_no(
            f"Server '{server_name}' already exists in {claude_path}. Replace it?",
            default=False,
        ):
            overwrite_server = True
        else:
            raise SystemExit(
                f"Server '{server_name}' already exists in {claude_path}. "
                "Use --overwrite-mcp-server to replace it."
            )

    backup_path = None
    if claude_path.exists():
        backup_path = _backup_target(claude_path)
        if not dry_run:
            backup_path = _backup_file(claude_path)

    servers[server_name] = server_entry
    root["mcpServers"] = servers

    if dry_run:
        return backup_path

    _ensure_parent(claude_path)
    claude_path.write_text(json.dumps(root, indent=2) + "\n", encoding="utf-8")
    return backup_path


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="apstra-mcp-setup",
        description=(
            "Interactive setup wizard for local apstra-mcp files: config/instances.yaml, "
            ".vscode/mcp.json, optional Claude Desktop config, and optional "
            "RAG embedding setup."
        ),
    )
    parser.add_argument("--workspace", default=".", help="Workspace root path")
    parser.add_argument(
        "--ui",
        default="auto",
        choices=UI_MODES,
        help="Terminal UI mode: auto, plain, or rich",
    )
    parser.add_argument("--instance-name", default="dc-primary", help="Apstra instance name")
    parser.add_argument("--host", help="Apstra controller URL, e.g. https://apstra.example.com")
    parser.add_argument("--username", help="Apstra API username")
    parser.add_argument("--password", help="Apstra API password")
    parser.add_argument(
        "--ssl-verify",
        action="store_true",
        help="Enable TLS certificate verification (default: disabled)",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing config/instances.yaml",
    )
    parser.add_argument(
        "--overwrite-mcp-server",
        action="store_true",
        help="Replace existing server entry in MCP config files",
    )
    parser.add_argument(
        "--server-name",
        default="apstra",
        help="Server key to write in MCP config files (default: apstra)",
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Fail instead of prompting for missing host/username/password",
    )
    parser.add_argument(
        "--check-endpoint",
        action="store_true",
        help="Run a TCP reachability preflight check against the Apstra host",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview actions without writing files or running embedding builds",
    )
    parser.add_argument(
        "--skip-vscode",
        action="store_true",
        help="Do not write .vscode/mcp.json",
    )
    parser.add_argument(
        "--configure-claude",
        action="store_true",
        help="Also update Claude Desktop config JSON",
    )
    parser.add_argument(
        "--claude-config-path",
        help="Override Claude Desktop config path",
    )
    parser.add_argument(
        "--enable-rag",
        action="store_true",
        help="Write a rag: block into config/instances.yaml",
    )
    parser.add_argument(
        "--build-embeddings",
        action="store_true",
        help="Build knowledge/index.embeddings.json after writing config",
    )
    parser.add_argument(
        "--rag-provider",
        default="ollama",
        choices=RAG_PROVIDERS,
        help="Embedding provider for rag block and index build",
    )
    parser.add_argument(
        "--rag-model",
        default="qwen3-embedding",
        help="Embedding model name",
    )
    parser.add_argument(
        "--rag-url",
        default="http://localhost:11434/api/embed",
        help="Embedding endpoint URL",
    )
    parser.add_argument(
        "--rag-top-k",
        type=int,
        default=5,
        help="Top-k retrieval count for RAG queries",
    )
    parser.add_argument(
        "--rag-apstra-version",
        default="6.1",
        help="Apstra version metadata stored in built index",
    )
    parser.add_argument(
        "--rag-source-dir",
        default="knowledge/build/source_pdfs",
        help="Directory containing source PDFs for embedding build",
    )
    parser.add_argument(
        "--rag-output",
        default="knowledge/index.embeddings.json",
        help="Output JSON path for embedding index",
    )
    parser.add_argument(
        "--rag-api-key",
        default=None,
        help="Optional bearer token for OpenAI-compatible providers",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=400,
        help="Target chunk size for index build",
    )
    parser.add_argument(
        "--chunk-overlap",
        type=int,
        default=50,
        help="Chunk overlap setting for index build",
    )
    return parser


def _resolve_workspace_path(workspace: Path, path_value: str) -> Path:
    path = Path(path_value).expanduser()
    if not path.is_absolute():
        path = workspace / path
    return path.resolve()


def _run_embedding_build(
    workspace: Path,
    provider: str,
    model: str,
    url: str,
    apstra_version: str,
    source_dir_raw: str,
    output_raw: str,
    chunk_size: int,
    chunk_overlap: int,
    api_key: str | None,
) -> Path:
    source_dir = _resolve_workspace_path(workspace, source_dir_raw)
    output_path = _resolve_workspace_path(workspace, output_raw)
    script_path = workspace / "knowledge" / "build" / "build_index.py"

    if not script_path.exists():
        raise SystemExit(f"Embedding builder not found: {script_path}")
    if not source_dir.exists() or not source_dir.is_dir():
        raise SystemExit(f"RAG source directory not found: {source_dir}")

    pdfs = sorted(p for p in source_dir.glob("*.pdf") if p.is_file())
    if not pdfs:
        raise SystemExit(
            "No PDF files found for embedding build. "
            f"Add PDFs to {source_dir} and rerun with --build-embeddings."
        )

    cmd = [
        sys.executable,
        str(script_path),
        "--source-dir",
        str(source_dir),
        "--output",
        str(output_path),
        "--apstra-version",
        apstra_version,
        "--provider",
        provider,
        "--model",
        model,
        "--url",
        url,
        "--chunk-size",
        str(chunk_size),
        "--chunk-overlap",
        str(chunk_overlap),
    ]
    if api_key:
        cmd.extend(["--api-key", api_key])

    result = subprocess.run(
        cmd,
        cwd=str(script_path.parent),
        capture_output=True,
        text=True,
        check=False,
    )

    if result.returncode != 0:
        details = (result.stderr or result.stdout or "").strip()
        hint = ""
        if "No module named 'pypdf'" in details or "No module named 'httpx'" in details:
            hint = (
                "\nInstall embedding build dependencies with:\n"
                "  pip install -r knowledge/build/requirements.txt"
            )
        raise SystemExit(f"Embedding build failed.\n{details}{hint}")

    return output_path


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    _init_ui(args.ui)

    workspace = Path(args.workspace).expanduser().resolve()
    if not workspace.exists() or not workspace.is_dir():
        raise SystemExit(f"Invalid workspace path: {workspace}")

    interactive = not args.non_interactive
    dry_run = bool(args.dry_run)

    if interactive:
        _print_banner()
        _print_section("Workspace")
        _print_kv("Root", str(workspace))
        _print_kv("Instance name", args.instance_name)
        _print_kv("UI mode", _UI_MODE)
        _print_kv("Dry run", "yes" if dry_run else "no")
        if dry_run:
            _print_warn("Dry-run enabled: no files will be written and no embeddings will be built.")

    _print_section("Apstra connection") if interactive else None

    host = _require(args.host, "Enter APSTRA_HOST (e.g., https://apstra.example.com): ", interactive)
    host = _ensure_valid_http_url(
        host,
        "APSTRA_HOST",
        "Enter APSTRA_HOST (e.g., https://apstra.example.com): ",
        interactive,
    )
    username = _require(args.username, "Enter APSTRA_USERNAME: ", interactive)

    password = args.password
    if not password:
        if interactive:
            password = getpass.getpass("Enter APSTRA_PASSWORD: ").strip()
        else:
            raise SystemExit("Missing --password in non-interactive mode")
    if not password:
        raise SystemExit("APSTRA_PASSWORD cannot be empty")

    if interactive and not _arg_provided("--check-endpoint"):
        args.check_endpoint = _prompt_yes_no("Run endpoint reachability preflight check?", default=True)

    if interactive and not _arg_provided("--skip-vscode"):
        _print_section("Client configuration targets")
        configure_vscode = _prompt_yes_no("Configure .vscode/mcp.json?", default=True)
        args.skip_vscode = not configure_vscode

    if interactive and not _arg_provided("--configure-claude"):
        args.configure_claude = _prompt_yes_no("Configure Claude Desktop MCP config?", default=False)

    if interactive and not _arg_provided("--ssl-verify"):
        args.ssl_verify = _prompt_yes_no("Enable TLS certificate verification (ssl_verify)?", default=False)

    if interactive:
        _print_section("Optional RAG setup")

    if interactive and not _arg_provided("--enable-rag") and not _arg_provided("--build-embeddings"):
        args.enable_rag = _prompt_yes_no("Enable optional RAG config?", default=False)

    enable_rag = bool(args.enable_rag or args.build_embeddings)

    if interactive and enable_rag:
        if not _arg_provided("--rag-provider"):
            args.rag_provider = _prompt_choice_with_default(
                "Embedding provider",
                RAG_PROVIDERS,
                args.rag_provider,
            )
        if not _arg_provided("--rag-model"):
            args.rag_model = _prompt_with_default("Embedding model", args.rag_model)
        if not _arg_provided("--rag-url"):
            args.rag_url = _prompt_with_default("Embedding URL", args.rag_url)
        args.rag_url = _ensure_valid_http_url(
            args.rag_url,
            "RAG embedding URL",
            "Embedding URL: ",
            interactive,
        )
        if not _arg_provided("--rag-top-k"):
            args.rag_top_k = _prompt_int_with_default("RAG top_k", args.rag_top_k, minimum=1)

    if interactive and enable_rag and not _arg_provided("--build-embeddings"):
        args.build_embeddings = _prompt_yes_no("Build embeddings index now?", default=False)

    if interactive and args.build_embeddings:
        if not _arg_provided("--rag-source-dir"):
            args.rag_source_dir = _prompt_with_default("PDF source directory", args.rag_source_dir)
        args.rag_source_dir = _ensure_existing_source_dir(
            workspace,
            args.rag_source_dir,
            "PDF source directory: ",
            interactive,
        )
        if not _arg_provided("--rag-output"):
            args.rag_output = _prompt_with_default("Embeddings output path", args.rag_output)
        if not _arg_provided("--rag-apstra-version"):
            args.rag_apstra_version = _prompt_with_default("Apstra version metadata", args.rag_apstra_version)
        if not _arg_provided("--chunk-size"):
            args.chunk_size = _prompt_int_with_default("Chunk size", args.chunk_size, minimum=1)
        if not _arg_provided("--chunk-overlap"):
            args.chunk_overlap = _prompt_int_with_default("Chunk overlap", args.chunk_overlap, minimum=0)
        if (
            args.rag_provider in {"lmstudio", "openai_compatible"}
            and not _arg_provided("--rag-api-key")
            and not args.rag_api_key
        ):
            entered_key = getpass.getpass("Optional API key for embedding endpoint (press Enter to skip): ").strip()
            args.rag_api_key = entered_key or None

    if enable_rag:
        args.rag_url = _ensure_valid_http_url(
            args.rag_url,
            "RAG embedding URL",
            "Embedding URL: ",
            interactive,
        )

    if args.rag_top_k < 1:
        raise SystemExit("--rag-top-k must be >= 1")
    if args.chunk_size < 1:
        raise SystemExit("--chunk-size must be >= 1")
    if args.chunk_overlap < 0:
        raise SystemExit("--chunk-overlap must be >= 0")
    if args.chunk_overlap >= args.chunk_size:
        raise SystemExit("--chunk-overlap must be smaller than --chunk-size")

    if args.build_embeddings:
        args.rag_source_dir = _ensure_existing_source_dir(
            workspace,
            args.rag_source_dir,
            "PDF source directory: ",
            interactive,
        )

    instances_path = workspace / "config" / "instances.yaml"
    vscode_mcp_path = workspace / ".vscode" / "mcp.json"

    checks, has_fail, has_warn = _collect_preflight(
        workspace=workspace,
        host_url=host,
        check_endpoint=bool(args.check_endpoint),
        enable_rag=enable_rag,
        build_embeddings=bool(args.build_embeddings),
        source_dir_raw=args.rag_source_dir,
    )
    _print_preflight_review(checks)
    if has_fail:
        raise SystemExit("Preflight checks failed. Fix reported FAIL items and rerun.")
    if interactive and has_warn and not _prompt_yes_no("Proceed despite preflight warnings?", default=True):
        raise SystemExit("Setup cancelled by user.")

    if interactive:
        _print_section("Planned changes")
        write_label = "Would write" if dry_run else "Write"
        _print_kv(write_label, str(instances_path))
        if args.skip_vscode:
            _print_kv("VS Code MCP", "skip")
        else:
            _print_kv(write_label, str(vscode_mcp_path))
        if args.configure_claude:
            claude_target = (
                str(Path(args.claude_config_path).expanduser().resolve())
                if args.claude_config_path
                else str(_default_claude_config_path().expanduser().resolve())
            )
            _print_kv(write_label, claude_target)
        _print_kv("RAG", "enabled" if enable_rag else "disabled")
        _print_kv("Build embeddings", "yes" if args.build_embeddings else "no")
        _print_kv("Endpoint check", "yes" if args.check_endpoint else "no")
        if not _prompt_yes_no("Proceed with setup now?", default=True):
            raise SystemExit("Setup cancelled by user.")

    rag_config = None
    if enable_rag:
        rag_config = {
            "enabled": True,
            "embedding_provider": args.rag_provider,
            "embedding_model": args.rag_model,
            "embedding_url": args.rag_url,
            "top_k": args.rag_top_k,
        }

    total_steps = 1
    if not args.skip_vscode:
        total_steps += 1
    if args.configure_claude:
        total_steps += 1
    if args.build_embeddings:
        total_steps += 1

    current_step = 1
    backups: dict[Path, Path] = {}

    _print_step(current_step, total_steps, "Writing instance configuration")
    instances_backup = _write_instances_yaml(
        config_path=instances_path,
        instance_name=args.instance_name,
        host=host,
        username=username,
        password=password,
        ssl_verify=bool(args.ssl_verify),
        rag_config=rag_config,
        overwrite=bool(args.overwrite),
        interactive=interactive,
        dry_run=dry_run,
    )
    if instances_backup:
        backups[instances_path] = instances_backup
    _print_ok(f"{'Would write' if dry_run else 'Wrote'} {instances_path}")
    if instances_backup:
        _print_warn(f"{'Would create' if dry_run else 'Created'} backup: {instances_backup}")
    current_step += 1

    server_entry = _build_mcp_server_entry(workspace=workspace, config_file=instances_path)

    if not args.skip_vscode:
        _print_step(current_step, total_steps, "Writing VS Code MCP configuration")
        vscode_backup = _write_vscode_mcp_json(
            mcp_path=vscode_mcp_path,
            server_name=args.server_name,
            server_entry=server_entry,
            overwrite_server=bool(args.overwrite_mcp_server),
            interactive=interactive,
            dry_run=dry_run,
        )
        if vscode_backup:
            backups[vscode_mcp_path] = vscode_backup
        _print_ok(f"{'Would write' if dry_run else 'Wrote'} {vscode_mcp_path}")
        if vscode_backup:
            _print_warn(f"{'Would create' if dry_run else 'Created'} backup: {vscode_backup}")
        current_step += 1

    claude_path = None
    claude_backup = None
    if args.configure_claude:
        _print_step(current_step, total_steps, "Writing Claude Desktop MCP configuration")
        claude_path = (
            Path(args.claude_config_path).expanduser().resolve()
            if args.claude_config_path
            else _default_claude_config_path().expanduser().resolve()
        )
        claude_backup = _write_claude_desktop_config(
            claude_path=claude_path,
            server_name=args.server_name,
            server_entry=server_entry,
            overwrite_server=bool(args.overwrite_mcp_server),
            interactive=interactive,
            dry_run=dry_run,
        )
        _print_ok(f"{'Would write' if dry_run else 'Wrote'} {claude_path}")
        if claude_backup:
            backups[claude_path] = claude_backup
            _print_warn(f"{'Would create' if dry_run else 'Created'} backup: {claude_backup}")
        current_step += 1

    embedding_output = None
    if args.build_embeddings:
        _print_step(current_step, total_steps, "Building embeddings index")
        if dry_run:
            embedding_output = _resolve_workspace_path(workspace, args.rag_output)
            _print_ok(f"Would build embeddings index at {embedding_output}")
        else:
            embedding_output = _run_embedding_build(
                workspace=workspace,
                provider=args.rag_provider,
                model=args.rag_model,
                url=args.rag_url,
                apstra_version=args.rag_apstra_version,
                source_dir_raw=args.rag_source_dir,
                output_raw=args.rag_output,
                chunk_size=args.chunk_size,
                chunk_overlap=args.chunk_overlap,
                api_key=args.rag_api_key,
            )
            _print_ok(f"Built embeddings index at {embedding_output}")

    rollback_script = None
    if backups:
        if dry_run:
            rollback_script = workspace / ".setup-backups" / f"rollback-{_RUN_STAMP}.sh"
            _print_warn(f"Would write rollback script: {rollback_script}")
        else:
            rollback_script = _write_rollback_script(workspace, backups)
            _print_ok(f"Wrote rollback script: {rollback_script}")

    _print_section("Completion")
    _print_ok("Dry-run complete." if dry_run else "Setup complete.")

    action_label = "Would write" if dry_run else "Wrote"
    _print_kv(action_label, str(instances_path))
    if not args.skip_vscode:
        _print_kv(action_label, str(vscode_mcp_path))
    if claude_path:
        _print_kv(action_label, str(claude_path))
    for target_path, backup_path in backups.items():
        _print_kv("Backup", f"{target_path} <= {backup_path}")
    if rollback_script:
        _print_kv("Rollback script", str(rollback_script))
    if enable_rag:
        _print_kv("RAG", "enabled in config/instances.yaml")
    if args.build_embeddings:
        _print_kv(
            "Embeddings",
            f"{'planned at' if dry_run else 'built at'} {embedding_output}",
        )

    _print_section("Next steps")
    if dry_run:
        print("  1. Review the planned changes above.")
        print("  2. Re-run without --dry-run to apply changes.")
    else:
        print("  1. Open VS Code and verify MCP server entry in .vscode/mcp.json.")
        print("  2. If Claude Desktop was configured, restart Claude Desktop.")
        print("  3. Start using the server via: uvx --from . apstra-mcp")

    return 0


if __name__ == "__main__":
    sys.exit(main())
