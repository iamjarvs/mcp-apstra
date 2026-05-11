import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from handlers.audit import (
    handle_get_audit_events,
    handle_get_audit_device_config,
    _summarise_events,
    _build_filter_expr,
    AUDIT_PRESETS,
)


def _run(coro):
    return asyncio.run(coro)


def _make_session(name="dc-primary"):
    session = MagicMock()
    session.name = name
    return session


# ── _build_filter_expr ────────────────────────────────────────────────────────

class TestBuildFilterExpr:
    def test_none_returns_none(self):
        assert _build_filter_expr(None) is None

    def test_empty_list_returns_none(self):
        assert _build_filter_expr([]) is None

    def test_single_type(self):
        expr = _build_filter_expr(["BlueprintCommit"])
        assert expr == 'type in ["BlueprintCommit"]'

    def test_multiple_types(self):
        expr = _build_filter_expr(["BlueprintCommit", "DeviceConfigChange"])
        assert expr == 'type in ["BlueprintCommit", "DeviceConfigChange"]'


# ── AUDIT_PRESETS ─────────────────────────────────────────────────────────────

class TestAuditPresets:
    def test_presets_defined(self):
        assert "blueprint_changes" in AUDIT_PRESETS
        assert "device_config" in AUDIT_PRESETS
        assert "auth" in AUDIT_PRESETS
        assert "system_mode" in AUDIT_PRESETS
        assert "all" in AUDIT_PRESETS

    def test_all_preset_is_none(self):
        assert AUDIT_PRESETS["all"] is None

    def test_blueprint_changes_contains_commit(self):
        assert "BlueprintCommit" in AUDIT_PRESETS["blueprint_changes"]

    def test_device_config_contains_device_config_change(self):
        assert "DeviceConfigChange" in AUDIT_PRESETS["device_config"]

    def test_auth_contains_login_logout(self):
        assert "Login" in AUDIT_PRESETS["auth"]
        assert "Logout" in AUDIT_PRESETS["auth"]


# ── _summarise_events ─────────────────────────────────────────────────────────

class TestSummariseEvents:
    def test_counts_by_type_user_and_result(self):
        events = [
            {"user": "admin",  "type": "BlueprintCommit",    "result": "Success"},
            {"user": "admin",  "type": "BlueprintCommit",    "result": "Success"},
            {"user": "netops", "type": "DeviceConfigChange", "result": "Success"},
            {"user": "admin",  "type": "Login",              "result": "Failure"},
        ]
        summary = _summarise_events(events)
        assert summary["by_user"]["admin"] == 3
        assert summary["by_user"]["netops"] == 1
        assert summary["by_type"]["BlueprintCommit"] == 2
        assert summary["by_type"]["DeviceConfigChange"] == 1
        assert summary["by_result"]["Success"] == 3
        assert summary["by_result"]["Failure"] == 1

    def test_handles_empty_events(self):
        summary = _summarise_events([])
        assert summary == {"by_user": {}, "by_type": {}, "by_result": {}}

    def test_falls_back_for_missing_fields(self):
        events = [{"user": "alice"}]  # no type or result
        summary = _summarise_events(events)
        assert summary["by_user"]["alice"] == 1
        assert summary["by_type"]["unknown"] == 1
        assert summary["by_result"]["unknown"] == 1


# ── handle_get_audit_events ───────────────────────────────────────────────────

