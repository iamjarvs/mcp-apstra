import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from tools.triage_dispatch import register


def _run(coro):
    return asyncio.run(coro)


class StubMCP:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def decorator(fn):
            self.tools[fn.__name__] = fn
            return fn
        return decorator


def _make_session(name="dc-primary"):
    s = MagicMock()
    s.name = name
    return s


def _make_ctx(sessions=None, anomaly_store=None):
    ctx = MagicMock()
    ctx.lifespan_context = {
        "sessions":      sessions or [_make_session()],
        "graph_registry": MagicMock(),
        "anomaly_store": anomaly_store,
    }
    return ctx


_SINGLE_BP = [{"id": "bp-1", "label": "DC1", "instance_name": "dc-primary"}]
_MULTI_BP  = [
    {"id": "bp-1", "label": "DC1", "instance_name": "dc-primary"},
    {"id": "bp-2", "label": "DC2", "instance_name": "dc-primary"},
]


# ── baseline intent ───────────────────────────────────────────────────────────

class TestTriageBaseline:
    def test_single_blueprint_all_reachable(self):
        mcp = StubMCP()
        register(mcp)
        tool = mcp.tools["triage"]
        ctx = _make_ctx()

        active_jobs_result = {
            "instance": "dc-primary",
            "has_active_jobs": True,
            "active_job_count": 1,
            "active_jobs": [
                {
                    "job_id": 10,
                    "job_type": "upgrade",
                    "state": "inprogress",
                    "device": {"hostname": "Leaf1"},
                    "blueprint_matches": [
                        {"blueprint_id": "bp-1", "blueprint_label": "DC1"},
                    ],
                    "blueprint_match_count": 1,
                }
            ],
            "summary": {
                "by_job_type": {"upgrade": 1},
                "by_state": {"inprogress": 1},
                "impacted_device_count": 1,
                "impacted_blueprint_count": 1,
                "impacted_blueprints": ["DC1"],
            },
        }

        with patch("tools.triage_dispatch.resolve_blueprints",
                   new=AsyncMock(return_value=_SINGLE_BP)):
            with patch("tools.triage_dispatch.handle_get_system_liveness",
                       new=AsyncMock(return_value={
                           "all_systems_reachable": True,
                           "unreachable_count": 0,
                           "liveness_anomalies": [],
                       })):
                with patch("tools.triage_dispatch.handle_get_active_system_agent_jobs",
                           new=AsyncMock(return_value=active_jobs_result)):
                    result = _run(tool(intent="baseline", blueprint_id="bp-1", ctx=ctx))

        assert result["intent"] == "baseline"
        assert result["all_systems_reachable"] is True
        assert result["blueprint_label"] == "DC1"
        assert result["active_job_count"] == 1
        assert result["has_active_jobs"] is True
        assert result["active_jobs"]["active_jobs"][0]["job_id"] == 10

    def test_no_blueprints_returns_error(self):
        mcp = StubMCP()
        register(mcp)
        tool = mcp.tools["triage"]
        ctx = _make_ctx()

        with patch("tools.triage_dispatch.resolve_blueprints",
                   new=AsyncMock(return_value=[])):
            result = _run(tool(intent="baseline", blueprint_id="missing", ctx=ctx))

        assert "error" in result

    def test_multi_blueprint_aggregates_unreachable(self):
        mcp = StubMCP()
        register(mcp)
        tool = mcp.tools["triage"]
        ctx = _make_ctx()

        side_effects = [
            {"all_systems_reachable": False, "unreachable_count": 1, "liveness_anomalies": []},
            {"all_systems_reachable": True,  "unreachable_count": 0, "liveness_anomalies": []},
        ]
        active_jobs_result = {
            "instance": "dc-primary",
            "has_active_jobs": True,
            "active_job_count": 2,
            "active_jobs": [
                {
                    "job_id": 1,
                    "job_type": "upgrade",
                    "state": "inprogress",
                    "device": {"hostname": "leaf1"},
                    "blueprint_matches": [
                        {"blueprint_id": "bp-1", "blueprint_label": "DC1"},
                    ],
                    "blueprint_match_count": 1,
                },
                {
                    "job_id": 2,
                    "job_type": "reboot",
                    "state": "inprogress",
                    "device": {"hostname": "leaf2"},
                    "blueprint_matches": [
                        {"blueprint_id": "bp-2", "blueprint_label": "DC2"},
                    ],
                    "blueprint_match_count": 1,
                },
            ],
            "summary": {
                "by_job_type": {"upgrade": 1, "reboot": 1},
                "by_state": {"inprogress": 2},
                "impacted_device_count": 2,
                "impacted_blueprint_count": 2,
                "impacted_blueprints": ["DC1", "DC2"],
            },
        }

        with patch("tools.triage_dispatch.resolve_blueprints",
                   new=AsyncMock(return_value=_MULTI_BP)):
            with patch("tools.triage_dispatch.handle_get_system_liveness",
                       new=AsyncMock(side_effect=side_effects)):
                with patch("tools.triage_dispatch.handle_get_active_system_agent_jobs",
                           new=AsyncMock(return_value=active_jobs_result)):
                    result = _run(tool(intent="baseline", blueprint_id="all", ctx=ctx))

        assert result["blueprint_count"] == 2
        assert result["total_unreachable"] == 1
        assert result["total_active_jobs"] == 2
        assert result["has_active_jobs"] is True
        assert result["all_systems_reachable"] is False


