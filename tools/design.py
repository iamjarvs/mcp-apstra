from fastmcp import Context

from handlers.design_catalogue import (
    handle_get_design_configlets,
    handle_get_design_property_sets,
    handle_get_configlet_drift,
    handle_get_property_set_drift,
)


def register(mcp):

    @mcp.tool()
    async def get_design_configlets(
        instance_name: str = None,
        ctx: Context = None,
    ) -> dict:
        """
        Returns all configlets from the Apstra instance-level design catalogue.

        The design catalogue holds the authoritative (master) copies of
        configlets. When a configlet is applied to a blueprint, Apstra takes
        a copy — that blueprint copy can drift from the catalogue over time.

        Use get_blueprint_configlet_drift to see whether any blueprint copies
        have diverged from their catalogue originals.

        Data source: live network (live_data_client).

        Args:
            instance_name: Optional. The name of the Apstra instance to query
                           (as defined in instances.yaml). If omitted, all
                           instances are queried and results are merged.

        Returns:
            When querying a single instance:
              - instance: name of the Apstra instance queried
              - configlets: list of configlet objects, each with:
                  id               — catalogue ID (e.g. "flow_snmpv2")
                  display_name     — human-readable configlet name
                  ref_archs        — list of reference architectures this
                                     configlet targets
                  generators       — list of generator objects, each with
                                     config_style, section, template_text,
                                     negation_template_text, render_style,
                                     filename
                  created_at       — ISO 8601 creation timestamp
                  last_modified_at — ISO 8601 last modified timestamp
              - count: total number of configlets in the catalogue

            When querying all instances:
              - instance: "all"
              - results: list of per-instance result objects (same shape as above)
              - total_count: sum of catalogue configlets across all instances
        """
        return await handle_get_design_configlets(
            ctx.lifespan_context["sessions"], instance_name
        )

    @mcp.tool()
    async def get_design_property_sets(
        instance_name: str = None,
        ctx: Context = None,
    ) -> dict:
        """
        Returns all property sets from the Apstra instance-level design
        catalogue.

        The design catalogue holds the authoritative (master) copies of
        property sets. When a property set is applied to a blueprint, Apstra
        takes a copy — that copy can drift from the catalogue over time.

        Use get_blueprint_property_set_drift to see whether any blueprint copies
        have diverged from their catalogue originals.

        Data source: live network (live_data_client).

        Args:
            instance_name: Optional. The name of the Apstra instance to query
                           (as defined in instances.yaml). If omitted, all
                           instances are queried and results are merged.

        Returns:
            When querying a single instance:
              - instance: name of the Apstra instance queried
              - property_sets: list of property set objects, each with:
                  id         — property set ID / machine identifier
                               (e.g. "flow_data")
                  label      — human-readable name
                  values     — dict of key-value pairs in this property set
                  created_at — ISO 8601 creation timestamp
                  updated_at — ISO 8601 last updated timestamp
              - count: total number of property sets in the catalogue

            When querying all instances:
              - instance: "all"
              - results: list of per-instance result objects (same shape as above)
              - total_count: sum of catalogue property sets across all instances
        """
        return await handle_get_design_property_sets(
            ctx.lifespan_context["sessions"], instance_name
        )

    @mcp.tool()
    async def get_blueprint_configlet_drift(
        blueprint_id: str,
        instance_name: str = None,
        ctx: Context = None,
    ) -> dict:
        """
        Compares configlets applied to a blueprint against the instance-level
        design catalogue, reporting any that have drifted (template_text differs).

        When a configlet is applied to a blueprint, Apstra takes a copy. If the
        catalogue master is later updated (or the blueprint copy is directly
        edited), the two can diverge. This tool surfaces that drift.

        Configlets are matched between blueprint and catalogue by display_name
        (blueprint graph node IDs and catalogue IDs are in different namespaces
        and cannot be directly compared).

        Data source: graph database (blueprint configlets) + live network
        (design catalogue). Both are read for the same instance in one call.

        Use get_blueprints to discover valid blueprint_id values.

        Args:
            blueprint_id:  The Apstra blueprint ID to compare.
            instance_name: Optional. The name of the Apstra instance to query
                           (as defined in instances.yaml). If omitted, all
                           instances are queried and results are merged.

        Returns:
            When querying a single instance:
              - instance: name of the Apstra instance queried
              - blueprint_id: the blueprint queried
              - matched: list of configlets found in both blueprint and catalogue:
                  display_name      — configlet name
                  blueprint_id      — graph node ID in the blueprint
                  catalogue_id      — ID in the design catalogue
                  condition         — device match expression from blueprint
                  has_drift         — true if any generator template_text differs
                  generator_diffs   — list of per-generator differences (empty if
                                      no drift), each with:
                      generator_index        — 0-based position
                      config_style           — e.g. "junos", "eos"
                      section                — config section (e.g. "system")
                      blueprint_template_text — current text in the blueprint
                      catalogue_template_text — current text in the catalogue
              - blueprint_only: configlets applied to this blueprint that have
                  no matching entry in the design catalogue (display_name not
                  found). These may be locally created or renamed.
              - catalogue_only: configlets in the design catalogue that are not
                  applied to this blueprint. These are available but unused.

            When querying all instances:
              - instance: "all"
              - blueprint_id, as above
              - results: list of per-instance result objects (same shape as above)
        """
        return await handle_get_configlet_drift(
            ctx.lifespan_context["sessions"],
            ctx.lifespan_context["graph_registry"],
            blueprint_id,
            instance_name,
        )

    @mcp.tool()
    async def get_blueprint_property_set_drift(
        blueprint_id: str,
        instance_name: str = None,
        ctx: Context = None,
    ) -> dict:
        """
        Compares property sets applied to a blueprint against the instance-level
        design catalogue, reporting any whose values have drifted.

        When a property set is applied to a blueprint, Apstra takes a copy. If
        the catalogue master is later updated (or the blueprint copy is directly
        edited), the key-value pairs can diverge. This tool surfaces that drift.

        Property sets are matched between blueprint and catalogue by
        property_set_id (the blueprint graph `property_set_id` field is the
        same identifier as the design catalogue `id` field).

        Data source: graph database (blueprint property sets) + live network
        (design catalogue). Both are read for the same instance in one call.

        Use get_blueprints to discover valid blueprint_id values.

        Args:
            blueprint_id:  The Apstra blueprint ID to compare.
            instance_name: Optional. The name of the Apstra instance to query
                           (as defined in instances.yaml). If omitted, all
                           instances are queried and results are merged.

        Returns:
            When querying a single instance:
              - instance: name of the Apstra instance queried
              - blueprint_id: the blueprint queried
              - matched: list of property sets found in both blueprint and catalogue:
                  display_name      — property set name
                  property_set_id   — shared identifier (e.g. "flow_data")
                  has_drift         — true if values dict differs
                  blueprint_values  — current values in the blueprint copy
                  catalogue_values  — current values in the catalogue master
              - blueprint_only: property sets applied to this blueprint with no
                  matching catalogue entry (property_set_id not found in catalogue).
              - catalogue_only: property sets in the design catalogue that are
                  not applied to this blueprint.

            When querying all instances:
              - instance: "all"
              - blueprint_id: as above
              - results: list of per-instance result objects (same shape as above)
        """
        return await handle_get_property_set_drift(
            ctx.lifespan_context["sessions"],
            ctx.lifespan_context["graph_registry"],
            blueprint_id,
            instance_name,
        )