class TestHandleGetAuditEvents:

    def _api_response(self, events, total_count=None, is_truncated=False):
        """Helper to build a realistic API response shape."""
        n = total_count if total_count is not None else len(events)
        return {
            "status": {
                "total_count": n,
                "is_truncated": is_truncated,
                "result_code": "successTruncated" if is_truncated else "successComplete",
            },
            "items": events,
            "total_count": n,
            "page": 1,
            "per_page": 200,
            "offset": 0,
        }

    def test_single_session_returns_flat_result(self):
        session = _make_session("dc-primary")
        events = [
            {"user": "admin", "type": "BlueprintCommit", "result": "Success",
             "timestamp": "2026-05-10T12:00:00Z"},
            {"user": "admin", "type": "Login", "result": "Success",
             "timestamp": "2026-05-10T11:00:00Z"},
        ]

        with patch(
            "handlers.audit.live_data_client.get_audit_events",
            new=AsyncMock(return_value=self._api_response(events)),
        ):
            result = _run(handle_get_audit_events([session], days_back=1))

        assert result["instance"] == "dc-primary"
        assert result["total_count"] == 2
        assert result["returned"] == 2
        assert result["has_more"] is False
        assert result["is_truncated"] is False
        assert result["summary"]["by_user"]["admin"] == 2
        assert "begin_time" in result
        assert "end_time" in result

    def test_events_sorted_newest_first(self):
        session = _make_session()
        events = [
            {"timestamp": "2026-05-10T10:00:00Z", "type": "Login", "result": "Success"},
            {"timestamp": "2026-05-10T12:00:00Z", "type": "Login", "result": "Success"},
            {"timestamp": "2026-05-10T11:00:00Z", "type": "Login", "result": "Success"},
        ]
        with patch(
            "handlers.audit.live_data_client.get_audit_events",
            new=AsyncMock(return_value=self._api_response(events)),
        ):
            result = _run(handle_get_audit_events([session], days_back=1))

        timestamps = [e["timestamp"] for e in result["events"]]
        assert timestamps == sorted(timestamps, reverse=True)

    def test_server_truncation_sets_has_more(self):
        session = _make_session()
        events = [
            {"timestamp": f"2026-05-{i:02d}T00:00:00Z", "type": "Login", "result": "Success"}
            for i in range(1, 6)
        ]
        # Server reports 500 total but only returned 5 (is_truncated=True)
        with patch(
            "handlers.audit.live_data_client.get_audit_events",
            new=AsyncMock(return_value=self._api_response(events, total_count=500, is_truncated=True)),
        ):
            result = _run(handle_get_audit_events([session], days_back=7))

        assert result["total_count"] == 500
        assert result["returned"] == 5
        assert result["has_more"] is True
        assert result["is_truncated"] is True

    def test_limit_caps_returned_events_and_sets_has_more(self):
        session = _make_session()
        events = [
            {"timestamp": f"2026-05-{i:02d}T00:00:00Z", "type": "Login", "result": "Success"}
            for i in range(1, 11)
        ]
        with patch(
            "handlers.audit.live_data_client.get_audit_events",
            new=AsyncMock(return_value=self._api_response(events, total_count=10)),
        ):
            result = _run(handle_get_audit_events([session], days_back=7, limit=5))

        # API paging should enforce page size; handler should not slice locally.
        assert result["returned"] == 10
        assert result["total_count"] == 10
        assert result["has_more"] is False

    def test_offset_and_limit_are_forwarded_to_api(self):
        session = _make_session()
        with patch(
            "handlers.audit.live_data_client.get_audit_events",
            new=AsyncMock(return_value=self._api_response([], total_count=0)),
        ) as mock_get:
            _run(handle_get_audit_events([session], days_back=7, limit=50, offset=150))

        kwargs = mock_get.call_args.kwargs
        assert kwargs["per_page"] == 50
        assert kwargs["offset"] == 150

    def test_next_offset_returned_when_more_pages_exist(self):
        session = _make_session()
        events = [
            {"timestamp": "2026-05-10T12:00:00Z", "type": "Login", "result": "Success"},
            {"timestamp": "2026-05-10T11:00:00Z", "type": "Login", "result": "Success"},
            {"timestamp": "2026-05-10T10:00:00Z", "type": "Login", "result": "Success"},
        ]
        raw = self._api_response(events, total_count=100)
        raw["offset"] = 30
        raw["per_page"] = 3

        with patch(
            "handlers.audit.live_data_client.get_audit_events",
            new=AsyncMock(return_value=raw),
        ):
            result = _run(handle_get_audit_events([session], days_back=7, limit=3, offset=30))

        assert result["offset"] == 30
        assert result["per_page"] == 3
        assert result["has_more"] is True
        assert result["next_offset"] == 33

    def test_filter_passed_to_live_data_client(self):
        session = _make_session()
        with patch(
            "handlers.audit.live_data_client.get_audit_events",
            new=AsyncMock(return_value=self._api_response([])),
        ) as mock_get:
            _run(handle_get_audit_events(
                [session],
                days_back=1,
                event_types=["BlueprintCommit", "BlueprintRevert"],
            ))

        call_kwargs = mock_get.call_args
        filter_expr = call_kwargs[1].get("filter_expr") or call_kwargs[0][3] if len(call_kwargs[0]) > 3 else call_kwargs[1]["filter_expr"]
        assert "BlueprintCommit" in filter_expr
        assert "BlueprintRevert" in filter_expr

    def test_event_types_none_passes_no_filter(self):
        session = _make_session()
        with patch(
            "handlers.audit.live_data_client.get_audit_events",
            new=AsyncMock(return_value=self._api_response([])),
        ) as mock_get:
            _run(handle_get_audit_events([session], days_back=1, event_types=None))

        call_kwargs = mock_get.call_args
        assert call_kwargs[1].get("filter_expr") is None

    def test_api_error_returns_error_dict(self):
        session = _make_session()
        with patch(
            "handlers.audit.live_data_client.get_audit_events",
            new=AsyncMock(side_effect=Exception("connection refused")),
        ):
            result = _run(handle_get_audit_events([session], days_back=1))

        assert "error" in result
        assert "connection refused" in result["error"]
        assert result["total_count"] == 0

    def test_multiple_sessions_returns_aggregated_result(self):
        sessions = [_make_session("dc-a"), _make_session("dc-b")]
        events_a = [{"user": "admin",  "type": "BlueprintCommit", "result": "Success",
                     "timestamp": "2026-05-10T12:00:00Z"}]
        events_b = [{"user": "netops", "type": "Login",           "result": "Success",
                     "timestamp": "2026-05-10T11:00:00Z"}]

        with patch(
            "handlers.audit.live_data_client.get_audit_events",
            new=AsyncMock(side_effect=[
                self._api_response(events_a),
                self._api_response(events_b),
            ]),
        ):
            result = _run(handle_get_audit_events(sessions, days_back=1))

        assert result["instance_count"] == 2
        assert result["total_events"] == 2
        assert len(result["results"]) == 2

    def test_days_back_reflected_in_result(self):
        session = _make_session()
        with patch(
            "handlers.audit.live_data_client.get_audit_events",
            new=AsyncMock(return_value=self._api_response([])),
        ):
            result = _run(handle_get_audit_events([session], days_back=30))

        assert result["days_back"] == 30


