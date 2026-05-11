import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastmcp import FastMCP
from fastmcp.server.middleware.logging import LoggingMiddleware
from fastmcp.server.middleware.timing import TimingMiddleware

from config.settings import load_sessions
from primitives.anomaly_store import AnomalyStore
from primitives.counter_store import CounterStore
from primitives.graph_client import BlueprintGraphRegistry
from handlers.anomaly_poller import run_anomaly_poller
from handlers.counter_poller import run_counter_poller
from tools import anomalies as anomalies_tool
from tools import bgp as bgp_tool
from tools import blueprints as blueprints_tool
from tools import design as design_tool
from tools import interfaces as interfaces_tool
from tools import links as links_tool
from tools import config_rendering as config_rendering_tool
from tools import mtu_check as mtu_check_tool
from tools import reference as reference_tool
from tools import systems as systems_tool
from tools import anomaly_timeline as anomaly_timeline_tool
from tools import anomaly_analytics as anomaly_analytics_tool
from tools import run_commands as run_commands_tool
from tools import system_health as system_health_tool
from tools import virtual_networks as virtual_networks_tool
from tools import telemetry as telemetry_tool
from tools import probes as probes_tool
from tools import routing_policy as routing_policy_tool
from tools import anomaly_umbrella as anomaly_umbrella_tool
from tools import telemetry_dispatch as telemetry_dispatch_tool
from tools import virtual_networks_dispatch as virtual_networks_dispatch_tool
from tools import probes_dispatch as probes_dispatch_tool
from tools import triage_dispatch as triage_dispatch_tool
from tools import audit as audit_tool


def _env_enabled(name: str, default: str = "0") -> bool:
    value = os.environ.get(name, default)
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _resolve_store_paths() -> tuple[Path, Path]:
    """
    Resolves anomaly/counter SQLite file locations from environment variables.

    Precedence:
      1) MCP_ANOMALY_DB_PATH / MCP_COUNTER_DB_PATH (per-file overrides)
      2) MCP_DATA_DIR + default filenames
      3) package-local ./data directory (existing behavior)
    """
    data_dir_env = os.environ.get("MCP_DATA_DIR")
    base_dir = (
        Path(data_dir_env).expanduser()
        if data_dir_env
        else (Path(__file__).resolve().parent / "data")
    )

    anomaly_override = os.environ.get("MCP_ANOMALY_DB_PATH")
    counter_override = os.environ.get("MCP_COUNTER_DB_PATH")

    anomaly_path = (
        Path(anomaly_override).expanduser()
        if anomaly_override
        else (base_dir / "anomaly_timeseries.db")
    )
    counter_path = (
        Path(counter_override).expanduser()
        if counter_override
        else (base_dir / "counter_timeseries.db")
    )

    return anomaly_path, counter_path


def _delete_store_files_on_startup(base_files: tuple[Path, Path]) -> None:
    for base in base_files:
        for candidate in (base, Path(f"{base}-wal"), Path(f"{base}-shm")):
            try:
                if candidate.exists():
                    candidate.unlink()
                    logging.warning("Deleted store file on startup: %s", candidate)
            except Exception as exc:
                logging.warning("Failed deleting store file %s: %s", candidate, exc)


def _load_instructions() -> str:
    """
    Loads MCP server instructions from instructions.md alongside this file.
    Falls back to a minimal inline string if the file is missing, so the
    server still starts cleanly in unexpected environments.
    """
    instructions_path = Path(__file__).resolve().parent / "instructions.md"
    try:
        return instructions_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        logging.warning(
            "instructions.md not found at %s — using fallback instructions.",
            instructions_path,
        )
        return (
            "MCP server for Juniper Apstra network automation. "
            "This server is read-only. "
            "Run get_system_liveness and get_config_deviations before investigating "
            "individual devices or protocols."
        )


@asynccontextmanager
async def lifespan(app):
    sessions = load_sessions()
    registry = BlueprintGraphRegistry()
    anomaly_db_path, counter_db_path = _resolve_store_paths()
    if _env_enabled("MCP_RESET_STORES_ON_START", "0"):
        logging.warning(
            "MCP_RESET_STORES_ON_START enabled: deleting local anomaly/counter store files before initialization"
        )
        _delete_store_files_on_startup((anomaly_db_path, counter_db_path))
    store = AnomalyStore(db_path=anomaly_db_path)
    counter_store = CounterStore(db_path=counter_db_path)
    for session in sessions:
        try:
            await session.authenticate()
        except Exception as e:
            logging.error(
                "Failed to authenticate session '%s' at startup: %s: %s. "
                "Background refresh will retry every %d seconds.",
                session.name,
                type(e).__name__,
                str(e),
                15,  # AUTH_RETRY_INTERVAL_SECONDS
            )
        session.start_background_refresh()
    poller_task = asyncio.create_task(
        run_anomaly_poller(sessions, store),
        name="anomaly-poller",
    )
    counter_poller_task = asyncio.create_task(
        run_counter_poller(sessions, counter_store),
        name="counter-poller",
    )
    yield {
        "sessions":       sessions,
        "graph_registry": registry,
        "anomaly_store":  store,
        "counter_store":  counter_store,
    }
    poller_task.cancel()
    counter_poller_task.cancel()
    registry.close_all()
    store.close()
    counter_store.close()


