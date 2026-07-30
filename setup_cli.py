"""Bootstrap helper for apstra-mcp local setup.

Creates the local instance configuration and MCP client config files so users
can start quickly without manually editing multiple files.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import platform
import stat
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml


RAG_PROVIDERS = ("ollama", "lmstudio", "openai_compatible")


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
) -> None:
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

    _ensure_parent(config_path)
    config_path.write_text(
        yaml.safe_dump(payload, sort_keys=False),
        encoding="utf-8",
    )
    os.chmod(config_path, stat.S_IRUSR | stat.S_IWUSR)


def _write_vscode_mcp_json(
    mcp_path: Path,
    server_name: str,
    server_entry: dict[str, Any],
    overwrite_server: bool,
    interactive: bool,
) -> None:
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

    _ensure_parent(mcp_path)
    mcp_path.write_text(json.dumps(root, indent=2) + "\n", encoding="utf-8")


def _backup_file(path: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = path.with_suffix(path.suffix + f".bak.{stamp}")
    backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    return backup


def _write_claude_desktop_config(
    claude_path: Path,
    server_name: str,
    server_entry: dict[str, Any],
    overwrite_server: bool,
    interactive: bool,
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
        backup_path = _backup_file(claude_path)

    servers[server_name] = server_entry
    root["mcpServers"] = servers

    _ensure_parent(claude_path)
    claude_path.write_text(json.dumps(root, indent=2) + "\n", encoding="utf-8")
    return backup_path


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="apstra-mcp-setup",
        description=(
            "Auto-configure local apstra-mcp files: config/instances.yaml, "
            ".vscode/mcp.json, optional Claude Desktop config, and optional "
            "RAG embedding setup."
        ),
    )
    parser.add_argument("--workspace", default=".", help="Workspace root path")
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

    workspace = Path(args.workspace).expanduser().resolve()
    if not workspace.exists() or not workspace.is_dir():
        raise SystemExit(f"Invalid workspace path: {workspace}")

    interactive = not args.non_interactive

    if interactive and not _arg_provided("--skip-vscode"):
        configure_vscode = _prompt_yes_no("Configure .vscode/mcp.json?", default=True)
        args.skip_vscode = not configure_vscode

    if interactive and not _arg_provided("--configure-claude"):
        args.configure_claude = _prompt_yes_no("Configure Claude Desktop MCP config?", default=False)

    if interactive and not _arg_provided("--ssl-verify"):
        args.ssl_verify = _prompt_yes_no("Enable TLS certificate verification (ssl_verify)?", default=False)

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
        if not _arg_provided("--rag-top-k"):
            args.rag_top_k = _prompt_int_with_default("RAG top_k", args.rag_top_k, minimum=1)

    if interactive and enable_rag and not _arg_provided("--build-embeddings"):
        args.build_embeddings = _prompt_yes_no("Build embeddings index now?", default=False)

    if interactive and args.build_embeddings:
        if not _arg_provided("--rag-source-dir"):
            args.rag_source_dir = _prompt_with_default("PDF source directory", args.rag_source_dir)
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

    if args.rag_top_k < 1:
        raise SystemExit("--rag-top-k must be >= 1")
    if args.chunk_size < 1:
        raise SystemExit("--chunk-size must be >= 1")
    if args.chunk_overlap < 0:
        raise SystemExit("--chunk-overlap must be >= 0")

    host = _require(args.host, "Enter APSTRA_HOST (e.g., https://apstra.example.com): ", interactive)
    username = _require(args.username, "Enter APSTRA_USERNAME: ", interactive)

    password = args.password
    if not password:
        if interactive:
            password = getpass.getpass("Enter APSTRA_PASSWORD: ").strip()
        else:
            raise SystemExit("Missing --password in non-interactive mode")
    if not password:
        raise SystemExit("APSTRA_PASSWORD cannot be empty")

    instances_path = workspace / "config" / "instances.yaml"
    vscode_mcp_path = workspace / ".vscode" / "mcp.json"

    rag_config = None
    if enable_rag:
        rag_config = {
            "enabled": True,
            "embedding_provider": args.rag_provider,
            "embedding_model": args.rag_model,
            "embedding_url": args.rag_url,
            "top_k": args.rag_top_k,
        }

    _write_instances_yaml(
        config_path=instances_path,
        instance_name=args.instance_name,
        host=host,
        username=username,
        password=password,
        ssl_verify=bool(args.ssl_verify),
        rag_config=rag_config,
        overwrite=bool(args.overwrite),
        interactive=interactive,
    )

    server_entry = _build_mcp_server_entry(workspace=workspace, config_file=instances_path)

    if not args.skip_vscode:
        _write_vscode_mcp_json(
            mcp_path=vscode_mcp_path,
            server_name=args.server_name,
            server_entry=server_entry,
            overwrite_server=bool(args.overwrite_mcp_server),
            interactive=interactive,
        )

    claude_path = None
    claude_backup = None
    if args.configure_claude:
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
        )

    embedding_output = None
    if args.build_embeddings:
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

    print("Setup complete.")
    print(f"- Wrote: {instances_path}")
    if not args.skip_vscode:
        print(f"- Wrote: {vscode_mcp_path}")
    if claude_path:
        print(f"- Wrote: {claude_path}")
        if claude_backup:
            print(f"- Backup: {claude_backup}")
    if enable_rag:
        print("- RAG: enabled in config/instances.yaml")
    if embedding_output:
        print(f"- Embeddings index built: {embedding_output}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
