from typing import Annotated

from pydantic import Field

from config.settings import RagConfig
from handlers.docs import handle_query_apstra_product_docs
from primitives.rag_client import preflight_rag


def register(mcp, rag_config: RagConfig):
    preflight_rag(rag_config)

    @mcp.tool()
    async def query_apstra_product_docs(
        query: Annotated[
            str,
            Field(
                description=(
                    "Natural-language query about Apstra product features, configuration, and best practices. "
                    "Examples: 'how to configure BGP peering', 'what is an IBA probe', "
                    "'how does VXLAN work in Apstra', 'configlet best practices'. "
                    "Use this for product documentation and 'how-to' guidance. "
                    "For live network topology or device state, use API tools instead."
                ),
            ),
        ],
    ) -> dict:
        """
        Query Apstra product documentation for configuration guidance, features, and best practices.

        This tool searches the official Apstra documentation (admin guides, best practices, feature docs).
        Use it to answer "how do I..." questions about using and configuring Apstra.

        NOT for:
        - Live network state queries (use topology/API tools instead)
        - Reference architecture questions (use get_reference_design_* tools)
        - Device-level diagnostics (use run_device_commands or anomaly tools)
        """
        return await handle_query_apstra_product_docs(query=query, rag_config=rag_config)