# ── tool registration ─────────────────────────────────────────────────────────


# ── handle_get_audit_device_config ───────────────────────────────────────────

class TestHandleGetAuditDeviceConfig:
    def test_returns_config_on_success(self):
        session = _make_session("dc-primary")
        config_payload = {"config": "interfaces {\n  et-0/0/0;\n}"}

        with patch(
            "handlers.audit.live_data_client.get_audit_device_config",
            new=AsyncMock(return_value=config_payload),
        ):
            result = _run(handle_get_audit_device_config(
                [session],
                device_id="sys-abc123",
                file_name="2026-05-11_config.txt",
            ))

        assert result["instance"] == "dc-primary"
        assert result["device_id"] == "sys-abc123"
        assert result["file_name"] == "2026-05-11_config.txt"
        assert result["config"] == config_payload

    def test_error_returned_gracefully(self):
        session = _make_session()
        with patch(
            "handlers.audit.live_data_client.get_audit_device_config",
            new=AsyncMock(side_effect=Exception("not found")),
        ):
            result = _run(handle_get_audit_device_config(
                [session],
                device_id="missing-device",
                file_name="no-such-file.txt",
            ))

        assert "error" in result
        assert "not found" in result["error"]
        assert result["device_id"] == "missing-device"

    def test_passes_correct_args_to_live_data_client(self):
        session = _make_session()
        with patch(
            "handlers.audit.live_data_client.get_audit_device_config",
            new=AsyncMock(return_value={}),
        ) as mock_get:
            _run(handle_get_audit_device_config(
                [session],
                device_id="dev-xyz",
                file_name="cfg-file.txt",
            ))

        mock_get.assert_awaited_once_with(
            session,
            device_id="dev-xyz",
            file_name="cfg-file.txt",
        )

    def test_instance_name_selects_correct_session(self):
        sessions = [_make_session("dc-a"), _make_session("dc-b")]
        with patch(
            "handlers.audit.live_data_client.get_audit_device_config",
            new=AsyncMock(return_value={}),
        ) as mock_get:
            result = _run(handle_get_audit_device_config(
                sessions,
                device_id="dev-1",
                file_name="cfg.txt",
                instance_name="dc-b",
            ))

        assert result["instance"] == "dc-b"
        called_session = mock_get.call_args[0][0]
        assert called_session.name == "dc-b"


