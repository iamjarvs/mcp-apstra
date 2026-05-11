import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from handlers.routing_policy import (
    _build_show_policy_command,
    _categorize_hidden_reason,
    _infer_responsible_term_for_prefix,
    _paginate_list,
    _parse_bgp_neighbor_summary,
    _parse_bgp_summary_peer_row,
    _parse_hidden_routes_text,
    _parse_policy_names,
    _parse_policy_terms,
    handle_routing_policy_intent,
)
from tools.routing_policy import register


class StubMCP:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def decorator(fn):
            self.tools[fn.__name__] = fn
            return fn

        return decorator


def _run(coro):
    return asyncio.run(coro)


class TestRoutingPolicyParsers:
    def test_parse_policy_names(self):
        neighbor = """
        Peer: 192.0.2.1+179 AS 65001
        Import policies: [ IMP-EDGE IMP-SAFETY ]
        Export policy: EXP-EDGE
        """
        parsed = _parse_policy_names(neighbor)
        assert parsed["import"] == ["IMP-EDGE", "IMP-SAFETY"]
        assert parsed["export"] == ["EXP-EDGE"]

    def test_parse_policy_names_nested_labels_and_multiline(self):
        neighbor = """
        Peer: 192.0.2.1+179 AS 65001
        Import policy: Import: RoutesFromExt-default-Default_immutable
        Export policies: [
          Export: RoutesToExt-default-Default_immutable
        ]
        """
        parsed = _parse_policy_names(neighbor)
        assert parsed["import"] == ["RoutesFromExt-default-Default_immutable"]
        assert parsed["export"] == ["RoutesToExt-default-Default_immutable"]

    def test_parse_policy_names_reclassifies_nested_direction_tokens(self):
        neighbor = """
        Peer: 192.0.2.1+179 AS 65001
        Export policies: [ Export: RoutesToExt-default-Default_immutable Import: RoutesFromExt-default-Default_immutable ]
        """
        parsed = _parse_policy_names(neighbor)
        assert parsed["import"] == ["RoutesFromExt-default-Default_immutable"]
        assert parsed["export"] == ["RoutesToExt-default-Default_immutable"]

    def test_build_show_policy_command_quotes_special_characters(self):
        assert _build_show_policy_command("RoutesFromExt-default") == "show policy RoutesFromExt-default"
        assert _build_show_policy_command("Import:") is None
        assert _build_show_policy_command("policy with spaces") == 'show policy "policy with spaces"'

    def test_parse_policy_terms(self):
        policy_text = """
        Policy IMP-EDGE:
          Term allow-default:
            from route-filter 0.0.0.0/0 exact;
            then accept;
          Term reject-rest:
            then reject;
        """
        terms = _parse_policy_terms("IMP-EDGE", policy_text)
        assert len(terms) == 2
        assert terms[0]["term_name"] == "allow-default"
        assert terms[0]["flow_control"] == "accept"
        assert terms[1]["term_name"] == "reject-rest"
        assert terms[1]["flow_control"] == "reject"

    def test_categorize_hidden_reason(self):
        assert _categorize_hidden_reason("Policy rejected") == "policy_rejected"
        assert _categorize_hidden_reason("Unusable next hop") == "next_hop_unusable"
        assert _categorize_hidden_reason("AS path loop") == "as_path_loop"
        assert _categorize_hidden_reason("(Looped: 64514)") == "as_path_loop"

    def test_parse_hidden_routes_text(self):
        hidden = """
inet.0: 2 destinations, 2 routes (0 active, 0 holddown, 2 hidden)
10.10.10.0/24    *[BGP/170] 00:10:11, localpref 100
                      AS path: 65100 I
                      Communities: 65000:100
                      Protocol next hop: 192.0.2.1
                      Inactive reason: Policy rejected
10.20.20.0/24    [BGP/170] 00:03:01, localpref 100
                      Protocol next hop: 192.0.2.2
                      Inactive reason: Unusable
        """
        routes = _parse_hidden_routes_text(hidden)
        assert len(routes) == 2
        assert routes[0]["prefix"] == "10.10.10.0/24"
        assert routes[0]["reason_category"] == "policy_rejected"
        assert routes[1]["reason_category"] == "next_hop_unusable"

    def test_parse_hidden_routes_infers_looped_as_path(self):
        hidden = """
inet.0: 1 destinations, 1 routes (0 active, 0 holddown, 1 hidden)
10.30.30.0/24    [BGP/170] 00:00:11, localpref 100
                      AS path: 64514 64514 I (Looped: 64514)
                      Protocol next hop: 192.0.2.3
        """
        routes = _parse_hidden_routes_text(hidden)
        assert len(routes) == 1
        assert routes[0]["reason_category"] == "as_path_loop"
        assert routes[0]["inactive_reason"]

    def test_parse_bgp_neighbor_summary_ignores_last_flap_event_as_uptime(self):
        text = """
BGP neighbor is 192.0.2.2
  State: Established
    Peer AS: 65099
  Last flap event: RecvNotify
  Received prefixes: 3
  Accepted prefixes: 2
  Advertised prefixes: 1
        """
        parsed = _parse_bgp_neighbor_summary(text, fallback_bfd=True)
        assert parsed["session_state"] == "Established"
        assert parsed["peer_as"] == 65099
        assert parsed["uptime"] is None

    def test_parse_bgp_summary_peer_row_extracts_uptime(self):
        summary = """
Peer               AS      InPkt     OutPkt    OutQ   Flaps Last Up/Dwn State|#Active/Received/Accepted/Damped...
192.0.2.2        65099       10         12       0       3      1w2d    12/12/12/0
        """
        row = _parse_bgp_summary_peer_row(summary, "192.0.2.2")
        assert row["peer_as"] == 65099
        assert row["uptime"] == "1w2d"
        assert row["state"] == "Established"

    def test_infer_responsible_term_for_prefix_respects_term_order(self):
        import_terms = [
            {
                "policy_name": "RoutesFromExt-default-Default_immutable",
                "term_name": "allow-default",
                "match_conditions": ["route-filter 0.0.0.0/0 exact"],
                "flow_control": "accept",
            },
            {
                "policy_name": "RoutesFromExt-default-Default_immutable",
                "term_name": "reject-rest",
                "match_conditions": [],
                "flow_control": "reject",
            },
        ]

        default_route = _infer_responsible_term_for_prefix("0.0.0.0/0", import_terms)
        hidden_route = _infer_responsible_term_for_prefix("10.10.10.0/24", import_terms)

        assert default_route is None
        assert hidden_route == {
            "policy_name": "RoutesFromExt-default-Default_immutable",
            "term_name": "reject-rest",
        }

    def test_paginate_list(self):
        page = _paginate_list(list(range(7)), limit=3, cursor="3")
        assert page["items"] == [3, 4, 5]
        assert page["has_more"] is True
        assert page["next_cursor"] == "6"


