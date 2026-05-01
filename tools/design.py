from typing import Annotated

from fastmcp import Context
from pydantic import Field

from handlers.design_catalogue import (
    handle_get_design_configlets,
    handle_get_design_property_sets,
    handle_get_configlet_drift,
    handle_get_property_set_drift,
)


def register(mcp):

    _BP_DESC_REQ = (
        "Required. Apstra blueprint ID or partial label (e.g. 'DC1'). "
        "Use get_blueprints to list available blueprints and their IDs. "
        "Pass a full UUID for a specific blueprint."
    )
    _INST_DESC = (
        "Apstra instance name. Do not ask the user for this — leave as None to query all instances. "
        "Only set if the user explicitly names a specific instance."
    )

    @mcp.tool()
    async def get_design_configlets(
        instance_name: Annotated[str | None, Field(default=None, description=_INST_DESC)] = None,
        ctx: Context = None,
    ) -> dict:
        """
        Return all configlets from the Apstra instance-level design catalogue.

        The design catalogue holds the master copies of configlets. Blueprint-applied copies
        can drift from these originals — use get_blueprint_configlet_drift to detect that.

        Returns: configlets (list with id, display_name, ref_archs, generators (each with
        config_style, section, template_text, negation_template_text, render_style, filename),
        created_at, last_modified_at), count.
        Data source: live Apstra API.
        """
        return await handle_get_design_configlets(
            ctx.lifespan_context["sessions"], instance_name
        )

    @mcp.tool()
    async def get_design_property_sets(
        instance_name: Annotated[str | None, Field(default=None, description=_INST_DESC)] = None,
        ctx: Context = None,
    ) -> dict:
        """
        Return all property sets from the Apstra instance-level design catalogue.

        Property sets are key-value dictionaries used in configlet templates (e.g. SNMP
        community strings, syslog server IPs). Blueprint-applied copies can drift from catalogue
        originals — use get_blueprint_property_set_drift to detect that.

        Returns: property_sets (list with id, label, values (dict), created_at, updated_at), count.
        Data source: live Apstra API.
        """
        return await handle_get_design_property_sets(
            ctx.lifespan_context["sessions"], instance_name
        )

    @mcp.tool()
    async def get_blueprint_configlet_drift(
        blueprint_id: Annotated[str, Field(description=_BP_DESC_REQ)],
        instance_name: Annotated[str | None, Field(default=None, description=_INST_DESC)] = None,
        ctx: Context = None,
    ) -> dict:
        """
        Compare configlets applied to a blueprint against the instance-level design catalogue
        and report any whose template_text has drifted.

        When a configlet is applied to a blueprint, Apstra takes a copy. If the catalogue master
        is later updated (or the blueprint copy is directly edited), the two diverge. Configlets
        are matched by display_name.

        Returns: matched (list with display_name, blueprint_id, catalogue_id, condition,
        has_drift, generator_diffs (list with generator_index, config_style, section,
        blueprint_template_text, catalogue_template_text)), blueprint_only (applied but not in
        catalogue), catalogue_only (in catalogue but not applied to this blueprint).
        Data source: graph database (blueprint configlets) + live Apstra API (catalogue).
        """
        return await handle_get_configlet_drift(
            ctx.lifespan_context["sessions"],
            ctx.lifespan_context["graph_registry"],
            blueprint_id,
            instance_name,
        )

    @mcp.tool()
    async def get_blueprint_property_set_drift(
        blueprint_id: Annotated[str, Field(description=_BP_DESC_REQ)],
        instance_name: Annotated[str | None, Field(default=None, description=_INST_DESC)] = None,
        ctx: Context = None,
    ) -> dict:
        """
        Compare property sets applied to a blueprint against the instance-level design catalogue
        and report any whose values have drifted.

        When a property set is applied to a blueprint, Apstra takes a copy. If the catalogue
        master is later updated (or the blueprint copy is directly edited), the key-value pairs
        diverge. Property sets are matched by property_set_id.

        Returns: matched (list with display_name, property_set_id, has_drift, blueprint_values,
        catalogue_values), blueprint_only (applied but not in catalogue), catalogue_only (in
        catalogue but not applied to this blueprint).
        Data source: graph database (blueprint property sets) + live Apstra API (catalogue).
        """
        return await handle_get_property_set_drift(
            ctx.lifespan_context["sessions"],
            ctx.lifespan_context["graph_registry"],
            blueprint_id,
            instance_name,
        )
