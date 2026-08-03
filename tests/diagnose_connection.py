#!/usr/bin/env python3
"""
diagnose_connection.py

Quick diagnostic tool to test Apstra connectivity without starting the full MCP server.
Useful for debugging SSL/TLS and authentication issues.

Usage:
    python diagnose_connection.py

Tests:
  1. Can Python reach the host? (TCP/TLS handshake)
  2. Is the /api/version endpoint responding?
  3. Can we authenticate with the provided credentials?
"""

import asyncio
import os
import sys

try:
    import httpx
except ImportError:
    print("ERROR: httpx not installed. Install with: pip install httpx")
    sys.exit(1)

from config.settings import load_sessions


async def test_host_reachability(host: str, ssl_verify: bool = False) -> tuple[bool, str]:
    """
    Test if the host is reachable via HTTPS.
    Returns (success, message).
    """
    try:
        async with httpx.AsyncClient(verify=ssl_verify, timeout=10.0) as client:
            response = await client.get(f"{host}/api/version", follow_redirects=True)
            if response.status_code == 200:
                return True, f"✓ Host reachable: {host} (returned {response.status_code})"
            else:
                return False, f"✗ Host returned HTTP {response.status_code}"
    except httpx.ConnectError as e:
        return False, f"✗ Connection failed: {type(e).__name__} — host may be unreachable or port is blocked"
    except httpx.TimeoutException:
        return False, f"✗ Connection timeout — host not responding within 10 seconds"
    except Exception as e:
        return False, f"✗ Error: {type(e).__name__}: {e}"


async def test_authentication(host: str, username: str, password: str, ssl_verify: bool = False) -> tuple[bool, str]:
    """
    Test if authentication works.
    Returns (success, message).
    """
    try:
        async with httpx.AsyncClient(verify=ssl_verify, timeout=10.0) as client:
            response = await client.post(
                f"{host}/api/aaa/login",
                json={"username": username, "password": password},
            )

            # Some Apstra/controller variants return 201 on successful login.
            if 200 <= response.status_code < 300:
                try:
                    data = response.json()
                except ValueError:
                    return False, (
                        f"✗ Login returned HTTP {response.status_code} but response was not JSON"
                    )

                if data.get("token"):
                    return True, (
                        f"✓ Authentication successful (HTTP {response.status_code}, token obtained)"
                    )

                return False, (
                    f"✗ Login returned HTTP {response.status_code} but no token in response"
                )

            if response.status_code == 401:
                return False, f"✗ Authentication failed (401 Unauthorized) — check username and password"

            return False, f"✗ Login returned HTTP {response.status_code}: {response.text[:100]}"
    except httpx.ConnectError:
        return False, f"✗ Connection failed — cannot reach {host}"
    except httpx.TimeoutException:
        return False, f"✗ Connection timeout"
    except Exception as e:
        return False, f"✗ Error: {type(e).__name__}: {e}"


async def main():
    print("=" * 80)
    print("Apstra MCP Connection Diagnostics")
    print("=" * 80)
    print()

    # Load sessions from configuration
    try:
        sessions = load_sessions()
    except Exception as e:
        print(f"ERROR: Could not load configuration: {e}")
        print()
        print("Make sure you have one of the following:")
        print("  1. config/instances.yaml with your Apstra instance(s)")
        print("  2. APSTRA_HOST, APSTRA_USERNAME, APSTRA_PASSWORD environment variables")
        print("  3. APSTRA_CONFIG_FILE pointing to a valid YAML file")
        sys.exit(1)

    if not sessions:
        print("ERROR: No Apstra instances configured")
        sys.exit(1)

    print(f"Found {len(sessions)} Apstra instance(s) to test:\n")

    all_ok = True
    for i, session in enumerate(sessions, 1):
        print(f"Instance {i}: {session.name}")
        print(f"  Host: {session.host}")
        print(f"  Username: {session._username}")
        print(f"  SSL Verify: {session._ssl_verify}")
        print()

        # Test reachability
        ok, msg = await test_host_reachability(session.host, session._ssl_verify)
        print(f"  {msg}")
        if not ok:
            all_ok = False
            continue

        # Test authentication
        ok, msg = await test_authentication(
            session.host,
            session._username,
            session._password,
            session._ssl_verify,
        )
        print(f"  {msg}")
        if not ok:
            all_ok = False

        print()

    print("=" * 80)
    if all_ok:
        print("✓ All diagnostics passed. The MCP server should be able to connect.")
    else:
        print("✗ Some diagnostics failed. Fix the issues above and try again.")
        print()
        print("Troubleshooting tips:")
        print("  - If you see 'Connection refused', the host is not reachable. Check:")
        print("    • Is the IP/hostname correct?")
        print("    • Is the Apstra instance running?")
        print("    • Is there a firewall blocking port 443?")
        print()
        print("  - If you see 'SSL' errors:")
        print("    • Make sure APSTRA_SSL_VERIFY=false (or omit it, defaults to false)")
        print("    • Self-signed certificates are normal for Apstra deployments")
        print()
        print("  - If you see '401 Unauthorized':")
        print("    • Check your username and password")
        print("    • Verify APSTRA_USERNAME and APSTRA_PASSWORD are set correctly")
        print()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
