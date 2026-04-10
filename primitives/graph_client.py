"""
graph_client.py

Manages per-blueprint Kuzu graph databases built from Apstra's graph API.

Each blueprint gets its own in-memory Kuzu database, built from the nodes and
relationships returned by GET /api/blueprints/{blueprint_id}. The registry
tracks the blueprint version at build time and rebuilds automatically when
the version increments.

Usage from a handler:

    graph = await registry.get_or_rebuild(session, blueprint_id)
    rows = graph.query("MATCH (n) RETURN n.id, n.type LIMIT 10")

The registry is created once at server startup and held in the lifespan
context under the key "graph_registry". Handlers receive it the same way
they receive sessions:

    registry = ctx.lifespan_context["graph_registry"]
"""

from __future__ import annotations

import asyncio
import json
import shutil
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import kuzu

from primitives.live_data_client import get_blueprint_graph, get_blueprint_versions


# ---------------------------------------------------------------------------
# Helpers (ported from ApstraKuzuGraph source implementation)
# ---------------------------------------------------------------------------

def _quote_identifier(identifier: str) -> str:
    escaped = identifier.replace("`", "``")
    return f"`{escaped}`"


def _is_scalar(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def _infer_kuzu_type(values: list[Any]) -> str:
    non_null = [v for v in values if v is not None]
    if not non_null:
        # All values are null for this property in the current dataset (common when
        # an Apstra field exists in the API schema but has no data in this environment
        # or this Apstra version). Default to STRING so the property is still added
        # to the Kuzu schema — Cypher queries that reference it will return null
        # rather than failing with "Cannot find property".
        return "STRING"
    if all(isinstance(v, bool) for v in non_null):
        return "BOOLEAN"
    if all(isinstance(v, int) and not isinstance(v, bool) for v in non_null):
        return "INT64"
    if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in non_null):
        return "DOUBLE"
    return "STRING"


def _coerce_for_kuzu(value: Any, kuzu_type: str) -> Any:
    if value is None:
        return None
    try:
        if kuzu_type == "STRING":
            return str(value)
        if kuzu_type == "INT64":
            return int(value) if not isinstance(value, bool) else int(value)
        if kuzu_type == "DOUBLE":
            return float(value)
        if kuzu_type == "BOOLEAN":
            if isinstance(value, bool):
                return value
            if isinstance(value, (int, float)):
                return bool(value)
            if isinstance(value, str):
                lowered = value.strip().lower()
                if lowered in {"true", "1", "yes"}:
                    return True
                if lowered in {"false", "0", "no"}:
                    return False
            return None
    except (TypeError, ValueError):
        return None
    return value


def _to_payload_json(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str)


def _remove_path(path: Path) -> None:
    if not path.exists():
        return
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()


# ---------------------------------------------------------------------------
# ImportStats
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ImportStats:
    nodes_total: int
    relationships_total: int
    node_type_count: int
    relationship_type_count: int
    skipped_relationships: int = 0

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


# ---------------------------------------------------------------------------
# ApstraKuzuGraph
# ---------------------------------------------------------------------------

