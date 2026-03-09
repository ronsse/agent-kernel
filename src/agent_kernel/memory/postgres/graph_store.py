"""PostgreSQL implementation of GraphStore.

Uses PostgreSQL's recursive CTEs (same approach as SQLite)
with JSONB for property storage and indexing.
"""

from __future__ import annotations

import json
from typing import Any

import structlog

from agent_kernel.core.ids import generate_ulid
from agent_kernel.core.schemas.base import utc_now
from agent_kernel.memory.graph_store import GraphStore
from agent_kernel.memory.postgres.connection import PostgresConnection, PostgresConnectionPool

logger = structlog.get_logger(__name__)


class PostgresGraphStore(GraphStore):
    """PostgreSQL implementation of graph store with JSONB properties."""

    def __init__(self, pool: PostgresConnectionPool) -> None:
        self._pool = pool
        self._init_schema()
        logger.info("postgres_graph_store_initialized")

    def _init_schema(self) -> None:
        """Initialize database schema."""
        with PostgresConnection(self._pool) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS nodes (
                        node_id TEXT PRIMARY KEY,
                        node_type TEXT NOT NULL,
                        properties_json JSONB NOT NULL DEFAULT '{}',
                        created_at TIMESTAMPTZ NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS edges (
                        edge_id TEXT PRIMARY KEY,
                        source_id TEXT NOT NULL REFERENCES nodes(node_id) ON DELETE CASCADE,
                        target_id TEXT NOT NULL REFERENCES nodes(node_id) ON DELETE CASCADE,
                        edge_type TEXT NOT NULL,
                        properties_json JSONB NOT NULL DEFAULT '{}',
                        created_at TIMESTAMPTZ NOT NULL
                    );

                    CREATE INDEX IF NOT EXISTS idx_nodes_type ON nodes(node_type);
                    CREATE INDEX IF NOT EXISTS idx_nodes_properties
                        ON nodes USING GIN(properties_json);
                    CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_id);
                    CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target_id);
                    CREATE INDEX IF NOT EXISTS idx_edges_type ON edges(edge_type);
                    CREATE INDEX IF NOT EXISTS idx_edges_source_type
                        ON edges(source_id, edge_type);
                """)

    def upsert_node(
        self,
        node_id: str | None,
        node_type: str,
        properties: dict[str, Any],
    ) -> str:
        """Insert or update a node."""
        if node_id is None:
            node_id = generate_ulid()

        now = utc_now().isoformat()
        properties_json = json.dumps(properties)

        with PostgresConnection(self._pool) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO nodes (node_id, node_type, properties_json, created_at, updated_at)
                    VALUES (%s, %s, %s::jsonb, %s, %s)
                    ON CONFLICT (node_id) DO UPDATE SET
                        node_type = EXCLUDED.node_type,
                        properties_json = EXCLUDED.properties_json,
                        updated_at = EXCLUDED.updated_at
                    """,
                    (node_id, node_type, properties_json, now, now),
                )

        logger.debug("node_upserted", node_id=node_id, node_type=node_type)
        return node_id

    def upsert_edge(
        self,
        source_id: str,
        target_id: str,
        edge_type: str,
        properties: dict[str, Any] | None = None,
    ) -> str:
        """Insert or update an edge."""
        now = utc_now().isoformat()
        properties_json = json.dumps(properties or {})

        with PostgresConnection(self._pool) as conn:
            with conn.cursor() as cur:
                # Check if edge exists
                cur.execute(
                    """
                    SELECT edge_id FROM edges
                    WHERE source_id = %s AND target_id = %s AND edge_type = %s
                    """,
                    (source_id, target_id, edge_type),
                )
                row = cur.fetchone()

                if row:
                    edge_id = row[0]
                    cur.execute(
                        "UPDATE edges SET properties_json = %s::jsonb WHERE edge_id = %s",
                        (properties_json, edge_id),
                    )
                else:
                    edge_id = generate_ulid()
                    cur.execute(
                        """
                        INSERT INTO edges
                            (edge_id, source_id, target_id, edge_type, properties_json, created_at)
                        VALUES (%s, %s, %s, %s, %s::jsonb, %s)
                        """,
                        (edge_id, source_id, target_id, edge_type, properties_json, now),
                    )

        logger.debug(
            "edge_upserted",
            edge_id=edge_id,
            source=source_id,
            target=target_id,
            type=edge_type,
        )
        return edge_id

    def get_node(self, node_id: str) -> dict[str, Any] | None:
        """Get a node by ID."""
        with PostgresConnection(self._pool) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT node_id, node_type, properties_json, created_at, updated_at FROM nodes WHERE node_id = %s",
                    (node_id,),
                )
                row = cur.fetchone()

        if row is None:
            return None

        return {
            "node_id": row[0],
            "node_type": row[1],
            "properties": row[2] if isinstance(row[2], dict) else json.loads(row[2]),
            "created_at": str(row[3]),
            "updated_at": str(row[4]),
        }

    def get_subgraph(
        self,
        seed_ids: list[str],
        depth: int = 2,
        edge_types: list[str] | None = None,
    ) -> dict[str, Any]:
        """Get a subgraph around seed nodes using recursive CTE."""
        if not seed_ids:
            return {"nodes": [], "edges": []}

        with PostgresConnection(self._pool) as conn:
            with conn.cursor() as cur:
                # Build edge type filter
                edge_filter = ""
                params: list[Any] = [tuple(seed_ids)]

                if edge_types:
                    edge_filter = "AND e.edge_type = ANY(%s)"
                    params.append(tuple(edge_types))

                params.append(depth)

                cur.execute(
                    f"""
                    WITH RECURSIVE traversal(node_id, depth) AS (
                        SELECT node_id, 0
                        FROM nodes WHERE node_id = ANY(%s)

                        UNION

                        SELECT
                            CASE
                                WHEN e.source_id = t.node_id THEN e.target_id
                                ELSE e.source_id
                            END,
                            t.depth + 1
                        FROM traversal t
                        JOIN edges e ON (e.source_id = t.node_id OR e.target_id = t.node_id)
                            {edge_filter}
                        WHERE t.depth < %s
                    ),
                    unique_nodes AS (
                        SELECT node_id, MIN(depth) as min_depth
                        FROM traversal
                        GROUP BY node_id
                    )
                    SELECT n.node_id, n.node_type, n.properties_json, n.created_at, n.updated_at
                    FROM unique_nodes un
                    JOIN nodes n ON n.node_id = un.node_id
                    ORDER BY un.min_depth, n.node_id
                    """,
                    params,
                )
                node_rows = cur.fetchall()

                collected_nodes = []
                node_ids = set()
                for row in node_rows:
                    node_ids.add(row[0])
                    collected_nodes.append({
                        "node_id": row[0],
                        "node_type": row[1],
                        "properties": row[2] if isinstance(row[2], dict) else json.loads(row[2]),
                        "created_at": str(row[3]),
                        "updated_at": str(row[4]),
                    })

                # Fetch edges between collected nodes
                collected_edges = []
                if node_ids:
                    node_list = tuple(node_ids)
                    edge_params: list[Any] = [node_list, node_list]
                    edge_type_filter = ""
                    if edge_types:
                        edge_type_filter = "AND edge_type = ANY(%s)"
                        edge_params.append(tuple(edge_types))

                    cur.execute(
                        f"""
                        SELECT edge_id, source_id, target_id, edge_type, properties_json, created_at
                        FROM edges
                        WHERE source_id = ANY(%s)
                          AND target_id = ANY(%s)
                          {edge_type_filter}
                        """,
                        edge_params,
                    )

                    for row in cur.fetchall():
                        collected_edges.append({
                            "edge_id": row[0],
                            "source_id": row[1],
                            "target_id": row[2],
                            "edge_type": row[3],
                            "properties": row[4] if isinstance(row[4], dict) else json.loads(row[4]),
                            "created_at": str(row[5]),
                        })

        logger.debug(
            "subgraph_fetched",
            seed_count=len(seed_ids),
            depth=depth,
            nodes_found=len(collected_nodes),
            edges_found=len(collected_edges),
        )

        return {"nodes": collected_nodes, "edges": collected_edges}

    def query(
        self,
        node_type: str | None = None,
        properties: dict[str, Any] | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Query nodes using JSONB operators."""
        conditions = ["TRUE"]
        params: list[Any] = []

        if node_type:
            conditions.append("node_type = %s")
            params.append(node_type)

        if properties:
            # Use JSONB containment operator @>
            conditions.append("properties_json @> %s::jsonb")
            params.append(json.dumps(properties))

        where_clause = " AND ".join(conditions)
        params.append(limit)

        with PostgresConnection(self._pool) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT node_id, node_type, properties_json, created_at
                    FROM nodes
                    WHERE {where_clause}
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    params,
                )

                return [
                    {
                        "node_id": row[0],
                        "node_type": row[1],
                        "properties": row[2] if isinstance(row[2], dict) else json.loads(row[2]),
                        "created_at": str(row[3]),
                    }
                    for row in cur.fetchall()
                ]

    def delete_node(self, node_id: str) -> bool:
        """Delete a node (edges cascade via FK)."""
        with PostgresConnection(self._pool) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM nodes WHERE node_id = %s",
                    (node_id,),
                )
                return cur.rowcount > 0

    def delete_edge(self, edge_id: str) -> bool:
        """Delete an edge."""
        with PostgresConnection(self._pool) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM edges WHERE edge_id = %s",
                    (edge_id,),
                )
                return cur.rowcount > 0

    def delete_edges_from_source(
        self,
        source_id: str,
        edge_type: str | None = None,
        exclude_targets: list[str] | None = None,
    ) -> int:
        """Delete edges from a source node."""
        conditions = ["source_id = %s"]
        params: list[Any] = [source_id]

        if edge_type:
            conditions.append("edge_type = %s")
            params.append(edge_type)

        if exclude_targets:
            conditions.append("target_id != ALL(%s)")
            params.append(tuple(exclude_targets))

        where_clause = " AND ".join(conditions)

        with PostgresConnection(self._pool) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"DELETE FROM edges WHERE {where_clause}",
                    params,
                )
                deleted = cur.rowcount

        if deleted > 0:
            logger.debug(
                "edges_deleted",
                source_id=source_id,
                edge_type=edge_type,
                count=deleted,
            )
        return deleted

    def close(self) -> None:
        """No-op; pool manages connections."""
        logger.info("postgres_graph_store_closed")
