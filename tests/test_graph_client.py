import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from primitives.graph_client import ApstraKuzuGraph, BlueprintGraphRegistry


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def make_session(name="dc-primary"):
    session = MagicMock()
    session.name = name
    return session


# Minimal graph data with two node types and one relationship type
NODES = {
    "n1": {"id": "n1", "type": "system", "label": "spine-1"},
    "n2": {"id": "n2", "type": "system", "label": "leaf-1"},
    "n3": {"id": "n3", "type": "interface", "label": "et-0/0/0"},
}

RELATIONSHIPS = {
    "r1": {"id": "r1", "type": "hosted_interfaces", "source_id": "n1", "target_id": "n3"},
}

RAW_BLUEPRINT_RESPONSE = {
    "id": "bp-001",
    "label": "DC1",
    "version": 10,
    "nodes": NODES,
    "relationships": RELATIONSHIPS,
}

RAW_BLUEPRINT_RESPONSE_V2 = {
    **RAW_BLUEPRINT_RESPONSE,
    "version": 11,
}

# Version map returned by get_blueprint_versions (cheap list call)
VERSION_MAP_V10 = {"bp-001": 10}
VERSION_MAP_V11 = {"bp-001": 11}
VERSION_MAP_TWO_BPS = {"bp-001": 10, "bp-002": 10}


# ---------------------------------------------------------------------------
# ApstraKuzuGraph — basic build and query
# ---------------------------------------------------------------------------

class TestApstraKuzuGraph:
    def test_rebuild_from_dicts_builds_queryable_graph(self):
        graph = ApstraKuzuGraph(db_path=":memory:")
        stats = graph.rebuild_from_dicts(NODES, RELATIONSHIPS)
        graph.close()

        assert stats.nodes_total == 3
        assert stats.relationships_total == 1
        assert stats.node_type_count == 2  # system, interface
        assert stats.relationship_type_count == 1
        assert stats.skipped_relationships == 0

    def test_query_returns_nodes(self):
        graph = ApstraKuzuGraph(db_path=":memory:")
        graph.rebuild_from_dicts(NODES, RELATIONSHIPS)
        rows = graph.query("MATCH (n:system) RETURN n.id, n.label ORDER BY n.id")
        graph.close()

        assert len(rows) == 2
        ids = {r["n.id"] for r in rows}
        assert ids == {"n1", "n2"}

    def test_query_returns_relationship(self):
        graph = ApstraKuzuGraph(db_path=":memory:")
        graph.rebuild_from_dicts(NODES, RELATIONSHIPS)
        rows = graph.query(
            "MATCH (src:system)-[r]->(dst:interface) RETURN src.id, dst.id"
        )
        graph.close()

        assert len(rows) == 1
        assert rows[0]["src.id"] == "n1"
        assert rows[0]["dst.id"] == "n3"

    def test_skipped_relationship_with_missing_node(self):
        rels_with_bad = {
            **RELATIONSHIPS,
            "r_bad": {"id": "r_bad", "type": "hosted_interfaces", "source_id": "n_missing", "target_id": "n1"},
        }
        graph = ApstraKuzuGraph(db_path=":memory:")
        stats = graph.rebuild_from_dicts(NODES, rels_with_bad)
        graph.close()

        assert stats.skipped_relationships == 1

    def test_empty_graph_builds_without_error(self):
        graph = ApstraKuzuGraph(db_path=":memory:")
        stats = graph.rebuild_from_dicts({}, {})
        graph.close()

        assert stats.nodes_total == 0
        assert stats.relationships_total == 0

    def test_rebuild_resets_previous_data(self):
        graph = ApstraKuzuGraph(db_path=":memory:")
        graph.rebuild_from_dicts(NODES, RELATIONSHIPS)
        # Rebuild with empty data — nodes from first build must be gone
        graph.rebuild_from_dicts({}, {})
        rows = graph.query("MATCH (n) RETURN n")
        graph.close()

        assert rows == []

    def test_context_manager_closes_cleanly(self):
        with ApstraKuzuGraph(db_path=":memory:") as graph:
            graph.rebuild_from_dicts(NODES, RELATIONSHIPS)
            rows = graph.query("MATCH (n:system) RETURN n.id")
        assert len(rows) == 2
        # After exit, connection should be None
        assert graph._conn is None

    def test_all_null_property_added_to_schema_as_string(self):
        """A property present in the API data but always null must be queryable.

        This covers the case where an Apstra field exists in the response JSON
        but carries null for every node (e.g. ipv6_subnet on IPv4-only networks).
        Previously _infer_kuzu_type returned None, the property was skipped from
        the schema, and any Cypher referencing it raised 'Cannot find property'.
        Now it defaults to STRING so queries return null rather than failing.
        """
        nodes = {
            "vn1": {"id": "vn1", "type": "virtual_network", "label": "Net-A",
                    "ipv6_subnet": None, "vn_id": "10000"},
            "vn2": {"id": "vn2", "type": "virtual_network", "label": "Net-B",
                    "ipv6_subnet": None, "vn_id": "10001"},
        }
        graph = ApstraKuzuGraph(db_path=":memory:")
        graph.rebuild_from_dicts(nodes, {})
        rows = graph.query("MATCH (vn:virtual_network) RETURN vn.label, vn.ipv6_subnet ORDER BY vn.label")
        graph.close()

        assert len(rows) == 2
        assert rows[0]["vn.label"] == "Net-A"
        assert rows[0]["vn.ipv6_subnet"] is None
        assert rows[1]["vn.ipv6_subnet"] is None