class ApstraKuzuGraph:
    """
    A Kuzu graph database containing the nodes and relationships for one
    Apstra blueprint. Typically held in memory (db_path=":memory:").

    Do not instantiate directly — use BlueprintGraphRegistry.get_or_rebuild().
    """

    def __init__(self, db_path: str | Path = ":memory:"):
        self.db_path = str(db_path)
        self._rel_table_name_map: dict[str, str] = {}
        self._db: kuzu.Database | None = None
        self._conn: kuzu.Connection | None = None
        self._open()

    def _open(self) -> None:
        self._db = kuzu.Database(database_path=self.db_path)
        self._conn = kuzu.Connection(self._db)

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
        if self._db is not None:
            try:
                self._db.close()
            except Exception:
                pass
        self._conn = None
        self._db = None

    def __enter__(self) -> "ApstraKuzuGraph":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    @property
    def connection(self) -> kuzu.Connection:
        if self._conn is None:
            raise RuntimeError("Kuzu connection is not available.")
        return self._conn

    def execute(self, cypher: str, params: dict[str, Any] | None = None) -> kuzu.QueryResult:
        return self.connection.execute(cypher, params or {})

    def query(self, cypher: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """
        Executes a Cypher query and returns results as a list of dicts.
        Each dict maps column name to value.
        """
        result = self.execute(cypher, params=params)
        try:
            columns = list(result.get_column_names())
        except RuntimeError:
            columns = []

        rows: list[dict[str, Any]] = []
        while result.has_next():
            values = result.get_next()
            row: dict[str, Any] = {}
            for i, value in enumerate(values):
                col = columns[i] if i < len(columns) else f"col_{i}"
                row[col] = value
            rows.append(row)
        return rows

    def rebuild_from_dicts(
        self,
        nodes_data: dict[str, Any],
        relationships_data: dict[str, Any],
    ) -> ImportStats:
        """
        Builds the graph from pre-fetched API dicts (not from files).
        Always starts from a fresh in-memory state.
        """
        self.close()
        self._rel_table_name_map = {}
        self._open()
        return self._import_graph(nodes_data, relationships_data)

    def relationship_table_name(self, relationship_type: str) -> str:
        return self._rel_table_name_map.get(relationship_type, relationship_type)

    # ------------------------------------------------------------------
    # Internal graph construction (ported verbatim from core.py)
    # ------------------------------------------------------------------

    def _import_graph(
        self,
        nodes_data: dict[str, Any],
        relationships_data: dict[str, Any],
    ) -> ImportStats:
        nodes_by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
        node_id_to_type: dict[str, str] = {}

        for node_id, node_props in nodes_data.items():
            node_record = dict(node_props)
            node_record.setdefault("id", node_id)
            node_record["id"] = str(node_record["id"])
            node_type = str(node_record.get("type") or "unknown")
            nodes_by_type[node_type].append(node_record)
            node_id_to_type[node_record["id"]] = node_type

        node_property_types: dict[str, dict[str, str]] = {}
        for node_type, records in nodes_by_type.items():
            value_buckets: dict[str, list[Any]] = defaultdict(list)
            for record in records:
                for key, value in record.items():
                    if key.lower() in {"id", "payload"}:
                        continue
                    if not _is_scalar(value):
                        continue
                    value_buckets[key].append(value)
            inferred = {}
            for key in sorted(value_buckets.keys()):
                inferred[key] = _infer_kuzu_type(value_buckets[key])
            node_property_types[node_type] = inferred

        self._create_node_tables(node_property_types)
        self._insert_nodes(nodes_by_type, node_property_types)

        rels_by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
        rel_endpoints: dict[str, set[tuple[str, str]]] = defaultdict(set)
        skipped_relationships = 0

        for rel_id, rel_props in relationships_data.items():
            rel_record = dict(rel_props)
            rel_record.setdefault("id", rel_id)
            rel_record["id"] = str(rel_record["id"])
            rel_type = str(rel_record.get("type") or "unknown")

            source_id = None if rel_record.get("source_id") is None else str(rel_record["source_id"])
            target_id = None if rel_record.get("target_id") is None else str(rel_record["target_id"])

            src_type = node_id_to_type.get(source_id or "")
            dst_type = node_id_to_type.get(target_id or "")
            if src_type is None or dst_type is None:
                skipped_relationships += 1
                continue

            rel_record["source_id"] = source_id
            rel_record["target_id"] = target_id
            rel_record["_src_type"] = src_type
            rel_record["_dst_type"] = dst_type
            rels_by_type[rel_type].append(rel_record)
            rel_endpoints[rel_type].add((src_type, dst_type))

        rel_property_types: dict[str, dict[str, str]] = {}
        for rel_type, records in rels_by_type.items():
            value_buckets = defaultdict(list)
            for record in records:
                for key, value in record.items():
                    if key in {"id", "source_id", "target_id", "type", "_src_type", "_dst_type", "payload"}:
                        continue
                    if not _is_scalar(value):
                        continue
                    value_buckets[key].append(value)
            inferred = {}
            for key in sorted(value_buckets.keys()):
                inferred[key] = _infer_kuzu_type(value_buckets[key])
            rel_property_types[rel_type] = inferred

        self._rel_table_name_map = self._build_rel_table_name_map(
            rel_types=set(rel_endpoints.keys()),
            node_types=set(nodes_by_type.keys()),
        )

        self._create_rel_tables(rel_endpoints, rel_property_types, self._rel_table_name_map)
        self._insert_relationships(rels_by_type, rel_property_types, self._rel_table_name_map)

        return ImportStats(
            nodes_total=len(nodes_data),
            relationships_total=len(relationships_data),
            node_type_count=len(nodes_by_type),
            relationship_type_count=len(rels_by_type),
            skipped_relationships=skipped_relationships,
        )

    @staticmethod
    def _build_rel_table_name_map(
        rel_types: set[str],
        node_types: set[str],
    ) -> dict[str, str]:
        used_names = {t.casefold() for t in node_types}
        mapping: dict[str, str] = {}
        for rel_type in sorted(rel_types):
            candidate = rel_type
            if candidate.casefold() in used_names:
                base = f"{rel_type}__rel"
                candidate = base
                suffix = 1
                while candidate.casefold() in used_names:
                    candidate = f"{base}_{suffix}"
                    suffix += 1
            mapping[rel_type] = candidate
            used_names.add(candidate.casefold())
        return mapping

    def _create_node_tables(self, node_property_types: dict[str, dict[str, str]]) -> None:
        for node_type in sorted(node_property_types.keys()):
            columns = ["id STRING", "payload STRING"]
            for key, kuzu_type in node_property_types[node_type].items():
                if key.lower() in {"id", "payload"}:
                    continue
                columns.append(f"{_quote_identifier(key)} {kuzu_type}")
            ddl = (
                f"CREATE NODE TABLE {_quote_identifier(node_type)} "
                f"({', '.join(columns)}, PRIMARY KEY(id));"
            )
            self.execute(ddl)

    def _insert_nodes(
        self,
        nodes_by_type: dict[str, list[dict[str, Any]]],
        node_property_types: dict[str, dict[str, str]],
    ) -> None:
        for node_type in sorted(nodes_by_type.keys()):
            typed_columns = [
                col
                for col in sorted(node_property_types[node_type].keys())
                if col.lower() not in {"id", "payload"}
            ]
            assignments = ["id: $p0", "payload: $p1"]
            for i, col in enumerate(typed_columns, start=2):
                assignments.append(f"{_quote_identifier(col)}: $p{i}")
            query = (
                f"CREATE (n:{_quote_identifier(node_type)} "
                f"{{{', '.join(assignments)}}});"
            )
            for record in nodes_by_type[node_type]:
                params: dict[str, Any] = {
                    "p0": str(record["id"]),
                    "p1": _to_payload_json(record),
                }
                for i, col in enumerate(typed_columns, start=2):
                    kuzu_type = node_property_types[node_type][col]
                    params[f"p{i}"] = _coerce_for_kuzu(record.get(col), kuzu_type)
                self.execute(query, params)

    def _create_rel_tables(
        self,
        rel_endpoints: dict[str, set[tuple[str, str]]],
        rel_property_types: dict[str, dict[str, str]],
        rel_table_names: dict[str, str],
    ) -> None:
        for rel_type in sorted(rel_endpoints.keys()):
            table_name = rel_table_names[rel_type]
            endpoint_clauses = [
                f"FROM {_quote_identifier(src)} TO {_quote_identifier(dst)}"
                for src, dst in sorted(rel_endpoints[rel_type])
            ]
            property_clauses = [
                "id STRING",
                "payload STRING",
                "source_id STRING",
                "target_id STRING",
            ]
            for key, kuzu_type in rel_property_types.get(rel_type, {}).items():
                if key in {"id", "payload", "source_id", "target_id", "type"}:
                    continue
                property_clauses.append(f"{_quote_identifier(key)} {kuzu_type}")
            ddl = (
                f"CREATE REL TABLE {_quote_identifier(table_name)} "
                f"({', '.join(endpoint_clauses + property_clauses)});"
            )
            self.execute(ddl)

    def _insert_relationships(
        self,
        rels_by_type: dict[str, list[dict[str, Any]]],
        rel_property_types: dict[str, dict[str, str]],
        rel_table_names: dict[str, str],
    ) -> None:
        query_cache: dict[tuple[str, str, str], str] = {}
        for rel_type in sorted(rels_by_type.keys()):
            typed_columns = [
                col
                for col in sorted(rel_property_types.get(rel_type, {}).keys())
                if col not in {"id", "payload", "source_id", "target_id", "type"}
            ]
            for record in sorted(rels_by_type[rel_type], key=lambda r: str(r.get("id", ""))):
                src_type = str(record["_src_type"])
                dst_type = str(record["_dst_type"])
                rel_table_name = rel_table_names[rel_type]
                cache_key = (rel_table_name, src_type, dst_type)
                if cache_key not in query_cache:
                    assignments = ["id: $p0", "payload: $p1", "source_id: $p2", "target_id: $p3"]
                    for i, col in enumerate(typed_columns, start=4):
                        assignments.append(f"{_quote_identifier(col)}: $p{i}")
                    query_cache[cache_key] = (
                        f"MATCH (src:{_quote_identifier(src_type)} {{id: $src_id}}), "
                        f"(dst:{_quote_identifier(dst_type)} {{id: $dst_id}}) "
                        f"CREATE (src)-[r:{_quote_identifier(rel_table_name)} "
                        f"{{{', '.join(assignments)}}}]->(dst);"
                    )
                payload_record = {k: v for k, v in record.items() if not k.startswith("_")}
                params: dict[str, Any] = {
                    "src_id": record["source_id"],
                    "dst_id": record["target_id"],
                    "p0": str(record["id"]),
                    "p1": _to_payload_json(payload_record),
                    "p2": record["source_id"],
                    "p3": record["target_id"],
                }
                for i, col in enumerate(typed_columns, start=4):
                    kuzu_type = rel_property_types[rel_type][col]
                    params[f"p{i}"] = _coerce_for_kuzu(record.get(col), kuzu_type)
                self.execute(query_cache[cache_key], params)


# ---------------------------------------------------------------------------
# BlueprintGraphRegistry
# ---------------------------------------------------------------------------

class BlueprintGraphRegistry:
    """
    Manages one ApstraKuzuGraph per (instance, blueprint) pair.

    Version checking uses a two-call strategy:
      1. GET /api/blueprints — cheap, returns current version for ALL
         blueprints on the instance in one call. Used to detect staleness.
      2. GET /api/blueprints/{id} — expensive, returns full nodes and
         relationships. Only called when a rebuild is actually needed.

    On a cache hit (version unchanged) only call 1 is made.
    On a cache miss (version changed or first access) both calls are made.

    An asyncio lock per blueprint prevents simultaneous rebuilds from
    concurrent tool calls.

    Held in the MCP lifespan context under the key "graph_registry".
    """

    def __init__(self) -> None:
        # key: (instance_name, blueprint_id) → (graph, version_at_build)
        self._graphs: dict[tuple[str, str], tuple[ApstraKuzuGraph, int]] = {}
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}

    def _lock(self, key: tuple[str, str]) -> asyncio.Lock:
        if key not in self._locks:
            self._locks[key] = asyncio.Lock()
        return self._locks[key]

    async def get_or_rebuild(self, session: Any, blueprint_id: str) -> ApstraKuzuGraph:
        """
        Returns the Kuzu graph for the given blueprint, rebuilding it from
        the Apstra API if:
          - no graph has been built yet for this (instance, blueprint), or
          - the blueprint version has changed since the last build.

        On a cache hit: one API call (GET /api/blueprints).
        On a cache miss: two API calls (GET /api/blueprints, then
          GET /api/blueprints/{blueprint_id}).

        Raises KeyError if blueprint_id does not exist on the instance.
        Raises RuntimeError if an API call fails.
        """
        key = (session.name, blueprint_id)
        async with self._lock(key):
            # Step 1: cheap version check across all blueprints
            version_map = await get_blueprint_versions(session)
            if blueprint_id not in version_map:
                raise KeyError(
                    f"Blueprint '{blueprint_id}' not found on instance '{session.name}'."
                )
            current_version: int = version_map[blueprint_id]

            if key in self._graphs:
                graph, built_version = self._graphs[key]
                if current_version == built_version:
                    return graph
                # Version changed — close the stale graph
                graph.close()

            # Step 2: full fetch only when rebuild is needed
            raw = await get_blueprint_graph(session, blueprint_id)
            nodes: dict = raw.get("nodes", {})
            rels: dict = raw.get("relationships", {})

            graph = ApstraKuzuGraph(db_path=":memory:")
            graph.rebuild_from_dicts(nodes, rels)
            self._graphs[key] = (graph, current_version)
            return graph

    def close_all(self) -> None:
        """Close all open graph instances. Called on server shutdown."""
        for graph, _ in self._graphs.values():
            graph.close()
        self._graphs.clear()
