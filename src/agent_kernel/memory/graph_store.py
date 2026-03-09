"""Graph Store - entity relationships and knowledge graph."""

from __future__ import annotations

import json
import sqlite3
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import structlog

from agent_kernel.core.ids import generate_ulid
from agent_kernel.core.schemas.base import utc_now

logger = structlog.get_logger(__name__)


class GraphStore(ABC):
    """Abstract interface for graph storage.

    Stores nodes (entities) and edges (relationships) with
    metadata and provenance tracking.
    """

    @abstractmethod
    def upsert_node(
        self,
        node_id: str | None,
        node_type: str,
        properties: dict[str, Any],
    ) -> str:
        """Insert or update a node.

        Args:
            node_id: Optional node ID (generated if not provided).
            node_type: Type of node (e.g., "project", "person", "concept").
            properties: Node properties.

        Returns:
            The node ID.
        """

    @abstractmethod
    def upsert_edge(
        self,
        source_id: str,
        target_id: str,
        edge_type: str,
        properties: dict[str, Any] | None = None,
    ) -> str:
        """Insert or update an edge.

        Args:
            source_id: Source node ID.
            target_id: Target node ID.
            edge_type: Type of relationship.
            properties: Optional edge properties.

        Returns:
            The edge ID.
        """

    @abstractmethod
    def get_node(self, node_id: str) -> dict[str, Any] | None:
        """Get a node by ID."""

    def get_nodes_bulk(self, node_ids: list[str]) -> list[dict[str, Any]]:
        """Get multiple nodes by ID in a single query.

        Default implementation calls get_node() in a loop.
        Subclasses should override for batch efficiency.

        Args:
            node_ids: List of node IDs to retrieve.

        Returns:
            List of found nodes (missing IDs are skipped).
        """
        results = []
        for nid in node_ids:
            node = self.get_node(nid)
            if node is not None:
                results.append(node)
        return results

    @abstractmethod
    def get_subgraph(
        self,
        seed_ids: list[str],
        depth: int = 2,
        edge_types: list[str] | None = None,
    ) -> dict[str, Any]:
        """Get a subgraph around seed nodes.

        Args:
            seed_ids: Starting node IDs.
            depth: How many hops to traverse.
            edge_types: Optional filter for edge types.

        Returns:
            Dict with nodes and edges lists.
        """

    @abstractmethod
    def query(
        self,
        node_type: str | list[str] | None = None,
        properties: dict[str, Any] | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Query nodes.

        Args:
            node_type: Filter by node type (single, list for IN, or None).
            properties: Filter by properties.
            limit: Maximum results.

        Returns:
            List of matching nodes.
        """

    @abstractmethod
    def delete_node(self, node_id: str) -> bool:
        """Delete a node and its edges."""

    @abstractmethod
    def delete_edge(self, edge_id: str) -> bool:
        """Delete an edge."""

    @abstractmethod
    def delete_edges_from_source(
        self,
        source_id: str,
        edge_type: str | None = None,
        exclude_targets: list[str] | None = None,
    ) -> int:
        """Delete edges from a source node, optionally filtered.

        Args:
            source_id: The source node ID.
            edge_type: Optional filter by edge type.
            exclude_targets: Target IDs to keep (don't delete).

        Returns:
            Number of edges deleted.
        """

    def get_edges_for_nodes(
        self,
        node_ids: list[str],
        direction: str = "outgoing",
        edge_type: str | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        """Get edges for multiple nodes in a single query.

        Default implementation calls get_edges() in a loop.
        Subclasses should override for batch efficiency.

        Args:
            node_ids: List of node IDs.
            direction: "outgoing", "incoming", or "both".
            edge_type: Optional filter by edge type.

        Returns:
            Dict mapping node_id -> list of edges.
        """
        result: dict[str, list[dict[str, Any]]] = {}
        for nid in node_ids:
            result[nid] = self.get_edges(nid, direction=direction, edge_type=edge_type)
        return result

    def delete_nodes_bulk(self, node_ids: list[str]) -> int:
        """Delete multiple nodes and their edges in batch.

        Default implementation calls delete_node() in a loop.
        Subclasses should override for batch efficiency.

        Args:
            node_ids: List of node IDs to delete.

        Returns:
            Number of nodes actually deleted.
        """
        count = 0
        for nid in node_ids:
            if self.delete_node(nid):
                count += 1
        return count

    @abstractmethod
    def close(self) -> None:
        """Close the store."""


class SQLiteGraphStore(GraphStore):
    """SQLite implementation of graph store."""

    def __init__(self, db_path: str | Path) -> None:
        """Initialize SQLite graph store.

        Args:
            db_path: Path to SQLite database.
        """
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()
        logger.info("sqlite_graph_store_initialized", db_path=str(self._db_path))

    def _init_schema(self) -> None:
        """Initialize database schema."""
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS nodes (
                node_id TEXT PRIMARY KEY,
                node_type TEXT NOT NULL,
                properties_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS edges (
                edge_id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                edge_type TEXT NOT NULL,
                properties_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (source_id) REFERENCES nodes(node_id),
                FOREIGN KEY (target_id) REFERENCES nodes(node_id)
            );

            CREATE INDEX IF NOT EXISTS idx_nodes_type ON nodes(node_type);
            CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_id);
            CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target_id);
            CREATE INDEX IF NOT EXISTS idx_edges_type ON edges(edge_type);
        """)
        self._conn.commit()

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

        existing = self.get_node(node_id)
        if existing:
            self._conn.execute(
                """
                UPDATE nodes
                SET node_type = ?, properties_json = ?, updated_at = ?
                WHERE node_id = ?
                """,
                (node_type, properties_json, now, node_id),
            )
        else:
            self._conn.execute(
                """
                INSERT INTO nodes (node_id, node_type, properties_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (node_id, node_type, properties_json, now, now),
            )

        self._conn.commit()
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
        # Check if edge exists
        cursor = self._conn.execute(
            """
            SELECT edge_id FROM edges
            WHERE source_id = ? AND target_id = ? AND edge_type = ?
            """,
            (source_id, target_id, edge_type),
        )
        row = cursor.fetchone()

        now = utc_now().isoformat()
        properties_json = json.dumps(properties or {})

        if row:
            edge_id = row["edge_id"]
            self._conn.execute(
                """
                UPDATE edges SET properties_json = ? WHERE edge_id = ?
                """,
                (properties_json, edge_id),
            )
        else:
            edge_id = generate_ulid()
            self._conn.execute(
                """
                INSERT INTO edges
                (edge_id, source_id, target_id, edge_type, properties_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (edge_id, source_id, target_id, edge_type, properties_json, now),
            )

        self._conn.commit()
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
        cursor = self._conn.execute(
            "SELECT * FROM nodes WHERE node_id = ?",
            (node_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None

        return {
            "node_id": row["node_id"],
            "node_type": row["node_type"],
            "properties": json.loads(row["properties_json"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def get_nodes_bulk(self, node_ids: list[str]) -> list[dict[str, Any]]:
        """Get multiple nodes in a single SQL query."""
        if not node_ids:
            return []
        placeholders = ",".join("?" for _ in node_ids)
        cursor = self._conn.execute(
            f"SELECT * FROM nodes WHERE node_id IN ({placeholders})",
            node_ids,
        )
        return [
            {
                "node_id": row["node_id"],
                "node_type": row["node_type"],
                "properties": json.loads(row["properties_json"]),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in cursor.fetchall()
        ]

    def get_edges(
        self,
        node_id: str,
        direction: str = "both",
        edge_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """Get edges connected to a node.

        Args:
            node_id: The node ID.
            direction: "outgoing", "incoming", or "both".
            edge_type: Optional filter by edge type.

        Returns:
            List of edge dicts.
        """
        conditions = []
        params: list[Any] = []

        if direction in ("outgoing", "both"):
            conditions.append("source_id = ?")
            params.append(node_id)
        if direction in ("incoming", "both"):
            conditions.append("target_id = ?")
            params.append(node_id)

        where_clause = " OR ".join(conditions)

        if edge_type:
            where_clause = f"({where_clause}) AND edge_type = ?"
            params.append(edge_type)

        cursor = self._conn.execute(
            f"SELECT * FROM edges WHERE {where_clause}",
            params,
        )

        return [
            {
                "edge_id": row["edge_id"],
                "source_id": row["source_id"],
                "target_id": row["target_id"],
                "edge_type": row["edge_type"],
                "properties": json.loads(row["properties_json"]),
                "created_at": row["created_at"],
            }
            for row in cursor.fetchall()
        ]

    def get_subgraph(
        self,
        seed_ids: list[str],
        depth: int = 2,
        edge_types: list[str] | None = None,
    ) -> dict[str, Any]:
        """Get a subgraph around seed nodes.

        Uses optimized batch query with recursive CTE to avoid N+1 queries.
        Falls back to iterative method for complex edge type filtering.
        """
        # Use optimized batch method
        return self._get_subgraph_batch(seed_ids, depth, edge_types)

    def _get_subgraph_batch(
        self,
        seed_ids: list[str],
        depth: int = 2,
        edge_types: list[str] | None = None,
    ) -> dict[str, Any]:
        """Get subgraph using recursive CTE for O(1) query complexity.

        This method uses a single SQL query with recursive CTE to traverse
        the graph, avoiding N+1 query issues of the iterative approach.

        Performance: O(1) queries vs O(N) where N = number of nodes visited.
        """
        if not seed_ids:
            return {"nodes": [], "edges": []}

        # Build edge type filter
        edge_filter = ""
        edge_params: list[Any] = []
        if edge_types:
            placeholders = ",".join("?" for _ in edge_types)
            edge_filter = f"AND e.edge_type IN ({placeholders})"
            edge_params = list(edge_types)

        # Build seed IDs placeholders
        seed_placeholders = ",".join("?" for _ in seed_ids)

        # Recursive CTE to traverse graph
        # This collects all reachable node IDs within the specified depth
        query = f"""
        WITH RECURSIVE traversal(node_id, depth) AS (
            -- Base case: seed nodes at depth 0
            SELECT node_id, 0 FROM nodes WHERE node_id IN ({seed_placeholders})

            UNION

            -- Recursive case: follow edges up to max depth
            SELECT
                CASE
                    WHEN e.source_id = t.node_id THEN e.target_id
                    ELSE e.source_id
                END as node_id,
                t.depth + 1
            FROM traversal t
            JOIN edges e ON (e.source_id = t.node_id OR e.target_id = t.node_id)
                {edge_filter}
            WHERE t.depth < ?
        ),
        -- Deduplicate nodes (keep minimum depth)
        unique_nodes AS (
            SELECT node_id, MIN(depth) as min_depth
            FROM traversal
            GROUP BY node_id
        )
        -- Fetch full node data
        SELECT
            n.node_id,
            n.node_type,
            n.properties_json,
            n.created_at,
            n.updated_at,
            un.min_depth
        FROM unique_nodes un
        JOIN nodes n ON n.node_id = un.node_id
        ORDER BY un.min_depth, n.node_id
        """

        # Execute node query
        params = list(seed_ids) + edge_params + [depth]
        cursor = self._conn.execute(query, params)

        collected_nodes = []
        node_ids = set()
        for row in cursor.fetchall():
            node_ids.add(row["node_id"])
            collected_nodes.append({
                "node_id": row["node_id"],
                "node_type": row["node_type"],
                "properties": json.loads(row["properties_json"]),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            })

        # Now fetch all edges between collected nodes
        if len(node_ids) > 0:
            node_list = list(node_ids)
            node_placeholders = ",".join("?" for _ in node_list)

            edge_query = f"""
            SELECT
                edge_id,
                source_id,
                target_id,
                edge_type,
                properties_json,
                created_at
            FROM edges
            WHERE source_id IN ({node_placeholders})
              AND target_id IN ({node_placeholders})
            """
            if edge_types:
                edge_query += f" AND edge_type IN ({','.join('?' for _ in edge_types)})"

            edge_params = node_list + node_list
            if edge_types:
                edge_params += list(edge_types)

            cursor = self._conn.execute(edge_query, edge_params)

            collected_edges = [
                {
                    "edge_id": row["edge_id"],
                    "source_id": row["source_id"],
                    "target_id": row["target_id"],
                    "edge_type": row["edge_type"],
                    "properties": json.loads(row["properties_json"]),
                    "created_at": row["created_at"],
                }
                for row in cursor.fetchall()
            ]
        else:
            collected_edges = []

        logger.debug(
            "subgraph_batch_fetched",
            seed_count=len(seed_ids),
            depth=depth,
            nodes_found=len(collected_nodes),
            edges_found=len(collected_edges),
        )

        return {
            "nodes": collected_nodes,
            "edges": collected_edges,
        }

    def _get_subgraph_iterative(
        self,
        seed_ids: list[str],
        depth: int = 2,
        edge_types: list[str] | None = None,
    ) -> dict[str, Any]:
        """Get subgraph using iterative traversal (legacy method).

        This method is kept for backwards compatibility and edge cases
        where the recursive CTE might not work as expected.

        Warning: This has O(N) query complexity where N = nodes visited.
        """
        visited_nodes: set[str] = set()
        collected_nodes: list[dict[str, Any]] = []
        collected_edges: list[dict[str, Any]] = []

        current_layer = set(seed_ids)

        for _ in range(depth + 1):
            next_layer: set[str] = set()

            for node_id in current_layer:
                if node_id in visited_nodes:
                    continue

                visited_nodes.add(node_id)

                node = self.get_node(node_id)
                if node:
                    collected_nodes.append(node)

                    edges = self.get_edges(node_id)
                    for edge in edges:
                        if edge_types and edge["edge_type"] not in edge_types:
                            continue

                        collected_edges.append(edge)
                        next_layer.add(edge["source_id"])
                        next_layer.add(edge["target_id"])

            current_layer = next_layer - visited_nodes

        # Deduplicate edges
        seen_edges: set[str] = set()
        unique_edges = []
        for edge in collected_edges:
            if edge["edge_id"] not in seen_edges:
                seen_edges.add(edge["edge_id"])
                unique_edges.append(edge)

        return {
            "nodes": collected_nodes,
            "edges": unique_edges,
        }

    def query(
        self,
        node_type: str | list[str] | None = None,
        properties: dict[str, Any] | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Query nodes with SQL-pushed filtering.

        Uses json_extract() for property filtering in SQL to reduce
        data transfer and Python-side filtering.

        Args:
            node_type: Single type, list for IN clause, or None for all.
            properties: Filter by properties.
            limit: Maximum results.
        """
        conditions = ["1=1"]
        params: list[Any] = []

        if isinstance(node_type, list):
            if node_type:
                placeholders = ",".join("?" for _ in node_type)
                conditions.append(f"node_type IN ({placeholders})")
                params.extend(node_type)
        elif node_type:
            conditions.append("node_type = ?")
            params.append(node_type)

        # Push property filters to SQL using json_extract
        if properties:
            for key, value in properties.items():
                if isinstance(value, str):
                    conditions.append(f"json_extract(properties_json, '$.{key}') = ?")
                    params.append(value)
                elif isinstance(value, (int, float)):
                    conditions.append(f"json_extract(properties_json, '$.{key}') = ?")
                    params.append(value)
                elif isinstance(value, bool):
                    # SQLite json_extract returns 1/0 for booleans
                    conditions.append(f"json_extract(properties_json, '$.{key}') = ?")
                    params.append(1 if value else 0)
                elif value is None:
                    conditions.append(
                        f"json_extract(properties_json, '$.{key}') IS NULL"
                    )
                # Complex types (lists, dicts) still need Python filtering

        where_clause = " AND ".join(conditions)
        params.append(limit)

        cursor = self._conn.execute(
            f"""
            SELECT * FROM nodes
            WHERE {where_clause}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            params,
        )

        results = []
        for row in cursor.fetchall():
            node_props = json.loads(row["properties_json"])

            # Apply complex property filters that can't be pushed to SQL
            if properties:
                match = True
                for key, value in properties.items():
                    if isinstance(value, (list, dict)):
                        if node_props.get(key) != value:
                            match = False
                            break
                if not match:
                    continue

            results.append({
                "node_id": row["node_id"],
                "node_type": row["node_type"],
                "properties": node_props,
                "created_at": row["created_at"],
            })

        return results

    def query_by_property(
        self,
        property_path: str,
        value: Any,
        node_type: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Query nodes by a specific property path using SQL json_extract.

        This is more efficient than the general query() method for
        simple property lookups.

        Args:
            property_path: JSON path to property (e.g., "name" or "metadata.source").
            value: Value to match.
            node_type: Optional filter by node type.
            limit: Maximum results.

        Returns:
            List of matching nodes.
        """
        conditions = [f"json_extract(properties_json, '$.{property_path}') = ?"]
        params: list[Any] = [value]

        if node_type:
            conditions.append("node_type = ?")
            params.append(node_type)

        where_clause = " AND ".join(conditions)
        params.append(limit)

        cursor = self._conn.execute(
            f"""
            SELECT * FROM nodes
            WHERE {where_clause}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            params,
        )

        return [
            {
                "node_id": row["node_id"],
                "node_type": row["node_type"],
                "properties": json.loads(row["properties_json"]),
                "created_at": row["created_at"],
            }
            for row in cursor.fetchall()
        ]

    def delete_node(self, node_id: str) -> bool:
        """Delete a node and its edges."""
        # Delete edges first
        self._conn.execute(
            "DELETE FROM edges WHERE source_id = ? OR target_id = ?",
            (node_id, node_id),
        )

        cursor = self._conn.execute(
            "DELETE FROM nodes WHERE node_id = ?",
            (node_id,),
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def delete_edge(self, edge_id: str) -> bool:
        """Delete an edge."""
        cursor = self._conn.execute(
            "DELETE FROM edges WHERE edge_id = ?",
            (edge_id,),
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def delete_edges_from_source(
        self,
        source_id: str,
        edge_type: str | None = None,
        exclude_targets: list[str] | None = None,
    ) -> int:
        """Delete edges from a source node, optionally filtered.

        Args:
            source_id: The source node ID.
            edge_type: Optional filter by edge type.
            exclude_targets: Target IDs to keep (don't delete).

        Returns:
            Number of edges deleted.
        """
        conditions = ["source_id = ?"]
        params: list[Any] = [source_id]

        if edge_type:
            conditions.append("edge_type = ?")
            params.append(edge_type)

        if exclude_targets:
            placeholders = ",".join("?" for _ in exclude_targets)
            conditions.append(f"target_id NOT IN ({placeholders})")
            params.extend(exclude_targets)

        where_clause = " AND ".join(conditions)

        cursor = self._conn.execute(
            f"DELETE FROM edges WHERE {where_clause}",
            params,
        )
        self._conn.commit()

        deleted = cursor.rowcount
        if deleted > 0:
            logger.debug(
                "edges_deleted",
                source_id=source_id,
                edge_type=edge_type,
                count=deleted,
            )

        return deleted

    def get_edges_for_nodes(
        self,
        node_ids: list[str],
        direction: str = "outgoing",
        edge_type: str | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        """Get edges for multiple nodes in a single SQL query."""
        if not node_ids:
            return {}

        placeholders = ",".join("?" for _ in node_ids)

        # Build direction clause
        if direction == "outgoing":
            dir_clause = f"source_id IN ({placeholders})"
            key_col = "source_id"
        elif direction == "incoming":
            dir_clause = f"target_id IN ({placeholders})"
            key_col = "target_id"
        else:
            dir_clause = (
                f"(source_id IN ({placeholders})"
                f" OR target_id IN ({placeholders}))"
            )
            key_col = None  # Need special handling

        params: list[Any] = list(node_ids)
        if direction == "both":
            params.extend(node_ids)

        if edge_type:
            dir_clause = f"({dir_clause}) AND edge_type = ?"
            params.append(edge_type)

        cursor = self._conn.execute(
            f"SELECT * FROM edges WHERE {dir_clause}",
            params,
        )

        node_id_set = set(node_ids)
        result: dict[str, list[dict[str, Any]]] = {nid: [] for nid in node_ids}

        for row in cursor.fetchall():
            edge = {
                "edge_id": row["edge_id"],
                "source_id": row["source_id"],
                "target_id": row["target_id"],
                "edge_type": row["edge_type"],
                "properties": json.loads(row["properties_json"]),
                "created_at": row["created_at"],
            }

            if key_col:
                result[row[key_col]].append(edge)
            else:
                # "both" direction: add to each matching node
                if row["source_id"] in node_id_set:
                    result[row["source_id"]].append(edge)
                if row["target_id"] in node_id_set:
                    result[row["target_id"]].append(edge)

        return result

    def delete_nodes_bulk(self, node_ids: list[str]) -> int:
        """Delete multiple nodes and their edges in a single batch."""
        if not node_ids:
            return 0

        placeholders = ",".join("?" for _ in node_ids)

        # Delete edges referencing these nodes
        self._conn.execute(
            f"DELETE FROM edges WHERE source_id IN ({placeholders})"
            f" OR target_id IN ({placeholders})",
            node_ids + node_ids,
        )

        # Delete the nodes
        cursor = self._conn.execute(
            f"DELETE FROM nodes WHERE node_id IN ({placeholders})",
            node_ids,
        )
        self._conn.commit()

        deleted = cursor.rowcount
        if deleted > 0:
            logger.debug("nodes_bulk_deleted", count=deleted)
        return deleted

    def count_nodes(self) -> int:
        """Count total nodes."""
        cursor = self._conn.execute("SELECT COUNT(*) as cnt FROM nodes")
        return cursor.fetchone()["cnt"]

    def count_edges(self) -> int:
        """Count total edges."""
        cursor = self._conn.execute("SELECT COUNT(*) as cnt FROM edges")
        return cursor.fetchone()["cnt"]

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()
        logger.info("sqlite_graph_store_closed")
