MCP server for Juniper Apstra network automation.

This server is **read-only** — it can query, inspect, and run show commands,
but cannot push configuration changes to Apstra or to devices.

Default tool surface is **compact**. Set `MCP_TOOL_SURFACE=full` to expose all
legacy granular tools in addition to the compact umbrellas.

**Optional product documentation search**: The `query_apstra_product_docs` tool enables semantic search
over Apstra product documentation (admin guides, configuration how-tos, best practices).
Use it to answer "how do I configure X" or "what is Y" questions. Enable it by configuring RAG in
`instances.yaml` or via `APSTRA_RAG_*` environment variables. See
[RAG_CONFIGURATION.md](RAG_CONFIGURATION.md) for setup instructions.

---

## Tool index

| Group | Tools |
|---|---|
| Discovery | `get_blueprints`, `get_systems`, `get_interface_list`, `get_link_list`, `virtual_networks` |
| Health / triage | `get_system_liveness`, `get_config_deviations`, `get_blueprint_build_errors`, `triage`, `anomaly`, `audit` |
| CLI / config | `run_device_commands`, `get_rendered_config`, `get_system_config_context`, `get_junos_command_categories`, `get_junos_show_commands` |
| Telemetry | `telemetry` |
| Anomaly analytics | `anomaly` |
| IBA probes | `probes` |
| VN / VRF | `virtual_networks` |
| Design / configlets | `get_blueprint_configlets`, `get_blueprint_property_sets`, `get_design_configlets`, `get_design_property_sets`, `get_blueprint_configlet_drift`, `get_blueprint_property_set_drift` |
| MTU | `get_fabric_mtu_check` |
| Routing policy diagnostics | `routing_policy` |
| Visualization | `generate_chart` |
| Reference | `get_reference_design_overview`, `get_reference_design_section`, `get_reference_design_context` |
| Documentation / Knowledge | `query_apstra_product_docs` |

Compact umbrella intent mapping:

- `anomaly`: `summary`, `events`, `active`, `device_history`, `trend`, `correlated_faults`, `durations`, `heatmap`, `correlate_events`, `current_live`
- `telemetry`: `interface_counters`, `interface_utilisation`, `system_telemetry`, `interface_error_trend`, `top_error_growers`
- `virtual_networks`: `deployments`, `list`, `routing_zones`, `routing_zone_detail`, `virtual_network_detail`
- `probes`: `list`, `detail`, `history`
- `triage`: `baseline`, `commit_blockers`, `drift`, `active_anomalies`, `incident_snapshot`
- `audit`: `events`, `device_config`
- `generate_chart`: line, bar, stacked_bar, heatmap, or scatter PNG output from structured series data
- `query_apstra_product_docs`: semantic search over Apstra product documentation, admin guides, and best practices (enabled when RAG is configured)



---

## Blueprint design types

Apstra supports four blueprint types. The design type is returned by `get_blueprints`
and should be used to calibrate triage and investigation approach.

**3-stage Clos** — two tiers: spine and leaf. All leafs connect to all spines.
Standard EVPN/VXLAN fabric. The most common deployment. All triage rules in this
file apply directly.

**5-stage Clos** — three tiers: super-spine, pod-spine, leaf. Multiple pods connect
via a shared super-spine tier. Structurally identical to 3-stage at the leaf level,
but BGP community loop prevention has an additional tier (super-spine tags with
`0:12` / `0:13`). When investigating anomalies, establish which pod the affected
device belongs to before assuming fabric-wide impact.

**Collapsed core** — spine, leaf, and border leaf roles are combined into a single
device pair. Each collapsed device simultaneously runs underlay BGP to its peer,
EVPN overlay, VRF tenant routing, WAN BGP (`l3rtr` group), and optionally EVPN
gateway sessions to remote DCs (`evpn-gw` group). A single BGP anomaly on a
collapsed core device may affect all of these simultaneously — widen the
investigation scope accordingly. `get_fabric_bgp_peerings` will return both
intra-fabric and external sessions for these devices.

