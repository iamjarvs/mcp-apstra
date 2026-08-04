# 3. Install the server

> **Why a virtual environment?** The server pins specific versions of FastMCP, Kuzu,
> matplotlib, and pydantic. A virtual environment keeps those out of your system Python so
> nothing else on your machine breaks — and so you can delete it cleanly later.

There are two ways to install. Most people want **Option A** (editable install into a
venv). If you only ever plan to *run* the server from an MCP client and never hack on it,
**Option B** (`uvx`) skips the manual install entirely.

---

## Option A — Editable install into a virtual environment (recommended)

Clone the repo, create a virtual environment, activate it, and install the package with
its development extras:

```bash
git clone <your-repo-url> apstra-mcp-server
cd apstra-mcp-server

python3 -m venv .venv
source .venv/bin/activate

pip install -e ".[dev]"
```

![Cloning the repo and installing with pip install -e ".[dev]"](images/02_install.png)

What just happened:

- `pip install -e ".[dev]"` did an **editable** install — your working copy *is* the
  installed package, so edits take effect without reinstalling.
- The `[dev]` extra pulls in the test tooling (`pytest`, `pytest-asyncio`) so you can run
  the suite in [Section 6](06-validate-and-run.md). For a runtime-only install, use
  `pip install -e .`.
- Two **console commands** were created on your `PATH` (inside the venv):

  | Command | Equivalent | Purpose |
  |---------|------------|---------|
  | `apstra-mcp` | `python server.py` | Start the MCP server |
  | `apstra-mcp-setup` | `python setup_cli.py` | Run the configuration wizard |

> **Windows note:** activate with `.venv\Scripts\activate` instead of `source .venv/bin/activate`.

### Verify the install

```bash
apstra-mcp-setup --help
```

If you see the wizard's usage text (covered in [Section 4](04-configure.md)), the install
succeeded.

---

## Option B — Run from source with `uvx` (no manual install)

If you have [`uv`](https://docs.astral.sh/uv/) installed, you can skip the venv entirely.
`uvx` builds an ephemeral environment from the project and runs the entry point:

```bash
# From inside the cloned repo
uvx --from . apstra-mcp
```

This is exactly what the generated VS Code config uses (see
[Connect a client](05-connect-clients.md)), which is why `uvx` is convenient: your MCP
client can launch the server on demand without you keeping a venv activated.

> You still need to **configure** the server (Section 4) before it can reach Apstra —
> `uvx` only handles the Python environment, not your credentials.

---

**Next:** [4. Configure with the setup wizard →](04-configure.md)
