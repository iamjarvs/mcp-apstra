import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastmcp import FastMCP
from fastmcp.server.middleware.logging import LoggingMiddleware
from fastmcp.server.middleware.timing import TimingMiddleware

from config.settings import load_sessions
from primitives.anomaly_store import AnomalyStore
from primitives.graph_client import BlueprintGraphRegistry
from handlers.anomaly_poller import run_anomaly_poller
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
from tools import virtual_networks as virtual_networks_tool


@asynccontextmanager
async def lifespan(app):
    sessions = load_sessions()
    registry = BlueprintGraphRegistry()
    store = AnomalyStore()
    for session in sessions:
        await session.authenticate()
        session.start_background_refresh()
    poller_task = asyncio.create_task(
        run_anomaly_poller(sessions, store),
        name="anomaly-poller",
    )
    yield {"sessions": sessions, "graph_registry": registry, "anomaly_store": store}
    poller_task.cancel()
    registry.close_all()
    store.close()


mcp = FastMCP(
    "apstra-mcp",
    lifespan=lifespan,
    instructions=(
        "MCP server for Juniper Apstra network automation. "
        "\n\n"
        "## Key concepts\n\n"
        "**Instance**: A single Apstra controller (virtual machine). One instance manages "
        "its own set of blueprints independently. This server may be connected to one or "
        "more instances simultaneously, each identified by a name (e.g. 'dc-primary').\n\n"
        "**Blueprint**: A running data centre managed by an Apstra instance. Each instance "
        "can contain multiple blueprints. A blueprint represents a complete, deployed fabric "
        "— its devices, cabling, routing policy, and intent. When a tool asks for a "
        "`blueprint_id`, it refers to a specific data centre within a specific instance.\n\n"
        "**Relationship**: An installation with 3 instances each managing 3 blueprints "
        "gives 9 blueprints in total. Tools that accept `instance_name` work at the instance "
        "level; tools that accept `blueprint_id` work at the data centre level."
    ),
)

anomaly_timeline_tool.register(mcp)
anomalies_tool.register(mcp)
anomaly_analytics_tool.register(mcp)
bgp_tool.register(mcp)
blueprints_tool.register(mcp)
config_rendering_tool.register(mcp)
design_tool.register(mcp)
interfaces_tool.register(mcp)
links_tool.register(mcp)
mtu_check_tool.register(mcp)
reference_tool.register(mcp)
run_commands_tool.register(mcp)
systems_tool.register(mcp)
virtual_networks_tool.register(mcp)

if os.environ.get("MCP_VERBOSE"):
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    mcp.add_middleware(TimingMiddleware())
    mcp.add_middleware(LoggingMiddleware(include_payloads=True, max_payload_length=2000))

if __name__ == "__main__":
    transport = os.environ.get("MCP_TRANSPORT", "stdio")
    host = os.environ.get("MCP_HOST", "0.0.0.0")
    port = int(os.environ.get("MCP_PORT", "8000"))
    if transport == "http":
        mcp.run(transport="streamable-http", host=host, port=port)
    else:
        mcp.run()