# ── active_jobs intent ───────────────────────────────────────────────────────

class TestTriageActiveJobs:
    def test_returns_jobs_scoped_to_requested_blueprint(self):
        mcp = StubMCP()
        register(mcp)
        tool = mcp.tools["triage"]
        ctx = _make_ctx()

        active_jobs_result = {
            "instance": "dc-primary",
            "has_active_jobs": True,
            "active_job_count": 2,
            "active_jobs": [
                {
                    "job_id": 1,
                    "job_type": "upgrade",
                    "state": "inprogress",
                    "device": {"hostname": "leaf1"},
                    "blueprint_matches": [
                        {"blueprint_id": "bp-1", "blueprint_label": "DC1"},
                    ],
                    "blueprint_match_count": 1,
                },
                {
                    "job_id": 2,
                    "job_type": "upgrade",
                    "state": "inprogress",
                    "device": {"hostname": "leaf2"},
                    "blueprint_matches": [
                        {"blueprint_id": "bp-2", "blueprint_label": "DC2"},
                    ],
                    "blueprint_match_count": 1,
                },
            ],
            "summary": {
                "by_job_type": {"upgrade": 2},
                "by_state": {"inprogress": 2},
                "impacted_device_count": 2,
                "impacted_blueprint_count": 2,
                "impacted_blueprints": ["DC1", "DC2"],
            },
        }

        with patch("tools.triage_dispatch.resolve_blueprints",
                   new=AsyncMock(return_value=_SINGLE_BP)):
            with patch("tools.triage_dispatch.handle_get_active_system_agent_jobs",
                       new=AsyncMock(return_value=active_jobs_result)):
                result = _run(tool(intent="active_jobs", blueprint_id="bp-1", ctx=ctx))

        assert result["intent"] == "active_jobs"
        assert result["blueprint_ref"] == "bp-1"
        assert result["active_job_count"] == 1
        assert result["has_active_jobs"] is True
        assert len(result["active_jobs"]) == 1
        assert result["active_jobs"][0]["job_id"] == 1
        assert result["summary"]["impacted_blueprints"] == ["DC1"]


# ── commit_blockers intent ────────────────────────────────────────────────────

class TestTriageCommitBlockers:
    def test_returns_build_error_details(self):
        mcp = StubMCP()
        register(mcp)
        tool = mcp.tools["triage"]
        ctx = _make_ctx()

        build_result = {
            "errors_count": 2,
            "warnings_count": 1,
            "has_blocking_errors": True,
            "issues": [{"message": "Bad policy config"}],
        }

        with patch("tools.triage_dispatch.resolve_blueprints",
                   new=AsyncMock(return_value=_SINGLE_BP)):
            with patch("tools.triage_dispatch.handle_get_blueprint_build_errors",
                       new=AsyncMock(return_value=build_result)):
                result = _run(tool(intent="commit_blockers", blueprint_id="bp-1", ctx=ctx))

        assert result["intent"] == "commit_blockers"
        assert result["has_blocking_errors"] is True
        assert result["errors_count"] == 2


# ── drift intent ──────────────────────────────────────────────────────────────

class TestTriageDrift:
    def test_returns_deviated_count(self):
        mcp = StubMCP()
        register(mcp)
        tool = mcp.tools["triage"]
        ctx = _make_ctx()

        drift_result = {"deviated_count": 1, "systems": []}

        with patch("tools.triage_dispatch.resolve_blueprints",
                   new=AsyncMock(return_value=_SINGLE_BP)):
            with patch("tools.triage_dispatch.handle_get_config_deviations",
                       new=AsyncMock(return_value=drift_result)):
                result = _run(tool(intent="drift", blueprint_id="bp-1", ctx=ctx))

        assert result["intent"] == "drift"
        assert result["deviated_count"] == 1


# ── active_anomalies intent ───────────────────────────────────────────────────

class TestTriageActiveAnomalies:
    def test_returns_summary_when_store_available(self):
        store = MagicMock()
        store.get_currently_active.return_value = [
            {"anomaly_type": "bgp",  "device": "Leaf1"},
            {"anomaly_type": "bgp",  "device": "Leaf2"},
            {"anomaly_type": "cabling", "device": "Spine1"},
        ]
        mcp = StubMCP()
        register(mcp)
        tool = mcp.tools["triage"]
        ctx = _make_ctx(anomaly_store=store)

        with patch("tools.triage_dispatch.resolve_blueprints",
                   new=AsyncMock(return_value=_SINGLE_BP)):
            result = _run(tool(intent="active_anomalies", blueprint_id="bp-1", ctx=ctx))

        assert result["intent"] == "active_anomalies"
        assert result["available"] is True
        assert result["active_count"] == 3
        assert result["by_type"]["bgp"] == 2

    def test_returns_unavailable_when_store_is_none(self):
        mcp = StubMCP()
        register(mcp)
        tool = mcp.tools["triage"]
        ctx = _make_ctx(anomaly_store=None)

        with patch("tools.triage_dispatch.resolve_blueprints",
                   new=AsyncMock(return_value=_SINGLE_BP)):
            result = _run(tool(intent="active_anomalies", blueprint_id="bp-1", ctx=ctx))

        assert result["available"] is False
        assert "hint" in result