# ── get_device_audit_config tool ──────────────────────────────────────────────

class TestDeviceAuditConfigTool:
    def _make_stub_mcp(self):
        class StubMCP:
            def __init__(self):
                self.tools = {}
            def tool(self):
                def decorator(fn):
                    self.tools[fn.__name__] = fn
                    return fn
                return decorator
        return StubMCP()

    def test_tool_registered(self):
        from tools.audit import register
        stub = self._make_stub_mcp()
        register(stub)
        assert "get_device_audit_config" in stub.tools

    def test_tool_dispatches_to_handler(self):
        from tools.audit import register
        stub = self._make_stub_mcp()
        register(stub)
        tool = stub.tools["get_device_audit_config"]

        ctx = MagicMock()
        ctx.lifespan_context = {"sessions": [_make_session()]}

        with patch(
            "tools.audit.handle_get_audit_device_config",
            new=AsyncMock(return_value={"instance": "dc-primary", "config": {}}),
        ) as mock_handler:
            result = _run(tool(
                device_id="dev-abc",
                file_name="cfg.txt",
                ctx=ctx,
            ))

        mock_handler.assert_awaited_once_with(
            ctx.lifespan_context["sessions"],
            device_id="dev-abc",
            file_name="cfg.txt",
            instance_name=None,
        )
        assert result["config"] == {}


# ── get_audit_log tool ────────────────────────────────────────────────────────

class TestAuditTool:
    def _make_stub_mcp(self):
        class StubMCP:
            def __init__(self):
                self.tools = {}
            def tool(self):
                def decorator(fn):
                    self.tools[fn.__name__] = fn
                    return fn
                return decorator
        return StubMCP()

    def test_tool_registers_get_audit_log(self):
        from tools.audit import register
        stub = self._make_stub_mcp()
        register(stub)
        assert "get_audit_log" in stub.tools

    def test_preset_resolves_to_event_types(self):
        from tools.audit import register
        stub = self._make_stub_mcp()
        register(stub)
        tool = stub.tools["get_audit_log"]

        ctx = MagicMock()
        ctx.lifespan_context = {"sessions": [_make_session()]}

        with patch(
            "tools.audit.handle_get_audit_events",
            new=AsyncMock(return_value={"total_count": 0, "events": []}),
        ) as mock_handler:
            _run(tool(preset="blueprint_changes", ctx=ctx))

        called_types = mock_handler.call_args[1]["event_types"]
        assert "BlueprintCommit" in called_types
        assert mock_handler.call_args[1]["offset"] == 0

    def test_explicit_event_types_overrides_preset(self):
        from tools.audit import register
        stub = self._make_stub_mcp()
        register(stub)
        tool = stub.tools["get_audit_log"]

        ctx = MagicMock()
        ctx.lifespan_context = {"sessions": [_make_session()]}

        with patch(
            "tools.audit.handle_get_audit_events",
            new=AsyncMock(return_value={"total_count": 0, "events": []}),
        ) as mock_handler:
            _run(tool(preset="auth", event_types=["DeviceConfigChange"], ctx=ctx))

        called_types = mock_handler.call_args[1]["event_types"]
        assert called_types == ["DeviceConfigChange"]

    def test_all_preset_passes_none_filter(self):
        from tools.audit import register
        stub = self._make_stub_mcp()
        register(stub)
        tool = stub.tools["get_audit_log"]

        ctx = MagicMock()
        ctx.lifespan_context = {"sessions": [_make_session()]}

        with patch(
            "tools.audit.handle_get_audit_events",
            new=AsyncMock(return_value={"total_count": 0, "events": []}),
        ) as mock_handler:
            _run(tool(preset="all", ctx=ctx))

        called_types = mock_handler.call_args[1]["event_types"]
        assert called_types is None

    def test_legacy_tool_offset_is_forwarded(self):
        from tools.audit import register
        stub = self._make_stub_mcp()
        register(stub)
        tool = stub.tools["get_audit_log"]

        ctx = MagicMock()
        ctx.lifespan_context = {"sessions": [_make_session()]}

        with patch(
            "tools.audit.handle_get_audit_events",
            new=AsyncMock(return_value={"total_count": 0, "events": []}),
        ) as mock_handler:
            _run(tool(limit=25, offset=75, ctx=ctx))

        assert mock_handler.call_args[1]["limit"] == 25
        assert mock_handler.call_args[1]["offset"] == 75


