from primitives import live_data_client
from primitives.response_parser import (
    parse_design_configlets,
    parse_design_property_sets,
    parse_configlets,
    parse_property_sets,
)

_CONFIGLET_QUERY = "MATCH (configlet:configlet) RETURN *"
_PROPERTY_SET_QUERY = "MATCH (propset:property_set) RETURN *"


# ---------------------------------------------------------------------------
# Design catalogue — instance-level (no blueprint_id)
# ---------------------------------------------------------------------------

async def handle_get_design_configlets(sessions, instance_name=None) -> dict:
    target_sessions = _select_sessions(sessions, instance_name)
    all_results = []

    for session in target_sessions:
        try:
            raw = await live_data_client.get_design_configlets(session)
            parsed = parse_design_configlets(raw)
            all_results.append({
                "instance": session.name,
                "configlets": parsed,
                "count": len(parsed),
            })
        except Exception as e:
            all_results.append({
                "instance": session.name,
                "error": str(e),
                "configlets": [],
                "count": 0,
            })

    if len(all_results) == 1:
        return all_results[0]

    return {
        "instance": "all",
        "results": all_results,
        "total_count": sum(r.get("count", 0) for r in all_results),
    }


async def handle_get_design_property_sets(sessions, instance_name=None) -> dict:
    target_sessions = _select_sessions(sessions, instance_name)
    all_results = []

    for session in target_sessions:
        try:
            raw = await live_data_client.get_design_property_sets(session)
            parsed = parse_design_property_sets(raw)
            all_results.append({
                "instance": session.name,
                "property_sets": parsed,
                "count": len(parsed),
            })
        except Exception as e:
            all_results.append({
                "instance": session.name,
                "error": str(e),
                "property_sets": [],
                "count": 0,
            })

    if len(all_results) == 1:
        return all_results[0]

    return {
        "instance": "all",
        "results": all_results,
        "total_count": sum(r.get("count", 0) for r in all_results),
    }


# ---------------------------------------------------------------------------
# Drift comparison — blueprint vs design catalogue
# ---------------------------------------------------------------------------

def _compare_generators(bp_gens: list, cat_gens: list) -> list:
    """
    Compare two generator lists by index and return diffs where template_text
    differs. Generators are matched positionally (index 0 to 0, etc.).
    Returns a list of diff objects (empty if no differences).
    """
    diffs = []
    max_len = max(len(bp_gens), len(cat_gens)) if (bp_gens or cat_gens) else 0
    for i in range(max_len):
        bp_text = bp_gens[i].get("template_text") if i < len(bp_gens) else None
        cat_text = cat_gens[i].get("template_text") if i < len(cat_gens) else None
        if bp_text != cat_text:
            ref_gen = bp_gens[i] if i < len(bp_gens) else cat_gens[i]
            diffs.append({
                "generator_index": i,
                "config_style": ref_gen.get("config_style"),
                "section": ref_gen.get("section"),
                "blueprint_template_text": bp_text,
                "catalogue_template_text": cat_text,
            })
    return diffs


