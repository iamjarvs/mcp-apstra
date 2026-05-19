from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from math import isfinite
import os
from typing import Annotated, Literal

import httpx
import matplotlib
matplotlib.use("Agg")
from matplotlib import dates as mdates
from matplotlib import pyplot as plt
from pydantic import BaseModel, Field

from fastmcp import Context
from fastmcp.utilities.types import Image

_CHART_TYPES = {"line", "bar", "stacked_bar", "heatmap", "scatter"}
_X_AXIS_TYPES = {"datetime", "categorical", "numeric"}
_DEFAULT_WIDTH = 1200
_DEFAULT_HEIGHT = 600
_DEFAULT_DPI = 96
_MAX_SERIES = 12
_MAX_POINTS_PER_SERIES = 1500
_MAX_TOTAL_POINTS = 12000
_MAX_TEXT_LEN = 180

_PALETTE = [
    "#1B4965",
    "#1F7A8C",
    "#4D9078",
    "#B5C99A",
    "#F2CC8F",
    "#E07A5F",
    "#8D6A9F",
    "#6D597A",
]


class ChartSeries(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    data: list[tuple[str | float, float]] = Field(min_length=1)
    colour: str | None = Field(default=None, max_length=20)


class ChartAnnotation(BaseModel):
    x: str | float
    label: str = Field(min_length=1, max_length=120)
    colour: str | None = "#FF0000"


@dataclass
class _NormalizedSeries:
    name: str
    color: str
    x_values: list[datetime | float | str]
    y_values: list[float]


@dataclass(frozen=True)
class _ChartPublishConfig:
    enabled: bool
    provider: str
    timeout_seconds: float
    strict: bool
    ssl_verify: bool
    catbox_userhash: str | None = None
    postimages_api_url: str | None = None
    postimages_api_key: str | None = None
    freeimage_api_url: str | None = None
    freeimage_api_key: str | None = None


def _is_truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _get_publish_config() -> _ChartPublishConfig:
    enabled = _is_truthy(os.environ.get("APSTRA_CHART_PUBLISH_ENABLED", "0"))
    provider = str(os.environ.get("APSTRA_CHART_PUBLISH_PROVIDER", "catbox")).strip().lower() or "catbox"

    timeout_raw = str(os.environ.get("APSTRA_CHART_PUBLISH_TIMEOUT_SECONDS", "20")).strip()
    try:
        timeout_seconds = float(timeout_raw)
    except ValueError:
        timeout_seconds = 20.0

    if timeout_seconds <= 0:
        timeout_seconds = 20.0

    return _ChartPublishConfig(
        enabled=enabled,
        provider=provider,
        timeout_seconds=timeout_seconds,
        strict=_is_truthy(os.environ.get("APSTRA_CHART_PUBLISH_STRICT", "0")),
        ssl_verify=not _is_truthy(os.environ.get("APSTRA_CHART_PUBLISH_INSECURE_SKIP_VERIFY", "0")),
        catbox_userhash=(os.environ.get("APSTRA_CHART_CATBOX_USERHASH") or None),
        postimages_api_url=(os.environ.get("APSTRA_CHART_POSTIMAGES_API_URL") or None),
        postimages_api_key=(os.environ.get("APSTRA_CHART_POSTIMAGES_API_KEY") or None),
        freeimage_api_url=(
            os.environ.get("APSTRA_CHART_FREEIMAGE_API_URL")
            or "https://freeimage.host/api/1/upload"
        ),
        freeimage_api_key=(os.environ.get("APSTRA_CHART_FREEIMAGE_API_KEY") or None),
    )


def _upload_to_catbox(png_bytes: bytes, config: _ChartPublishConfig) -> tuple[str | None, str | None]:
    data = {"reqtype": "fileupload"}
    if config.catbox_userhash:
        data["userhash"] = config.catbox_userhash

    try:
        response = httpx.post(
            "https://catbox.moe/user/api.php",
            data=data,
            files={"fileToUpload": ("chart.png", png_bytes, "image/png")},
            timeout=config.timeout_seconds,
            verify=config.ssl_verify,
        )
    except Exception as exc:
        return None, f"catbox upload failed: {exc}"

    if response.status_code >= 400:
        return None, f"catbox upload failed with status {response.status_code}"

    url = response.text.strip()
    if not url.startswith("http"):
        return None, "catbox upload response did not include a URL"
    return url, None


def _extract_url_from_json(payload: dict) -> str | None:
    candidates = (
        payload.get("url"),
        payload.get("link"),
        payload.get("direct_url"),
        payload.get("image_url"),
    )
    for item in candidates:
        if isinstance(item, str) and item.startswith("http"):
            return item

    data_node = payload.get("data")
    if isinstance(data_node, dict):
        nested = (
            data_node.get("url"),
            data_node.get("link"),
            data_node.get("display_url"),
            data_node.get("image"),
        )
        for item in nested:
            if isinstance(item, str) and item.startswith("http"):
                return item
    return None


def _upload_to_postimages(png_bytes: bytes, config: _ChartPublishConfig) -> tuple[str | None, str | None]:
    if not config.postimages_api_url:
        return None, (
            "postimages upload requested but APSTRA_CHART_POSTIMAGES_API_URL is not set"
        )

    data = {}
    if config.postimages_api_key:
        data["key"] = config.postimages_api_key

    try:
        response = httpx.post(
            config.postimages_api_url,
            data=data,
            files={"file": ("chart.png", png_bytes, "image/png")},
            timeout=config.timeout_seconds,
            verify=config.ssl_verify,
        )
    except Exception as exc:
        return None, f"postimages upload failed: {exc}"

    if response.status_code >= 400:
        return None, f"postimages upload failed with status {response.status_code}"

    try:
        payload = response.json()
    except ValueError:
        return None, "postimages upload response was not JSON"

    url = _extract_url_from_json(payload)
    if not url:
        return None, "postimages upload response did not include a URL"
    return url, None


def _upload_to_freeimage(png_bytes: bytes, config: _ChartPublishConfig) -> tuple[str | None, str | None]:
    if not config.freeimage_api_key:
        return None, (
            "freeimage upload requested but APSTRA_CHART_FREEIMAGE_API_KEY is not set"
        )

    api_url = config.freeimage_api_url or "https://freeimage.host/api/1/upload"
    data = {
        "key": config.freeimage_api_key,
        "action": "upload",
        "format": "json",
    }

    try:
        response = httpx.post(
            api_url,
            data=data,
            files={"source": ("chart.png", png_bytes, "image/png")},
            timeout=config.timeout_seconds,
            verify=config.ssl_verify,
        )
    except Exception as exc:
        return None, f"freeimage upload failed: {exc}"

    if response.status_code >= 400:
        return None, f"freeimage upload failed with status {response.status_code}"

    try:
        payload = response.json()
    except ValueError:
        return None, "freeimage upload response was not JSON"

    image_node = payload.get("image")
    if isinstance(image_node, dict):
        direct_url = image_node.get("url")
        display_url = image_node.get("display_url")
        if isinstance(direct_url, str) and direct_url.startswith("http"):
            return direct_url, None
        if isinstance(display_url, str) and display_url.startswith("http"):
            return display_url, None

    url = _extract_url_from_json(payload)
    if not url:
        return None, "freeimage upload response did not include a URL"
    return url, None


def _upload_chart(png_bytes: bytes, config: _ChartPublishConfig) -> tuple[str | None, str | None]:
    if config.provider == "catbox":
        return _upload_to_catbox(png_bytes, config)
    if config.provider == "postimages":
        return _upload_to_postimages(png_bytes, config)
    if config.provider == "freeimage":
        return _upload_to_freeimage(png_bytes, config)
    return None, (
        f"unsupported APSTRA_CHART_PUBLISH_PROVIDER '{config.provider}'; "
        "supported values are 'catbox', 'postimages', and 'freeimage'"
    )


def _parse_iso_datetime(raw: str) -> datetime:
    value = raw.strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)


