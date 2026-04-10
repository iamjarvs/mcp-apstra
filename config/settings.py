"""
settings.py

Reads instances.yaml and builds the session pool used by all handlers.

Each Apstra instance in instances.yaml becomes an ApstraSession object.
Credentials can be overridden per instance using environment variables,
which is the recommended approach for anything beyond local development.

Environment variable override pattern:
    APSTRA_<NAME>_USERNAME
    APSTRA_<NAME>_PASSWORD

Where <NAME> is the instance name from instances.yaml, uppercased with
hyphens replaced by underscores. For example, an instance named dc-primary
can have its credentials overridden with:
    APSTRA_DC_PRIMARY_USERNAME=admin
    APSTRA_DC_PRIMARY_PASSWORD=secretpassword

load_sessions() returns a list of ApstraSession objects ready to be passed
to authenticate() and start_background_refresh(). It does not perform any
network calls — that happens in server.py during the lifespan startup hook.
"""

import logging
import os
from pathlib import Path
from typing import List

import yaml

from primitives.auth_manager import ApstraSession

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).parent / "instances.yaml"


def _env_key(instance_name: str) -> str:
    """
    Converts an instance name to the environment variable prefix used for
    credential overrides. Uppercases the name and replaces hyphens with
    underscores, so dc-primary becomes APSTRA_DC_PRIMARY.
    """
    return "APSTRA_" + instance_name.upper().replace("-", "_")


def _resolve_credentials(instance: dict) -> tuple[str, str]:
    """
    Returns the username and password for an instance, preferring environment
    variable overrides over the values in instances.yaml.

    Environment variables take precedence so that secrets never need to be
    stored in the config file in production environments.
    """
    prefix = _env_key(instance["name"])

    username = os.environ.get(f"{prefix}_USERNAME") or instance.get("username")
    password = os.environ.get(f"{prefix}_PASSWORD") or instance.get("password")

    if not username:
        raise ValueError(
            f"No username configured for instance '{instance['name']}'. "
            f"Set it in instances.yaml or via the {prefix}_USERNAME environment variable."
        )
    if not password:
        raise ValueError(
            f"No password configured for instance '{instance['name']}'. "
            f"Set it in instances.yaml or via the {prefix}_PASSWORD environment variable."
        )

    return username, password


def load_sessions() -> List[ApstraSession]:
    """
    Reads instances.yaml and returns a list of ApstraSession objects.

    Does not authenticate or start background tasks — call authenticate()
    and start_background_refresh() on each session in the server lifespan
    startup hook (server.py).

    Raises FileNotFoundError if instances.yaml does not exist.
    Raises ValueError if any instance is missing required fields or credentials.
    Raises yaml.YAMLError if instances.yaml is not valid YAML.
    """
    if not _CONFIG_PATH.exists():
        raise FileNotFoundError(
            f"instances.yaml not found at {_CONFIG_PATH}. "
            "Copy the example config and fill in your Apstra instance details."
        )

    with open(_CONFIG_PATH) as f:
        config = yaml.safe_load(f)

    raw_instances = config.get("instances", [])

    if not raw_instances:
        raise ValueError(
            "instances.yaml contains no instances. "
            "Add at least one Apstra instance under the 'instances' key."
        )

    sessions = []
    for instance in raw_instances:
        name = instance.get("name")
        host = instance.get("host")

        if not name:
            raise ValueError(
                "An entry in instances.yaml is missing the required 'name' field."
            )
        if not host:
            raise ValueError(
                f"Instance '{name}' in instances.yaml is missing the required 'host' field."
            )

        username, password = _resolve_credentials(instance)
        ssl_verify = instance.get("ssl_verify", False)

        session = ApstraSession(
            name=name,
            host=host,
            username=username,
            password=password,
            ssl_verify=ssl_verify,
        )

        sessions.append(session)
        logger.info("Loaded instance '%s' (%s)", name, host)

    logger.info("Session pool built with %d instance(s).", len(sessions))
    return sessions