# ── incident_snapshot intent ──────────────────────────────────────────────────

class TestTriageIncidentSnapshot:
    def test_parallel_results_aggregated(self):
        store = MagicMock()
        store.get_currently_active.return_value = [
            {"anomaly_type": "bgp", "device": "Leaf1"},
        ]
        mcp = StubMCP()
        register(mcp)
        tool = mcp.tools["triage"]
        ctx = _make_ctx(anomaly_store=store)

        liveness_result = {
            "all_systems_reachable": True,
            "unreachable_count": 0,
            "liveness_anomalies": [],
        }
        build_result = {
            "errors_count": 0,
            "warnings_count": 0,
            "has_blocking_errors": False,
            "issues": [],
        }
        active_jobs_result = {
            "instance": "all",
            "results": [
                {
                    "instance": "dc-primary",
                    "has_active_jobs": True,
                    "active_job_count": 1,
                    "active_jobs": [
                        {
                            "job_id": 10,
                            "job_type": "upgrade",
                            "state": "inprogress",
                            "device": {"hostname": "Leaf1"},
                            "blueprint_matches": [
                                {"blueprint_id": "bp-1", "blueprint_label": "DC1"},
                            ],
                            "blueprint_match_count": 1,
                        }
                    ],
                    "summary": {
                        "by_job_type": {"upgrade": 1},
                        "by_state": {"inprogress": 1},
                        "impacted_device_count": 1,
                        "impacted_blueprint_count": 1,
                        "impacted_blueprints": ["DC1"],
                    },
                }
            ],
            "total_active_jobs": 1,
            "has_active_jobs": True,
        }

        with patch("tools.triage_dispatch.resolve_blueprints",
                   new=AsyncMock(return_value=_SINGLE_BP)):
            with patch("tools.triage_dispatch.handle_get_system_liveness",
                       new=AsyncMock(return_value=liveness_result)):
                with patch("tools.triage_dispatch.handle_get_blueprint_build_errors",
                           new=AsyncMock(return_value=build_result)):
                    with patch("tools.triage_dispatch.handle_get_active_system_agent_jobs",
                               new=AsyncMock(return_value=active_jobs_result)):
                        result = _run(
                            tool(intent="incident_snapshot", blueprint_id="bp-1", ctx=ctx)
                        )

        assert result["intent"] == "incident_snapshot"
        assert result["blueprint_count"] == 1
        snap = result["results"][0]
        assert snap["unreachable_count"] == 0
        assert snap["active_job_count"] == 1
        assert snap["active_anomaly_count"] == 1
        assert snap["has_blocking_errors"] is False
        assert snap["needs_attention"] is True  # because active anomalies > 0

    def test_snapshot_marks_needs_attention_on_unreachable(self):
        store = MagicMock()
        store.get_currently_active.return_value = []
        mcp = StubMCP()
        register(mcp)
        tool = mcp.tools["triage"]
        ctx = _make_ctx(anomaly_store=store)

        liveness_result = {
            "all_systems_reachable": False,
            "unreachable_count": 2,
            "liveness_anomalies": [],
        }
        build_result = {
            "errors_count": 0, "warnings_count": 0,
            "has_blocking_errors": False, "issues": [],
        }
        active_jobs_result = {
            "instance": "dc-primary",
            "has_active_jobs": False,
            "active_job_count": 0,
            "active_jobs": [],
            "summary": {
                "by_job_type": {},
                "by_state": {},
                "impacted_device_count": 0,
                "impacted_blueprint_count": 0,
                "impacted_blueprints": [],
            },
        }

        with patch("tools.triage_dispatch.resolve_blueprints",
                   new=AsyncMock(return_value=_SINGLE_BP)):
            with patch("tools.triage_dispatch.handle_get_system_liveness",
                       new=AsyncMock(return_value=liveness_result)):
                with patch("tools.triage_dispatch.handle_get_blueprint_build_errors",
                           new=AsyncMock(return_value=build_result)):
                    with patch("tools.triage_dispatch.handle_get_active_system_agent_jobs",
                               new=AsyncMock(return_value=active_jobs_result)):
                        result = _run(
                            tool(intent="incident_snapshot", blueprint_id="bp-1", ctx=ctx)
                        )

        snap = result["results"][0]
        assert snap["needs_attention"] is True
        assert snap["unreachable_count"] == 2


# ── tool surface test ─────────────────────────────────────────────────────────

class TestToolSurface:
    def test_triage_registered_as_tool(self):
        mcp = StubMCP()
        register(mcp)
        assert "triage" in mcp.tools