def _validate_text_fields(title: str, x_label: str, y_label: str) -> str | None:
    if not title.strip():
        return "title must not be empty"
    if not x_label.strip():
        return "x_label must not be empty"
    if not y_label.strip():
        return "y_label must not be empty"
    if len(title) > _MAX_TEXT_LEN:
        return f"title exceeds max length {_MAX_TEXT_LEN}"
    if len(x_label) > _MAX_TEXT_LEN:
        return f"x_label exceeds max length {_MAX_TEXT_LEN}"
    if len(y_label) > _MAX_TEXT_LEN:
        return f"y_label exceeds max length {_MAX_TEXT_LEN}"
    return None


def _normalize_series(
    series: list[ChartSeries],
    x_axis_type: str,
) -> tuple[list[_NormalizedSeries], str | None]:
    if not series:
        return [], "series must contain at least one series"
    if len(series) > _MAX_SERIES:
        return [], f"series count exceeds max {_MAX_SERIES}"

    normalized: list[_NormalizedSeries] = []
    total_points = 0

    for idx, item in enumerate(series):
        if not item.data:
            return [], f"series '{item.name}' must contain at least one data point"
        if len(item.data) > _MAX_POINTS_PER_SERIES:
            return [], f"series '{item.name}' exceeds max points {_MAX_POINTS_PER_SERIES}"

        x_values: list[datetime | float | str] = []
        y_values: list[float] = []

        for point_idx, (x_raw, y_raw) in enumerate(item.data):
            try:
                y_val = float(y_raw)
            except (TypeError, ValueError):
                return [], f"series '{item.name}' point {point_idx} has non-numeric y"

            if not isfinite(y_val):
                return [], f"series '{item.name}' point {point_idx} has non-finite y"

            if x_axis_type == "datetime":
                if not isinstance(x_raw, str):
                    return [], f"series '{item.name}' point {point_idx} x must be ISO datetime string"
                try:
                    x_val = _parse_iso_datetime(x_raw)
                except ValueError:
                    return [], f"series '{item.name}' point {point_idx} has invalid ISO datetime x"
            elif x_axis_type == "numeric":
                try:
                    x_val = float(x_raw)
                except (TypeError, ValueError):
                    return [], f"series '{item.name}' point {point_idx} x must be numeric"
                if not isfinite(x_val):
                    return [], f"series '{item.name}' point {point_idx} has non-finite numeric x"
            else:
                x_val = str(x_raw)

            x_values.append(x_val)
            y_values.append(y_val)

        total_points += len(y_values)
        color = item.colour or _PALETTE[idx % len(_PALETTE)]
        normalized.append(_NormalizedSeries(item.name, color, x_values, y_values))

    if total_points > _MAX_TOTAL_POINTS:
        return [], f"total points exceed max {_MAX_TOTAL_POINTS}"

    return normalized, None


