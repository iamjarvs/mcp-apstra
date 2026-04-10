"""
auth_manager.py

Manages authentication lifecycle for one or more Apstra instances.

Each Apstra instance is represented by an ApstraSession object. On startup,
call authenticate() to obtain an initial token, then start_background_refresh()
to launch two concurrent asyncio tasks per session that run for the lifetime
of the server process:

  - Token refresh loop: decodes the JWT exp claim and proactively re-authenticates
    before the token expires.
  - Probe loop: calls a lightweight API endpoint on a fixed interval to confirm
    the host is reachable, independent of token validity.

Session status is always available via session.status() and reflects both
token health and host reachability separately.
"""

import asyncio
import base64
import json
import logging
import time
from datetime import datetime, timezone
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# How many seconds before JWT expiry to trigger a proactive refresh.
TOKEN_REFRESH_MARGIN_SECONDS = 300  # 5 minutes

# How often the probe loop checks host reachability.
PROBE_INTERVAL_SECONDS = 30

# How long to wait before retrying after a failed authentication.
AUTH_RETRY_INTERVAL_SECONDS = 15

# Lightweight endpoint used to probe host reachability.
PROBE_ENDPOINT = "/api/version"


def _decode_jwt_expiry(token: str) -> Optional[float]:
    """
    Decodes the exp claim from a JWT token without verifying the signature.
    Returns the expiry as a Unix timestamp (float), or None if it cannot be read.

    JWT tokens are three base64url-encoded segments separated by dots.
    The second segment is the payload containing claims including exp.
    """
    try:
        payload_segment = token.split(".")[1]
        # base64url requires padding to a multiple of 4 characters
        padding = 4 - len(payload_segment) % 4
        if padding != 4:
            payload_segment += "=" * padding
        payload = json.loads(base64.urlsafe_b64decode(payload_segment))
        return float(payload["exp"])
    except Exception as e:
        logger.warning("Could not decode JWT expiry claim: %s", e)
        return None


