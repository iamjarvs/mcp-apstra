#!/usr/bin/env bash
# Render every captured transcript into a terminal-window PNG for the lab guide.
set -eu
REPO="/Users/ajarvis/Documents/Coding/v2_apstra-mcp-server-v2"
PY="$REPO/.venv/bin/python"
R="$PY $REPO/lab-guide/tools/render_terminal.py"
CAP="$REPO/lab-guide/tools/transcripts"
IMG="$REPO/lab-guide/images"
mkdir -p "$IMG"

render() { $R "$CAP/$1" "$IMG/$2" --title "$3"; }

render 01_prereqs.txt        01_prerequisites.png     "zsh — check prerequisites"
render 02_install.txt        02_install.png           "zsh — clone & install"
render 03_setup_help.txt     03_setup_help.png        "zsh — apstra-mcp-setup --help"
render 04_setup_dryrun.txt   04_setup_dryrun.png      "zsh — setup wizard (dry run)"
render 05_setup_write.txt    05_setup_write.png       "zsh — setup wizard"
render 06_instances_yaml.txt 06_instances_yaml.png    "zsh — config/instances.yaml"
render 07_mcp_json.txt        07_mcp_json.png         "zsh — .vscode/mcp.json"
render 08_tools_compact.txt  08_tools_compact.png     "zsh — compact tool surface"
render 09_tools_full.txt     09_tools_full.png        "zsh — full tool surface"
render 10_diagnose.txt        10_diagnose.png         "zsh — diagnose_connection.py"
render 11_pytest.txt          11_pytest.png           "zsh — pytest"
render 12_server_start.txt   12_server_start.png      "zsh — python server.py"
render 13_setup_claude.txt   13_setup_claude.png      "zsh — configure Claude Desktop"
echo "--- rendered images ---"
ls -1 "$IMG"