def _coerce_series(series: list[ChartSeries | dict]) -> tuple[list[ChartSeries], str | None]:
    coerced: list[ChartSeries] = []
    for idx, item in enumerate(series):
        if isinstance(item, ChartSeries):
            coerced.append(item)
            continue
        try:
            coerced.append(ChartSeries.model_validate(item))
        except Exception as exc:
            return [], f"series[{idx}] validation failed: {exc}"
    return coerced, None


def _coerce_annotations(
    annotations: list[ChartAnnotation | dict] | None,
) -> tuple[list[ChartAnnotation], str | None]:
    if not annotations:
        return [], None

    coerced: list[ChartAnnotation] = []
    for idx, item in enumerate(annotations):
        if isinstance(item, ChartAnnotation):
            coerced.append(item)
            continue
        try:
            coerced.append(ChartAnnotation.model_validate(item))
        except Exception as exc:
            return [], f"annotations[{idx}] validation failed: {exc}"
    return coerced, None


def _build_categorical_positions(normalized: list[_NormalizedSeries]) -> tuple[dict[str, int], list[str]]:
    labels: list[str] = []
    seen: set[str] = set()
    for s in normalized:
        for raw in s.x_values:
            label = str(raw)
            if label not in seen:
                labels.append(label)
                seen.add(label)
    return {label: idx for idx, label in enumerate(labels)}, labels


