import struct
from unittest.mock import patch

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


def test_generate_chart_publish_enabled_returns_image_and_markdown(monkeypatch):
    tool = _build_tools()["generate_chart"]

    monkeypatch.setenv("APSTRA_CHART_PUBLISH_ENABLED", "true")
    monkeypatch.setenv("APSTRA_CHART_PUBLISH_PROVIDER", "catbox")

    with patch("tools.charting.httpx.post") as mock_post:
        mock_post.return_value.status_code = 200
        mock_post.return_value.text = "https://files.catbox.moe/abc123.png"

        result = tool(
            chart_type="line",
            title="BGP Flaps",
            x_label="Time",
            y_label="Flaps",
            series=_base_line_series(),
        )

    assert isinstance(result, list)
    assert isinstance(result[0], Image)
    assert isinstance(result[1], dict)
    assert result[1]["chart_url"] == "https://files.catbox.moe/abc123.png"
    assert result[1]["provider"] == "catbox"
    assert "![BGP Flaps](https://files.catbox.moe/abc123.png)" in result[1]["markdown"]


def test_generate_chart_publish_override_false_ignores_env(monkeypatch):
    tool = _build_tools()["generate_chart"]

    monkeypatch.setenv("APSTRA_CHART_PUBLISH_ENABLED", "true")

    result = tool(
        chart_type="line",
        title="BGP Flaps",
        x_label="Time",
        y_label="Flaps",
        series=_base_line_series(),
        publish_public_url=False,
    )

    assert isinstance(result, Image)


def test_generate_chart_publish_provider_error(monkeypatch):
    tool = _build_tools()["generate_chart"]

    monkeypatch.setenv("APSTRA_CHART_PUBLISH_ENABLED", "true")
    monkeypatch.setenv("APSTRA_CHART_PUBLISH_PROVIDER", "unknown-provider")

    result = tool(
        chart_type="line",
        title="BGP Flaps",
        x_label="Time",
        y_label="Flaps",
        series=_base_line_series(),
    )

    assert isinstance(result, list)
    assert isinstance(result[0], Image)
    assert "chart_publish_error" in result[1]
    assert "unsupported APSTRA_CHART_PUBLISH_PROVIDER" in result[1]["chart_publish_error"]


def test_generate_chart_publish_provider_error_strict_mode(monkeypatch):
    tool = _build_tools()["generate_chart"]

    monkeypatch.setenv("APSTRA_CHART_PUBLISH_ENABLED", "true")
    monkeypatch.setenv("APSTRA_CHART_PUBLISH_PROVIDER", "unknown-provider")
    monkeypatch.setenv("APSTRA_CHART_PUBLISH_STRICT", "true")

    result = tool(
        chart_type="line",
        title="BGP Flaps",
        x_label="Time",
        y_label="Flaps",
        series=_base_line_series(),
    )

    assert result["error"] == "chart_publish_failed"
    assert "unsupported APSTRA_CHART_PUBLISH_PROVIDER" in result["detail"]


def test_generate_chart_publish_freeimage_success(monkeypatch):
    tool = _build_tools()["generate_chart"]

    monkeypatch.setenv("APSTRA_CHART_PUBLISH_ENABLED", "true")
    monkeypatch.setenv("APSTRA_CHART_PUBLISH_PROVIDER", "freeimage")
    monkeypatch.setenv("APSTRA_CHART_FREEIMAGE_API_KEY", "test-key")

    with patch("tools.charting.httpx.post") as mock_post:
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {
            "status_code": 200,
            "image": {
                "url": "https://freeimage.host/i/example.png",
                "display_url": "https://freeimage.host/i/example.md.png",
            },
        }

        result = tool(
            chart_type="line",
            title="BGP Flaps",
            x_label="Time",
            y_label="Flaps",
            series=_base_line_series(),
        )

    assert isinstance(result, list)
    assert isinstance(result[0], Image)
    assert result[1]["provider"] == "freeimage"
    assert result[1]["chart_url"] == "https://freeimage.host/i/example.png"


def test_generate_chart_publish_freeimage_insecure_skip_verify(monkeypatch):
    tool = _build_tools()["generate_chart"]

    monkeypatch.setenv("APSTRA_CHART_PUBLISH_ENABLED", "true")
    monkeypatch.setenv("APSTRA_CHART_PUBLISH_PROVIDER", "freeimage")
    monkeypatch.setenv("APSTRA_CHART_FREEIMAGE_API_KEY", "test-key")
    monkeypatch.setenv("APSTRA_CHART_PUBLISH_INSECURE_SKIP_VERIFY", "true")

    with patch("tools.charting.httpx.post") as mock_post:
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {
            "status_code": 200,
            "image": {"url": "https://freeimage.host/i/example.png"},
        }

        tool(
            chart_type="line",
            title="BGP Flaps",
            x_label="Time",
            y_label="Flaps",
            series=_base_line_series(),
        )

    kwargs = mock_post.call_args.kwargs
    assert kwargs.get("verify") is False


def test_generate_chart_publish_freeimage_missing_key(monkeypatch):
    tool = _build_tools()["generate_chart"]

    monkeypatch.setenv("APSTRA_CHART_PUBLISH_ENABLED", "true")
    monkeypatch.setenv("APSTRA_CHART_PUBLISH_PROVIDER", "freeimage")
    monkeypatch.delenv("APSTRA_CHART_FREEIMAGE_API_KEY", raising=False)

    result = tool(
        chart_type="line",
        title="BGP Flaps",
        x_label="Time",
        y_label="Flaps",
        series=_base_line_series(),
    )

    assert isinstance(result, list)
    assert isinstance(result[0], Image)
    assert "freeimage upload requested but APSTRA_CHART_FREEIMAGE_API_KEY is not set" in result[1]["chart_publish_error"]
