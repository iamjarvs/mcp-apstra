# Apstra MCP Server — Hands-On Lab Guide

A complete, screenshot-driven walkthrough for installing the **apstra-mcp** server
and using every one of its options. It takes you from an empty terminal to a fully
configured, working MCP server that your AI client (VS Code Copilot, Claude Desktop,
or Cursor) can use to query and troubleshoot a Juniper Apstra fabric.

Every terminal image in this guide is a real capture of the commands run against
this repository — not a mock-up.

---

## Who this guide is for

This guide serves two readers at once:

- **New to MCP / new to this server?** Follow the sections in order. Each one explains
  *why* a step matters before showing you *how*, and ends with something you can verify.
- **Experienced?** Jump straight to the [Fast path](#the-fast-path-5-minutes) below, then
  dip into the [Tool reference](07-tools-reference.md) and
  [Configuration reference](08-configuration-reference.md) as needed.

> **What is apstra-mcp?** A **read-only** [FastMCP](https://gofastmcp.com) server that
> exposes Juniper Apstra as a set of tools an LLM can call: live fabric state, design
> intent, JunOS `show` commands via Apstra, anomaly timelines, and commit-blocker
> diagnostics. It never changes your network. See [Introduction](01-introduction.md).

---

## The fast path (5 minutes)

For readers who already know the drill:

```bash
git clone <your-repo-url> apstra-mcp-server
cd apstra-mcp-server

python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Write config/instances.yaml + .vscode/mcp.json (prompts for password)
apstra-mcp-setup --host https://apstra.example.com --username admin

# Verify connectivity, then run
python tests/diagnose_connection.py
python server.py
```

Then point your MCP client at the server — see [Connect a client](05-connect-clients.md).

---

## Contents

| # | Section | What you get |
|---|---------|--------------|
| 1 | [Introduction](01-introduction.md) | What MCP is, what this server does, the read-only safety model, and how data flows |
| 2 | [Prerequisites](02-prerequisites.md) | The exact tools and versions you need before you start |
| 3 | [Install the server](03-install.md) | Clone, virtual environment, editable install, and the `uvx` alternative |
| 4 | [Configure with the setup wizard](04-configure.md) | Every `apstra-mcp-setup` option, generated files, multi-instance, and secrets handling |
| 5 | [Connect an MCP client](05-connect-clients.md) | VS Code Copilot, Claude Desktop, Cursor, and running from source |
| 6 | [Validate & run](06-validate-and-run.md) | Connection diagnostics, starting the server, transports, and the test suite |
| 7 | [Tool reference](07-tools-reference.md) | The compact vs full tool surfaces, every tool group, and example prompts |
| 8 | [Configuration reference](08-configuration-reference.md) | All environment variables, RAG docs search, and chart publishing |
| 9 | [Troubleshooting](09-troubleshooting.md) | The failures you will actually hit, and how to fix them |

---

## A note on safety

This server is **read-only** by design — it queries Apstra and runs JunOS `show`
commands, but never pushes configuration. The one thing you must protect is your
**credentials**: the setup wizard writes them to `config/instances.yaml`, which is
git-ignored. Prefer per-instance environment variables for secrets where you can.
See [Configure](04-configure.md#45-keeping-credentials-safe).