def _annotation_x_value(annotation_x: str | float, x_axis_type: str) -> datetime | float | str:
    if x_axis_type == "datetime":
        if not isinstance(annotation_x, str):
            raise ValueError("annotation x must be ISO datetime string for datetime x_axis_type")
        return _parse_iso_datetime(annotation_x)
    if x_axis_type == "numeric":
        value = float(annotation_x)
        if not isfinite(value):
            raise ValueError("annotation x must be finite numeric for numeric x_axis_type")
        return value
    return str(annotation_x)


def _apply_annotations(
    ax,
    annotations: list[ChartAnnotation],
    x_axis_type: str,
    x_positions: dict[str, int] | None,
) -> str | None:
    for ann in annotations:
        try:
            x_val = _annotation_x_value(ann.x, x_axis_type)
        except (TypeError, ValueError) as exc:
            return str(exc)

        if x_axis_type == "categorical":
            assert x_positions is not None
            key = str(x_val)
            if key not in x_positions:
                return f"annotation x '{key}' not found in categorical axis"
            plot_x = x_positions[key]
        else:
            plot_x = x_val

        color = ann.colour or "#FF0000"
        ax.axvline(plot_x, linestyle="--", linewidth=1.0, color=color, alpha=0.85)
        ax.text(
            plot_x,
            0.98,
            ann.label,
            color=color,
            fontsize=8,
            ha="left",
            va="top",
            rotation=90,
            transform=ax.get_xaxis_transform(),
            clip_on=True,
        )
    return None


def _style_axis(ax, title: str, x_label: str, y_label: str):
    ax.set_title(title, fontsize=14, fontweight="semibold", pad=14)
    ax.set_xlabel(x_label)
    ax.set_ylabel(y_label)
    ax.grid(False)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_alpha(0.35)
    ax.spines["bottom"].set_alpha(0.35)


def _render_line(
    ax,
    normalized: list[_NormalizedSeries],
    x_axis_type: str,
    category_positions: dict[str, int] | None,
):
    for s in normalized:
        if x_axis_type == "categorical":
            assert category_positions is not None
            x_vals = [category_positions[str(x)] for x in s.x_values]
        else:
            x_vals = s.x_values
        ax.plot(x_vals, s.y_values, label=s.name, color=s.color, linewidth=2)


def _render_scatter(
    ax,
    normalized: list[_NormalizedSeries],
    x_axis_type: str,
    category_positions: dict[str, int] | None,
):
    for s in normalized:
        if x_axis_type == "categorical":
            assert category_positions is not None
            x_vals = [category_positions[str(x)] for x in s.x_values]
        else:
            x_vals = s.x_values
        ax.scatter(x_vals, s.y_values, label=s.name, color=s.color, s=20, alpha=0.9)


def _render_bar(ax, normalized: list[_NormalizedSeries], stacked: bool):
    positions, labels = _build_categorical_positions(normalized)
    if not labels:
        return "bar chart requires at least one categorical x value"

    bar_count = len(normalized)
    x_slots = [positions[label] for label in labels]
    width = 0.8 if stacked else max(0.2, 0.85 / max(1, bar_count))

    running = [0.0] * len(labels)

    for idx, s in enumerate(normalized):
        y_map = {str(x): y for x, y in zip(s.x_values, s.y_values)}
        y_values = [y_map.get(label, 0.0) for label in labels]

        if stacked:
            ax.bar(x_slots, y_values, width=width, bottom=running, label=s.name, color=s.color)
            running = [base + y for base, y in zip(running, y_values)]
        else:
            offsets = [x + (idx - (bar_count - 1) / 2) * width for x in x_slots]
            ax.bar(offsets, y_values, width=width, label=s.name, color=s.color)

    ax.set_xticks(x_slots)
    ax.set_xticklabels(labels, rotation=30, ha="right")
    return None


def _render_heatmap(ax, normalized: list[_NormalizedSeries], y_label: str):
    positions, labels = _build_categorical_positions(normalized)
    if not labels:
        return "heatmap requires categorical x values"

    matrix: list[list[float]] = []
    for s in normalized:
        y_map = {str(x): y for x, y in zip(s.x_values, s.y_values)}
        matrix.append([y_map.get(label, 0.0) for label in labels])

    image = ax.imshow(matrix, aspect="auto", cmap="YlOrRd", interpolation="nearest")
    ax.set_xticks(list(range(len(labels))))
    ax.set_xticklabels(labels, rotation=30, ha="right")
    ax.set_yticks(list(range(len(normalized))))
    ax.set_yticklabels([s.name for s in normalized])
    cbar = ax.figure.colorbar(image, ax=ax, pad=0.02)
    cbar.set_label(y_label)
    return None


