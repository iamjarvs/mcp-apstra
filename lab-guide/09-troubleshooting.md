# 9. Troubleshooting

> **How to use this page:** Find the symptom, apply the fix. Most issues fall into four
> buckets — Python/install, connectivity, client wiring, and tools. When in doubt, run
> `python tests/diagnose_connection.py` first (see [Section 6.1](06-validate-and-run.md#61-test-connectivity-with-the-diagnostics-script)).

---

## Install & environment

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `ERROR: Package requires a different Python` | System `python3` is < 3.10. | Install Python 3.11+/3.13 and create the venv with it, e.g. `python3.13 -m venv .venv`. |
| `apstra-mcp: command not found` | The venv isn't activated, or you used `pip install` without the console scripts. | `source .venv/bin/activate`, then reinstall with `pip install -e ".[dev]"`. You can always fall back to `python server.py` / `python setup_cli.py`. |
| `pip` SSL / certificate errors | A TLS-intercepting proxy on your network. | Use your organisation's trusted CA bundle, or install from a network without interception. |
| `pytest` reports collection errors | A test module needs a live controller or an optional extra. | Exclude them — see the command in [Section 6.2](06-validate-and-run.md#62-run-the-test-suite-optional-but-reassuring). You should still get **853 passed**. |

---

## Connectivity

These map directly to the diagnostics output:

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `Connection failed: ConnectError` / `Connection refused` | Wrong host, controller down, or firewall blocking 443. | Verify the URL in `config/instances.yaml`, confirm the controller is up, and check port 443 reachability (`--check-endpoint` runs a preflight). |
| `SSLCertVerificationError` | `ssl_verify: true` against a self-signed cert. | Set `ssl_verify: false` (the default) for lab/self-signed controllers, or install the CA. |
| `401 Unauthorized` | Wrong username or password. | Fix the credentials in `instances.yaml` or the `APSTRA_<NAME>_USERNAME` / `_PASSWORD` env vars. |
| Server logs a `WARNING`/`ERROR` about auth at startup but keeps running | The controller was unreachable at boot. | This is expected and non-fatal — the server retries auth in the background. Fix reachability and it recovers without a restart. |

---

## Client wiring

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| The `apstra` server doesn't appear in VS Code | No `.vscode/mcp.json`, or it's in the wrong workspace. | Re-run `apstra-mcp-setup`, or copy the `servers.apstra` block into that workspace's `.vscode/mcp.json` and fix the paths. |
| Claude Desktop shows no Apstra tools | Config written but Claude not restarted, or wrong config path. | Restart Claude Desktop; if needed, re-run with `--configure-claude --claude-config-path <path>`. |
| `Refusing to overwrite existing file: ...instances.yaml` | The wizard won't clobber your credential file. | Intentional. Add `--overwrite` only if you really want to replace it. |
| Client launches but tools error immediately | `APSTRA_CONFIG_FILE` in the client env points at the wrong path. | Make it an absolute path to your `config/instances.yaml`. |

---

## Tools & data

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| Expected a granular tool (e.g. `get_current_anomalies`) but it's missing | You're on the compact surface. | Use the umbrella (`anomaly`), or start with `MCP_TOOL_SURFACE=full`. |
| Anomaly timeline / trend is empty | The background pollers haven't populated the stores yet, or they're corrupted. | Give it time after startup; if needed, reset with `MCP_RESET_STORES_ON_START=1`. |
| Topology looks stale after a blueprint change | The graph cache is keyed to blueprint version. | It rebuilds automatically on the next version; retry shortly. |
| `generate_chart` shows no inline URL | Chart publishing is disabled (default). | Enable it — see [Chart publishing](08-configuration-reference.md#84-chart-publishing-optional). |

---

## Turn up the logs

When a symptom isn't obvious, get more detail:

```bash
MCP_VERBOSE=2 python server.py
```

Level `2` logs full debug including payloads. Turn it back to `0` when you're done — it can
expose sensitive data in logs.

---

## Still stuck?

- Re-run the diagnostics: `python tests/diagnose_connection.py`.
- Confirm your install: `pytest -q` (see [Section 6.2](06-validate-and-run.md)).
- Re-read the config precedence in [Section 4.6](04-configure.md#46-configuration-precedence).

---

[← Back to the guide index](README.md)