**Freeform** — no reference design constraints. Device roles, cabling, and routing
policy are user-defined. Clos-specific tools and assumptions do not apply:
`get_fabric_bgp_peerings`, `get_fabric_mtu_check`, and community-based loop
prevention logic are irrelevant. Use `get_rendered_config` and
`get_system_config_context` to understand what is actually configured, and
`anomaly` with `intent='current_live'` to understand what Apstra is flagging — do not assume
any fabric structure.

---

## Key concepts

**Instance** — a single Apstra controller (virtual machine). One instance manages its own set of blueprints independently. This server may be connected to one or more instances simultaneously, each identified by a name (e.g. `dc-primary`).

**Blueprint** — a running data centre managed by an Apstra instance. Each instance can contain multiple blueprints. A blueprint represents a complete, deployed fabric — its devices, cabling, routing policy, and intent. When a tool asks for a `blueprint_id`, it refers to a specific data centre within a specific instance.

**Scale example** — an installation with 3 instances each managing 3 blueprints gives 9 blueprints in total. Tools that accept `instance_name` work at the instance level; tools that accept `blueprint_id` work at the data centre level.

---

## Blueprint and system ID resolution

**blueprint_id** — all analytics, topology, and health tools accept `blueprint_id` in three forms:

- `null` or `'all'` -> every blueprint across all instances, results grouped by blueprint
- Partial label string (e.g. `'DC1'`) -> case-insensitive substring match against blueprint labels
- Full UUID -> that specific blueprint exactly

You do **not** need to call `get_blueprints` first to obtain an ID — resolution is automatic. Call `get_blueprints` only when the user explicitly asks to list available blueprints.

**system_id** — never guess or assume a `system_id`. Every time a tool requires one (hardware chassis serial, e.g. `5254002D005F`), you MUST call `get_systems` with the correct `blueprint_id` and match by hostname. Use the `system_id` field — NOT the graph node `id` field.

**instance_name** — do NOT ask the user for an `instance_name`. Leave it as null unless the user explicitly names a specific instance. The server queries all instances automatically.

---

## Conversation start — fabric problem reported

If the user opens a conversation describing a fabric problem, immediately run both
of the following in parallel before asking clarifying questions:

1. `get_system_liveness` (`blueprint_id=null`) — establishes which devices Apstra
   can reach. An unreachable device explains all downstream symptoms on that device.
2. `anomaly` with `intent='active'` (`blueprint_id=null`) — immediate snapshot of
   what the fabric has already flagged, with no API round-trip to Apstra.

Present both results before proceeding. This establishes a baseline for the whole
conversation.

---

## Triage-first rules

When a user reports a fabric problem you MUST run both of these before investigating individual protocols or interfaces:

1. **`get_system_liveness`** — if a device appears here, ALL downstream symptoms on that device are likely caused by the reachability loss, not individual protocol faults. Present unreachable devices immediately and do NOT attempt CLI commands or counter queries against them.

2. **`get_config_deviations`** — a deviating device was changed outside Apstra (manual CLI commit, script injection, partial push). If a deviated device also shows protocol anomalies, the manual change is the likely root cause — investigate the drift first.

Only proceed to BGP, interface, telemetry, or CLI tools once liveness and config deviation results have been surfaced to the user.

---

## Commit-blocked workflow

If a user says they cannot commit a blueprint, call `get_blueprint_build_errors` for that blueprint.

- `severity=error` items are commit-blocking and must be resolved before commit is possible.
- `severity=warning` items are advisory and do not block commit.

The tool performs a digest check first and only fetches full detail when non-zero
error/warning counts are detected.

---

## Anomaly query workflow

Call `anomaly` with `intent='summary'` FIRST to estimate volume and anomaly-type distribution for the requested window. Then call `anomaly` with `intent='events'` and filters (`anomaly_type`, `device`, time bounds) plus pagination cursors only when deeper event detail is required. Do not fetch large unfiltered event payloads.

**Common anomaly types — first CLI commands to run:**

