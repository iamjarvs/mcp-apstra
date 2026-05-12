"""
settings.py

Reads configuration and builds the session pool used by all handlers.

Configuration is resolved in the following priority order:

1. APSTRA_CONFIG_FILE env var — path to a YAML file anywhere on disk.
   Useful for multi-instance users running via uvx who don't want the YAML
   inside the package directory.

2. Default config/instances.yaml — the existing local development path.
   Behaviour is unchanged for users who already have this file.

3. Single-instance env vars — APSTRA_HOST, APSTRA_USERNAME, APSTRA_PASSWORD.
   No YAML file required. Intended for the simplest Claude Desktop / uvx
   deployment where all config lives in the MCP server's env block.
   APSTRA_SSL_VERIFY defaults to "false" (typical for Apstra deployments).
   APSTRA_INSTANCE_NAME defaults to "default".

Per-instance credential overrides (pattern APSTRA_<NAME>_USERNAME /
APSTRA_<NAME>_PASSWORD) still work when loading from a YAML file and take
precedence over values in the file.

load_sessions() returns a list of ApstraSession objects ready to be passed
to authenticate() and start_background_refresh(). It does not perform any
network calls — that happens in server.py during the lifespan startup hook.
"""

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import List

import yaml

from primitives.auth_manager import ApstraSession

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG_PATH = Path(__file__).parent / "instances.yaml"


@dataclass(frozen=True)
class RagConfig:
    enabled: bool
    embedding_provider: str
    embedding_model: str
    embedding_url: str
    top_k: int = 5


def _resolve_config_path() -> Path | None:
    """
    Returns the Path to the YAML config file to use, or None if no file is
    available and the caller should fall back to single-instance env vars.

    Priority:
        1. APSTRA_CONFIG_FILE env var (explicit path)
        2. Default config/instances.yaml (local dev)
    """
    explicit = os.environ.get("APSTRA_CONFIG_FILE")
    if explicit:
        path = Path(explicit)
        if not path.exists():
            raise FileNotFoundError(
                f"APSTRA_CONFIG_FILE points to '{path}' but the file does not exist."
            )
        return path

    if _DEFAULT_CONFIG_PATH.exists():
        return _DEFAULT_CONFIG_PATH

    return None


def _build_single_instance_session() -> ApstraSession:
    """
    Constructs a single ApstraSession from the simple env vars:
        APSTRA_HOST      — required, e.g. https://apstra.example.com
        APSTRA_USERNAME  — required
        APSTRA_PASSWORD  — required
        APSTRA_SSL_VERIFY — optional, 'true' or 'false', defaults to 'false'
        APSTRA_INSTANCE_NAME — optional, defaults to 'default'
    """
    host = os.environ.get("APSTRA_HOST")
    username = os.environ.get("APSTRA_USERNAME")
    password = os.environ.get("APSTRA_PASSWORD")

    missing = [name for name, val in (
        ("APSTRA_HOST", host),
        ("APSTRA_USERNAME", username),
        ("APSTRA_PASSWORD", password),
    ) if not val]

    if missing:
        raise ValueError(
            "No instances.yaml found and the following required environment variables are "
            f"not set: {', '.join(missing)}.\n\n"
            "Configure the server using one of:\n"
            "  1. Set APSTRA_CONFIG_FILE=/path/to/instances.yaml (multi-instance)\n"
            "  2. Place instances.yaml in the config/ directory (local dev)\n"
            "  3. Set APSTRA_HOST, APSTRA_USERNAME, and APSTRA_PASSWORD (single instance)\n"
        )

    ssl_verify_raw = os.environ.get("APSTRA_SSL_VERIFY", "false").lower()
    ssl_verify = ssl_verify_raw in ("1", "true", "yes")
    name = os.environ.get("APSTRA_INSTANCE_NAME", "default")

    logger.info("Loaded single instance '%s' from environment variables.", name)
    return ApstraSession(
        name=name,
        host=host,
        username=username,
        password=password,
        ssl_verify=ssl_verify,
    )


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