class TestRoutingPolicyHandler:
    def test_discover_intent(self):
        sessions = [SimpleNamespace(name="dc-primary")]
        registry = object()

        with patch(
            "handlers.routing_policy.handle_get_systems",
            new=AsyncMock(
                return_value={
                    "instance": "dc-primary",
                    "systems": [
                        {
                            "label": "Leaf1",
                            "hostname": "leaf1",
                            "role": "leaf",
                            "system_id": "SERIAL-1",
                        }
                    ],
                }
            ),
        ):
            with patch(
                "handlers.routing_policy.handle_get_external_peerings",
                new=AsyncMock(
                    return_value={
                        "instance": "dc-primary",
                        "peerings": [
                            {
                                "bfd": True,
                                "ipv4_safi": "enabled",
                                "ipv6_safi": "disabled",
                                "local": {
                                    "hostname": "Leaf1",
                                    "serial": "SERIAL-1",
                                    "interface": "ge-0/0/0",
                                    "local_asn": 65001,
                                },
                                "remote": {
                                    "hostname": "WAN-1",
                                    "ip_address": "192.0.2.2/31",
                                    "local_asn": 65099,
                                },
                            }
                        ],
                    }
                ),
            ):
                result = _run(
                    handle_routing_policy_intent(
                        sessions,
                        registry,
                        blueprint_id="bp-1",
                        intent="discover",
                        limit=10,
                    )
                )

        assert result["intent"] == "discover"
        assert result["summary"]["device_count"] == 1
        assert result["summary"]["external_peer_count"] == 1
        assert result["available"]["external_peers"][0]["peer_ip"] == "192.0.2.2"


class TestRoutingPolicyTool:
    def test_multi_blueprint_wrapper(self):
        mcp = StubMCP()
        register(mcp)
        tool = mcp.tools["routing_policy"]

        ctx = SimpleNamespace(
            lifespan_context={
                "sessions": [SimpleNamespace(name="dc-primary")],
                "graph_registry": object(),
            }
        )

        with patch(
            "tools.routing_policy.resolve_blueprints",
            new=AsyncMock(
                return_value=[
                    {"id": "bp-1", "label": "DC1", "instance_name": "dc-primary"},
                    {"id": "bp-2", "label": "DC2", "instance_name": "dc-primary"},
                ]
            ),
        ):
            with patch(
                "tools.routing_policy.handle_routing_policy_intent",
                new=AsyncMock(side_effect=[{"intent": "discover"}, {"intent": "discover"}]),
            ):
                result = _run(tool(intent="discover", blueprint_id="dc", ctx=ctx))

        assert result["blueprint_count"] == 2
        assert len(result["results"]) == 2