async def handle_get_configlet_drift(
    sessions,
    registry,
    blueprint_id: str,
    instance_name: str = None,
) -> dict:
    """
    Compares configlets applied to a blueprint against the design catalogue.
    Matches by display_name (the natural key — blueprint graph node IDs and
    catalogue IDs are different namespaces).
    """
    target_sessions = _select_sessions(sessions, instance_name)
    all_results = []

    for session in target_sessions:
        try:
            # Blueprint configlets from graph
            graph = await registry.get_or_rebuild(session, blueprint_id)
            bp_rows = graph.query(_CONFIGLET_QUERY)
            bp_configlets = parse_configlets(bp_rows)

            # Design catalogue configlets from live API
            raw = await live_data_client.get_design_configlets(session)
            catalogue = {
                c["display_name"]: c
                for c in parse_design_configlets(raw)
                if c["display_name"]
            }

            matched = []
            blueprint_only = []
            matched_names = set()

            for bp in bp_configlets:
                name = bp["display_name"]
                if name and name in catalogue:
                    matched_names.add(name)
                    cat = catalogue[name]
                    diffs = _compare_generators(bp["generators"], cat["generators"])
                    matched.append({
                        "display_name": name,
                        "blueprint_id": bp["id"],
                        "catalogue_id": cat["id"],
                        "condition": bp["condition"],
                        "has_drift": bool(diffs),
                        "generator_diffs": diffs,
                    })
                else:
                    blueprint_only.append({
                        "display_name": name,
                        "blueprint_id": bp["id"],
                        "condition": bp["condition"],
                    })

            catalogue_only = [
                {
                    "display_name": c["display_name"],
                    "catalogue_id": c["id"],
                    "ref_archs": c["ref_archs"],
                }
                for name, c in catalogue.items()
                if name not in matched_names
            ]

            all_results.append({
                "instance": session.name,
                "blueprint_id": blueprint_id,
                "matched": matched,
                "blueprint_only": blueprint_only,
                "catalogue_only": catalogue_only,
            })
        except Exception as e:
            all_results.append({
                "instance": session.name,
                "blueprint_id": blueprint_id,
                "error": str(e),
            })

    if len(all_results) == 1:
        return all_results[0]

    return {
        "instance": "all",
        "blueprint_id": blueprint_id,
        "results": all_results,
    }


async def handle_get_property_set_drift(
    sessions,
    registry,
    blueprint_id: str,
    instance_name: str = None,
) -> dict:
    """
    Compares property sets applied to a blueprint against the design catalogue.
    Matches by property_set_id (the blueprint graph `property_set_id` field
    equals the design catalogue `id` field).
    """
    target_sessions = _select_sessions(sessions, instance_name)
    all_results = []

    for session in target_sessions:
        try:
            # Blueprint property sets from graph
            graph = await registry.get_or_rebuild(session, blueprint_id)
            bp_rows = graph.query(_PROPERTY_SET_QUERY)
            bp_propsets = parse_property_sets(bp_rows)

            # Design catalogue property sets from live API
            raw = await live_data_client.get_design_property_sets(session)
            catalogue = {c["id"]: c for c in parse_design_property_sets(raw)}

            matched = []
            blueprint_only = []
            matched_ids = set()

            for bp in bp_propsets:
                ps_id = bp["property_set_id"]
                if ps_id and ps_id in catalogue:
                    matched_ids.add(ps_id)
                    cat = catalogue[ps_id]
                    has_drift = bp["values"] != cat["values"]
                    matched.append({
                        "display_name": bp["display_name"],
                        "property_set_id": ps_id,
                        "has_drift": has_drift,
                        "blueprint_values": bp["values"],
                        "catalogue_values": cat["values"],
                    })
                else:
                    blueprint_only.append({
                        "display_name": bp["display_name"],
                        "property_set_id": ps_id,
                        "values": bp["values"],
                    })

            catalogue_only = [
                {
                    "label": c["label"],
                    "property_set_id": c["id"],
                    "values": c["values"],
                }
                for c_id, c in catalogue.items()
                if c_id not in matched_ids
            ]

            all_results.append({
                "instance": session.name,
                "blueprint_id": blueprint_id,
                "matched": matched,
                "blueprint_only": blueprint_only,
                "catalogue_only": catalogue_only,
            })
        except Exception as e:
            all_results.append({
                "instance": session.name,
                "blueprint_id": blueprint_id,
                "error": str(e),
            })

    if len(all_results) == 1:
        return all_results[0]

    return {
        "instance": "all",
        "blueprint_id": blueprint_id,
        "results": all_results,
    }


def _select_sessions(sessions, instance_name):
    if instance_name is None or instance_name == "all":
        return sessions
    matched = [s for s in sessions if s.name == instance_name]
    if not matched:
        raise ValueError(f"No instance named '{instance_name}' in session pool")
    return matched
