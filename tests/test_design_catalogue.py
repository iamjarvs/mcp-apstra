import json
import pytest
from unittest.mock import AsyncMock, MagicMock

from primitives.response_parser import parse_design_configlets, parse_design_property_sets
from handlers.design_catalogue import (
    handle_get_design_configlets,
    handle_get_design_property_sets,
    _compare_generators,
    handle_get_configlet_drift,
    handle_get_property_set_drift,
    _select_sessions,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_session(name="dc-primary"):
    session = MagicMock()
    session.name = name
    return session


def make_registry(configlet_rows=None, propset_rows=None, error=None):
    """
    Returns a mock registry whose graph.query() returns either configlet or
    property set rows depending on the query string it receives.
    """
    registry = MagicMock()
    if error:
        registry.get_or_rebuild = AsyncMock(side_effect=error)
        return registry
    graph = MagicMock()
    def _query(q):
        if "configlet" in q:
            return configlet_rows or []
        return propset_rows or []
    graph.query = MagicMock(side_effect=_query)
    registry.get_or_rebuild = AsyncMock(return_value=graph)
    return registry


# ---------------------------------------------------------------------------
# Fixture data — design catalogue raw API responses
# ---------------------------------------------------------------------------

_CAT_GEN_SAME = {
    "config_style": "junos",
    "template_text": "snmp { community public; }",
    "section": "system",
    "negation_template_text": "",
    "filename": "",
    "render_style": "standard",
}

_CAT_GEN_DRIFTED_NEW = {
    "config_style": "junos",
    "template_text": "snmp { community NEW_COMMUNITY; }",
    "section": "system",
    "negation_template_text": "",
    "filename": "",
    "render_style": "standard",
}

_CAT_GEN_CATALOGUE_ONLY = {
    "config_style": "junos",
    "template_text": "set system ntp server 1.1.1.1",
    "section": "set_based_system",
    "negation_template_text": "",
    "filename": "",
    "render_style": "standard",
}

RAW_DESIGN_CONFIGLETS = {
    "items": [
        {
            "id": "cat-cid-1",
            "display_name": "MyConfiglet",
            "ref_archs": ["two_stage_l3clos"],
            "generators": [_CAT_GEN_SAME],
            "created_at": "2025-01-01T00:00:00.000000Z",
            "last_modified_at": "2025-01-01T00:00:00.000000Z",
        },
        {
            "id": "cat-cid-2",
            "display_name": "DriftedConfiglet",
            "ref_archs": ["two_stage_l3clos"],
            "generators": [_CAT_GEN_DRIFTED_NEW],
            "created_at": "2025-01-01T00:00:00.000000Z",
            "last_modified_at": "2025-06-01T00:00:00.000000Z",
        },
        {
            "id": "cat-cid-3",
            "display_name": "CatalogueOnlyConfiglet",
            "ref_archs": ["two_stage_l3clos"],
            "generators": [_CAT_GEN_CATALOGUE_ONLY],
            "created_at": "2025-01-01T00:00:00.000000Z",
            "last_modified_at": "2025-01-01T00:00:00.000000Z",
        },
    ]
}

PARSED_DESIGN_CONFIGLETS = [
    {
        "id": "cat-cid-1",
        "display_name": "MyConfiglet",
        "ref_archs": ["two_stage_l3clos"],
        "generators": [_CAT_GEN_SAME],
        "created_at": "2025-01-01T00:00:00.000000Z",
        "last_modified_at": "2025-01-01T00:00:00.000000Z",
    },
    {
        "id": "cat-cid-2",
        "display_name": "DriftedConfiglet",
        "ref_archs": ["two_stage_l3clos"],
        "generators": [_CAT_GEN_DRIFTED_NEW],
        "created_at": "2025-01-01T00:00:00.000000Z",
        "last_modified_at": "2025-06-01T00:00:00.000000Z",
    },
    {
        "id": "cat-cid-3",
        "display_name": "CatalogueOnlyConfiglet",
        "ref_archs": ["two_stage_l3clos"],
        "generators": [_CAT_GEN_CATALOGUE_ONLY],
        "created_at": "2025-01-01T00:00:00.000000Z",
        "last_modified_at": "2025-01-01T00:00:00.000000Z",
    },
]

RAW_DESIGN_PROPSETS = {
    "items": [
        {
            "id": "flow_data",
            "label": "Flow Data For Optional Flow Analytics",
            "values": {"collector_ip": "10.28.173.6"},
            "created_at": "2025-01-01T00:00:00.000000Z",
            "updated_at": "2025-01-01T00:00:00.000000Z",
        },
        {
            "id": "drifted_ps",
            "label": "Drifted Property Set",
            "values": {"key": "new_value"},
            "created_at": "2025-01-01T00:00:00.000000Z",
            "updated_at": "2025-06-01T00:00:00.000000Z",
        },
        {
            "id": "cat_only_ps",
            "label": "Catalogue Only Property Set",
            "values": {"y": "2"},
            "created_at": "2025-01-01T00:00:00.000000Z",
            "updated_at": "2025-01-01T00:00:00.000000Z",
        },
    ]
}

PARSED_DESIGN_PROPSETS = [
    {
        "id": "flow_data",
        "label": "Flow Data For Optional Flow Analytics",
        "values": {"collector_ip": "10.28.173.6"},
        "created_at": "2025-01-01T00:00:00.000000Z",
        "updated_at": "2025-01-01T00:00:00.000000Z",
    },
    {
        "id": "drifted_ps",
        "label": "Drifted Property Set",
        "values": {"key": "new_value"},
        "created_at": "2025-01-01T00:00:00.000000Z",
        "updated_at": "2025-06-01T00:00:00.000000Z",
    },
    {
        "id": "cat_only_ps",
        "label": "Catalogue Only Property Set",
        "values": {"y": "2"},
        "created_at": "2025-01-01T00:00:00.000000Z",
        "updated_at": "2025-01-01T00:00:00.000000Z",
    },
]


# ---------------------------------------------------------------------------
# Fixture data — blueprint graph rows (for drift tests)
# ---------------------------------------------------------------------------

# Blueprint scenario:
#   "MyConfiglet"           — matches catalogue cat-cid-1, SAME template → no drift
#   "DriftedConfiglet"      — matches catalogue cat-cid-2, DIFFERENT template → drift
#   "BlueprintOnlyConfiglet"— not in design catalogue

_BP_GEN_SAME = {
    "config_style": "junos",
    "template_text": "snmp { community public; }",
    "section": "system",
    "negation_template_text": "",
    "filename": "",
    "render_style": "standard",
    "section_condition": None,
}

_BP_GEN_DRIFTED_OLD = {
    "config_style": "junos",
    "template_text": "snmp { community OLD_COMMUNITY; }",
    "section": "system",
    "negation_template_text": "",
    "filename": "",
    "render_style": "standard",
    "section_condition": None,
}

_BP_GEN_BP_ONLY = {
    "config_style": "junos",
    "template_text": "set system syslog host 1.2.3.4",
    "section": "set_based_system",
    "negation_template_text": "",
    "filename": "",
    "render_style": "standard",
    "section_condition": None,
}

RAW_BP_CONFIGLET_ROWS = [
    {
        "configlet": {
            "id": "bp-cid-1",
            "condition": 'role in ["spine", "leaf"]',
            "payload": json.dumps({
                "condition": 'role in ["spine", "leaf"]',
                "configlet": {
                    "display_name": "MyConfiglet",
                    "generators": [_BP_GEN_SAME],
                },
                "id": "bp-cid-1",
                "label": "MyConfiglet",
                "type": "configlet",
            }),
            "type": "configlet",
        }
    },
    {
        "configlet": {
            "id": "bp-cid-2",
            "condition": "true",
            "payload": json.dumps({
                "condition": "true",
                "configlet": {
                    "display_name": "DriftedConfiglet",
                    "generators": [_BP_GEN_DRIFTED_OLD],
                },
                "id": "bp-cid-2",
                "label": "DriftedConfiglet",
                "type": "configlet",
            }),
            "type": "configlet",
        }
    },
    {
        "configlet": {
            "id": "bp-cid-3",
            "condition": "true",
            "payload": json.dumps({
                "condition": "true",
                "configlet": {
                    "display_name": "BlueprintOnlyConfiglet",
                    "generators": [_BP_GEN_BP_ONLY],
                },
                "id": "bp-cid-3",
                "label": "BlueprintOnlyConfiglet",
                "type": "configlet",
            }),
            "type": "configlet",
        }
    },
]

# Blueprint property set scenario:
#   "flow_data"  — matches catalogue, SAME values → no drift
#   "drifted_ps" — matches catalogue, DIFFERENT values → drift
#   "bp_only_ps" — not in catalogue

RAW_BP_PROPSET_ROWS = [
    {
        "propset": {
            "id": "bp-ps-id-1",
            "property_set_id": "flow_data",
            "stale": False,
            "payload": json.dumps({
                "id": "bp-ps-id-1",
                "label": "Flow Data For Optional Flow Analytics",
                "property_set_id": "flow_data",
                "stale": False,
                "type": "property_set",
                "values": {"collector_ip": "10.28.173.6"},
            }),
            "type": "property_set",
        }
    },
    {
        "propset": {
            "id": "bp-ps-id-2",
            "property_set_id": "drifted_ps",
            "stale": False,
            "payload": json.dumps({
                "id": "bp-ps-id-2",
                "label": "Drifted Property Set",
                "property_set_id": "drifted_ps",
                "stale": False,
                "type": "property_set",
                "values": {"key": "old_value"},
            }),
            "type": "property_set",
        }
    },
    {
        "propset": {
            "id": "bp-ps-id-3",
            "property_set_id": "bp_only_ps",
            "stale": False,
            "payload": json.dumps({
                "id": "bp-ps-id-3",
                "label": "Blueprint Only Property Set",
                "property_set_id": "bp_only_ps",
                "stale": False,
                "type": "property_set",
                "values": {"x": "1"},
            }),
            "type": "property_set",
        }
    },
]


# ---------------------------------------------------------------------------
# parse_design_configlets
# ---------------------------------------------------------------------------

class TestParseDesignConfiglets:

    def test_returns_correct_count(self):
        result = parse_design_configlets(RAW_DESIGN_CONFIGLETS)
        assert len(result) == 3

    def test_empty_items_returns_empty_list(self):
        assert parse_design_configlets({"items": []}) == []

    def test_no_items_key_returns_empty_list(self):
        assert parse_design_configlets({}) == []

    def test_id_and_display_name(self):
        result = parse_design_configlets(RAW_DESIGN_CONFIGLETS)
        assert result[0]["id"] == "cat-cid-1"
        assert result[0]["display_name"] == "MyConfiglet"

    def test_ref_archs(self):
        result = parse_design_configlets(RAW_DESIGN_CONFIGLETS)
        assert result[0]["ref_archs"] == ["two_stage_l3clos"]

    def test_generators_passed_through_intact(self):
        result = parse_design_configlets(RAW_DESIGN_CONFIGLETS)
        assert result[0]["generators"] == [_CAT_GEN_SAME]
        assert result[0]["generators"][0]["template_text"] == "snmp { community public; }"

    def test_timestamps(self):
        result = parse_design_configlets(RAW_DESIGN_CONFIGLETS)
        assert result[0]["created_at"] == "2025-01-01T00:00:00.000000Z"
        assert result[1]["last_modified_at"] == "2025-06-01T00:00:00.000000Z"

    def test_parsed_output_matches_fixture(self):
        result = parse_design_configlets(RAW_DESIGN_CONFIGLETS)
        assert result == PARSED_DESIGN_CONFIGLETS

    def test_missing_optional_fields_use_defaults(self):
        result = parse_design_configlets({"items": [{"id": "x"}]})
        assert result[0]["id"] == "x"
        assert result[0]["display_name"] is None
        assert result[0]["ref_archs"] == []
        assert result[0]["generators"] == []
        assert result[0]["created_at"] is None
        assert result[0]["last_modified_at"] is None


# ---------------------------------------------------------------------------
# parse_design_property_sets
# ---------------------------------------------------------------------------

class TestParseDesignPropertySets:

    def test_returns_correct_count(self):
        result = parse_design_property_sets(RAW_DESIGN_PROPSETS)
        assert len(result) == 3

    def test_empty_items_returns_empty_list(self):
        assert parse_design_property_sets({"items": []}) == []

    def test_no_items_key_returns_empty_list(self):
        assert parse_design_property_sets({}) == []

    def test_id_and_label(self):
        result = parse_design_property_sets(RAW_DESIGN_PROPSETS)
        assert result[0]["id"] == "flow_data"
        assert result[0]["label"] == "Flow Data For Optional Flow Analytics"

    def test_values_dict(self):
        result = parse_design_property_sets(RAW_DESIGN_PROPSETS)
        assert result[0]["values"] == {"collector_ip": "10.28.173.6"}
        assert result[1]["values"] == {"key": "new_value"}

    def test_timestamps(self):
        result = parse_design_property_sets(RAW_DESIGN_PROPSETS)
        assert result[0]["created_at"] == "2025-01-01T00:00:00.000000Z"
        assert result[1]["updated_at"] == "2025-06-01T00:00:00.000000Z"

    def test_parsed_output_matches_fixture(self):
        result = parse_design_property_sets(RAW_DESIGN_PROPSETS)
        assert result == PARSED_DESIGN_PROPSETS

    def test_missing_optional_fields_use_defaults(self):
        result = parse_design_property_sets({"items": [{"id": "y"}]})
        assert result[0]["id"] == "y"
        assert result[0]["label"] is None
        assert result[0]["values"] == {}
        assert result[0]["created_at"] is None
        assert result[0]["updated_at"] is None


# ---------------------------------------------------------------------------
# _compare_generators
# ---------------------------------------------------------------------------

class TestCompareGenerators:

    def test_identical_generators_returns_empty(self):
        gen = {"config_style": "junos", "template_text": "X", "section": "system"}
        assert _compare_generators([gen], [gen]) == []

    def test_both_empty_returns_empty(self):
        assert _compare_generators([], []) == []

    def test_different_template_text_detected(self):
        bp_gen = {"config_style": "junos", "template_text": "OLD", "section": "system"}
        cat_gen = {"config_style": "junos", "template_text": "NEW", "section": "system"}
        result = _compare_generators([bp_gen], [cat_gen])
        assert len(result) == 1
        assert result[0]["generator_index"] == 0
        assert result[0]["blueprint_template_text"] == "OLD"
        assert result[0]["catalogue_template_text"] == "NEW"
        assert result[0]["config_style"] == "junos"
        assert result[0]["section"] == "system"

    def test_extra_blueprint_generator_reported(self):
        gen = {"config_style": "junos", "template_text": "X", "section": "s"}
        extra = {"config_style": "junos", "template_text": "EXTRA", "section": "s"}
        result = _compare_generators([gen, extra], [gen])
        assert len(result) == 1
        assert result[0]["generator_index"] == 1
        assert result[0]["blueprint_template_text"] == "EXTRA"
        assert result[0]["catalogue_template_text"] is None

    def test_extra_catalogue_generator_reported(self):
        gen = {"config_style": "junos", "template_text": "X", "section": "s"}
        extra = {"config_style": "junos", "template_text": "EXTRA", "section": "s"}
        result = _compare_generators([gen], [gen, extra])
        assert len(result) == 1
        assert result[0]["generator_index"] == 1
        assert result[0]["blueprint_template_text"] is None
        assert result[0]["catalogue_template_text"] == "EXTRA"

    def test_multiple_generators_only_differing_reported(self):
        gen_same = {"config_style": "junos", "template_text": "SAME", "section": "s"}
        bp_diff = {"config_style": "junos", "template_text": "BP_TEXT", "section": "s"}
        cat_diff = {"config_style": "junos", "template_text": "CAT_TEXT", "section": "s"}
        result = _compare_generators([gen_same, bp_diff], [gen_same, cat_diff])
        assert len(result) == 1
        assert result[0]["generator_index"] == 1

    def test_empty_string_template_vs_none_is_a_diff(self):
        bp_gen = {"config_style": "junos", "template_text": "", "section": "s"}
        cat_gen = {"config_style": "junos", "section": "s"}  # no template_text key
        result = _compare_generators([bp_gen], [cat_gen])
        # "" != None → should be a diff
        assert len(result) == 1


# ---------------------------------------------------------------------------
# handle_get_design_configlets
# ---------------------------------------------------------------------------

class TestHandleGetDesignConfiglets:

    async def test_single_instance_returns_flat_result(self):
        session = make_session("dc-primary")
        import primitives.live_data_client as ldc
        original = ldc.get_design_configlets
        ldc.get_design_configlets = AsyncMock(return_value=RAW_DESIGN_CONFIGLETS)

        result = await handle_get_design_configlets([session])

        ldc.get_design_configlets = original
        assert result["instance"] == "dc-primary"
        assert "configlets" in result
        assert result["count"] == 3

    async def test_single_instance_configlets_parsed(self):
        session = make_session()
        import primitives.live_data_client as ldc
        original = ldc.get_design_configlets
        ldc.get_design_configlets = AsyncMock(return_value=RAW_DESIGN_CONFIGLETS)

        result = await handle_get_design_configlets([session])

        ldc.get_design_configlets = original
        assert result["configlets"][0]["id"] == "cat-cid-1"
        assert result["configlets"][0]["display_name"] == "MyConfiglet"

    async def test_instance_name_filter_selects_correct_session(self):
        s1 = make_session("dc-primary")
        s2 = make_session("dc-secondary")
        import primitives.live_data_client as ldc
        original = ldc.get_design_configlets
        ldc.get_design_configlets = AsyncMock(return_value={"items": []})

        result = await handle_get_design_configlets([s1, s2], "dc-secondary")

        ldc.get_design_configlets = original
        assert result["instance"] == "dc-secondary"

    async def test_unknown_instance_name_raises(self):
        session = make_session("dc-primary")
        with pytest.raises(ValueError, match="No instance named"):
            await handle_get_design_configlets([session], "nonexistent")

    async def test_api_error_returns_error_key(self):
        session = make_session()
        import primitives.live_data_client as ldc
        original = ldc.get_design_configlets
        ldc.get_design_configlets = AsyncMock(side_effect=RuntimeError("timeout"))

        result = await handle_get_design_configlets([session])

        ldc.get_design_configlets = original
        assert "error" in result
        assert result["count"] == 0
        assert result["configlets"] == []

    async def test_multi_instance_returns_all_wrapper(self):
        s1 = make_session("dc-primary")
        s2 = make_session("dc-secondary")
        import primitives.live_data_client as ldc
        original = ldc.get_design_configlets
        ldc.get_design_configlets = AsyncMock(return_value=RAW_DESIGN_CONFIGLETS)

        result = await handle_get_design_configlets([s1, s2])

        ldc.get_design_configlets = original
        assert result["instance"] == "all"
        assert len(result["results"]) == 2
        assert result["total_count"] == 6  # 3 + 3

    async def test_empty_catalogue_returns_zero_count(self):
        session = make_session()
        import primitives.live_data_client as ldc
        original = ldc.get_design_configlets
        ldc.get_design_configlets = AsyncMock(return_value={"items": []})

        result = await handle_get_design_configlets([session])

        ldc.get_design_configlets = original
        assert result["count"] == 0
        assert result["configlets"] == []


# ---------------------------------------------------------------------------
# handle_get_design_property_sets
# ---------------------------------------------------------------------------

class TestHandleGetDesignPropertySets:

    async def test_single_instance_returns_flat_result(self):
        session = make_session("dc-primary")
        import primitives.live_data_client as ldc
        original = ldc.get_design_property_sets
        ldc.get_design_property_sets = AsyncMock(return_value=RAW_DESIGN_PROPSETS)

        result = await handle_get_design_property_sets([session])

        ldc.get_design_property_sets = original
        assert result["instance"] == "dc-primary"
        assert result["count"] == 3

    async def test_single_instance_property_sets_parsed(self):
        session = make_session()
        import primitives.live_data_client as ldc
        original = ldc.get_design_property_sets
        ldc.get_design_property_sets = AsyncMock(return_value=RAW_DESIGN_PROPSETS)

        result = await handle_get_design_property_sets([session])

        ldc.get_design_property_sets = original
        assert result["property_sets"][0]["id"] == "flow_data"
        assert result["property_sets"][0]["label"] == "Flow Data For Optional Flow Analytics"

    async def test_api_error_returns_error_key(self):
        session = make_session()
        import primitives.live_data_client as ldc
        original = ldc.get_design_property_sets
        ldc.get_design_property_sets = AsyncMock(side_effect=ConnectionError("refused"))

        result = await handle_get_design_property_sets([session])

        ldc.get_design_property_sets = original
        assert "error" in result
        assert result["count"] == 0

    async def test_multi_instance_returns_all_wrapper(self):
        s1 = make_session("dc-primary")
        s2 = make_session("dc-secondary")
        import primitives.live_data_client as ldc
        original = ldc.get_design_property_sets
        ldc.get_design_property_sets = AsyncMock(return_value=RAW_DESIGN_PROPSETS)

        result = await handle_get_design_property_sets([s1, s2])

        ldc.get_design_property_sets = original
        assert result["instance"] == "all"
        assert result["total_count"] == 6

    async def test_empty_catalogue_returns_zero_count(self):
        session = make_session()
        import primitives.live_data_client as ldc
        original = ldc.get_design_property_sets
        ldc.get_design_property_sets = AsyncMock(return_value={"items": []})

        result = await handle_get_design_property_sets([session])

        ldc.get_design_property_sets = original
        assert result["count"] == 0
        assert result["property_sets"] == []

    async def test_unknown_instance_name_raises(self):
        session = make_session("dc-primary")
        with pytest.raises(ValueError, match="No instance named"):
            await handle_get_design_property_sets([session], "nonexistent")


# ---------------------------------------------------------------------------
# handle_get_configlet_drift
# ---------------------------------------------------------------------------

class TestHandleGetConfigletDrift:

    async def _call(self, sessions, registry, blueprint_id="bp-001", instance_name=None):
        return await handle_get_configlet_drift(
            sessions, registry, blueprint_id, instance_name
        )

    async def test_no_drift_when_templates_identical(self):
        session = make_session()
        registry = make_registry(configlet_rows=RAW_BP_CONFIGLET_ROWS)
        import primitives.live_data_client as ldc
        original = ldc.get_design_configlets
        ldc.get_design_configlets = AsyncMock(return_value=RAW_DESIGN_CONFIGLETS)

        result = await self._call([session], registry)

        ldc.get_design_configlets = original
        matched = {m["display_name"]: m for m in result["matched"]}
        assert matched["MyConfiglet"]["has_drift"] is False
        assert matched["MyConfiglet"]["generator_diffs"] == []

    async def test_drift_detected_when_template_differs(self):
        session = make_session()
        registry = make_registry(configlet_rows=RAW_BP_CONFIGLET_ROWS)
        import primitives.live_data_client as ldc
        original = ldc.get_design_configlets
        ldc.get_design_configlets = AsyncMock(return_value=RAW_DESIGN_CONFIGLETS)

        result = await self._call([session], registry)

        ldc.get_design_configlets = original
        matched = {m["display_name"]: m for m in result["matched"]}
        assert matched["DriftedConfiglet"]["has_drift"] is True
        diffs = matched["DriftedConfiglet"]["generator_diffs"]
        assert len(diffs) == 1
        assert diffs[0]["blueprint_template_text"] == "snmp { community OLD_COMMUNITY; }"
        assert diffs[0]["catalogue_template_text"] == "snmp { community NEW_COMMUNITY; }"

    async def test_drift_result_includes_catalogue_and_blueprint_ids(self):
        session = make_session()
        registry = make_registry(configlet_rows=RAW_BP_CONFIGLET_ROWS)
        import primitives.live_data_client as ldc
        original = ldc.get_design_configlets
        ldc.get_design_configlets = AsyncMock(return_value=RAW_DESIGN_CONFIGLETS)

        result = await self._call([session], registry)

        ldc.get_design_configlets = original
        matched = {m["display_name"]: m for m in result["matched"]}
        assert matched["MyConfiglet"]["blueprint_id"] == "bp-cid-1"
        assert matched["MyConfiglet"]["catalogue_id"] == "cat-cid-1"

    async def test_blueprint_only_when_no_catalogue_match(self):
        session = make_session()
        registry = make_registry(configlet_rows=RAW_BP_CONFIGLET_ROWS)
        import primitives.live_data_client as ldc
        original = ldc.get_design_configlets
        ldc.get_design_configlets = AsyncMock(return_value=RAW_DESIGN_CONFIGLETS)

        result = await self._call([session], registry)

        ldc.get_design_configlets = original
        bp_only_names = [b["display_name"] for b in result["blueprint_only"]]
        assert "BlueprintOnlyConfiglet" in bp_only_names

    async def test_catalogue_only_includes_unmatched_catalogue_items(self):
        session = make_session()
        registry = make_registry(configlet_rows=RAW_BP_CONFIGLET_ROWS)
        import primitives.live_data_client as ldc
        original = ldc.get_design_configlets
        ldc.get_design_configlets = AsyncMock(return_value=RAW_DESIGN_CONFIGLETS)

        result = await self._call([session], registry)

        ldc.get_design_configlets = original
        cat_only_names = [c["display_name"] for c in result["catalogue_only"]]
        assert "CatalogueOnlyConfiglet" in cat_only_names
        assert "cat-cid-3" in [c["catalogue_id"] for c in result["catalogue_only"]]

    async def test_matched_not_in_catalogue_only(self):
        session = make_session()
        registry = make_registry(configlet_rows=RAW_BP_CONFIGLET_ROWS)
        import primitives.live_data_client as ldc
        original = ldc.get_design_configlets
        ldc.get_design_configlets = AsyncMock(return_value=RAW_DESIGN_CONFIGLETS)

        result = await self._call([session], registry)

        ldc.get_design_configlets = original
        cat_only_names = {c["display_name"] for c in result["catalogue_only"]}
        assert "MyConfiglet" not in cat_only_names
        assert "DriftedConfiglet" not in cat_only_names

    async def test_returns_instance_and_blueprint_id(self):
        session = make_session("dc-primary")
        registry = make_registry(configlet_rows=[])
        import primitives.live_data_client as ldc
        original = ldc.get_design_configlets
        ldc.get_design_configlets = AsyncMock(return_value={"items": []})

        result = await self._call([session], registry, blueprint_id="bp-xyz")

        ldc.get_design_configlets = original
        assert result["instance"] == "dc-primary"
        assert result["blueprint_id"] == "bp-xyz"

    async def test_empty_blueprint_empty_catalogue_all_sections_empty(self):
        session = make_session()
        registry = make_registry(configlet_rows=[])
        import primitives.live_data_client as ldc
        original = ldc.get_design_configlets
        ldc.get_design_configlets = AsyncMock(return_value={"items": []})

        result = await self._call([session], registry)

        ldc.get_design_configlets = original
        assert result["matched"] == []
        assert result["blueprint_only"] == []
        assert result["catalogue_only"] == []

    async def test_registry_error_returns_error_key(self):
        session = make_session()
        registry = make_registry(error=RuntimeError("graph unavailable"))
        import primitives.live_data_client as ldc
        original = ldc.get_design_configlets
        ldc.get_design_configlets = AsyncMock(return_value=RAW_DESIGN_CONFIGLETS)

        result = await self._call([session], registry)

        ldc.get_design_configlets = original
        assert "error" in result
        assert "matched" not in result

    async def test_multi_instance_returns_all_wrapper(self):
        s1 = make_session("dc-primary")
        s2 = make_session("dc-secondary")
        registry = make_registry(configlet_rows=[])
        import primitives.live_data_client as ldc
        original = ldc.get_design_configlets
        ldc.get_design_configlets = AsyncMock(return_value={"items": []})

        result = await self._call([s1, s2], registry)

        ldc.get_design_configlets = original
        assert result["instance"] == "all"
        assert "results" in result
        assert len(result["results"]) == 2

    async def test_condition_preserved_in_matched(self):
        session = make_session()
        registry = make_registry(configlet_rows=RAW_BP_CONFIGLET_ROWS)
        import primitives.live_data_client as ldc
        original = ldc.get_design_configlets
        ldc.get_design_configlets = AsyncMock(return_value=RAW_DESIGN_CONFIGLETS)

        result = await self._call([session], registry)

        ldc.get_design_configlets = original
        matched = {m["display_name"]: m for m in result["matched"]}
        assert matched["MyConfiglet"]["condition"] == 'role in ["spine", "leaf"]'


# ---------------------------------------------------------------------------
# handle_get_property_set_drift
# ---------------------------------------------------------------------------

class TestHandleGetPropertySetDrift:

    async def _call(self, sessions, registry, blueprint_id="bp-001", instance_name=None):
        return await handle_get_property_set_drift(
            sessions, registry, blueprint_id, instance_name
        )

    async def test_no_drift_when_values_identical(self):
        session = make_session()
        registry = make_registry(propset_rows=RAW_BP_PROPSET_ROWS)
        import primitives.live_data_client as ldc
        original = ldc.get_design_property_sets
        ldc.get_design_property_sets = AsyncMock(return_value=RAW_DESIGN_PROPSETS)

        result = await self._call([session], registry)

        ldc.get_design_property_sets = original
        matched = {m["property_set_id"]: m for m in result["matched"]}
        assert matched["flow_data"]["has_drift"] is False
        assert matched["flow_data"]["blueprint_values"] == {"collector_ip": "10.28.173.6"}
        assert matched["flow_data"]["catalogue_values"] == {"collector_ip": "10.28.173.6"}

    async def test_drift_detected_when_values_differ(self):
        session = make_session()
        registry = make_registry(propset_rows=RAW_BP_PROPSET_ROWS)
        import primitives.live_data_client as ldc
        original = ldc.get_design_property_sets
        ldc.get_design_property_sets = AsyncMock(return_value=RAW_DESIGN_PROPSETS)

        result = await self._call([session], registry)

        ldc.get_design_property_sets = original
        matched = {m["property_set_id"]: m for m in result["matched"]}
        assert matched["drifted_ps"]["has_drift"] is True
        assert matched["drifted_ps"]["blueprint_values"] == {"key": "old_value"}
        assert matched["drifted_ps"]["catalogue_values"] == {"key": "new_value"}

    async def test_blueprint_only_when_no_catalogue_match(self):
        session = make_session()
        registry = make_registry(propset_rows=RAW_BP_PROPSET_ROWS)
        import primitives.live_data_client as ldc
        original = ldc.get_design_property_sets
        ldc.get_design_property_sets = AsyncMock(return_value=RAW_DESIGN_PROPSETS)

        result = await self._call([session], registry)

        ldc.get_design_property_sets = original
        bp_only_ids = [b["property_set_id"] for b in result["blueprint_only"]]
        assert "bp_only_ps" in bp_only_ids

    async def test_catalogue_only_includes_unmatched_catalogue_items(self):
        session = make_session()
        registry = make_registry(propset_rows=RAW_BP_PROPSET_ROWS)
        import primitives.live_data_client as ldc
        original = ldc.get_design_property_sets
        ldc.get_design_property_sets = AsyncMock(return_value=RAW_DESIGN_PROPSETS)

        result = await self._call([session], registry)

        ldc.get_design_property_sets = original
        cat_only_ids = [c["property_set_id"] for c in result["catalogue_only"]]
        assert "cat_only_ps" in cat_only_ids

    async def test_matched_not_in_catalogue_only(self):
        session = make_session()
        registry = make_registry(propset_rows=RAW_BP_PROPSET_ROWS)
        import primitives.live_data_client as ldc
        original = ldc.get_design_property_sets
        ldc.get_design_property_sets = AsyncMock(return_value=RAW_DESIGN_PROPSETS)

        result = await self._call([session], registry)

        ldc.get_design_property_sets = original
        cat_only_ids = {c["property_set_id"] for c in result["catalogue_only"]}
        assert "flow_data" not in cat_only_ids
        assert "drifted_ps" not in cat_only_ids

    async def test_returns_instance_and_blueprint_id(self):
        session = make_session("dc-primary")
        registry = make_registry(propset_rows=[])
        import primitives.live_data_client as ldc
        original = ldc.get_design_property_sets
        ldc.get_design_property_sets = AsyncMock(return_value={"items": []})

        result = await self._call([session], registry, blueprint_id="bp-abc")

        ldc.get_design_property_sets = original
        assert result["instance"] == "dc-primary"
        assert result["blueprint_id"] == "bp-abc"

    async def test_empty_blueprint_empty_catalogue_all_sections_empty(self):
        session = make_session()
        registry = make_registry(propset_rows=[])
        import primitives.live_data_client as ldc
        original = ldc.get_design_property_sets
        ldc.get_design_property_sets = AsyncMock(return_value={"items": []})

        result = await self._call([session], registry)

        ldc.get_design_property_sets = original
        assert result["matched"] == []
        assert result["blueprint_only"] == []
        assert result["catalogue_only"] == []

    async def test_registry_error_returns_error_key(self):
        session = make_session()
        registry = make_registry(error=RuntimeError("graph error"))
        import primitives.live_data_client as ldc
        original = ldc.get_design_property_sets
        ldc.get_design_property_sets = AsyncMock(return_value=RAW_DESIGN_PROPSETS)

        result = await self._call([session], registry)

        ldc.get_design_property_sets = original
        assert "error" in result
        assert "matched" not in result

    async def test_multi_instance_returns_all_wrapper(self):
        s1 = make_session("dc-primary")
        s2 = make_session("dc-secondary")
        registry = make_registry(propset_rows=[])
        import primitives.live_data_client as ldc
        original = ldc.get_design_property_sets
        ldc.get_design_property_sets = AsyncMock(return_value={"items": []})

        result = await self._call([s1, s2], registry)

        ldc.get_design_property_sets = original
        assert result["instance"] == "all"
        assert len(result["results"]) == 2

    async def test_display_name_preserved_in_matched(self):
        session = make_session()
        registry = make_registry(propset_rows=RAW_BP_PROPSET_ROWS)
        import primitives.live_data_client as ldc
        original = ldc.get_design_property_sets
        ldc.get_design_property_sets = AsyncMock(return_value=RAW_DESIGN_PROPSETS)

        result = await self._call([session], registry)

        ldc.get_design_property_sets = original
        matched = {m["property_set_id"]: m for m in result["matched"]}
        assert matched["flow_data"]["display_name"] == "Flow Data For Optional Flow Analytics"


# ---------------------------------------------------------------------------
# _select_sessions
# ---------------------------------------------------------------------------

class TestDesignCatalogueSelectSessions:

    def test_none_returns_all(self):
        sessions = [make_session("a"), make_session("b")]
        result = _select_sessions(sessions, None)
        assert result == sessions

    def test_named_returns_matching_session(self):
        s1 = make_session("dc-primary")
        s2 = make_session("dc-secondary")
        result = _select_sessions([s1, s2], "dc-secondary")
        assert result == [s2]

    def test_unknown_name_raises_value_error(self):
        sessions = [make_session("dc-primary")]
        with pytest.raises(ValueError, match="No instance named 'nope'"):
            _select_sessions(sessions, "nope")

    def test_empty_sessions_with_none_returns_empty(self):
        assert _select_sessions([], None) == []
