from unittest.mock import patch

from config.settings import RagConfig

import server


class StubMCP:
    def __init__(self):
        self.tools: dict = {}
        self.resources: dict = {}

    def tool(self):
        def decorator(fn):
            self.tools[fn.__name__] = fn
            return fn

        return decorator

    def resource(self, uri):
        def decorator(fn):
            self.resources[uri] = fn
            return fn

        return decorator


def test_resolve_tool_surface_defaults_to_compact_when_unset():
    with patch.dict("os.environ", {}, clear=True):
        assert server._resolve_tool_surface() == "compact"


def test_resolve_tool_surface_accepts_full_and_rejects_unknown():
    assert server._resolve_tool_surface("full") == "full"
    assert server._resolve_tool_surface("compact") == "compact"
    assert server._resolve_tool_surface("not-real") == "compact"


def test_register_tools_compact_exposes_umbrellas_and_hides_granular_clusters():
    stub = StubMCP()
    surface = server._register_tools(stub, tool_surface="compact")

    assert surface == "compact"
    assert "anomaly" in stub.tools
    assert "telemetry" in stub.tools
    assert "virtual_networks" in stub.tools
    assert "probes" in stub.tools

    # Clustered granular tools are hidden in compact mode.
    assert "get_anomaly_summary" not in stub.tools
    assert "get_interface_counters" not in stub.tools
    assert "get_virtual_networks" not in stub.tools
    assert "get_probe_list" not in stub.tools

    # Core standalone tools remain.
    assert "get_active_system_agent_jobs" in stub.tools
    assert "get_system_liveness" in stub.tools
    assert "get_blueprints" in stub.tools
    assert "routing_policy" in stub.tools
    assert "run_device_commands" in stub.tools
    assert "triage" in stub.tools
    assert "audit" in stub.tools
    assert "get_audit_log" not in stub.tools
    assert "get_device_audit_config" not in stub.tools
    assert len(stub.tools) == 32


def test_register_tools_full_exposes_umbrellas_and_granular_tools():
    stub = StubMCP()
    surface = server._register_tools(stub, tool_surface="full")

    assert surface == "full"
    assert "anomaly" in stub.tools
    assert "telemetry" in stub.tools
    assert "virtual_networks" in stub.tools
    assert "probes" in stub.tools

    assert "get_anomaly_summary" in stub.tools
    assert "get_interface_counters" in stub.tools
    assert "get_virtual_networks" in stub.tools
    assert "get_probe_list" in stub.tools
    assert "audit" in stub.tools
    assert "get_audit_log" in stub.tools
    assert "get_device_audit_config" in stub.tools
    assert len(stub.tools) == 57


def test_register_tools_includes_query_apstra_product_docs_when_rag_enabled():
    stub = StubMCP()
    rag_cfg = RagConfig(
        enabled=True,
        embedding_provider="ollama",
        embedding_model="nomic-embed-text",
        embedding_url="http://localhost:11434/api/embeddings",
        top_k=5,
    )

    server._register_tools(stub, tool_surface="compact", rag_config=rag_cfg)
    assert "query_apstra_product_docs" in stub.tools


def test_register_tools_skips_query_apstra_product_docs_when_rag_disabled():
    stub = StubMCP()

    server._register_tools(stub, tool_surface="compact", rag_config=None)
    assert "query_apstra_product_docs" not in stub.tools


def test_register_tools_does_not_hard_stop_when_rag_config_is_invalid():
    stub = StubMCP()

    with patch("server.get_rag_config", side_effect=ValueError("bad rag config")):
        surface = server._register_tools(stub, tool_surface="compact", rag_config=None)

    assert surface == "compact"
    assert "query_apstra_product_docs" not in stub.tools
    assert "triage" in stub.tools
