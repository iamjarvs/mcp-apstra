from typing import Annotated, Literal

from fastmcp import Context
from pydantic import Field

from tools import anomalies as anomalies_tool
from tools import anomaly_analytics as anomaly_analytics_tool
from tools import anomaly_timeline as anomaly_timeline_tool


class _CaptureMCP:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def decorator(fn):
            self.tools[fn.__name__] = fn
            return fn

        return decorator


def _capture_registered_tools(register_fn):
    stub = _CaptureMCP()
    register_fn(stub)
    return stub.tools


def _missing_args(required: dict[str, object]) -> list[str]:
    return [key for key, value in required.items() if value is None]


def register(mcp):
    live_tools = _capture_registered_tools(anomalies_tool.register)
    timeline_tools = _capture_registered_tools(anomaly_timeline_tool.register)
    analytics_tools = _capture_registered_tools(anomaly_analytics_tool.register)

    @mcp.tool()
    async def anomaly(
        intent: Annotated[
            Literal[
                "summary",
                "events",
                "active",
                "device_history",
                "trend",
                "correlated_faults",
                "durations",
                "heatmap",
                "correlate_events",
                "current_live",
            ],
            Field(
                default="summary",
                description=(
                    "Dispatcher intent for anomaly workflows. Use summary first, then "
                    "run one targeted intent with filters and pagination."
                ),
            ),
        ] = "summary",
        blueprint_id: Annotated[
            str | None,
            Field(
                default=None,
                description=(
                    "Apstra blueprint ID, partial label, or null/'all'. "
                    "Null queries all blueprints."
                ),
            ),
        ] = None,
        anomaly_type: Annotated[
            str | None,
            Field(default=None, description="Optional anomaly type filter."),
        ] = None,
        device: Annotated[
            str | None,
            Field(default=None, description="Optional device hostname filter for event queries."),
        ] = None,
        device_label: Annotated[
            str | None,
            Field(default=None, description="Required for device_history intent."),
        ] = None,
        time_window: Annotated[
            str,
            Field(default="1-day", description="Summary window: 1-hour, 1-day, 7-day, 14-day, or 30-day."),
        ] = "1-day",
        hours_back: Annotated[
            int,
            Field(default=168, ge=1, description="Look-back window in hours (>=1). Default 168 (7 days)."),
        ] = 168,
        days_back: Annotated[
            int | None,
            Field(default=None, ge=1, description="Optional day window for events intent (>=1; no max)."),
        ] = None,
        from_time: Annotated[
            str | None,
            Field(default=None, description="Optional ISO-8601 UTC start time."),
        ] = None,
        to_time: Annotated[
            str | None,
            Field(default=None, description="Optional ISO-8601 UTC end time."),
        ] = None,
        raised_only: Annotated[
            bool,
            Field(default=False, description="For events intent: return raise events only."),
        ] = False,
        limit: Annotated[
            int,
            Field(default=20, ge=1, le=200, description="Page size for paginated anomaly intents."),
        ] = 20,
        cursor_timestamp: Annotated[
            str | None,
            Field(default=None, description="Pagination cursor timestamp from previous events response."),
        ] = None,
        cursor_event_id: Annotated[
            int | None,
            Field(default=None, ge=1, description="Pagination cursor event ID from previous events response."),
        ] = None,
        idle_gap_seconds: Annotated[
            int,
            Field(default=60, ge=5, le=3600, description="Event clustering gap in seconds for correlate_events."),
        ] = 60,
        min_cluster_size: Annotated[
            int,
            Field(default=2, ge=1, description="Minimum cluster size for correlate_events."),
        ] = 2,
        instance_name: Annotated[
            str | None,
            Field(default=None, description="Optional Apstra instance name; leave null unless explicitly provided by the user."),
        ] = None,
        ctx: Context = None,
    ) -> dict:
        """
        Umbrella dispatcher for anomaly workflows.

        Recommended sequence:
          1) summary
          2) one targeted intent (events, active, trend, or correlate_events)
          3) deep intent only when required (device_history, durations, heatmap)

        Intent mapping:
          - summary -> get_anomaly_summary
          - events -> get_anomaly_events
          - active -> get_active_anomalies_from_store
          - device_history -> get_device_anomaly_history
          - trend -> get_anomaly_trend
          - correlated_faults -> get_correlated_faults
          - durations -> get_fault_durations
          - heatmap -> get_device_anomaly_heatmap
          - correlate_events -> correlate_anomaly_events
          - current_live -> get_current_anomalies
        """
        if intent == "summary":
            result = await timeline_tools["get_anomaly_summary"](
                blueprint_id=blueprint_id,
                time_window=time_window,
                instance_name=instance_name,
                ctx=ctx,
            )
            result.setdefault("intent", intent)
            return result

        if intent == "events":
            result = await timeline_tools["get_anomaly_events"](
                blueprint_id=blueprint_id,
                hours_back=hours_back,
                days_back=days_back,
                anomaly_type=anomaly_type,
                device=device,
                from_time=from_time,
                to_time=to_time,
                raised_only=raised_only,
                limit=limit,
                cursor_timestamp=cursor_timestamp,
                cursor_event_id=cursor_event_id,
                instance_name=instance_name,
                ctx=ctx,
            )
            result.setdefault("intent", intent)
            return result

        if intent == "active":
            result = await timeline_tools["get_active_anomalies_from_store"](
                blueprint_id=blueprint_id,
                anomaly_type=anomaly_type,
                instance_name=instance_name,
                ctx=ctx,
            )
            result.setdefault("intent", intent)
            return result

        if intent == "device_history":
            missing = _missing_args({"device_label": device_label})
            if missing:
                return {
                    "error": "missing_required_parameters",
                    "intent": intent,
                    "missing": missing,
                    "hint": "Set device_label for device_history intent.",
                }
            result = await timeline_tools["get_device_anomaly_history"](
                blueprint_id=blueprint_id,
                device_label=device_label,
                from_time=from_time,
                to_time=to_time,
                anomaly_type=anomaly_type,
                instance_name=instance_name,
                ctx=ctx,
            )
            result.setdefault("intent", intent)
            return result

        if intent == "trend":
            result = await analytics_tools["get_anomaly_trend"](
                blueprint_id=blueprint_id,
                anomaly_type=anomaly_type,
                hours_back=hours_back,
                instance_name=instance_name,
                ctx=ctx,
            )
            result.setdefault("intent", intent)
            return result

        if intent == "correlated_faults":
            result = await analytics_tools["get_correlated_faults"](
                blueprint_id=blueprint_id,
                anomaly_type=anomaly_type,
                hours_back=hours_back,
                instance_name=instance_name,
                ctx=ctx,
            )
            result.setdefault("intent", intent)
            return result

        if intent == "durations":
            result = await analytics_tools["get_fault_durations"](
                blueprint_id=blueprint_id,
                anomaly_type=anomaly_type,
                hours_back=hours_back,
                instance_name=instance_name,
                ctx=ctx,
            )
            result.setdefault("intent", intent)
            return result

        if intent == "heatmap":
            result = await analytics_tools["get_device_anomaly_heatmap"](
                blueprint_id=blueprint_id,
                hours_back=hours_back,
                instance_name=instance_name,
                ctx=ctx,
            )
            result.setdefault("intent", intent)
            return result

        if intent == "correlate_events":
            result = await analytics_tools["correlate_anomaly_events"](
                blueprint_id=blueprint_id,
                hours_back=hours_back,
                idle_gap_seconds=idle_gap_seconds,
                min_cluster_size=min_cluster_size,
                anomaly_type=anomaly_type,
                instance_name=instance_name,
                ctx=ctx,
            )
            result.setdefault("intent", intent)
            return result

        result = await live_tools["get_current_anomalies"](
            blueprint_id=blueprint_id,
            instance_name=instance_name,
            ctx=ctx,
        )
        result.setdefault("intent", intent)
        return result
