import struct

from fastmcp.utilities.types import Image

from tools.charting import _MAX_POINTS_PER_SERIES, register


class StubMCP:
    def __init__(self):
        self.tools: dict = {}

    def tool(self):
        def decorator(fn):
            self.tools[fn.__name__] = fn
            return fn

        return decorator


def _png_dimensions(payload: bytes) -> tuple[int, int]:
    assert payload.startswith(b"\x89PNG\r\n\x1a\n")
    return struct.unpack(">II", payload[16:24])


def _build_tools() -> dict:
    mcp = StubMCP()
    register(mcp)
    return mcp.tools


def _base_line_series():
    return [
        {
            "name": "BGP session flaps",
            "data": [
                ("2026-05-13T12:00:00Z", 1.0),
                ("2026-05-13T13:00:00Z", 3.0),
                ("2026-05-13T14:00:00Z", 2.0),
            ],
        }
    ]


def test_register_exposes_generate_chart():
    tools = _build_tools()
    assert "generate_chart" in tools


def test_generate_chart_line_returns_png_default_dimensions():
    tool = _build_tools()["generate_chart"]

    result = tool(
        chart_type="line",
        title="BGP Flaps",
        x_label="Time",
        y_label="Flaps",
        series=_base_line_series(),
    )

    assert isinstance(result, Image)
    assert isinstance(result.data, bytes)
    assert len(result.data) > 1000
    assert _png_dimensions(result.data) == (1200, 600)


def test_generate_chart_supports_bar_stacked_heatmap_and_scatter():
    tool = _build_tools()["generate_chart"]

    bar = tool(
        chart_type="bar",
        title="Anomalies by Device",
        x_label="Device",
        y_label="Count",
        x_axis_type="categorical",
        series=[
            {"name": "Raised", "data": [("leaf-1", 4.0), ("leaf-2", 2.0)]},
            {"name": "Cleared", "data": [("leaf-1", 1.0), ("leaf-2", 3.0)]},
        ],
    )
    assert isinstance(bar, Image)

    stacked = tool(
        chart_type="stacked_bar",
        title="Anomalies by Device",
        x_label="Device",
        y_label="Count",
        x_axis_type="categorical",
        series=[
            {"name": "Raised", "data": [("leaf-1", 4.0), ("leaf-2", 2.0)]},
            {"name": "Cleared", "data": [("leaf-1", 1.0), ("leaf-2", 3.0)]},
        ],
    )
    assert isinstance(stacked, Image)

    heatmap = tool(
        chart_type="heatmap",
        title="Error Heatmap",
        x_label="Hour",
        y_label="Errors",
        x_axis_type="categorical",
        series=[
            {"name": "leaf-1", "data": [("12:00", 1.0), ("13:00", 3.0)]},
            {"name": "leaf-2", "data": [("12:00", 2.0), ("13:00", 4.0)]},
        ],
    )
    assert isinstance(heatmap, Image)

    scatter = tool(
        chart_type="scatter",
        title="Loss vs Delay",
        x_label="Loss",
        y_label="Delay",
        x_axis_type="numeric",
        series=[
            {"name": "leaf-1", "data": [(0.1, 12.0), (0.2, 14.0), (0.3, 16.0)]},
        ],
    )
    assert isinstance(scatter, Image)


def test_generate_chart_rejects_unsupported_chart_type():
    tool = _build_tools()["generate_chart"]

    result = tool(
        chart_type="pie",
        title="Bad Chart",
        x_label="x",
        y_label="y",
        series=_base_line_series(),
    )

    assert result["error"] == "unsupported_chart_type"


def test_generate_chart_rejects_invalid_datetime_value():
    tool = _build_tools()["generate_chart"]

    result = tool(
        chart_type="line",
        title="Bad Time",
        x_label="Time",
        y_label="Value",
        x_axis_type="datetime",
        series=[
            {"name": "bad", "data": [("not-a-time", 1.0)]},
        ],
    )

    assert result["error"] == "invalid_series"
    assert "invalid ISO datetime" in result["detail"]


def test_generate_chart_rejects_invalid_y_range():
    tool = _build_tools()["generate_chart"]

    result = tool(
        chart_type="line",
        title="Range",
        x_label="Time",
        y_label="Value",
        series=_base_line_series(),
        y_min=10.0,
        y_max=1.0,
    )

    assert result["error"] == "invalid_y_range"


def test_generate_chart_rejects_oversized_series():
    tool = _build_tools()["generate_chart"]
    too_many_points = [(float(i), float(i)) for i in range(_MAX_POINTS_PER_SERIES + 1)]

    result = tool(
        chart_type="line",
        title="Oversized",
        x_label="x",
        y_label="y",
        x_axis_type="numeric",
        series=[{"name": "oversized", "data": too_many_points}],
    )

    assert result["error"] == "invalid_series"
    assert "exceeds max points" in result["detail"]


def test_generate_chart_rejects_heatmap_without_categorical_axis():
    tool = _build_tools()["generate_chart"]

    result = tool(
        chart_type="heatmap",
        title="Heatmap",
        x_label="Time",
        y_label="Value",
        x_axis_type="datetime",
        series=[
            {"name": "leaf-1", "data": [("2026-05-13T12:00:00Z", 1.0)]},
        ],
    )

    assert result["error"] == "invalid_axis_for_heatmap"


def test_generate_chart_rejects_annotation_outside_categorical_axis():
    tool = _build_tools()["generate_chart"]

    result = tool(
        chart_type="bar",
        title="Anomalies",
        x_label="Device",
        y_label="Count",
        x_axis_type="categorical",
        series=[
            {"name": "Raised", "data": [("leaf-1", 2.0), ("leaf-2", 4.0)]},
        ],
        annotations=[{"x": "leaf-9", "label": "not-present"}],
    )

    assert result["error"] == "invalid_annotations"