def _load_config_data() -> dict:
    """
    Loads and returns the YAML config as a dict.

    Returns an empty dict when no config file is available.
    """
    config_path = _resolve_config_path()
    if config_path is None:
        return {}

    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def get_rag_config() -> RagConfig | None:
    """
    Returns the optional RAG configuration when enabled.

    Behavior (priority order):
      1. If YAML rag block exists and enabled=true -> use YAML values
      2. If YAML rag block missing/disabled -> check environment variables
      3. If environment variables present and valid -> use env values
      4. Otherwise -> return None (RAG disabled)

    Environment variables:
      - APSTRA_RAG_ENABLED: set to "1", "true", or "yes" to enable
      - APSTRA_RAG_EMBEDDING_PROVIDER: e.g., "ollama", "lmstudio", "openai_compatible"
      - APSTRA_RAG_EMBEDDING_MODEL: embedding model name
      - APSTRA_RAG_EMBEDDING_URL: embedding service URL
      - APSTRA_RAG_TOP_K: optional, defaults to 5
    """
    config = _load_config_data()
    rag = config.get("rag")

    # Try YAML config first
    if isinstance(rag, dict) and bool(rag.get("enabled", False)):
        provider = str(rag.get("embedding_provider") or "").strip()
        model = str(rag.get("embedding_model") or "").strip()
        url = str(rag.get("embedding_url") or "").strip()

        missing: list[str] = []
        if not provider:
            missing.append("rag.embedding_provider")
        if not model:
            missing.append("rag.embedding_model")
        if not url:
            missing.append("rag.embedding_url")

        if missing:
            raise ValueError(
                "RAG is enabled in YAML but missing required settings: "
                + ", ".join(missing)
            )

        top_k_raw = rag.get("top_k", 5)
        try:
            top_k = int(top_k_raw)
        except (TypeError, ValueError) as exc:
            raise ValueError("rag.top_k must be an integer") from exc

        if top_k < 1:
            raise ValueError("rag.top_k must be >= 1")

        logger.info("RAG configuration loaded from instances.yaml.")
        return RagConfig(
            enabled=True,
            embedding_provider=provider,
            embedding_model=model,
            embedding_url=url,
            top_k=top_k,
        )

    # Fall back to environment variables
    enabled_raw = os.environ.get("APSTRA_RAG_ENABLED", "").lower()
    if enabled_raw not in ("1", "true", "yes"):
        return None

    provider = os.environ.get("APSTRA_RAG_EMBEDDING_PROVIDER", "").strip()
    model = os.environ.get("APSTRA_RAG_EMBEDDING_MODEL", "").strip()
    url = os.environ.get("APSTRA_RAG_EMBEDDING_URL", "").strip()

    missing: list[str] = []
    if not provider:
        missing.append("APSTRA_RAG_EMBEDDING_PROVIDER")
    if not model:
        missing.append("APSTRA_RAG_EMBEDDING_MODEL")
    if not url:
        missing.append("APSTRA_RAG_EMBEDDING_URL")

    if missing:
        raise ValueError(
            "APSTRA_RAG_ENABLED is set but missing required environment variables: "
            + ", ".join(missing)
        )

    top_k_raw = os.environ.get("APSTRA_RAG_TOP_K", "5").strip()
    try:
        top_k = int(top_k_raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("APSTRA_RAG_TOP_K must be an integer") from exc

    if top_k < 1:
        raise ValueError("APSTRA_RAG_TOP_K must be >= 1")

    logger.info("RAG configuration loaded from environment variables.")
    return RagConfig(
        enabled=True,
        embedding_provider=provider,
        embedding_model=model,
        embedding_url=url,
        top_k=top_k,
    )


def load_sessions() -> List[ApstraSession]:
    """
    Builds the session pool using the priority order described in the module docstring.

    Does not authenticate or start background tasks — call authenticate()
    and start_background_refresh() on each session in the server lifespan
    startup hook (server.py).
    """
    config_path = _resolve_config_path()

    if config_path is None:
        # No YAML file available — try single-instance env vars.
        session = _build_single_instance_session()
        logger.info("Session pool built with 1 instance (env vars).")
        return [session]

    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    raw_instances = (config or {}).get("instances") or []

    if not raw_instances:
        # If this is the *default* config file (not explicitly set by the user),
        # treat an empty/commented-out file as "not configured" and fall through
        # to single-instance env vars so that APSTRA_HOST/USERNAME/PASSWORD work
        # without needing to remove instances.yaml from the repo.
        explicit_config = os.environ.get("APSTRA_CONFIG_FILE")
        if not explicit_config:
            logger.info(
                "'%s' has no instances defined — falling back to single-instance "
                "environment variables (APSTRA_HOST / APSTRA_USERNAME / APSTRA_PASSWORD).",
                config_path,
            )
            session = _build_single_instance_session()
            logger.info("Session pool built with 1 instance (env vars).")
            return [session]

        raise ValueError(
            f"'{config_path}' contains no instances. "
            "Add at least one Apstra instance under the 'instances' key."
        )

    sessions = []
    for instance in raw_instances:
        name = instance.get("name")
        host = instance.get("host")

        if not name:
            raise ValueError(
                f"An entry in '{config_path}' is missing the required 'name' field."
            )
        if not host:
            raise ValueError(
                f"Instance '{name}' in '{config_path}' is missing the required 'host' field."
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