mcp = FastMCP(
    "apstra-mcp",
    lifespan=lifespan,
    instructions=_load_instructions(),
)

_TOOL_SURFACE_COMPACT = "compact"
_TOOL_SURFACE_FULL = "full"


def _resolve_tool_surface(value: str | None = None) -> str:
    raw = value if value is not None else os.environ.get("MCP_TOOL_SURFACE", _TOOL_SURFACE_COMPACT)
    normalized = str(raw).strip().lower()
    if normalized in {_TOOL_SURFACE_COMPACT, _TOOL_SURFACE_FULL}:
        return normalized
    logging.warning(
        "Unknown MCP_TOOL_SURFACE '%s'; defaulting to '%s'. Valid values: %s, %s",
        raw,
        _TOOL_SURFACE_COMPACT,
        _TOOL_SURFACE_COMPACT,
        _TOOL_SURFACE_FULL,
    )
    return _TOOL_SURFACE_COMPACT


def _register_tools(app_mcp, tool_surface: str | None = None) -> str:
    surface = _resolve_tool_surface(tool_surface)

    # Core and non-clustered tools are always exposed.
    bgp_tool.register(app_mcp)
    blueprints_tool.register(app_mcp)
    config_rendering_tool.register(app_mcp)
    design_tool.register(app_mcp)
    interfaces_tool.register(app_mcp)
    links_tool.register(app_mcp)
    mtu_check_tool.register(app_mcp)
    reference_tool.register(app_mcp)
    routing_policy_tool.register(app_mcp)
    run_commands_tool.register(app_mcp)
    system_health_tool.register(app_mcp)
    systems_tool.register(app_mcp)

    # Umbrella tools provide a bounded compact surface.
    anomaly_umbrella_tool.register(app_mcp)
    telemetry_dispatch_tool.register(app_mcp)
    virtual_networks_dispatch_tool.register(app_mcp)
    probes_dispatch_tool.register(app_mcp)
    triage_dispatch_tool.register(app_mcp)
    audit_tool.register(app_mcp, include_legacy=(surface == _TOOL_SURFACE_FULL))

    # Full surface keeps all existing granular tools for backwards compatibility.
    if surface == _TOOL_SURFACE_FULL:
        anomaly_timeline_tool.register(app_mcp)
        anomalies_tool.register(app_mcp)
        anomaly_analytics_tool.register(app_mcp)
        virtual_networks_tool.register(app_mcp)
        telemetry_tool.register(app_mcp)
        probes_tool.register(app_mcp)

    return surface


_ACTIVE_TOOL_SURFACE = _register_tools(mcp)

_LOG_LEVEL = os.environ.get("MCP_VERBOSE", "0")

if _LOG_LEVEL == "1":
    # Standard operational logging:
    #   - every MCP tool call and its response (no payload bodies)
    #   - background poller activity (anomaly, counter, graph) at INFO+
    #   - any poller/store errors always visible
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    for noisy_logger in (
        "handlers.anomaly_poller",
        "handlers.counter_poller",
        "primitives.counter_store",
        "primitives.anomaly_store",
        "primitives.graph_client",
    ):
        logging.getLogger(noisy_logger).setLevel(logging.INFO)
    mcp.add_middleware(LoggingMiddleware(include_payloads=False))

elif _LOG_LEVEL == "2":
    # Full debug: tool payloads, all internal state, timing.
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    mcp.add_middleware(TimingMiddleware())
    mcp.add_middleware(LoggingMiddleware(include_payloads=True, max_payload_length=2000))


def main():
    transport = os.environ.get("MCP_TRANSPORT", "stdio")
    host = os.environ.get("MCP_HOST", "0.0.0.0")
    port = int(os.environ.get("MCP_PORT", "8000"))
    if transport == "http":
        mcp.run(transport="streamable-http", host=host, port=port)
    elif transport == "sse":
        mcp.run(transport="sse", host=host, port=port)
    else:
        mcp.run()


if __name__ == "__main__":
    main()