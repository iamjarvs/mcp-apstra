import json
import ipaddress
import re
from collections import defaultdict

from handlers.bgp import handle_get_external_peerings
from handlers.run_commands import handle_run_commands
from handlers.systems import handle_get_systems


_VALID_INTENTS = {
    "discover",
    "explain_policy",
    "diagnose_hidden_routes",
    "compare_rib",
    "peer_summary",
    "resolve_next_hop",
    "full_audit",
}

_DEFAULT_LIMIT = 20
_MAX_LIMIT = 100

_POLICY_LABEL_TOKENS = {
    "import",
    "import:",
    "export",
    "export:",
    "policy",
    "policy:",
    "policies",
    "policies:",
}

_PREFIX_RE = re.compile(r"^\s*([0-9A-Fa-f:.]+/\d+)\b")
_ROUTE_SUMMARY_RE = re.compile(
    r"(\d+)\s+destinations,\s+(\d+)\s+routes\s+\((\d+)\s+active,\s+(\d+)\s+holddown,\s+(\d+)\s+hidden\)",
    re.IGNORECASE,
)


def _select_sessions(sessions, instance_name):
    if instance_name is None or instance_name == "all":
        return sessions
    matched = [s for s in sessions if s.name == instance_name]
    if not matched:
        raise ValueError(f"No instance named '{instance_name}' in session pool")
    return matched


def _normalize_limit(limit: int | None) -> int:
    if limit is None:
        return _DEFAULT_LIMIT
    try:
        value = int(limit)
    except (TypeError, ValueError):
        return _DEFAULT_LIMIT
    return max(1, min(value, _MAX_LIMIT))


def _normalize_ip(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if "/" in text:
        return text.split("/", 1)[0]
    return text


def _flatten_items(result: dict, key: str) -> list:
    if not isinstance(result, dict):
        return []
    if "results" in result:
        out = []
        for entry in result.get("results", []):
            out.extend(entry.get(key, []))
        return out
    return list(result.get(key, []))


def _paginate_list(items: list, limit: int, cursor: str | None) -> dict:
    safe_limit = _normalize_limit(limit)
    if cursor is None:
        offset = 0
    else:
        try:
            offset = max(0, int(str(cursor)))
        except (TypeError, ValueError):
            return {
                "error": "invalid_cursor",
                "hint": "Cursor must be the numeric next_cursor value returned by a previous response.",
            }

    page = items[offset : offset + safe_limit]
    has_more = offset + safe_limit < len(items)
    return {
        "items": page,
        "returned_count": len(page),
        "total_count": len(items),
        "limit": safe_limit,
        "has_more": has_more,
        "next_cursor": str(offset + safe_limit) if has_more else None,
    }


def _json_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, indent=2, sort_keys=True)
    except Exception:
        return str(value)


def _build_system_aliases(systems: list[dict]) -> dict[str, dict]:
    aliases = {}
    for system in systems:
        for candidate in (
            system.get("label"),
            system.get("hostname"),
            system.get("system_id"),
            system.get("id"),
        ):
            if candidate:
                aliases[str(candidate).lower()] = system
    return aliases


def _resolve_system_from_input(systems: list[dict], device: str | None) -> dict | None:
    if not device:
        return None
    aliases = _build_system_aliases(systems)
    return aliases.get(str(device).strip().lower())


def _build_peer_catalog(peerings: list[dict]) -> list[dict]:
    out = []
    seen = set()

    for peering in peerings:
        local = peering.get("local") or {}
        remote = peering.get("remote") or {}
        peer_ip = _normalize_ip(remote.get("ip_address"))
        local_device = local.get("hostname") or "unknown"
        local_system_id = local.get("serial")

        if not peer_ip:
            continue

        key = (str(local_device).lower(), peer_ip)
        if key in seen:
            continue
        seen.add(key)

        address_families = []
        if peering.get("ipv4_safi") == "enabled":
            address_families.append("ipv4-unicast")
        if peering.get("ipv6_safi") == "enabled":
            address_families.append("ipv6-unicast")

        out.append(
            {
                "local_device": local_device,
                "local_system_id": local_system_id,
                "local_interface": local.get("interface"),
                "local_as": local.get("local_asn"),
                "peer_ip": peer_ip,
                "peer_cidr": remote.get("ip_address"),
                "peer_hostname": remote.get("hostname"),
                "peer_as": remote.get("local_asn"),
                "bfd": peering.get("bfd"),
                "address_families": address_families,
                "ipv4_safi": peering.get("ipv4_safi"),
                "ipv6_safi": peering.get("ipv6_safi"),
            }
        )

    out.sort(key=lambda item: (item["local_device"], item["peer_ip"]))
    return out


def _filter_peer_catalog(
    catalog: list[dict],
    device: str | None,
    resolved_system: dict | None,
    peer_ip: str | None,
) -> list[dict]:
    filtered = catalog

    if device:
        candidates = {str(device).lower()}
        if resolved_system:
            for key in (
                resolved_system.get("label"),
                resolved_system.get("hostname"),
                resolved_system.get("system_id"),
            ):
                if key:
                    candidates.add(str(key).lower())

        filtered = [
            item
            for item in filtered
            if str(item.get("local_device", "")).lower() in candidates
            or str(item.get("local_system_id", "")).lower() in candidates
        ]

    if peer_ip:
        normalized_peer = _normalize_ip(peer_ip)
        filtered = [item for item in filtered if item.get("peer_ip") == normalized_peer]

    return filtered


async def _run_commands_for_system(
    sessions,
    registry,
    blueprint_id: str,
    system_id: str,
    commands: list[str],
    instance_name: str | None,
    timeout_seconds: int = 45,
) -> dict:
    response = await handle_run_commands(
        sessions,
        registry,
        blueprint_id,
        commands,
        system_id=system_id,
        instance_name=instance_name,
        timeout_seconds=timeout_seconds,
        output_format="text",
        max_concurrent_systems=1,
    )

    systems = response.get("systems", []) if isinstance(response, dict) else []
    if not systems and isinstance(response, dict) and "results" in response:
        for entry in response.get("results", []):
            systems.extend(entry.get("systems", []))

    if not systems:
        return {
            "error": "no_command_results",
            "hint": "No device command output was returned.",
            "raw": response,
            "outputs": {},
            "failures": [],
        }

    command_results = systems[0].get("command_results") or []
    outputs = {}
    failures = []
    for result in command_results:
        command = str(result.get("command") or "").strip()
        if command:
            outputs[command] = _json_text(result.get("output"))

        status = str(result.get("result") or "").strip().lower()
        if status not in {"success", "ok", "done", "completed"}:
            failures.append(
                {
                    "command": command,
                    "result": result.get("result"),
                    "error": result.get("error"),
                    "hint": result.get("llm_hint"),
                }
            )

    return {"outputs": outputs, "failures": failures, "raw": response}