class ApstraSession:
    """
    Represents an authenticated connection to a single Apstra instance.

    Do not instantiate this directly in handlers or tools — use the session
    pool built by config/settings.py at server startup.

    Attributes:
        name:             Friendly name for this instance (from instances.yaml).
        host:             Base URL of the Apstra instance.
        token_valid:      True if the current token is present and not expired.
        host_reachable:   True if the last probe succeeded.
        last_token_refresh: datetime of the last successful authentication.
        last_probe:       datetime of the last successful probe.
    """

    def __init__(self, name: str, host: str, username: str, password: str, ssl_verify: bool = False):
        self.name = name
        self.host = host.rstrip("/")
        self._username = username
        self._password = password
        self._ssl_verify = ssl_verify

        self._token: Optional[str] = None
        self._token_expiry: Optional[float] = None

        self.token_valid: bool = False
        self.host_reachable: bool = False
        self.last_token_refresh: Optional[datetime] = None
        self.last_probe: Optional[datetime] = None

        self._refresh_task: Optional[asyncio.Task] = None
        self._probe_task: Optional[asyncio.Task] = None

    # ------------------------------------------------------------------
    # Public interface used by primitives
    # ------------------------------------------------------------------

    async def get_token(self) -> str:
        """
        Returns the current valid token.
        Raises RuntimeError if no valid token is available.
        Primitives call this before every API request.
        """
        if self._token and self.token_valid:
            return self._token
        raise RuntimeError(
            f"No valid token available for instance '{self.name}'. "
            "Authentication may have failed at startup or the refresh loop "
            "has not yet completed a retry."
        )

    def status(self) -> dict:
        """
        Returns a snapshot of this session's health state.
        Used by tools that expose instance health to the LLM.
        """
        return {
            "instance": self.name,
            "host": self.host,
            "token_valid": self.token_valid,
            "host_reachable": self.host_reachable,
            "last_token_refresh": (
                self.last_token_refresh.isoformat() if self.last_token_refresh else None
            ),
            "last_probe": (
                self.last_probe.isoformat() if self.last_probe else None
            ),
            "token_expires_in_seconds": self._seconds_until_expiry(),
        }

    # ------------------------------------------------------------------
    # Startup
    # ------------------------------------------------------------------

    async def authenticate(self):
        """
        Performs an initial authentication against the Apstra login endpoint.
        Call this once at startup before starting background tasks.
        Raises on failure so the server can fail fast with a clear error.
        """
        logger.info("[%s] Authenticating...", self.name)
        await self._do_authenticate()
        logger.info(
            "[%s] Authenticated. Token expires in %ds.",
            self.name,
            int(self._seconds_until_expiry()),
        )

    def start_background_refresh(self):
        """
        Launches the token refresh loop and probe loop as asyncio background tasks.
        Call this once per session after authenticate() succeeds.
        Both tasks run indefinitely until the server process exits.
        """
        self._refresh_task = asyncio.create_task(
            self._token_refresh_loop(),
            name=f"token-refresh-{self.name}",
        )
        self._probe_task = asyncio.create_task(
            self._probe_loop(),
            name=f"probe-{self.name}",
        )
        logger.info(
            "[%s] Background token refresh and probe tasks started.", self.name
        )

    # ------------------------------------------------------------------
    # Background task: token refresh
    # ------------------------------------------------------------------

    async def _token_refresh_loop(self):
        """
        Runs forever. Sleeps until TOKEN_REFRESH_MARGIN_SECONDS before expiry,
        then re-authenticates. On failure, retries every AUTH_RETRY_INTERVAL_SECONDS
        until successful.
        """
        while True:
            sleep_seconds = self._seconds_until_expiry() - TOKEN_REFRESH_MARGIN_SECONDS
            if sleep_seconds > 0:
                logger.debug(
                    "[%s] Token refresh sleeping for %ds.",
                    self.name,
                    int(sleep_seconds),
                )
                await asyncio.sleep(sleep_seconds)

            logger.info("[%s] Refreshing token...", self.name)
            try:
                await self._do_authenticate()
                logger.info(
                    "[%s] Token refreshed. Next expiry in %ds.",
                    self.name,
                    int(self._seconds_until_expiry()),
                )
            except Exception as e:
                self.token_valid = False
                logger.error(
                    "[%s] Token refresh failed: %s. Retrying in %ds.",
                    self.name,
                    e,
                    AUTH_RETRY_INTERVAL_SECONDS,
                )
                await asyncio.sleep(AUTH_RETRY_INTERVAL_SECONDS)

    # ------------------------------------------------------------------
    # Background task: probe
    # ------------------------------------------------------------------

    async def _probe_loop(self):
        """
        Runs forever. Calls PROBE_ENDPOINT every PROBE_INTERVAL_SECONDS.

        On probe failure, attempts a re-authentication before concluding the
        host is unreachable — a failed probe may indicate a server-side token
        revocation rather than a connectivity problem, and the JWT expiry check
        would not catch that. If re-auth succeeds, the probe is retried once
        with the fresh token. Only if both the re-auth and the retry fail is
        host_reachable set to False.
        """
        while True:
            probe_succeeded = await self._run_probe()

            if not probe_succeeded:
                logger.warning(
                    "[%s] Probe failed — attempting re-authentication in case "
                    "token was revoked server-side.",
                    self.name,
                )
                try:
                    await self._do_authenticate()
                    logger.info(
                        "[%s] Re-authentication succeeded after probe failure — "
                        "retrying probe with fresh token.",
                        self.name,
                    )
                    probe_succeeded = await self._run_probe()
                    if not probe_succeeded:
                        logger.error(
                            "[%s] Probe still failing after re-authentication — "
                            "host is likely unreachable.",
                            self.name,
                        )
                except Exception as auth_error:
                    logger.error(
                        "[%s] Re-authentication also failed: %s — "
                        "host is likely unreachable.",
                        self.name,
                        auth_error,
                    )

                self.host_reachable = probe_succeeded

            await asyncio.sleep(PROBE_INTERVAL_SECONDS)

    async def _run_probe(self) -> bool:
        """
        Makes a single probe request to PROBE_ENDPOINT.
        Returns True on success, False on any failure.
        Updates host_reachable and last_probe on success.
        """
        try:
            async with httpx.AsyncClient(verify=self._ssl_verify, timeout=10.0) as client:
                response = await client.get(
                    f"{self.host}{PROBE_ENDPOINT}",
                    headers=self._auth_headers(),
                )
                response.raise_for_status()
            self.host_reachable = True
            self.last_probe = datetime.now(timezone.utc)
            logger.debug("[%s] Probe successful.", self.name)
            return True
        except Exception as e:
            logger.warning("[%s] Probe request failed: %s", self.name, e)
            return False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _do_authenticate(self):
        """
        Makes the login API call, stores the token, and updates status flags.
        Raises httpx.HTTPError or RuntimeError on failure.
        """
        async with httpx.AsyncClient(verify=self._ssl_verify, timeout=15.0) as client:
            response = await client.post(
                f"{self.host}/api/aaa/login",
                json={"username": self._username, "password": self._password},
            )
            response.raise_for_status()
            data = response.json()

        token = data.get("token")
        if not token:
            raise RuntimeError(
                f"Login response from '{self.name}' did not contain a token. "
                f"Response keys: {list(data.keys())}"
            )

        expiry = _decode_jwt_expiry(token)
        if expiry is None:
            # Fall back to a conservative 1-hour TTL if JWT decode fails
            expiry = time.time() + 3600
            logger.warning(
                "[%s] Could not decode JWT expiry — assuming 1-hour TTL.", self.name
            )

        self._token = token
        self._token_expiry = expiry
        self.token_valid = True
        self.last_token_refresh = datetime.now(timezone.utc)

    def _seconds_until_expiry(self) -> float:
        """Returns seconds remaining until token expiry. Returns 0 if unknown."""
        if self._token_expiry is None:
            return 0.0
        return max(0.0, self._token_expiry - time.time())

    def _auth_headers(self) -> dict:
        """Returns the auth header dict for use in requests. Safe to call even if token is None."""
        if self._token:
            return {"AUTHTOKEN": self._token}
        return {}