from typing import Annotated

from fastmcp import Context
from pydantic import Field

from handlers.blueprints import handle_get_blueprints
from handlers.blueprint_policy import handle_get_configlets, handle_get_property_sets


def register(mcp):

    @mcp.tool()
    async def get_blueprints(
        instance_name: Annotated[
            str | None,
            Field(
                default=None,
                description="Apstra instance name. Do not ask the user for this — leave as None to query all instances. Only set if the user explicitly names a specific instance.",
            ),
        ] = None,
        ctx: Context = None,
    ) -> dict:
        """
        List all blueprints (data centres) managed by one or all Apstra instances.

        A blueprint represents a running data centre. Every other tool that queries fabric
        topology, anomalies, BGP sessions, interfaces, or virtual networks requires a
        blueprint_id. Call this first whenever you need to discover what blueprints exist.
        Do not call this if you already have the blueprint_id you need.

        Each blueprint object includes: id, label, status (deployed/staging), design
        (two_stage_l3clos, three_stage_l3clos, five_stage_l3clos), anomaly_counts (by type),
        and topology (leaf/spine/server/access counts).
        Data source: live Apstra API.
        """
        return await handle_get_blueprints(
            ctx.lifespan_context["sessions"], instance_name
        )

    @mcp.tool()
    async def get_blueprint_configlets(
        blueprint_id: Annotated[
            str,
            "Apstra blueprint ID. Use get_blueprints to discover valid values.",
        ],
        instance_name: Annotated[
            str | None,
            Field(default=None, description="Apstra instance name. Do not ask the user for this — leave as None to query all instances. Only set if the user explicitly names a specific instance."),
        ] = None,
        ctx: Context = None,
    ) -> dict:
        """
        Return all configlets applied to a blueprint.

        Configlets are Jinja2 configuration snippets injected into Apstra's rendered device
        config. Use this to inspect what custom configuration is being pushed to devices,
        understand the condition expression controlling which devices each snippet targets
        (e.g. 'role in ["spine"]'), and view the exact Jinja2 template text. Call
        get_blueprint_configlet_drift afterwards to check whether any have drifted from
        the design catalogue master.

        Each configlet includes: id, display_name, condition (device-match expression),
        generators (list, each with config_style e.g. "junos", section, template_text,
        negation_template_text, render_style).
        Data source: graph database (auto-rebuilt when blueprint version changes).
        """
        return await handle_get_configlets(
            ctx.lifespan_context["sessions"],
            ctx.lifespan_context["graph_registry"],
            blueprint_id,
            instance_name,
        )

    @mcp.tool()
    async def get_blueprint_property_sets(
        blueprint_id: Annotated[
            str,
            "Apstra blueprint ID. Use get_blueprints to discover valid values.",
        ],
        instance_name: Annotated[
            str | None,
            Field(default=None, description="Apstra instance name. Do not ask the user for this — leave as None to query all instances. Only set if the user explicitly names a specific instance."),
        ] = None,
        ctx: Context = None,
    ) -> dict:
        """
        Return all property sets applied to a blueprint.

        Property sets are key-value stores injected as Jinja2 variables into configlet
        templates, decoupling environment-specific values (SNMP community strings, syslog
        collector IPs, NTP server addresses) from the template logic. Use this to see what
        values configlets on this blueprint will render with. Check stale=true entries to
        find property sets whose catalogue master has been updated since last application.
        Call get_blueprint_property_set_drift to see exactly which values have diverged.

        Each property set includes: id, display_name, property_set_id (machine identifier,
        e.g. "flow_data"), stale (bool), values (key-value dict).
        Data source: graph database (auto-rebuilt when blueprint version changes).
        """
        return await handle_get_property_sets(
            ctx.lifespan_context["sessions"],
            ctx.lifespan_context["graph_registry"],
            blueprint_id,
            instance_name,
        )
