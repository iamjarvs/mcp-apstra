#!/usr/bin/env bash
# Capture driver for the Apstra MCP lab guide.
# Produces sanitized terminal transcripts in lab-guide/tools/transcripts/.
# All commands run against an ISOLATED workspace copy with FAKE credentials,
# so the user's real config/instances.yaml is never touched or exposed.
#
# REPRODUCIBILITY: this script expects an isolated checkout + venv at
# $WS (a `git archive HEAD` copy with `pip install -e ".[dev]"`). That
# scaffolding is not committed. Recreate it, point $WS at it, and re-run.
# Steps [2] and [3] also read two raw inputs you capture manually first:
#   transcripts/install.txt     <- output of `pip install -e ".[dev]"`
#   transcripts/setup_help.txt  <- output of `apstra-mcp-setup --help`
# The generated transcripts are what tools/render_all.sh turns into images/.
set -u

REPO="/Users/ajarvis/Documents/Coding/v2_apstra-mcp-server-v2"
WS="$REPO/lab-guide/_capture_ws/ws"
CAP="$REPO/lab-guide/tools/transcripts"
PY="$WS/.venv/bin/python"
SETUP="$WS/.venv/bin/apstra-mcp-setup"
MCPBIN="$WS/.venv/bin/apstra-mcp"
mkdir -p "$CAP"
cd "$WS" || exit 1

# Fake, safe values used everywhere.
HOST="https://apstra.example.com"
USER="admin"
PASS='ExamplePassw0rd!'
NAME="dc-primary"
DISPLAY_PATH="~/apstra-mcp-server"

# Sanitize absolute paths / home in any captured text.
sanitize() {
  sed -e "s#file://$WS#file:///Users/you/apstra-mcp-server#g" \
      -e "s#$WS#$DISPLAY_PATH#g" \
      -e "s#$REPO/lab-guide/_capture_ws/ws#$DISPLAY_PATH#g" \
      -e "s#/Users/ajarvis#/Users/you#g"
}

p() { printf '\xe2\x9d\xaf %s\n' "$1"; }   # prompt line marker: "❯ <cmd>"

BASEARGS=(--workspace "$WS" --instance-name "$NAME" --host "$HOST" \
          --username "$USER" --password "$PASS" --non-interactive --ui plain)

echo "[1/13] prereqs"
{
  p "python3 --version"; python3.13 --version 2>&1
  p "git --version";     git --version 2>&1
  p "uv --version";      uv --version 2>&1
} | sanitize > "$CAP/01_prereqs.txt"

echo "[2/13] install (trimmed from real log)"
{
  p "git clone <your-repo-url> apstra-mcp-server"
  p "cd apstra-mcp-server"
  p "python3 -m venv .venv"
  p "source .venv/bin/activate"
  p "pip install -e \".[dev]\""
  grep -E "^Obtaining " "$CAP/install.txt" | head -1
  echo "  Installing build dependencies: ... done"
  echo "  Preparing editable metadata (pyproject.toml): ... done"
  grep -E "^Collecting " "$CAP/install.txt" | head -8
  echo "  ... (resolving remaining dependencies) ..."
  echo "Building wheels for collected packages: apstra-mcp"
  echo "  Building editable for apstra-mcp (pyproject.toml): ... done"
  echo "Successfully built apstra-mcp"
  # Trim the very long "Successfully installed" line to key packages.
  grep -E "^Successfully installed " "$CAP/install.txt" \
    | tr ' ' '\n' | grep -E "^(apstra-mcp|fastmcp|kuzu|httpx|matplotlib|pydantic|pyyaml|pillow)-" \
    | paste -sd ' ' - \
    | sed 's/^/Successfully installed /; s/$/ (+ 50 more)/'
} | sanitize > "$CAP/02_install.txt"

echo "[3/13] setup --help"
{ p "apstra-mcp-setup --help"; cat "$CAP/setup_help.txt"; } | sanitize > "$CAP/03_setup_help.txt"

echo "[4/13] setup --dry-run"
{
  p "apstra-mcp-setup --host $HOST --username $USER --dry-run"
  "$SETUP" "${BASEARGS[@]}" --dry-run 2>&1
} | sanitize > "$CAP/04_setup_dryrun.txt"

echo "[5/13] setup write"
{
  p "apstra-mcp-setup --host $HOST --username $USER"
  "$SETUP" "${BASEARGS[@]}" --overwrite --overwrite-mcp-server 2>&1
} | sanitize > "$CAP/05_setup_write.txt"

echo "[6/13] generated instances.yaml"
{ p "cat config/instances.yaml"; cat "$WS/config/instances.yaml" 2>&1; } | sanitize > "$CAP/06_instances_yaml.txt"

echo "[7/13] generated mcp.json"
{ p "cat .vscode/mcp.json"; cat "$WS/.vscode/mcp.json" 2>&1; } | sanitize > "$CAP/07_mcp_json.txt"

echo "[8/13] compact tool surface"
{
  p "python -c \"import asyncio, server; ts=asyncio.run(server.mcp.list_tools()); print('\\\\n'.join(sorted(t.name for t in ts)))\""
  "$PY" -c "import asyncio, server; ts=asyncio.run(server.mcp.list_tools()); print('\n'.join(sorted(t.name for t in ts)))" 2>&1
} | sanitize > "$CAP/08_tools_compact.txt"

echo "[9/13] full tool surface"
{
  p "MCP_TOOL_SURFACE=full python -c \"import asyncio, server; ...list_tools()...\""
  MCP_TOOL_SURFACE=full "$PY" -c "import asyncio, server; ts=asyncio.run(server.mcp.list_tools()); print('\n'.join(sorted(t.name for t in ts)))" 2>&1
} | sanitize > "$CAP/09_tools_full.txt"

echo "[10/13] diagnose_connection"
{
  p "python tests/diagnose_connection.py"
  "$PY" tests/diagnose_connection.py 2>&1
} | sanitize > "$CAP/10_diagnose.txt"

echo "[11/13] pytest"
{
  p "pytest -q"
  "$PY" -m pytest -q -p no:cacheprovider --color=no 2>&1 | tail -30
} | sanitize > "$CAP/11_pytest.txt"

echo "[12/13] server start (EOF on stdin so it exits cleanly)"
{
  p "python server.py"
  MCP_VERBOSE=1 "$PY" server.py < /dev/null 2>&1 | head -25
} | sanitize > "$CAP/12_server_start.txt"

echo "[13/13] setup --configure-claude --dry-run"
{
  p "apstra-mcp-setup --host $HOST --username $USER --configure-claude --dry-run"
  "$SETUP" "${BASEARGS[@]}" --configure-claude --dry-run \
     --claude-config-path "$CAP/_fake_claude.json" 2>&1
} | sanitize > "$CAP/13_setup_claude.txt"
rm -f "$CAP/_fake_claude.json"

echo "DONE. Files:"
ls -1 "$CAP"/*.txt
