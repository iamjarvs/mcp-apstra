#!/usr/bin/env python3
"""
render_terminal.py — render a captured terminal transcript into a polished,
macOS-style "terminal window" PNG for the Apstra MCP lab guide.

Transcript format
-----------------
* A line beginning with "❯ " is treated as a typed command (rendered bright/bold
  with a green prompt glyph).
* All other lines are program output. A small set of well-known status tokens
  ([OK], WARNING, ERROR, ✓, ✗, "passed", "Successfully", [n/m], [Section]) are
  colourised so the screenshots read clearly, while the text stays faithful to
  what the command actually printed.

Usage
-----
    python render_terminal.py INPUT.txt OUTPUT.png --title "zsh — apstra-mcp"
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# --- Dracula-ish palette -----------------------------------------------------
BG = (40, 42, 54)          # window body
TITLEBAR = (33, 34, 45)    # title bar
BORDER = (58, 60, 78)
TEXT = (248, 248, 242)     # default foreground
GREEN = (80, 250, 123)
CYAN = (139, 233, 253)
YELLOW = (241, 250, 140)
RED = (255, 95, 95)
ORANGE = (255, 184, 108)
PURPLE = (189, 147, 249)
DIM = (120, 132, 170)
CANVAS = (236, 238, 241)   # neutral page behind the window
SHADOW = (0, 0, 0, 70)

TL_RED = (255, 95, 86)
TL_YELLOW = (255, 189, 46)
TL_GREEN = (39, 201, 63)

FONT_CANDIDATES = [
    "/System/Library/Fonts/Menlo.ttc",
    "/System/Library/Fonts/Monaco.ttf",
    "/System/Library/Fonts/Courier.ttc",
]


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    for path in FONT_CANDIDATES:
        try:
            # Menlo.ttc: index 0 = Regular, 1 = Bold, 2 = Italic, 3 = Bold Italic
            idx = 1 if (bold and path.endswith("Menlo.ttc")) else 0
            return ImageFont.truetype(path, size, index=idx)
        except Exception:
            continue
    return ImageFont.load_default()


def classify(line: str) -> str:
    s = line.rstrip("\n")
    if s.lstrip().startswith("❯"):
        return "cmd"
    if re.search(r"\[OK\]|Successfully|\bpassed\b|✓|status 'done'|\bdone\b", s):
        return "green"
    if re.search(r"\[WARN\]|WARNING|Will retry", s):
        return "yellow"
    if re.search(r"\bERROR\b|Error:|✗|Traceback|Failed|\bfailed\b|Errno|ConnectError|Unauthorized", s):
        return "red"
    if re.match(r"\s*\[\d+/\d+\]", s) or re.match(r"\s*\[[A-Z][a-z].*\]\s*$", s):
        return "cyan"
    if s.strip() and re.match(r"^[═╭╮╰╯│┌┐└┘─▄▀█\s🖥🚀]+$", s):
        return "purple"
    if re.match(r"^=+$", s.strip()):
        return "dim"
    return "text"


COLOR = {
    "green": GREEN, "yellow": YELLOW, "red": RED, "cyan": CYAN,
    "purple": PURPLE, "dim": DIM, "text": TEXT,
}


def wrap(line: str, cols: int) -> list[str]:
    if line == "":
        return [""]
    out: list[str] = []
    while len(line) > cols:
        cut = line.rfind(" ", 0, cols)
        if cut <= 0:
            cut = cols
        out.append(line[:cut])
        line = line[cut:].lstrip(" ") if cut < len(line) and line[cut] == " " else line[cut:]
    out.append(line)
    return out


# Emoji that Menlo cannot render as monospace; replace with two spaces so any
# surrounding ASCII box art stays aligned.
_EMOJI = {"\U0001F5A5": "  ", "\U0001F680": "  ", "\uFE0F": ""}


def _strip_emoji(text: str) -> str:
    for k, v in _EMOJI.items():
        text = text.replace(k, v)
    return text


def render(input_path: Path, output_path: Path, title: str) -> None:
    raw = _strip_emoji(input_path.read_text(encoding="utf-8", errors="replace")).rstrip("\n").split("\n")

    scale = 2
    fsize = 15 * scale
    reg = _font(fsize)
    bold = _font(fsize, bold=True)
    ui = _font(12 * scale)

    # Measure a monospace cell.
    cw = reg.getbbox("M")[2] - reg.getbbox("M")[0]
    lh = int(fsize * 1.45)

    longest = max((len(l) for l in raw), default=40)
    cols = min(96, max(longest, 46))

    # Wrap and record (text, kind).
    rendered: list[tuple[str, str]] = []
    for line in raw:
        kind = classify(line)
        for chunk in wrap(line, cols):
            rendered.append((chunk, kind))

    pad = 18 * scale
    titlebar_h = 30 * scale
    body_w = cols * cw + pad * 2
    body_h = titlebar_h + pad + len(rendered) * lh + pad

    margin = 26 * scale
    W = body_w + margin * 2
    H = body_h + margin * 2

    canvas = Image.new("RGB", (W, H), CANVAS)

    # Drop shadow.
    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    radius = 12 * scale
    sd.rounded_rectangle(
        [margin + 4 * scale, margin + 7 * scale, margin + body_w + 4 * scale, margin + body_h + 7 * scale],
        radius=radius, fill=SHADOW,
    )
    from PIL import ImageFilter
    shadow = shadow.filter(ImageFilter.GaussianBlur(9 * scale // 2))
    canvas.paste(Image.new("RGB", (W, H), CANVAS), (0, 0))
    canvas = Image.alpha_composite(canvas.convert("RGBA"), shadow).convert("RGB")

    d = ImageDraw.Draw(canvas)
    x0, y0 = margin, margin
    x1, y1 = margin + body_w, margin + body_h

    # Window body + title bar.
    d.rounded_rectangle([x0, y0, x1, y1], radius=radius, fill=BG, outline=BORDER, width=scale)
    d.rounded_rectangle([x0, y0, x1, y0 + titlebar_h], radius=radius, fill=TITLEBAR)
    d.rectangle([x0, y0 + titlebar_h - radius, x1, y0 + titlebar_h], fill=TITLEBAR)
    d.line([x0, y0 + titlebar_h, x1, y0 + titlebar_h], fill=BORDER, width=scale)

    # Traffic lights.
    cy = y0 + titlebar_h // 2
    r = 6 * scale
    for i, col in enumerate((TL_RED, TL_YELLOW, TL_GREEN)):
        cx = x0 + pad + i * (r * 2 + 6 * scale)
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=col)

    # Title (centred).
    tb = d.textbbox((0, 0), title, font=ui)
    d.text(((x0 + x1) / 2 - (tb[2] - tb[0]) / 2, cy - (tb[3] - tb[1]) / 2 - tb[1]),
           title, font=ui, fill=DIM)

    # Body text.
    tx = x0 + pad
    ty = y0 + titlebar_h + pad
    for text, kind in rendered:
        if kind == "cmd":
            stripped = text.lstrip()
            indent = len(text) - len(stripped)
            gx = tx + indent * cw
            # Prompt glyph in green, command in bright bold white.
            if stripped.startswith("❯"):
                d.text((gx, ty), "❯", font=bold, fill=GREEN)
                rest = stripped[1:]
                d.text((gx + cw, ty), rest, font=bold, fill=TEXT)
            else:
                d.text((gx, ty), stripped, font=bold, fill=TEXT)
        else:
            d.text((tx, ty), text, font=reg, fill=COLOR.get(kind, TEXT))
        ty += lh

    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path)
    print(f"wrote {output_path}  ({W}x{H})")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("output")
    ap.add_argument("--title", default="zsh — apstra-mcp")
    args = ap.parse_args()
    render(Path(args.input), Path(args.output), args.title)


if __name__ == "__main__":
    main()
