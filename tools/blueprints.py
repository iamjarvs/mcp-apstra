from fastmcp import Context

from handlers.blueprints import handle_get_blueprints
from handlers.blueprint_policy import handle_get_configlets, handle_get_property_sets


def register(mcp):

    @mcp.tool()
    async def get_blueprints(instance_name: str = None, ctx: Context = None) -> dict:
        """
        Returns all blueprints across all Apstra instances, or for a single
        named instance.

        A blueprint represents a running data centre managed by an Apstra
        instance. Each instance can contain multiple blueprints. Use this tool
        to discover what blueprints (data centres) exist before calling tools
        that require a blueprint_id.

        Data source: live network (live_data_client).

        Args:
            instance_name: Optional. The name of the Apstra instance to query
                           (as defined in instances.yaml). If omitted, all
                           instances are queried and results are merged.

        Returns:
            When querying a single instance:
              - instance: name of the Apstra instance queried
              - blueprints: list of blueprint objects, each with id, label,
                            status, design, anomaly_counts, and topology
              - count: total number of blueprints on that instance

            When querying all instances:
              - instance: "all"
              - results: list of per-instance result objects (same shape as above)
              - total_count: sum of all blueprints across all instances
        """
        return await handle_get_blueprints(
            ctx.lifespan_context["sessions"], instance_name
        )

    @mcp.tool()
    async def get_blueprint_configlets(
        blueprint_id: str,
        instance_name: str = None,
        ctx: Context = None,
    ) -> dict:
        """
        Returns all configlets applied to a blueprint.

        Configlets are user-defined configuration snippets injected into Apstra's
        rendered device configs. Each configlet has a condition expression that
        controls which devices it applies to (e.g. 'role in ["spine", "leaf"]'
        or 'id in ["<node-id>"]'), and one or more generators that contain the
        actual Jinja2 template text rendered into device config sections.

        Data source: graph database (graph_client). The graph is automatically
        rebuilt if the blueprint version has changed since the last query.

        Use get_blueprints to discover valid blueprint_id values.

        Args:
            blueprint_id:  The Apstra blueprint ID to query.
            instance_name: Optional. The name of the Apstra instance to query
                           (as defined in instances.yaml). If omitted, all
                           instances are queried and results are merged.

        Returns:
            When querying a single instance:
              - instance: name of the Apstra instance queried
              - blueprint_id: the blueprint queried
              - configlets: list of configlet objects, each with:
                  id           — configlet graph node ID
                  display_name — human-readable configlet name
                  condition    — device match expression (Jinja2/Python subset)
                  generators   — list of generator objects, each with:
                      config_style          — e.g. "junos", "eos"
                      render_style          — e.g. "standard", "set_based"
                      section               — config section targeted (e.g.
                                             "system", "set_based_system")
                      template_text         — Jinja2 template rendered into config
                      negation_template_text — template used for config removal
              - count: total number of configlets

            When querying all instances:
              - instance: "all"
              - blueprint_id: as above
              - results: list of per-instance result objects (same shape as above)
              - total_count: sum of configlets across all instances
        """
        return await handle_get_configlets(
            ctx.lifespan_context["sessions"],
            ctx.lifespan_context["graph_registry"],
            blueprint_id,
            instance_name,
        )

    @mcp.tool()
    async def get_blueprint_property_sets(
        blueprint_id: str,
        instance_name: str = None,
        ctx: Context = None,
    ) -> dict:
        """
        Returns all property sets applied to a blueprint.

        Property sets are user-defined key-value stores that are injected as
        variables into configlet Jinja2 templates. They decouple configuration
        values (e.g. SNMP community strings, collector IPs) from the template
        logic, making configlets reusable across environments.

        Data source: graph database (graph_client). The graph is automatically
        rebuilt if the blueprint version has changed since the last query.

        Use get_blueprints to discover valid blueprint_id values.
        Use get_blueprint_configlets to see the templates that reference these
        property set values.

        Args:
            blueprint_id:  The Apstra blueprint ID to query.
            instance_name: Optional. The name of the Apstra instance to query
                           (as defined in instances.yaml). If omitted, all
                           instances are queried and results are merged.

        Returns:
            When querying a single instance:
              - instance: name of the Apstra instance queried
              - blueprint_id: the blueprint queried
              - property_sets: list of property set objects, each with:
                  id               — property set graph node ID
                  display_name     — human-readable name
                  property_set_id  — short machine identifier (e.g. "flow_data")
                  stale            — true if the property set definition has
                                     changed since it was last applied
                  values           — dict of key-value pairs (e.g.
                                     {"collector_ip": "10.28.173.6"})
              - count: total number of property sets

            When querying all instances:
              - instance: "all"
              - blueprint_id: as above
              - results: list of per-instance result objects (same shape as above)
              - total_count: sum of property sets across all instances
        """
        return await handle_get_property_sets(
            ctx.lifespan_context["sessions"],
            ctx.lifespan_context["graph_registry"],
            blueprint_id,
            instance_name,
        )