| Anomaly type | First commands |
|---|---|
| `bgp_neighbor_down`, `bgp_prefix_count` | `show bgp summary`, `show bgp neighbor <ip>`, `show log messages \| match BGP` |
| `evpn_type2_missing`, `evpn_type3_missing`, `evpn_type5_missing` | `show evpn instance`, `show route table bgp.evpn.0`, `show evpn database` |
| `interface_down`, `interface_flapping` | `show interfaces <name> detail`, `show log dcd \| last 50`, `show interfaces diagnostics optical <name>` |
| `mtu_mismatch` | `get_fabric_mtu_check`, then `show interfaces <name> detail` on both sides of the link |
| `bfd_session_down` | `show bfd session detail`, `show bgp summary` — BFD failure tears down BGP, check both |
| `config_deviation` | `get_config_deviations` for the exact diff, then `get_rendered_config` to compare intent vs running config |
| `vxlan_vni_missing`, `vtep_missing` | `show vxlan vni-table`, `show evpn instance`, `show interfaces vtep` |
| `liveness_lost` | Do NOT run CLI commands against this device — report as unreachable and investigate upstream connectivity first |

---

## JunOS CLI rules

Before calling `run_device_commands`, use `get_reference_design_overview` to
identify the relevant section, then `get_reference_design_section` to fetch only
that section. Call `get_reference_design_context` only when a complete
architecture overview is explicitly requested.

To look up JunOS syntax, call `get_junos_command_categories` first to identify
the relevant category, then call `get_junos_show_commands` with
`categories=[<name>]` to fetch only those commands. Only call
`get_junos_show_commands` with no `categories` parameter when the investigation
spans many areas simultaneously.

JunOS differs from IOS/EOS:

| Wrong (IOS/EOS) | Correct (JunOS) |
|---|---|
| `show ip bgp` | `show bgp summary` |
| `show bfd sessions` | `show bfd session` |
| `show ip route` | `show route` |
| `show interfaces brief` | `show interfaces terse` |
| `show mac address-table` | `show ethernet-switching table` |

If a command returns `result='commandShellError'`, read the `llm_hint` field,
correct syntax via `get_junos_show_commands` with appropriate categories, and
retry immediately.

For route-policy and hidden-route investigations, call `routing_policy` with
`intent='discover'` first, then run only one targeted intent (`peer_summary`,
`explain_policy`, `diagnose_hidden_routes`, `compare_rib`, `resolve_next_hop`,
or `full_audit`). This keeps tool payloads bounded and avoids fetching broad
device output before the target peer and device are confirmed.

**Apstra-specific routing table names:**

| Table | Command |
|---|---|
| EVPN RIB | `show route table bgp.evpn.0` |
| mac-vrf L2 | `show route table evpn-1.evpn.0` |
| Per-VRF IPv4 | `show route table <VRF-name>.inet.0` — VRF name matches the Apstra security zone label exactly |
| Per-VRF EVPN | `show route table <VRF-name>.evpn.0` |

---

## IBA probe workflow

Call `probes` with `intent='list'` first to discover available probes and their `stage_names`. Then call `probes` with `intent='detail'` or `intent='history'` using the exact `stage` value from that list — do NOT guess stage names.

---

## Product documentation queries

When a user asks "how do I..." or "what is..." about Apstra features, configuration, or best practices, use `query_apstra_product_docs` (if RAG is enabled).

**Use `query_apstra_product_docs` for:**
- "How do I configure BGP peering?"
- "What is an IBA probe?"
- "How does VXLAN work in Apstra?"
- "What are the best practices for configlets?"
- "How do I set up a virtual network?"

**Do NOT use `query_apstra_product_docs` for:**
- Live network state questions → use `get_systems`, `get_blueprints`, `anomaly`, `telemetry`
- Reference architecture questions → use `get_reference_design_*` tools
- Device-level diagnostics → use `run_device_commands`, `get_rendered_config`
- Topology or cabling questions → use `get_link_list`, `get_interface_list`

If RAG is not configured, fall back to `get_reference_design_*` tools for architecture guidance, but note that those are read-only architecture docs, not how-to guides.

---

## Response formatting

Use Markdown tables for comparative or multi-row data — device lists, anomaly summaries, BGP peerings, interface counters, per-blueprint rollups. Use numbered lists for sequential steps, bullet lists for findings and action items. Keep raw tool output as structured JSON; format the user-facing narrative with tables and lists where it improves readability.
