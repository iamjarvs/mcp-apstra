import asyncio

import httpx
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from primitives.live_data_client import (
    submit_fetchcmd_multiple,
    submit_fetchcmd_single,
    poll_fetchcmd,
    delete_fetchcmd,
)
from handlers.run_commands import (
    _poll_until_done,
    _run_single_command,
    _run_on_system,
    handle_run_commands,
    _select_sessions,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_session(name="dc-primary"):
    session = MagicMock()
    session.name = name
    session.host = "https://apstra.example.com"
    session._ssl_verify = False
    session.get_token = AsyncMock(return_value="tok123")
    return session


def make_registry(rows=None, error=None):
    registry = MagicMock()
    graph = MagicMock()
    if error:
        registry.get_or_rebuild = AsyncMock(side_effect=error)
    else:
        graph.query = MagicMock(return_value=rows or [])
        registry.get_or_rebuild = AsyncMock(return_value=graph)
    return registry


def make_http_error(status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://apstra.example.com/api/telemetry/fetchcmd/multiple")
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError(
        f"HTTP {status_code}",
        request=request,
        response=response,
    )


SYSTEMS_ROWS = [
    {"sw.system_id": "SYS001", "sw.label": "Leaf1"},
    {"sw.system_id": "SYS002", "sw.label": "Leaf2"},
]


# ---------------------------------------------------------------------------
# live_data_client — unit tests for new primitives
# ---------------------------------------------------------------------------

class TestLiveDataClientPrimitives:
    async def test_submit_fetchcmd_multiple_builds_correct_body(self):
        session = make_session()
        with patch("primitives.live_data_client._request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {"request_ids": {"show version": "req-abc"}}
            result = await submit_fetchcmd_multiple(session, "SYS001", ["show version"], "json")

        assert result == {"show version": "req-abc"}
        mock_req.assert_called_once_with(
            session,
            "POST",
            "/api/telemetry/fetchcmd/multiple",
            body={
                "system_id": "SYS001",
                "commands": [{"format": "json", "text": "show version"}],
            },
        )

    async def test_submit_fetchcmd_multiple_json_format(self):
        session = make_session()
        with patch("primitives.live_data_client._request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {"request_ids": {"show version": "req-v", "show bgp": "req-b"}}
            result = await submit_fetchcmd_multiple(
                session, "SYS001", ["show version", "show bgp"], "json"
            )

        assert result == {"show version": "req-v", "show bgp": "req-b"}
        body = mock_req.call_args.kwargs["body"]
        assert body["commands"][0]["format"] == "json"
        assert body["commands"][1]["text"] == "show bgp"

    async def test_submit_fetchcmd_single_builds_correct_body(self):
        session = make_session()
        with patch("primitives.live_data_client._request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {"request_id": "req-single-1"}
            result = await submit_fetchcmd_single(session, "SYS001", "show interfaces", "json")

        assert result == "req-single-1"
        mock_req.assert_called_once_with(
            session,
            "POST",
            "/api/telemetry/fetchcmd",
            body={
                "system_id": "SYS001",
                "command_text": "show interfaces",
                "output_format": "json",
            },
        )

    async def test_poll_fetchcmd_calls_get(self):
        session = make_session()
        with patch("primitives.live_data_client._request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {"result": "success", "output": "Junos 21.4"}
            result = await poll_fetchcmd(session, "req-abc")

        assert result["result"] == "success"
        mock_req.assert_called_once_with(
            session, "GET", "/api/telemetry/fetchcmd/req-abc"
        )

    async def test_delete_fetchcmd_calls_delete(self):
        session = make_session()
        with patch("primitives.live_data_client._request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {}
            await delete_fetchcmd(session, "req-abc")

        mock_req.assert_called_once_with(
            session, "DELETE", "/api/telemetry/fetchcmd/req-abc"
        )


# ---------------------------------------------------------------------------
# _poll_until_done
# ---------------------------------------------------------------------------

class TestPollUntilDone:
    async def test_returns_immediately_on_success(self):
        session = make_session()
        with patch("handlers.run_commands.live_data_client.poll_fetchcmd",
                   new_callable=AsyncMock) as mock_poll:
            mock_poll.return_value = {"status": "success", "output": "hello"}
            result = await _poll_until_done(session, "req-1", timeout_seconds=10)

        assert result["status"] == "success"
        assert mock_poll.call_count == 1

    async def test_polls_multiple_times_before_success(self):
        session = make_session()
        responses = [
            {"status": "inprogress"},
            {"status": "inprogress"},
            {"status": "success", "output": "done"},
        ]
        with patch("handlers.run_commands.live_data_client.poll_fetchcmd",
                   new_callable=AsyncMock) as mock_poll:
            with patch("handlers.run_commands.asyncio.sleep", new_callable=AsyncMock):
                mock_poll.side_effect = responses
                result = await _poll_until_done(session, "req-1", timeout_seconds=30)

        assert result["status"] == "success"
        assert mock_poll.call_count == 3

    async def test_returns_timeout_when_deadline_exceeded(self):
        session = make_session()
        with patch("handlers.run_commands.live_data_client.poll_fetchcmd",
                   new_callable=AsyncMock) as mock_poll:
            with patch("handlers.run_commands.asyncio.sleep", new_callable=AsyncMock):
                with patch("handlers.run_commands.time.monotonic",
                           side_effect=[0.0, 0.0, 100.0]):
                    mock_poll.return_value = {"result": "inprogress"}
                    result = await _poll_until_done(session, "req-1", timeout_seconds=5)

        assert result["result"] == "timeout"
        assert result["request_id"] == "req-1"

    async def test_in_progress_variants_all_keep_polling(self):
        """Various 'still running' status strings should continue the poll loop."""
        session = make_session()
        for running_status in ("in_progress", "pending", "running", "queued"):
            responses = [{"status": running_status}, {"status": "success"}]
            with patch("handlers.run_commands.live_data_client.poll_fetchcmd",
                       new_callable=AsyncMock) as mock_poll:
                with patch("handlers.run_commands.asyncio.sleep", new_callable=AsyncMock):
                    mock_poll.side_effect = responses
                    result = await _poll_until_done(session, "req-x", timeout_seconds=30)
            assert result["status"] == "success", f"failed for status '{running_status}'"

    async def test_unknown_status_treated_as_terminal(self):
        session = make_session()
        with patch("handlers.run_commands.live_data_client.poll_fetchcmd",
                   new_callable=AsyncMock) as mock_poll:
            mock_poll.return_value = {"status": "done"}
            result = await _poll_until_done(session, "req-1", timeout_seconds=10)

        assert result["status"] == "done"
        assert mock_poll.call_count == 1


# ---------------------------------------------------------------------------
# _run_single_command
# ---------------------------------------------------------------------------

class TestRunSingleCommand:
    async def test_submits_polls_deletes_and_returns_result(self):
        session = make_session()
        with patch("handlers.run_commands.live_data_client.submit_fetchcmd_single",
                   new_callable=AsyncMock, return_value="req-s1") as mock_submit:
            with patch("handlers.run_commands.live_data_client.poll_fetchcmd",
                       new_callable=AsyncMock,
                       return_value={"result": "success", "output": "Junos 21.4"}) as mock_poll:
                with patch("handlers.run_commands.live_data_client.delete_fetchcmd",
                           new_callable=AsyncMock) as mock_delete:
                    result = await _run_single_command(
                        session, "SYS001", "show version", 30, "json"
                    )

        assert result["command"] == "show version"
        assert result["result"] == "success"
        assert result["output"] == "Junos 21.4"
        mock_submit.assert_called_once_with(session, "SYS001", "show version", "json")
        mock_delete.assert_called_once_with(session, "req-s1")

    async def test_cleanup_happens_even_on_timeout(self):
        session = make_session()
        with patch("handlers.run_commands.live_data_client.submit_fetchcmd_single",
                   new_callable=AsyncMock, return_value="req-timeout"):
            with patch("handlers.run_commands._poll_until_done",
                       new_callable=AsyncMock,
                       return_value={"result": "timeout", "request_id": "req-timeout"}):
                with patch("handlers.run_commands.live_data_client.delete_fetchcmd",
                           new_callable=AsyncMock) as mock_delete:
                    result = await _run_single_command(
                        session, "SYS001", "show route", 5, "json"
                    )

        assert result["result"] == "timeout"
        mock_delete.assert_called_once_with(session, "req-timeout")

    async def test_delete_failure_does_not_raise(self):
        session = make_session()
        with patch("handlers.run_commands.live_data_client.submit_fetchcmd_single",
                   new_callable=AsyncMock, return_value="req-del-fail"):
            with patch("handlers.run_commands.live_data_client.poll_fetchcmd",
                       new_callable=AsyncMock,
                       return_value={"result": "success", "output": "ok"}):
                with patch("handlers.run_commands.live_data_client.delete_fetchcmd",
                           new_callable=AsyncMock, side_effect=Exception("delete failed")):
                    result = await _run_single_command(
                        session, "SYS001", "show version", 30, "json"
                    )

        assert result["result"] == "success"  # delete error did not propagate


# ---------------------------------------------------------------------------
# _run_on_system
# ---------------------------------------------------------------------------

class TestRunOnSystem:
    async def test_uses_multiple_endpoint_when_available(self):
        session = make_session()
        # Multiple endpoint returns {command: uuid} — one uuid per command
        with patch("handlers.run_commands.live_data_client.submit_fetchcmd_multiple",
                   new_callable=AsyncMock,
                   return_value={"show version": "req-m1"}):
            with patch("handlers.run_commands._poll_until_done",
                       new_callable=AsyncMock,
                       return_value={"result": "success", "output": "v21"}):
                with patch("handlers.run_commands._safe_delete", new_callable=AsyncMock):
                    result = await _run_on_system(
                        session, "SYS001", "Leaf1", ["show version"], 30, "json"
                    )

        assert result["system_id"] == "SYS001"
        assert result["endpoint"] == "multiple"
        assert result["status"] == "success"
        assert len(result["command_results"]) == 1
        assert result["command_results"][0]["command"] == "show version"
        assert result["command_results"][0]["result"] == "success"

    async def test_multiple_endpoint_polls_each_command_independently(self):
        session = make_session()
        # Two commands — two separate request_ids returned by the batch endpoint
        request_ids_map = {"show version": "uuid-1", "show bgp summary": "uuid-2"}
        poll_responses = [
            {"result": "success", "output": "Junos 23.4"},
            {"result": "success", "output": "bgp output"},
        ]
        with patch("handlers.run_commands.live_data_client.submit_fetchcmd_multiple",
                   new_callable=AsyncMock, return_value=request_ids_map):
            with patch("handlers.run_commands._poll_until_done",
                       new_callable=AsyncMock,
                       side_effect=poll_responses) as mock_poll:
                with patch("handlers.run_commands._safe_delete",
                           new_callable=AsyncMock) as mock_delete:
                    result = await _run_on_system(
                        session, "SYS001", "Leaf1",
                        ["show version", "show bgp summary"], 30, "json"
                    )

        assert len(result["command_results"]) == 2
        assert mock_poll.call_count == 2
        assert mock_delete.call_count == 2
        polled_req_ids = [c.args[1] for c in mock_poll.call_args_list]
        assert set(polled_req_ids) == {"uuid-1", "uuid-2"}

    async def test_falls_back_to_single_on_404(self):
        session = make_session()
        with patch("handlers.run_commands.live_data_client.submit_fetchcmd_multiple",
                   new_callable=AsyncMock,
                   side_effect=make_http_error(404)):
            with patch("handlers.run_commands._run_single_command",
                       new_callable=AsyncMock,
                       return_value={"command": "show version", "result": "success",
                                     "output": "Junos 21.4", "error": None}) as mock_single:
                result = await _run_on_system(
                    session, "SYS001", "Leaf1", ["show version"], 30, "json"
                )

        assert result["endpoint"] == "single"
        assert result["status"] == "success"
        assert len(result["command_results"]) == 1
        mock_single.assert_called_once()

    async def test_falls_back_to_single_on_405(self):
        session = make_session()
        with patch("handlers.run_commands.live_data_client.submit_fetchcmd_multiple",
                   new_callable=AsyncMock, side_effect=make_http_error(405)):
            with patch("handlers.run_commands._run_single_command",
                       new_callable=AsyncMock,
                       return_value={"command": "show version", "result": "success",
                                     "output": "ok", "error": None}):
                result = await _run_on_system(
                    session, "SYS001", "Leaf1", ["show version"], 30, "json"
                )

        assert result["endpoint"] == "single"

    async def test_non_404_http_error_is_captured_as_error(self):
        session = make_session()
        with patch("handlers.run_commands.live_data_client.submit_fetchcmd_multiple",
                   new_callable=AsyncMock, side_effect=make_http_error(500)):
            result = await _run_on_system(
                session, "SYS001", "Leaf1", ["show version"], 30, "json"
            )

        assert result["status"] == "error"
        assert "500" in result["error"]

    async def test_fallback_runs_each_command_separately(self):
        session = make_session()
        commands = ["show version", "show bgp summary", "show interfaces"]

        async def single_side_effect(session, system_id, cmd, timeout, fmt):
            return {"command": cmd, "result": "success", "output": f"output-{cmd}",
                    "error": None}

        with patch("handlers.run_commands.live_data_client.submit_fetchcmd_multiple",
                   new_callable=AsyncMock, side_effect=make_http_error(404)):
            with patch("handlers.run_commands._run_single_command",
                       side_effect=single_side_effect) as mock_single:
                result = await _run_on_system(
                    session, "SYS001", "Leaf1", commands, 30, "json"
                )

        assert len(result["command_results"]) == 3
        assert mock_single.call_count == 3
        cmds_called = [c.args[2] for c in mock_single.call_args_list]
        assert cmds_called == commands

    async def test_system_metadata_always_present(self):
        session = make_session()
        with patch("handlers.run_commands.live_data_client.submit_fetchcmd_multiple",
                   new_callable=AsyncMock, side_effect=Exception("network error")):
            result = await _run_on_system(
                session, "SYS999", "BorderLeaf", ["show version"], 30, "json"
            )

        assert result["system_id"] == "SYS999"
        assert result["system_label"] == "BorderLeaf"
        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# handle_run_commands — single system
# ---------------------------------------------------------------------------

class TestHandleRunCommandsSingleSystem:
    async def test_single_session_single_system(self):
        session = make_session("dc-primary")
        registry = make_registry()

        with patch("handlers.run_commands._run_on_system",
                   new_callable=AsyncMock,
                   return_value={"system_id": "SYS001", "system_label": "Leaf1",
                                 "endpoint": "multiple", "status": "success",
                                 "command_results": [{"output": "Junos 21.4"}]}):
            result = await handle_run_commands(
                [session], registry, "bp-001", ["show version"], system_id="SYS001"
            )

        assert result["instance"] == "dc-primary"
        assert result["blueprint_id"] == "bp-001"
        assert result["system_count"] == 1
        assert result["systems"][0]["system_id"] == "SYS001"

    async def test_single_system_does_not_query_graph(self):
        session = make_session("dc-primary")
        registry = make_registry()

        with patch("handlers.run_commands._run_on_system", new_callable=AsyncMock,
                   return_value={"system_id": "SYS001", "system_label": "SYS001",
                                 "status": "success", "command_results": []}):
            await handle_run_commands(
                [session], registry, "bp-001", ["show version"], system_id="SYS001"
            )

        registry.get_or_rebuild.assert_not_called()

    async def test_session_error_returns_error_dict(self):
        session = make_session("dc-primary")
        registry = make_registry(error=Exception("graph error"))

        result = await handle_run_commands(
            [session], registry, "bp-001", ["show version"]
        )

        assert result["instance"] == "dc-primary"
        assert "error" in result
        assert result["systems"] == []


# ---------------------------------------------------------------------------
# handle_run_commands — all systems
# ---------------------------------------------------------------------------

class TestHandleRunCommandsAllSystems:
    async def test_all_systems_queries_graph_and_runs_concurrently(self):
        session = make_session("dc-primary")
        registry = make_registry(rows=SYSTEMS_ROWS)

        async def mock_run(session, sid, label, commands, timeout, fmt):
            return {"system_id": sid, "system_label": label,
                    "status": "success", "command_results": []}

        with patch("handlers.run_commands._run_on_system", side_effect=mock_run):
            result = await handle_run_commands(
                [session], registry, "bp-001", ["show version"]
            )

        assert result["system_count"] == 2
        assert {s["system_id"] for s in result["systems"]} == {"SYS001", "SYS002"}

    async def test_no_systems_in_graph_returns_empty(self):
        session = make_session("dc-primary")
        registry = make_registry(rows=[])

        result = await handle_run_commands(
            [session], registry, "bp-001", ["show version"]
        )

        assert result["system_count"] == 0
        assert result["systems"] == []
        assert "note" in result

    async def test_systems_with_null_system_id_filtered_out(self):
        session = make_session("dc-primary")
        rows = [
            {"sw.system_id": "SYS001", "sw.label": "Leaf1"},
            {"sw.system_id": None, "sw.label": "Spine1"},   # not onboarded
        ]
        registry = make_registry(rows=rows)

        async def mock_run(session, sid, label, commands, timeout, fmt):
            return {"system_id": sid, "status": "success", "command_results": []}

        with patch("handlers.run_commands._run_on_system", side_effect=mock_run):
            result = await handle_run_commands(
                [session], registry, "bp-001", ["show version"]
            )

        assert result["system_count"] == 1
        assert result["systems"][0]["system_id"] == "SYS001"

    async def test_multiple_sessions_aggregated(self):
        sessions = [make_session("dc-primary"), make_session("dc-secondary")]
        registry = make_registry(rows=SYSTEMS_ROWS)

        async def mock_run(session, sid, label, commands, timeout, fmt):
            return {"system_id": sid, "status": "success", "command_results": []}

        with patch("handlers.run_commands._run_on_system", side_effect=mock_run):
            result = await handle_run_commands(
                sessions, registry, "bp-001", ["show version"]
            )

        assert result["instance"] == "all"
        assert result["total_system_count"] == 4
        assert len(result["results"]) == 2

    async def test_instance_name_filters_sessions(self):
        sessions = [make_session("dc-primary"), make_session("dc-secondary")]
        registry = make_registry(rows=SYSTEMS_ROWS)

        async def mock_run(session, sid, label, commands, timeout, fmt):
            return {"system_id": sid, "status": "success", "command_results": []}

        with patch("handlers.run_commands._run_on_system", side_effect=mock_run):
            result = await handle_run_commands(
                sessions, registry, "bp-001", ["show version"],
                instance_name="dc-primary"
            )

        assert result["instance"] == "dc-primary"
        assert result["system_count"] == 2

    async def test_instance_name_all_uses_all_sessions(self):
        sessions = [make_session("a"), make_session("b")]
        registry = make_registry(rows=SYSTEMS_ROWS)

        async def mock_run(session, sid, label, commands, timeout, fmt):
            return {"system_id": sid, "status": "success", "command_results": []}

        with patch("handlers.run_commands._run_on_system", side_effect=mock_run):
            result = await handle_run_commands(
                sessions, registry, "bp-001", ["show version"],
                instance_name="all"
            )

        assert result["instance"] == "all"
        assert result["total_system_count"] == 4

    async def test_semaphore_limits_concurrency(self):
        """
        With max_concurrent_systems=2 and 5 switches, no more than 2 should
        be 'inside' _run_on_system at the same time.
        """
        import asyncio as _asyncio
        session = make_session("dc-primary")
        rows = [{"sw.system_id": f"SYS{i:03}", "sw.label": f"Leaf{i}"} for i in range(5)]
        registry = make_registry(rows=rows)

        active = 0
        peak = 0

        async def mock_run(sess, sid, label, cmds, timeout, fmt):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await _asyncio.sleep(0)   # yield so other coroutines can start
            active -= 1
            return {"system_id": sid, "status": "success", "command_results": []}

        with patch("handlers.run_commands._run_on_system", side_effect=mock_run):
            result = await handle_run_commands(
                [session], registry, "bp-001", ["show version"],
                max_concurrent_systems=2,
            )

        assert result["system_count"] == 5
        assert peak <= 2

    async def test_max_concurrent_systems_default_is_ten(self):
        """Default max_concurrent_systems should be 10 — enough for large fabrics."""
        import inspect
        sig = inspect.signature(handle_run_commands)
        assert sig.parameters["max_concurrent_systems"].default == 10


# ---------------------------------------------------------------------------
# _select_sessions
# ---------------------------------------------------------------------------


class TestSelectSessions:
    def test_none_returns_all(self):
        sessions = [make_session("a"), make_session("b")]
        assert _select_sessions(sessions, None) == sessions

    def test_all_returns_all(self):
        sessions = [make_session("a"), make_session("b")]
        assert _select_sessions(sessions, "all") == sessions

    def test_named_returns_matching(self):
        sessions = [make_session("a"), make_session("b")]
        result = _select_sessions(sessions, "b")
        assert len(result) == 1
        assert result[0].name == "b"

    def test_unknown_name_raises(self):
        sessions = [make_session("a")]
        with pytest.raises(ValueError, match="No instance named 'z'"):
            _select_sessions(sessions, "z")