# ---------------------------------------------------------------------------
# BlueprintGraphRegistry
# ---------------------------------------------------------------------------

class TestBlueprintGraphRegistry:
    async def test_builds_graph_on_first_access(self):
        session = make_session()
        registry = BlueprintGraphRegistry()

        with patch("primitives.graph_client.get_blueprint_versions", new=AsyncMock(return_value=VERSION_MAP_V10)), \
             patch("primitives.graph_client.get_blueprint_graph", new=AsyncMock(return_value=RAW_BLUEPRINT_RESPONSE)):
            graph = await registry.get_or_rebuild(session, "bp-001")

        rows = graph.query("MATCH (n:system) RETURN n.id")
        registry.close_all()
        assert len(rows) == 2

    async def test_cache_hit_calls_versions_only(self):
        """On a cache hit, only the cheap version check should be called."""
        session = make_session()
        registry = BlueprintGraphRegistry()
        mock_versions = AsyncMock(return_value=VERSION_MAP_V10)
        mock_graph_fetch = AsyncMock(return_value=RAW_BLUEPRINT_RESPONSE)

        with patch("primitives.graph_client.get_blueprint_versions", new=mock_versions), \
             patch("primitives.graph_client.get_blueprint_graph", new=mock_graph_fetch):
            graph1 = await registry.get_or_rebuild(session, "bp-001")
            graph2 = await registry.get_or_rebuild(session, "bp-001")

        assert graph1 is graph2
        assert mock_versions.call_count == 2       # checked every time
        assert mock_graph_fetch.call_count == 1    # full fetch only on first build
        registry.close_all()

    async def test_rebuilds_when_version_changes(self):
        session = make_session()
        registry = BlueprintGraphRegistry()

        with patch("primitives.graph_client.get_blueprint_versions", new=AsyncMock(return_value=VERSION_MAP_V10)), \
             patch("primitives.graph_client.get_blueprint_graph", new=AsyncMock(return_value=RAW_BLUEPRINT_RESPONSE)):
            graph1 = await registry.get_or_rebuild(session, "bp-001")

        with patch("primitives.graph_client.get_blueprint_versions", new=AsyncMock(return_value=VERSION_MAP_V11)), \
             patch("primitives.graph_client.get_blueprint_graph", new=AsyncMock(return_value=RAW_BLUEPRINT_RESPONSE_V2)):
            graph2 = await registry.get_or_rebuild(session, "bp-001")

        assert graph1 is not graph2
        registry.close_all()

    async def test_version_change_triggers_full_fetch(self):
        """On a version change, both the version check AND the full fetch must fire."""
        session = make_session()
        registry = BlueprintGraphRegistry()
        mock_graph_fetch = AsyncMock(return_value=RAW_BLUEPRINT_RESPONSE)

        with patch("primitives.graph_client.get_blueprint_versions", new=AsyncMock(return_value=VERSION_MAP_V10)), \
             patch("primitives.graph_client.get_blueprint_graph", new=mock_graph_fetch):
            await registry.get_or_rebuild(session, "bp-001")

        mock_graph_fetch_v2 = AsyncMock(return_value=RAW_BLUEPRINT_RESPONSE_V2)
        with patch("primitives.graph_client.get_blueprint_versions", new=AsyncMock(return_value=VERSION_MAP_V11)), \
             patch("primitives.graph_client.get_blueprint_graph", new=mock_graph_fetch_v2):
            await registry.get_or_rebuild(session, "bp-001")

        assert mock_graph_fetch_v2.call_count == 1
        registry.close_all()

    async def test_separate_blueprints_get_separate_graphs(self):
        session = make_session()
        registry = BlueprintGraphRegistry()

        with patch("primitives.graph_client.get_blueprint_versions", new=AsyncMock(return_value=VERSION_MAP_TWO_BPS)), \
             patch("primitives.graph_client.get_blueprint_graph", new=AsyncMock(return_value=RAW_BLUEPRINT_RESPONSE)):
            graph_a = await registry.get_or_rebuild(session, "bp-001")
            graph_b = await registry.get_or_rebuild(session, "bp-002")

        assert graph_a is not graph_b
        registry.close_all()

    async def test_separate_instances_get_separate_graphs(self):
        session_a = make_session("dc-primary")
        session_b = make_session("dc-secondary")
        registry = BlueprintGraphRegistry()

        with patch("primitives.graph_client.get_blueprint_versions", new=AsyncMock(return_value=VERSION_MAP_V10)), \
             patch("primitives.graph_client.get_blueprint_graph", new=AsyncMock(return_value=RAW_BLUEPRINT_RESPONSE)):
            graph_a = await registry.get_or_rebuild(session_a, "bp-001")
            graph_b = await registry.get_or_rebuild(session_b, "bp-001")

        assert graph_a is not graph_b
        registry.close_all()

    async def test_raises_key_error_for_unknown_blueprint(self):
        session = make_session()
        registry = BlueprintGraphRegistry()

        with patch("primitives.graph_client.get_blueprint_versions", new=AsyncMock(return_value={})):
            with pytest.raises(KeyError, match="bp-missing"):
                await registry.get_or_rebuild(session, "bp-missing")

    async def test_version_api_error_propagates(self):
        session = make_session()
        registry = BlueprintGraphRegistry()

        with patch("primitives.graph_client.get_blueprint_versions",
                   new=AsyncMock(side_effect=Exception("network error"))):
            with pytest.raises(Exception, match="network error"):
                await registry.get_or_rebuild(session, "bp-001")

    async def test_graph_fetch_api_error_propagates(self):
        session = make_session()
        registry = BlueprintGraphRegistry()

        with patch("primitives.graph_client.get_blueprint_versions", new=AsyncMock(return_value=VERSION_MAP_V10)), \
             patch("primitives.graph_client.get_blueprint_graph",
                   new=AsyncMock(side_effect=Exception("fetch failed"))):
            with pytest.raises(Exception, match="fetch failed"):
                await registry.get_or_rebuild(session, "bp-001")

    def test_close_all_clears_registry(self):
        registry = BlueprintGraphRegistry()
        mock_graph = MagicMock()
        registry._graphs[("dc-primary", "bp-001")] = (mock_graph, 1)

        registry.close_all()

        mock_graph.close.assert_called_once()
        assert len(registry._graphs) == 0