def _to_png(fig) -> bytes:
    buffer = BytesIO()
    fig.savefig(buffer, format="png", dpi=_DEFAULT_DPI, facecolor="white")
    buffer.seek(0)
    return buffer.read()


def register(mcp):
    @mcp.tool()
    def generate_chart(
        chart_type: Annotated[
            Literal["line", "bar", "stacked_bar", "heatmap", "scatter"],
            Field(description="Chart type: line, bar, stacked_bar, heatmap, or scatter."),
        ],
        title: Annotated[str, Field(description="Chart title.")],
        x_label: Annotated[str, Field(description="X-axis label.")],
        y_label: Annotated[str, Field(description="Y-axis label.")],
        series: Annotated[
            list[ChartSeries | dict],
            Field(description="Series list with data points as [x, y]. x is ISO datetime, numeric, or categorical."),
        ],
        x_axis_type: Annotated[
            Literal["datetime", "categorical", "numeric"],
            Field(default="datetime", description="Interpretation of x values."),
        ] = "datetime",
        annotations: Annotated[
            list[ChartAnnotation | dict] | None,
            Field(default=None, description="Optional markers to render as vertical dashed lines."),
        ] = None,
        y_min: Annotated[
            float | None,
            Field(default=None, description="Optional fixed y-axis minimum."),
        ] = None,
        y_max: Annotated[
            float | None,
            Field(default=None, description="Optional fixed y-axis maximum."),
        ] = None,
        publish_public_url: Annotated[
            bool | None,
            Field(
                default=None,
                description=(
                    "Optional override for chart URL publishing. If omitted, uses environment config. "
                    "When enabled and upload succeeds, tool returns both MCP image content and markdown URL text."
                ),
            ),
        ] = None,
        ctx: Context = None,
    ) -> Image | dict | list[object]:
        """
        Generate a chart image from structured data and return it as a PNG.

        Use this tool when you have time-series or categorical data to visualize, for example
        BGP session state changes over time, probe metric trends, anomaly counts by device, or
        interface error rates across a fabric.

        Provide all data points in the series list. For time-series data, use ISO 8601 timestamps
        as x values (for example "2026-05-13T14:32:00Z"). For categorical data, use string labels.

        Use annotations to mark significant events on the chart, such as when an anomaly was
        detected or when a configuration change was committed.

        Returns a PNG image.
        """
        del ctx

        if chart_type not in _CHART_TYPES:
            return {
                "error": "unsupported_chart_type",
                "supported_values": sorted(_CHART_TYPES),
            }

        if x_axis_type not in _X_AXIS_TYPES:
            return {
                "error": "unsupported_x_axis_type",
                "supported_values": sorted(_X_AXIS_TYPES),
            }

        text_error = _validate_text_fields(title=title, x_label=x_label, y_label=y_label)
        if text_error:
            return {"error": "invalid_text_fields", "detail": text_error}

        if y_min is not None and y_max is not None and y_min >= y_max:
            return {"error": "invalid_y_range", "detail": "y_min must be less than y_max"}

        typed_series, series_error = _coerce_series(series)
        if series_error:
            return {"error": "invalid_series", "detail": series_error}

        typed_annotations, annotation_type_error = _coerce_annotations(annotations)
        if annotation_type_error:
            return {"error": "invalid_annotations", "detail": annotation_type_error}

        normalized, normalize_error = _normalize_series(typed_series, x_axis_type)
        if normalize_error:
            return {"error": "invalid_series", "detail": normalize_error}

        if chart_type == "heatmap" and x_axis_type != "categorical":
            return {
                "error": "invalid_axis_for_heatmap",
                "detail": "heatmap requires x_axis_type='categorical'",
            }

        plt.style.use("default")
        fig, ax = plt.subplots(figsize=(_DEFAULT_WIDTH / _DEFAULT_DPI, _DEFAULT_HEIGHT / _DEFAULT_DPI), dpi=_DEFAULT_DPI)

        try:
            category_positions: dict[str, int] | None = None
            category_labels: list[str] | None = None

            if x_axis_type == "categorical":
                category_positions, category_labels = _build_categorical_positions(normalized)

            if chart_type == "line":
                _render_line(ax, normalized, x_axis_type=x_axis_type, category_positions=category_positions)
            elif chart_type == "scatter":
                _render_scatter(ax, normalized, x_axis_type=x_axis_type, category_positions=category_positions)
            elif chart_type == "bar":
                render_error = _render_bar(ax, normalized, stacked=False)
                if render_error:
                    return {"error": "invalid_bar_data", "detail": render_error}
                category_positions, _ = _build_categorical_positions(normalized)
            elif chart_type == "stacked_bar":
                render_error = _render_bar(ax, normalized, stacked=True)
                if render_error:
                    return {"error": "invalid_stacked_bar_data", "detail": render_error}
                category_positions, _ = _build_categorical_positions(normalized)
            else:
                render_error = _render_heatmap(ax, normalized, y_label)
                if render_error:
                    return {"error": "invalid_heatmap_data", "detail": render_error}
                category_positions, _ = _build_categorical_positions(normalized)

            if x_axis_type == "datetime" and chart_type in {"line", "scatter"}:
                locator = mdates.AutoDateLocator(minticks=4, maxticks=9)
                formatter = mdates.ConciseDateFormatter(locator)
                ax.xaxis.set_major_locator(locator)
                ax.xaxis.set_major_formatter(formatter)
                fig.autofmt_xdate(rotation=20)
            elif x_axis_type == "categorical" and chart_type in {"line", "scatter"}:
                assert category_labels is not None
                ax.set_xticks(list(range(len(category_labels))))
                ax.set_xticklabels(category_labels, rotation=30, ha="right")

            if typed_annotations:
                annotation_error = _apply_annotations(
                    ax=ax,
                    annotations=typed_annotations,
                    x_axis_type=x_axis_type,
                    x_positions=category_positions,
                )
                if annotation_error:
                    return {"error": "invalid_annotations", "detail": annotation_error}

            if y_min is not None or y_max is not None:
                ax.set_ylim(bottom=y_min, top=y_max)

            _style_axis(ax, title=title, x_label=x_label, y_label=y_label)
            if len(normalized) > 1:
                handles, labels = ax.get_legend_handles_labels()
                if handles and any(label and not label.startswith("_") for label in labels):
                    ax.legend(frameon=False)

            fig.tight_layout(pad=1.2)
            png_bytes = _to_png(fig)

            publish_cfg = _get_publish_config()
            publish_enabled = publish_cfg.enabled if publish_public_url is None else publish_public_url
            if not publish_enabled:
                return Image(data=png_bytes, format="png")

            url, publish_error = _upload_chart(png_bytes, publish_cfg)
            if publish_error:
                if publish_cfg.strict:
                    return {
                        "error": "chart_publish_failed",
                        "detail": publish_error,
                        "hint": (
                            "Set APSTRA_CHART_PUBLISH_ENABLED=0 to disable publishing, "
                            "fix provider configuration, or unset APSTRA_CHART_PUBLISH_STRICT."
                        ),
                    }
                return [
                    Image(data=png_bytes, format="png"),
                    {
                        "chart_publish_error": publish_error,
                        "provider": publish_cfg.provider,
                        "hint": (
                            "Upload failed; returning chart image only. "
                            "Set APSTRA_CHART_PUBLISH_STRICT=true to treat upload errors as hard failures."
                        ),
                    },
                ]

            assert url is not None
            markdown = f"![{title}]({url})"
            return [
                Image(data=png_bytes, format="png"),
                {
                    "chart_url": url,
                    "provider": publish_cfg.provider,
                    "markdown": markdown,
                    "hint": "Copy markdown into the assistant response to render inline in clients that support external image URLs.",
                },
            ]
        finally:
            plt.close(fig)
