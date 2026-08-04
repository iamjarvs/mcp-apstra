# Lab guide tooling

These scripts reproduce the terminal screenshots used throughout the guide. They exist so
the images can be regenerated deterministically instead of hand-cropped.

| File | Purpose |
|------|---------|
| `render_terminal.py` | Renders one sanitized transcript into a macOS-style terminal-window PNG (Dracula palette, Menlo font, syntax-aware line colouring). Usage: `render_terminal.py INPUT OUTPUT --title "..."`. |
| `render_all.sh` | Renders every transcript in `transcripts/` to `../images/` with descriptive titles. |
| `capture.sh` | The original capture driver. Runs each command against an **isolated** checkout with **fake** credentials and writes sanitized transcripts to `transcripts/`. |
| `transcripts/` | The 13 captured, sanitized terminal transcripts (plus two raw source captures) that `render_all.sh` turns into images. |

## Regenerate the images

The transcripts are already captured, so re-rendering the PNGs just needs the repo's
virtual environment (Pillow is included):

```bash
bash tools/render_all.sh
```

## Re-capture from scratch (optional)

`capture.sh` needs an isolated checkout + venv (a `git archive HEAD` copy with
`pip install -e ".[dev]"`) so it never touches your real `config/instances.yaml`. That
scaffolding is intentionally not committed. Recreate it, point the script's `$WS` at it,
then run `bash tools/capture.sh` followed by `bash tools/render_all.sh`.

All captures use **fake** placeholder credentials
(`https://apstra.example.com` / `admin`), so no real secrets ever appear in the images.