class TestAuditUmbrellaTool:
    def _make_stub_mcp(self):
        class StubMCP:
            def __init__(self):
                self.tools = {}

            def tool(self):
                def decorator(fn):
                    self.tools[fn.__name__] = fn
                    return fn

                return decorator

        return StubMCP()

    def test_compact_registration_exposes_only_umbrella(self):
        from tools.audit import register

        stub = self._make_stub_mcp()
        register(stub, include_legacy=False)

        assert "audit" in stub.tools
        assert "get_audit_log" not in stub.tools
        assert "get_device_audit_config" not in stub.tools

    def test_events_intent_dispatches_to_event_handler(self):
        from tools.audit import register

        stub = self._make_stub_mcp()
        register(stub, include_legacy=False)
        tool = stub.tools["audit"]

        ctx = MagicMock()
        ctx.lifespan_context = {"sessions": [_make_session()]}

        with patch(
            "tools.audit.handle_get_audit_events",
            new=AsyncMock(return_value={"total_count": 3, "events": []}),
        ) as mock_handler:
            result = _run(
                tool(
                    intent="events",
                    preset="device_config",
                    days_back=7,
                    limit=50,
                    offset=20,
                    ctx=ctx,
                )
            )

        mock_handler.assert_awaited_once()
        assert mock_handler.call_args[1]["offset"] == 20
        assert result["total_count"] == 3

    def test_device_config_intent_requires_identifiers(self):
        from tools.audit import register

        stub = self._make_stub_mcp()
        register(stub, include_legacy=False)
        tool = stub.tools["audit"]

        ctx = MagicMock()
        ctx.lifespan_context = {"sessions": [_make_session()]}

        result = _run(tool(intent="device_config", ctx=ctx))
        assert "error" in result
        assert "requires both device_id and file_name" in result["error"]

    def test_device_config_intent_dispatches_to_config_handler(self):
        from tools.audit import register

        stub = self._make_stub_mcp()
        register(stub, include_legacy=False)
        tool = stub.tools["audit"]

        ctx = MagicMock()
        ctx.lifespan_context = {"sessions": [_make_session()]}

        with patch(
            "tools.audit.handle_get_audit_device_config",
            new=AsyncMock(return_value={"config": {"text": "set interfaces"}}),
        ) as mock_handler:
            result = _run(
                tool(
                    intent="device_config",
                    device_id="dev-1",
                    file_name="cfg-1.txt",
                    ctx=ctx,
                )
            )

        mock_handler.assert_awaited_once_with(
            ctx.lifespan_context["sessions"],
            device_id="dev-1",
            file_name="cfg-1.txt",
            instance_name=None,
        )
        assert "config" in result