def _parse_policy_names(neighbor_output: str) -> dict:
    policies = {"import": [], "export": []}
    pending_direction = None
    pending_chunks = []

    for line in (neighbor_output or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        match = re.search(
            r"\b(Import|Export)(?:\s+policy|\s+policies)?\s*:\s*(.+?)\s*$",
            line,
            flags=re.IGNORECASE,
        )
        if match:
            direction = match.group(1).strip().lower()
            raw_value = match.group(2).strip()

            if "[" in raw_value and "]" not in raw_value:
                pending_direction = direction
                pending_chunks = [raw_value]
                continue

            for parsed_direction, name in _extract_policy_names_from_value(raw_value, direction):
                if name not in policies[parsed_direction]:
                    policies[parsed_direction].append(name)
            continue

        if pending_direction:
            pending_chunks.append(stripped)
            joined = " ".join(pending_chunks)
            if "]" in joined:
                for parsed_direction, name in _extract_policy_names_from_value(joined, pending_direction):
                    if name not in policies[parsed_direction]:
                        policies[parsed_direction].append(name)
                pending_direction = None
                pending_chunks = []

    return policies


def _extract_policy_names_from_value(raw_value: str, direction: str) -> list[tuple[str, str]]:
    text = str(raw_value or "").strip()
    if not text:
        return []

    # Some outputs include nested labels like "Import policy: Import: NAME".
    while True:
        nested = re.match(r"^(Import|Export)\s*:\s*(.+)$", text, flags=re.IGNORECASE)
        if not nested:
            break
        if nested.group(1).strip().lower() != direction:
            break
        text = nested.group(2).strip()

    text = text.replace("[", " ").replace("]", " ").replace(",", " ")
    names: list[tuple[str, str]] = []
    active_direction = direction
    for token in text.split():
        normalized = token.strip().strip("\"'").rstrip(";,")
        if not normalized:
            continue
        lowered = normalized.lower()
        if lowered in {"none", "<none>", "-"}:
            continue
        if lowered in _POLICY_LABEL_TOKENS:
            if lowered.startswith("import"):
                active_direction = "import"
            elif lowered.startswith("export"):
                active_direction = "export"
            continue
        names.append((active_direction, normalized))
    return names


def _infer_policy_level(neighbor_output: str) -> str:
    text = (neighbor_output or "").lower()
    if "neighbor-level" in text or "neighbor level" in text:
        return "neighbor"
    if "group-level" in text or "group level" in text:
        return "group"
    if "global-level" in text or "global level" in text:
        return "global"
    # Neighbor policy is the safest default for peer-scoped troubleshooting.
    return "neighbor"


def _flow_control(actions: list[str]) -> str:
    joined = " ".join(actions).lower()
    if "next policy" in joined:
        return "next_policy"
    if "next term" in joined:
        return "next_term"
    if "reject" in joined:
        return "reject"
    if "accept" in joined:
        return "accept"
    return "default"


def _dedupe_keep_order(values: list[str]) -> list[str]:
    seen = set()
    out = []
    for value in values:
        text = str(value).strip()
        if not text:
            continue
        if text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _build_show_policy_command(policy_name: str | None) -> str | None:
    name = str(policy_name or "").strip().strip("\"'")
    if not name:
        return None

    # Skip direction labels accidentally captured from neighbor output.
    if name.lower() in _POLICY_LABEL_TOKENS:
        return None
    if name.lower() in {"none", "<none>", "-", "null", "n/a"}:
        return None

    if re.fullmatch(r"[A-Za-z0-9_.-]+", name):
        return f"show policy {name}"

    escaped = name.replace("\\", "\\\\").replace('"', '\\"')
    return f"show policy \"{escaped}\""


def _parse_policy_terms(policy_name: str, policy_output: str) -> list[dict]:
    lines = (policy_output or "").splitlines()
    term_indexes = []

    for index, line in enumerate(lines):
        if re.match(r"^\s*(?:Term|term)\s+[A-Za-z0-9_.:-]+\s*(?::|\{)\s*$", line):
            term_indexes.append(index)

    if not term_indexes:
        actions = []
        for line in lines:
            text = line.strip().rstrip(";")
            if not text:
                continue
            lower = text.lower()
            if lower in {"accept", "reject", "next term", "next policy"}:
                actions.append(text)

        deduped_actions = _dedupe_keep_order(actions)
        if not deduped_actions:
            return []

        return [
            {
                "policy_name": policy_name,
                "term_name": "default",
                "match_conditions": [],
                "actions": deduped_actions,
                "flow_control": _flow_control(deduped_actions),
            }
        ]

    terms = []
    for idx, start in enumerate(term_indexes):
        end = term_indexes[idx + 1] if idx + 1 < len(term_indexes) else len(lines)
        header = lines[start].strip()
        header_match = re.match(r"^(?:Term|term)\s+([A-Za-z0-9_.:-]+)", header)
        term_name = header_match.group(1).rstrip(":{") if header_match else f"term_{idx + 1}"

        block = lines[start:end]
        match_conditions = []
        actions = []
        mode = None

        for raw_line in block:
            text = raw_line.strip().rstrip(";")
            if not text:
                continue
            lower = text.lower()

            if lower.startswith("term "):
                continue
            if lower.startswith("policy") or lower.startswith("policy-statement"):
                continue
            if lower == "from" or lower == "from {":
                mode = "from"
                continue
            if lower == "then" or lower == "then {":
                mode = "then"
                continue
            if lower == "}" and mode in {"from", "then"}:
                mode = None
                continue

            if lower.startswith("from "):
                mode = "from"
                candidate = text[5:].strip()
                if candidate and candidate != "{":
                    match_conditions.append(candidate)
                continue

            if lower.startswith("then "):
                mode = "then"
                candidate = text[5:].strip()
                if candidate and candidate != "{":
                    actions.append(candidate)
                continue

            if mode == "from":
                if text not in {"{", "}"}:
                    match_conditions.append(text)
                continue

            if mode == "then":
                if text not in {"{", "}"}:
                    actions.append(text)
                continue

        match_conditions = _dedupe_keep_order(match_conditions)
        actions = _dedupe_keep_order(actions)

        terms.append(
            {
                "policy_name": policy_name,
                "term_name": term_name,
                "match_conditions": match_conditions,
                "actions": actions,
                "flow_control": _flow_control(actions),
            }
        )

    return terms


def _summarize_policy_explanation(policy_config: dict, peer_context: dict) -> dict:
    import_terms = policy_config.get("import_terms", [])
    export_terms = policy_config.get("export_terms", [])

    def describe(terms: list[dict]) -> list[str]:
        out = []
        for term in terms:
            conditions = ", ".join(term.get("match_conditions") or []) or "all routes"
            actions = ", ".join(term.get("actions") or []) or "no terminal action"
            out.append(
                f"{term.get('policy_name')}/{term.get('term_name')}: match {conditions}; action {actions}."
            )
        return out

    import_explanation = describe(import_terms)
    export_explanation = describe(export_terms)

    warnings = []
    has_import_reject = any(t.get("flow_control") == "reject" for t in import_terms)
    has_import_catch_all_reject = any(
        t.get("flow_control") == "reject" and not t.get("match_conditions")
        for t in import_terms
    )
    if import_terms and not has_import_reject:
        warnings.append(
            "Import policy has no explicit reject term. Unmatched routes can fall through to protocol defaults."
        )
    if has_import_reject and not has_import_catch_all_reject:
        warnings.append(
            "Import policy has rejects but no catch-all reject term. Verify unmatched routes are intentionally allowed."
        )

    peer_ip = peer_context.get("peer_ip")
    local_device = peer_context.get("device")
    summary = (
        f"Policy analysis for peer {peer_ip} on {local_device}. "
        f"Import terms: {len(import_terms)}. Export terms: {len(export_terms)}."
    )

    return {
        "summary": summary,
        "import_explanation": import_explanation,
        "export_explanation": export_explanation,
        "warnings": warnings,
        "policy_level_note": (
            f"Policy inferred at {policy_config.get('policy_level')} level for this peer."
        ),
    }


def _categorize_hidden_reason(reason: str | None) -> str:
    text = (reason or "").lower()
    if "looped" in text:
        return "as_path_loop"
    if "policy" in text:
        return "policy_rejected"
    if "unusable" in text or "next hop" in text or "nexthop" in text:
        return "next_hop_unusable"
    if "as path loop" in text or "as-path loop" in text or "as loop" in text:
        return "as_path_loop"
    if "martian" in text:
        return "martian"
    if "route target" in text or "rt mismatch" in text:
        return "route_target_mismatch"
    if "prefix" in text and "limit" in text:
        return "prefix_limit"
    return "other"


def _parse_hidden_routes_text(hidden_output: str) -> list[dict]:
    routes = []
    current = None

    for raw_line in (hidden_output or "").splitlines():
        prefix_match = _PREFIX_RE.match(raw_line)
        if prefix_match:
            if current:
                routes.append(current)
            current = {
                "prefix": prefix_match.group(1),
                "as_path": "",
                "communities": [],
                "next_hop": None,
                "inactive_reason": "",
                "reason_category": "other",
                "responsible_term": None,
            }
            continue

        if current is None:
            continue

        line = raw_line.strip()
        lower = line.lower()
        reason_match = re.match(r"^(?:inactive|hidden)\s+reason\s*:\s*(.+)$", line, flags=re.IGNORECASE)
        if reason_match:
            reason = reason_match.group(1).strip()
            current["inactive_reason"] = reason
            current["reason_category"] = _categorize_hidden_reason(reason)
            match = re.search(
                r"policy\s+([A-Za-z0-9_.:-]+)(?:\s+term\s+([A-Za-z0-9_.:-]+))?",
                reason,
                flags=re.IGNORECASE,
            )
            if match:
                current["responsible_term"] = match.group(2) or match.group(1)
        elif lower.startswith("as path:"):
            current["as_path"] = line.split(":", 1)[1].strip()
            if "looped" in current["as_path"].lower() and not current.get("inactive_reason"):
                current["inactive_reason"] = "AS path loop"
                current["reason_category"] = "as_path_loop"
        elif lower.startswith("communities:"):
            values = line.split(":", 1)[1].strip().split()
            current["communities"] = values
        elif "protocol next hop:" in lower:
            current["next_hop"] = line.split(":", 1)[1].strip().split()[0]
        elif lower.startswith("next hop:"):
            current["next_hop"] = line.split(":", 1)[1].strip().split()[0]
        elif "looped" in lower and not current.get("inactive_reason"):
            current["inactive_reason"] = line
            current["reason_category"] = "as_path_loop"

    if current:
        routes.append(current)

    for route in routes:
        if route.get("reason_category") == "other" and "looped" in str(route.get("as_path") or "").lower():
            route["reason_category"] = "as_path_loop"
            if not route.get("inactive_reason"):
                route["inactive_reason"] = f"AS path loop ({route['as_path']})"
        if not route.get("reason_category"):
            route["reason_category"] = _categorize_hidden_reason(route.get("inactive_reason"))

    return routes


def _safe_network(value: str | None):
    if not value:
        return None
    try:
        return ipaddress.ip_network(str(value).strip(), strict=False)
    except Exception:
        return None


def _route_filter_match(prefix: str, condition: str) -> bool | None:
    match = re.search(
        r"\broute-filter\s+([0-9A-Fa-f:.]+/\d+)\s+(.+)$",
        condition,
        flags=re.IGNORECASE,
    )
    if not match:
        return None

    route = _safe_network(prefix)
    base = _safe_network(match.group(1))
    if route is None or base is None:
        return False
    if route.version != base.version:
        return False

    spec = match.group(2).strip().lower()
    if spec.startswith("exact"):
        return route == base
    if spec.startswith("orlonger"):
        return route.subnet_of(base)
    if spec.startswith("longer"):
        return route.subnet_of(base) and route.prefixlen > base.prefixlen

    upto = re.match(r"upto\s+/?(\d+)", spec)
    if upto:
        upper = int(upto.group(1))
        return route.subnet_of(base) and route.prefixlen <= upper

    length_range = re.match(r"prefix-length-range\s+/?(\d+)\s*-\s*/?(\d+)", spec)
    if length_range:
        low = int(length_range.group(1))
        high = int(length_range.group(2))
        return route.subnet_of(base) and low <= route.prefixlen <= high

    return False


def _term_matches_prefix(term: dict, prefix: str) -> bool:
    conditions = term.get("match_conditions") or []
    if not conditions:
        return True

    route_filter_conditions = [c for c in conditions if "route-filter" in str(c).lower()]
    if route_filter_conditions:
        return any(_route_filter_match(prefix, c) is True for c in route_filter_conditions)

    # Conditions exist but none are prefix-evaluable (community/as-path/protocol/etc.).
    return False


def _infer_responsible_term_for_prefix(prefix: str, import_terms: list[dict]) -> dict | None:
    for term in import_terms:
        if not _term_matches_prefix(term, prefix):
            continue
        flow_control = str(term.get("flow_control") or "").lower()
        if flow_control == "reject":
            return {
                "policy_name": term.get("policy_name"),
                "term_name": term.get("term_name"),
            }
        # Explicit accept terminates evaluation before reject terms.
        if flow_control == "accept":
            return None
    return None


def _parse_route_summary_counts(route_output: str) -> dict | None:
    match = _ROUTE_SUMMARY_RE.search(route_output or "")
    if not match:
        return None
    return {
        "destinations": int(match.group(1)),
        "routes": int(match.group(2)),
        "active": int(match.group(3)),
        "holddown": int(match.group(4)),
        "hidden": int(match.group(5)),
    }


def _parse_received_prefixes(route_output: str) -> list[dict]:
    prefixes = {}
    for raw_line in (route_output or "").splitlines():
        prefix_match = _PREFIX_RE.match(raw_line)
        if not prefix_match:
            continue
        prefix = prefix_match.group(1)
        marker_area = raw_line.split("[", 1)[0]
        is_active = "*" in marker_area
        if prefix not in prefixes:
            prefixes[prefix] = is_active
        else:
            prefixes[prefix] = prefixes[prefix] or is_active

    return [{"prefix": pfx, "active": active} for pfx, active in sorted(prefixes.items())]


def _extract_first(patterns: list[str], text: str) -> str | None:
    for pattern in patterns:
        match = re.search(pattern, text or "", flags=re.IGNORECASE | re.MULTILINE)
        if match:
            return match.group(1).strip()
    return None


def _extract_int(patterns: list[str], text: str) -> int | None:
    value = _extract_first(patterns, text)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_bgp_summary_peer_row(summary_output: str, peer_ip: str) -> dict:
    target = _normalize_ip(peer_ip)
    if not target:
        return {}

    known_states = {
        "active",
        "connect",
        "idle",
        "opensent",
        "openconfirm",
        "established",
        "recvnotify",
        "sentnotify",
    }

    for raw_line in (summary_output or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        tokens = line.split()
        if len(tokens) < 7:
            continue

        peer_token = _normalize_ip(tokens[0].split("+", 1)[0])
        if peer_token != target:
            continue

        state_column = " ".join(tokens[7:]).strip() if len(tokens) > 7 else ""
        state_token = state_column.split()[0].strip() if state_column else ""
        if re.fullmatch(r"\d+/\d+/\d+(?:/\d+)?", state_token):
            state = "Established"
        elif state_token.lower() in known_states:
            state = state_token
        else:
            state = None

        return {
            "peer_as": int(tokens[1]) if tokens[1].isdigit() else None,
            "uptime": tokens[6],
            "state": state,
            "state_column": state_column or None,
        }

    return {}


def _parse_bgp_neighbor_summary(neighbor_output: str, fallback_bfd: bool | None) -> dict:
    state = _extract_first(
        [
            r"\bState:\s*([A-Za-z]+)",
            r"\bBGP state\s*=\s*([A-Za-z]+)",
        ],
        neighbor_output,
    )

    received = _extract_int(
        [
            r"\bReceived prefixes:\s*(\d+)",
            r"\bPrefixes Received:\s*(\d+)",
        ],
        neighbor_output,
    )
    active = _extract_int(
        [
            r"\bAccepted prefixes:\s*(\d+)",
            r"\bActive prefixes:\s*(\d+)",
            r"\bPrefixes accepted:\s*(\d+)",
        ],
        neighbor_output,
    )
    sent = _extract_int(
        [
            r"\bAdvertised prefixes:\s*(\d+)",
            r"\bPrefixes advertised:\s*(\d+)",
        ],
        neighbor_output,
    )
    uptime = _extract_first(
        [
            r"\bEstablished for\s+(.+)",
            r"\bUp for\s+(.+)",
            r"\bLast State change:\s*(.+)",
        ],
        neighbor_output,
    )
    if uptime and re.search(r"notify|opensent|openconfirm|idle|active", uptime, flags=re.IGNORECASE):
        uptime = None

    peer_as = _extract_int(
        [
            r"\bPeer AS:\s*(\d+)",
            r"\bPeer:\s+[0-9A-Fa-f:.+]+\s+AS\s+(\d+)",
        ],
        neighbor_output,
    )

    bfd_state = _extract_first([r"\bBFD\s*:\s*([A-Za-z]+)"], neighbor_output)
    if bfd_state is None and fallback_bfd is not None:
        bfd_state = "Up" if fallback_bfd else "Not configured"

    return {
        "session_state": state or "Unknown",
        "peer_as": peer_as,
        "prefixes_received": received or 0,
        "prefixes_active": active or 0,
        "prefixes_sent": sent or 0,
        "uptime": uptime,
        "bfd_state": bfd_state,
    }


async def _build_context(sessions, registry, blueprint_id: str, instance_name: str | None) -> dict:
    systems_result = await handle_get_systems(sessions, registry, blueprint_id, instance_name)
    systems = _flatten_items(systems_result, "systems")

    peerings_result = await handle_get_external_peerings(
        sessions,
        registry,
        blueprint_id,
        device=None,
        instance_name=instance_name,
    )
    peerings = _flatten_items(peerings_result, "peerings")

    return {
        "systems": systems,
        "peerings": peerings,
        "peer_catalog": _build_peer_catalog(peerings),
    }


def _resolve_single_peer(context: dict, device: str | None, peer_ip: str | None) -> dict:
    systems = context.get("systems", [])
    peer_catalog = context.get("peer_catalog", [])
    resolved_system = _resolve_system_from_input(systems, device)
    filtered = _filter_peer_catalog(peer_catalog, device, resolved_system, peer_ip)

    if not filtered:
        return {
            "error": "peer_not_found",
            "hint": "Run routing_policy intent='discover' to list available device/peer combinations.",
            "available_peer_count": len(peer_catalog),
        }

    if len(filtered) > 1 and not peer_ip:
        return {
            "error": "peer_required",
            "hint": "Multiple peers matched. Provide peer_ip to scope this request.",
            "candidate_peers": [
                {"device": item["local_device"], "peer_ip": item["peer_ip"]}
                for item in filtered[:10]
            ],
            "candidate_count": len(filtered),
        }

    target = filtered[0]
    if not target.get("local_system_id"):
        return {
            "error": "system_resolution_failed",
            "hint": "Could not resolve a managed system_id for the selected device.",
            "target": target,
        }

    return {"target": target, "candidate_count": len(filtered)}


def _route_table_name(vrf: str | None) -> str | None:
    if not vrf:
        return None
    text = str(vrf).strip()
    if not text:
        return None
    if text in {"inet.0", "master", "global"}:
        return None
    if text.endswith(".inet.0"):
        return text
    return f"{text}.inet.0"


def _receive_protocol_command(peer_ip: str, vrf: str | None, hidden: bool, detail: bool) -> str:
    table = _route_table_name(vrf)
    parts = ["show", "route"]
    if table:
        parts.extend(["table", table])
    parts.extend(["receive-protocol", "bgp", peer_ip])
    if hidden:
        parts.append("hidden")
    if detail:
        parts.append("detail")
    return " ".join(parts)


async def _intent_discover(
    sessions,
    registry,
    blueprint_id: str,
    limit: int,
    cursor: str | None,
    instance_name: str | None,
) -> dict:
    context = await _build_context(sessions, registry, blueprint_id, instance_name)
    systems = context["systems"]
    peer_catalog = context["peer_catalog"]

    devices = [
        {
            "label": s.get("label"),
            "hostname": s.get("hostname"),
            "role": s.get("role"),
            "system_id": s.get("system_id"),
        }
        for s in sorted(
            systems,
            key=lambda item: (
                str(item.get("role") or ""),
                str(item.get("label") or ""),
            ),
        )
    ]

    paged_peers = _paginate_list(peer_catalog, limit, cursor)
    if "error" in paged_peers:
        return paged_peers

    return {
        "intent": "discover",
        "blueprint_id": blueprint_id,
        "summary": {
            "device_count": len(devices),
            "external_peer_count": len(peer_catalog),
            "vrf_hints": ["inet.0"],
        },
        "available": {
            "devices": devices[: min(25, len(devices))],
            "external_peers": paged_peers["items"],
        },
        "pagination": {
            "scope": "external_peers",
            "limit": paged_peers["limit"],
            "has_more": paged_peers["has_more"],
            "next_cursor": paged_peers["next_cursor"],
            "returned_count": paged_peers["returned_count"],
            "total_count": paged_peers["total_count"],
        },
        "supported_intents": [
            "discover",
            "peer_summary",
            "explain_policy",
            "diagnose_hidden_routes",
            "compare_rib",
            "resolve_next_hop",
            "full_audit",
        ],
        "required_fields_by_intent": {
            "peer_summary": "Optional device and peer_ip. Use no peer_ip for paginated peer overview.",
            "explain_policy": "Provide peer_ip (and device when peer_ip is ambiguous).",
            "diagnose_hidden_routes": "Provide peer_ip (and device when peer_ip is ambiguous).",
            "compare_rib": "Provide peer_ip (and device when peer_ip is ambiguous).",
            "resolve_next_hop": "Provide device and next_hop_ip. peer_ip optional for context.",
            "full_audit": "Provide peer_ip (and device when peer_ip is ambiguous).",
        },
        "next_action_prompt": (
            "Choose one intent and pass only the required fields. "
            "Start with peer_summary for quick health, then explain_policy or diagnose_hidden_routes as needed."
        ),
    }


async def _intent_peer_summary(
    sessions,
    registry,
    blueprint_id: str,
    device: str | None,
    peer_ip: str | None,
    limit: int,
    cursor: str | None,
    instance_name: str | None,
) -> dict:
    context = await _build_context(sessions, registry, blueprint_id, instance_name)
    systems = context["systems"]
    resolved_system = _resolve_system_from_input(systems, device)
    filtered = _filter_peer_catalog(context["peer_catalog"], device, resolved_system, peer_ip)

    paged = _paginate_list(filtered, limit, cursor)
    if "error" in paged:
        return paged

    summaries = []
    for peer in paged["items"]:
        if not peer.get("local_system_id"):
            continue

        summary_command = "show bgp summary"
        neighbor_command = f"show bgp neighbor {peer['peer_ip']}"
        command_result = await _run_commands_for_system(
            sessions,
            registry,
            blueprint_id,
            peer["local_system_id"],
            [summary_command, neighbor_command],
            instance_name,
        )

        summary_output = command_result.get("outputs", {}).get(summary_command, "")
        neighbor_output = command_result.get("outputs", {}).get(neighbor_command, "")

        parsed = _parse_bgp_neighbor_summary(neighbor_output, peer.get("bfd"))
        summary_row = _parse_bgp_summary_peer_row(summary_output, peer["peer_ip"])
        if summary_row.get("uptime"):
            parsed["uptime"] = summary_row["uptime"]
        if parsed.get("session_state") == "Unknown" and summary_row.get("state"):
            parsed["session_state"] = summary_row["state"]

        received = parsed.get("prefixes_received") or 0
        active = parsed.get("prefixes_active") or 0
        filtering_suspected = received > 0 and active < max(1, int(received * 0.5))

        summaries.append(
            {
                "peer_ip": peer["peer_ip"],
                "peer_as": peer.get("peer_as") or parsed.get("peer_as") or summary_row.get("peer_as"),
                "device": peer.get("local_device"),
                "session_state": parsed.get("session_state", "Unknown"),
                "uptime": parsed.get("uptime"),
                "prefixes_received": received,
                "prefixes_active": active,
                "prefixes_sent": parsed.get("prefixes_sent") or 0,
                "filtering_suspected": filtering_suspected,
                "address_families": peer.get("address_families") or [],
                "bfd_state": parsed.get("bfd_state"),
                "policy_level": _infer_policy_level(neighbor_output),
                "command_failures": command_result.get("failures", []),
            }
        )

    return {
        "intent": "peer_summary",
        "blueprint_id": blueprint_id,
        "peer_count": paged["total_count"],
        "returned_count": len(summaries),
        "peers": summaries,
        "pagination": {
            "limit": paged["limit"],
            "has_more": paged["has_more"],
            "next_cursor": paged["next_cursor"],
        },
    }


async def _collect_policy_config_for_peer(
    sessions,
    registry,
    blueprint_id: str,
    target_peer: dict,
    direction: str,
    instance_name: str | None,
) -> dict:
    peer_ip = target_peer["peer_ip"]
    system_id = target_peer["local_system_id"]

    neighbor_command = f"show bgp neighbor {peer_ip}"
    first = await _run_commands_for_system(
        sessions,
        registry,
        blueprint_id,
        system_id,
        [neighbor_command],
        instance_name,
    )

    neighbor_output = first.get("outputs", {}).get(neighbor_command, "")
    policy_names = _parse_policy_names(neighbor_output)
    policy_level = _infer_policy_level(neighbor_output)

    include_import = direction in {"import", "both"}
    include_export = direction in {"export", "both"}

    selected_import = _dedupe_keep_order(policy_names["import"] if include_import else [])
    selected_export = _dedupe_keep_order(policy_names["export"] if include_export else [])
    failures = list(first.get("failures", []))

    command_list = []
    policy_to_command = {}
    for name in (selected_import + selected_export):
        command = _build_show_policy_command(name)
        if not command:
            failures.append(
                {
                    "command": f"show policy {name}",
                    "result": "skipped",
                    "error": "invalid policy token",
                    "hint": "Filtered a non-policy label token while building policy commands.",
                }
            )
            continue
        if command in command_list:
            policy_to_command[name] = command
            continue
        policy_to_command[name] = command
        command_list.append(command)

    policy_outputs = {}
    if command_list:
        second = await _run_commands_for_system(
            sessions,
            registry,
            blueprint_id,
            system_id,
            command_list,
            instance_name,
        )
        policy_outputs = second.get("outputs", {})
        failures.extend(second.get("failures", []))

    import_terms = []
    for name in selected_import:
        command = policy_to_command.get(name)
        output = policy_outputs.get(command, "") if command else ""
        if not str(output).strip():
            continue
        import_terms.extend(_parse_policy_terms(name, output))

    export_terms = []
    for name in selected_export:
        command = policy_to_command.get(name)
        output = policy_outputs.get(command, "") if command else ""
        if not str(output).strip():
            continue
        export_terms.extend(_parse_policy_terms(name, output))

    return {
        "peer_ip": peer_ip,
        "import_policy_names": selected_import,
        "export_policy_names": selected_export,
        "policy_level": policy_level,
        "import_terms": import_terms,
        "export_terms": export_terms,
        "command_failures": failures,
    }


async def _intent_explain_policy(
    sessions,
    registry,
    blueprint_id: str,
    device: str | None,
    peer_ip: str | None,
    direction: str,
    instance_name: str | None,
) -> dict:
    context = await _build_context(sessions, registry, blueprint_id, instance_name)
    resolved = _resolve_single_peer(context, device, peer_ip)
    if "error" in resolved:
        return resolved

    target = resolved["target"]
    policy_config = await _collect_policy_config_for_peer(
        sessions,
        registry,
        blueprint_id,
        target,
        direction,
        instance_name,
    )
    explanation = _summarize_policy_explanation(
        policy_config,
        {
            "peer_ip": target["peer_ip"],
            "device": target["local_device"],
            "peer_as": target.get("peer_as"),
            "local_as": target.get("local_as"),
        },
    )

    return {
        "intent": "explain_policy",
        "blueprint_id": blueprint_id,
        "device": target["local_device"],
        "peer_ip": target["peer_ip"],
        "policy_config": policy_config,
        "explanation": explanation,
    }


async def _intent_diagnose_hidden_routes(
    sessions,
    registry,
    blueprint_id: str,
    device: str | None,
    peer_ip: str | None,
    vrf: str | None,
    limit: int,
    cursor: str | None,
    instance_name: str | None,
) -> dict:
    context = await _build_context(sessions, registry, blueprint_id, instance_name)
    resolved = _resolve_single_peer(context, device, peer_ip)
    if "error" in resolved:
        return resolved

    target = resolved["target"]
    command = _receive_protocol_command(target["peer_ip"], vrf, hidden=True, detail=True)
    result = await _run_commands_for_system(
        sessions,
        registry,
        blueprint_id,
        target["local_system_id"],
        [command],
        instance_name,
        timeout_seconds=60,
    )

    hidden_output = result.get("outputs", {}).get(command, "")
    hidden_routes = _parse_hidden_routes_text(hidden_output)

    policy_config = await _collect_policy_config_for_peer(
        sessions,
        registry,
        blueprint_id,
        target,
        direction="import",
        instance_name=instance_name,
    )
    import_terms = policy_config.get("import_terms") or []
    for route in hidden_routes:
        if route.get("responsible_term"):
            continue
        if route.get("reason_category") not in {"policy_rejected", "other"}:
            continue
        inferred = _infer_responsible_term_for_prefix(route.get("prefix"), import_terms)
        if inferred:
            route["responsible_term"] = inferred.get("term_name")
            if not route.get("inactive_reason"):
                route["inactive_reason"] = (
                    f"Policy rejected by {inferred.get('policy_name')}/{inferred.get('term_name')}"
                )
            route["reason_category"] = "policy_rejected"

    by_reason = defaultdict(list)
    for route in hidden_routes:
        by_reason[route["reason_category"]].append(route)

    all_categories = [
        "policy_rejected",
        "next_hop_unusable",
        "as_path_loop",
        "martian",
        "route_target_mismatch",
        "prefix_limit",
        "other",
    ]
    by_reason_complete = {category: by_reason.get(category, []) for category in all_categories}

    paged = _paginate_list(hidden_routes, limit, cursor)
    if "error" in paged:
        return paged

    policy_count = len(by_reason_complete["policy_rejected"])
    non_policy_count = len(hidden_routes) - policy_count

    return {
        "intent": "diagnose_hidden_routes",
        "blueprint_id": blueprint_id,
        "device": target["local_device"],
        "peer_ip": target["peer_ip"],
        "vrf": vrf or "inet.0",
        "total_hidden": len(hidden_routes),
        "by_reason_counts": {key: len(value) for key, value in by_reason_complete.items()},
        "intentionally_hidden_likely": policy_count > 0 and policy_count >= (non_policy_count * 2),
        "routes": paged["items"],
        "pagination": {
            "limit": paged["limit"],
            "has_more": paged["has_more"],
            "next_cursor": paged["next_cursor"],
        },
        "command_failures": result.get("failures", []) + policy_config.get("command_failures", []),
    }


async def _intent_compare_rib(
    sessions,
    registry,
    blueprint_id: str,
    device: str | None,
    peer_ip: str | None,
    vrf: str | None,
    limit: int,
    instance_name: str | None,
) -> dict:
    context = await _build_context(sessions, registry, blueprint_id, instance_name)
    resolved = _resolve_single_peer(context, device, peer_ip)
    if "error" in resolved:
        return resolved

    target = resolved["target"]
    receive_cmd = _receive_protocol_command(target["peer_ip"], vrf, hidden=False, detail=False)
    hidden_cmd = _receive_protocol_command(target["peer_ip"], vrf, hidden=True, detail=False)

    result = await _run_commands_for_system(
        sessions,
        registry,
        blueprint_id,
        target["local_system_id"],
        [receive_cmd, hidden_cmd],
        instance_name,
        timeout_seconds=60,
    )

    receive_output = result.get("outputs", {}).get(receive_cmd, "")
    hidden_output = result.get("outputs", {}).get(hidden_cmd, "")

    counts = _parse_route_summary_counts(receive_output) or {}
    received_rows = _parse_received_prefixes(receive_output)
    hidden_rows = _parse_received_prefixes(hidden_output)
    hidden_prefixes = {row["prefix"] for row in hidden_rows}

    received_prefixes = [row["prefix"] for row in received_rows]
    active_prefixes = [
        row["prefix"]
        for row in received_rows
        if row["active"] and row["prefix"] not in hidden_prefixes
    ]
    inactive_prefixes = [
        row["prefix"]
        for row in received_rows
        if (not row["active"]) and row["prefix"] not in hidden_prefixes
    ]

    received_count = counts.get("routes", len(received_prefixes))
    active_count = counts.get("active", len(active_prefixes))
    hidden_count = counts.get("hidden", len(hidden_prefixes))
    inactive_count = max(received_count - active_count - hidden_count, 0)
    filter_ratio = round(hidden_count / received_count, 3) if received_count else 0.0

    safe_limit = _normalize_limit(limit)
    return {
        "intent": "compare_rib",
        "blueprint_id": blueprint_id,
        "device": target["local_device"],
        "peer_ip": target["peer_ip"],
        "vrf": vrf or "inet.0",
        "received_count": received_count,
        "active_count": active_count,
        "inactive_count": inactive_count,
        "hidden_count": hidden_count,
        "filter_ratio": filter_ratio,
        "mismatch_flag": received_count > max(active_count + hidden_count, 0),
        "prefix_samples": {
            "limit": safe_limit,
            "active_prefixes": active_prefixes[:safe_limit],
            "inactive_prefixes": inactive_prefixes[:safe_limit],
            "hidden_prefixes": sorted(hidden_prefixes)[:safe_limit],
            "has_more": {
                "active_prefixes": len(active_prefixes) > safe_limit,
                "inactive_prefixes": len(inactive_prefixes) > safe_limit,
                "hidden_prefixes": len(hidden_prefixes) > safe_limit,
            },
        },
        "command_failures": result.get("failures", []),
    }


def _parse_next_hop_resolution(route_output: str) -> dict:
    lines = route_output.splitlines() if route_output else []
    has_not_found = any("not in table" in line.lower() for line in lines)
    prefix = None
    for line in lines:
        match = _PREFIX_RE.match(line)
        if match:
            prefix = match.group(1)
            break

    protocol = _extract_first([r"\[([A-Za-z0-9_-]+)/\d+\]"], route_output)
    interface = _extract_first([r"\bvia\s+([A-Za-z0-9./:-]+)"], route_output)

    return {
        "reachable": (not has_not_found) and (prefix is not None),
        "resolving_route": prefix,
        "resolution_protocol": protocol,
        "resolving_interface": interface,
    }


async def _intent_resolve_next_hop(
    sessions,
    registry,
    blueprint_id: str,
    device: str | None,
    peer_ip: str | None,
    next_hop_ip: str | None,
    vrf: str | None,
    instance_name: str | None,
) -> dict:
    if not next_hop_ip:
        return {
            "error": "next_hop_ip_required",
            "hint": "Provide next_hop_ip for resolve_next_hop intent.",
        }

    context = await _build_context(sessions, registry, blueprint_id, instance_name)
    systems = context.get("systems", [])

    target_system = _resolve_system_from_input(systems, device)
    if target_system is None and peer_ip:
        resolved_peer = _resolve_single_peer(context, device, peer_ip)
        if "target" in resolved_peer:
            target = resolved_peer["target"]
            target_system = _resolve_system_from_input(systems, target.get("local_system_id"))

    if target_system is None:
        return {
            "error": "device_required",
            "hint": "Provide device for resolve_next_hop, or provide peer_ip that uniquely identifies a device.",
        }

    table = _route_table_name(vrf)
    route_cmd = (
        f"show route table {table} {next_hop_ip} detail"
        if table
        else f"show route {next_hop_ip} detail"
    )
    fib_cmd = f"show route forwarding-table destination {next_hop_ip}"
    command_list = [route_cmd, fib_cmd]

    if table:
        command_list.append(f"show route {next_hop_ip} detail")

    result = await _run_commands_for_system(
        sessions,
        registry,
        blueprint_id,
        target_system["system_id"],
        command_list,
        instance_name,
    )

    route_output = result.get("outputs", {}).get(route_cmd, "")
    fib_output = result.get("outputs", {}).get(fib_cmd, "")
    global_output = ""
    if table:
        global_output = result.get("outputs", {}).get(f"show route {next_hop_ip} detail", "")

    parsed = _parse_next_hop_resolution(route_output)
    global_parsed = _parse_next_hop_resolution(global_output) if table else None

    note = None
    if table and (not parsed["reachable"]) and global_parsed and global_parsed["reachable"]:
        note = (
            f"Next-hop resolves in inet.0 but not in {table}. "
            "VRF resolution mismatch is likely."
        )

    return {
        "intent": "resolve_next_hop",
        "blueprint_id": blueprint_id,
        "device": target_system.get("label") or target_system.get("hostname"),
        "next_hop_ip": next_hop_ip,
        "reachable": parsed["reachable"],
        "resolving_route": parsed["resolving_route"],
        "resolving_interface": parsed["resolving_interface"],
        "resolution_table": table or "inet.0",
        "resolution_protocol": parsed["resolution_protocol"],
        "note": note,
        "forwarding_table_excerpt": "\n".join(fib_output.splitlines()[:25]),
        "command_failures": result.get("failures", []),
    }


async def _intent_full_audit(
    sessions,
    registry,
    blueprint_id: str,
    device: str | None,
    peer_ip: str | None,
    direction: str,
    vrf: str | None,
    limit: int,
    next_hop_ip: str | None,
    instance_name: str | None,
) -> dict:
    context = await _build_context(sessions, registry, blueprint_id, instance_name)
    resolved = _resolve_single_peer(context, device, peer_ip)
    if "error" in resolved:
        return resolved

    target = resolved["target"]

    peer_summary = await _intent_peer_summary(
        sessions,
        registry,
        blueprint_id,
        target["local_device"],
        target["peer_ip"],
        limit=1,
        cursor=None,
        instance_name=instance_name,
    )
    policy = await _intent_explain_policy(
        sessions,
        registry,
        blueprint_id,
        target["local_device"],
        target["peer_ip"],
        direction,
        instance_name,
    )
    hidden = await _intent_diagnose_hidden_routes(
        sessions,
        registry,
        blueprint_id,
        target["local_device"],
        target["peer_ip"],
        vrf,
        limit,
        cursor=None,
        instance_name=instance_name,
    )
    rib = await _intent_compare_rib(
        sessions,
        registry,
        blueprint_id,
        target["local_device"],
        target["peer_ip"],
        vrf,
        limit,
        instance_name,
    )

    next_hop = None
    if next_hop_ip:
        next_hop = await _intent_resolve_next_hop(
            sessions,
            registry,
            blueprint_id,
            target["local_device"],
            target["peer_ip"],
            next_hop_ip,
            vrf,
            instance_name,
        )

    return {
        "intent": "full_audit",
        "blueprint_id": blueprint_id,
        "device": target["local_device"],
        "peer_ip": target["peer_ip"],
        "expensive": True,
        "note": "full_audit runs multiple device command sets; prefer targeted intents for routine checks.",
        "peer_summary": peer_summary,
        "policy": policy,
        "hidden_routes": hidden,
        "rib_comparison": rib,
        "next_hop": next_hop,
    }


async def handle_routing_policy_intent(
    sessions,
    registry,
    blueprint_id: str,
    intent: str = "discover",
    device: str | None = None,
    peer_ip: str | None = None,
    direction: str = "both",
    vrf: str | None = None,
    prefix: str | None = None,
    next_hop_ip: str | None = None,
    limit: int = _DEFAULT_LIMIT,
    cursor: str | None = None,
    instance_name: str | None = None,
) -> dict:
    normalized_intent = str(intent or "discover").strip().lower()
    if normalized_intent not in _VALID_INTENTS:
        return {
            "error": "unsupported_intent",
            "intent": intent,
            "supported_intents": sorted(_VALID_INTENTS),
        }

    safe_limit = _normalize_limit(limit)
    normalized_direction = str(direction or "both").strip().lower()
    if normalized_direction not in {"import", "export", "both"}:
        normalized_direction = "both"

    if normalized_intent == "discover":
        return await _intent_discover(
            sessions,
            registry,
            blueprint_id,
            limit=safe_limit,
            cursor=cursor,
            instance_name=instance_name,
        )

    if normalized_intent == "peer_summary":
        return await _intent_peer_summary(
            sessions,
            registry,
            blueprint_id,
            device=device,
            peer_ip=peer_ip,
            limit=safe_limit,
            cursor=cursor,
            instance_name=instance_name,
        )

    if normalized_intent == "explain_policy":
        return await _intent_explain_policy(
            sessions,
            registry,
            blueprint_id,
            device=device,
            peer_ip=peer_ip,
            direction=normalized_direction,
            instance_name=instance_name,
        )

    if normalized_intent == "diagnose_hidden_routes":
        return await _intent_diagnose_hidden_routes(
            sessions,
            registry,
            blueprint_id,
            device=device,
            peer_ip=peer_ip,
            vrf=vrf,
            limit=safe_limit,
            cursor=cursor,
            instance_name=instance_name,
        )

    if normalized_intent == "compare_rib":
        return await _intent_compare_rib(
            sessions,
            registry,
            blueprint_id,
            device=device,
            peer_ip=peer_ip,
            vrf=vrf,
            limit=safe_limit,
            instance_name=instance_name,
        )

    if normalized_intent == "resolve_next_hop":
        return await _intent_resolve_next_hop(
            sessions,
            registry,
            blueprint_id,
            device=device,
            peer_ip=peer_ip,
            next_hop_ip=next_hop_ip,
            vrf=vrf,
            instance_name=instance_name,
        )

    # full_audit
    return await _intent_full_audit(
        sessions,
        registry,
        blueprint_id,
        device=device,
        peer_ip=peer_ip,
        direction=normalized_direction,
        vrf=vrf,
        limit=safe_limit,
        next_hop_ip=next_hop_ip,
        instance_name=instance_name,
    )
