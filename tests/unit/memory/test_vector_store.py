"""Tests for vector store implementations."""

import importlib.util

import pytest

from agent_kernel.memory.vector_store import (
    LANCEDB_AVAILABLE,
    LanceDBVectorStore,
    SQLiteVectorStore,
    create_vector_store,
)


class TestSQLiteVectorStore:
    """Tests for SQLiteVectorStore."""

    def test_upsert_and_get(self, vector_store: SQLiteVectorStore):
        """Test storing and retrieving a vector."""
        vector = [0.1, 0.2, 0.3, 0.4, 0.5]

        vector_store.upsert(
            item_id="vec_1",
            vector=vector,
            metadata={"title": "Test Vector", "type": "note"},
        )

        result = vector_store.get("vec_1")
        assert result is not None
        assert result["item_id"] == "vec_1"
        assert len(result["vector"]) == 5
        assert result["metadata"]["title"] == "Test Vector"

    def test_query_similar(self, vector_store: SQLiteVectorStore):
        """Test querying for similar vectors."""
        # Insert some vectors
        vector_store.upsert("v1", [1.0, 0.0, 0.0], {"label": "x-axis"})
        vector_store.upsert("v2", [0.0, 1.0, 0.0], {"label": "y-axis"})
        vector_store.upsert("v3", [0.0, 0.0, 1.0], {"label": "z-axis"})
        vector_store.upsert("v4", [0.9, 0.1, 0.0], {"label": "near-x"})

        # Query for vectors similar to x-axis
        results = vector_store.query([1.0, 0.0, 0.0], top_k=2)

        assert len(results) == 2
        # v1 should be most similar (identical)
        assert results[0]["item_id"] == "v1"
        assert results[0]["score"] > 0.99
        # v4 should be second (near x-axis)
        assert results[1]["item_id"] == "v4"

    def test_query_with_filters(self, vector_store: SQLiteVectorStore):
        """Test querying with metadata filters."""
        vector_store.upsert("v1", [1.0, 0.0], {"category": "notes"})
        vector_store.upsert("v2", [0.9, 0.1], {"category": "tasks"})
        vector_store.upsert("v3", [0.8, 0.2], {"category": "notes"})

        results = vector_store.query(
            [1.0, 0.0],
            top_k=10,
            filters={"category": "notes"},
        )

        assert len(results) == 2
        for r in results:
            assert r["metadata"]["category"] == "notes"

    def test_delete_vector(self, vector_store: SQLiteVectorStore):
        """Test deleting a vector."""
        vector_store.upsert("to_delete", [0.5, 0.5])

        deleted = vector_store.delete("to_delete")
        assert deleted is True

        result = vector_store.get("to_delete")
        assert result is None

    def test_count_vectors(self, vector_store: SQLiteVectorStore):
        """Test counting vectors."""
        for i in range(5):
            vector_store.upsert(f"v_{i}", [float(i), 0.0])

        count = vector_store.count()
        assert count == 5

    def test_update_vector(self, vector_store: SQLiteVectorStore):
        """Test updating an existing vector."""
        vector_store.upsert("update_me", [0.1, 0.2], {"version": 1})
        vector_store.upsert("update_me", [0.3, 0.4], {"version": 2})

        result = vector_store.get("update_me")
        # Use approximate comparison for float32 storage
        assert len(result["vector"]) == 2
        assert abs(result["vector"][0] - 0.3) < 0.001
        assert abs(result["vector"][1] - 0.4) < 0.001
        assert result["metadata"]["version"] == 2

    def test_query_with_sql_metadata_filter(self, vector_store: SQLiteVectorStore):
        """Test that metadata filters are pushed to SQL for efficiency."""
        vector_store.upsert("v1", [1.0, 0.0], {"category": "notes", "priority": 1})
        vector_store.upsert("v2", [0.9, 0.1], {"category": "tasks", "priority": 2})
        vector_store.upsert("v3", [0.8, 0.2], {"category": "notes", "priority": 3})
        vector_store.upsert("v4", [0.7, 0.3], {"category": "events", "priority": 1})

        # Filter by string
        results = vector_store.query(
            [1.0, 0.0], top_k=10, filters={"category": "notes"}
        )
        assert len(results) == 2
        assert all(r["metadata"]["category"] == "notes" for r in results)

        # Filter by int
        results = vector_store.query(
            [1.0, 0.0], top_k=10, filters={"priority": 1}
        )
        assert len(results) == 2
        assert all(r["metadata"]["priority"] == 1 for r in results)

        # Filter by multiple fields
        results = vector_store.query(
            [1.0, 0.0], top_k=10, filters={"category": "notes", "priority": 1}
        )
        assert len(results) == 1
        assert results[0]["item_id"] == "v1"


@pytest.mark.skipif(not LANCEDB_AVAILABLE, reason="LanceDB not installed")
@pytest.mark.skipif(importlib.util.find_spec("pandas") is None, reason="pandas not installed")
class TestLanceDBVectorStore:
    """Tests for LanceDBVectorStore."""

    def test_upsert_and_get(self, lancedb_vector_store: LanceDBVectorStore):
        """Test storing and retrieving a vector."""
        if lancedb_vector_store is None:
            pytest.skip("LanceDB not available")

        vector = [0.1, 0.2, 0.3, 0.4, 0.5]

        lancedb_vector_store.upsert(
            item_id="vec_1",
            vector=vector,
            metadata={"title": "Test Vector", "type": "note"},
        )

        result = lancedb_vector_store.get("vec_1")
        assert result is not None
        assert result["item_id"] == "vec_1"
        assert len(result["vector"]) == 5
        assert result["metadata"]["title"] == "Test Vector"

    def test_query_similar(self, lancedb_vector_store: LanceDBVectorStore):
        """Test querying for similar vectors with HNSW index."""
        if lancedb_vector_store is None:
            pytest.skip("LanceDB not available")

        # Insert some vectors
        lancedb_vector_store.upsert("v1", [1.0, 0.0, 0.0], {"label": "x-axis"})
        lancedb_vector_store.upsert("v2", [0.0, 1.0, 0.0], {"label": "y-axis"})
        lancedb_vector_store.upsert("v3", [0.0, 0.0, 1.0], {"label": "z-axis"})
        lancedb_vector_store.upsert("v4", [0.9, 0.1, 0.0], {"label": "near-x"})

        # Query for vectors similar to x-axis
        results = lancedb_vector_store.query([1.0, 0.0, 0.0], top_k=2)

        assert len(results) == 2
        # v1 should be most similar (identical)
        assert results[0]["item_id"] == "v1"
        assert results[0]["score"] > 0.99
        # v4 should be second (near x-axis)
        assert results[1]["item_id"] == "v4"

    def test_query_with_filters(self, lancedb_vector_store: LanceDBVectorStore):
        """Test querying with metadata filters."""
        if lancedb_vector_store is None:
            pytest.skip("LanceDB not available")

        lancedb_vector_store.upsert("v1", [1.0, 0.0], {"category": "notes"})
        lancedb_vector_store.upsert("v2", [0.9, 0.1], {"category": "tasks"})
        lancedb_vector_store.upsert("v3", [0.8, 0.2], {"category": "notes"})

        results = lancedb_vector_store.query(
            [1.0, 0.0],
            top_k=10,
            filters={"category": "notes"},
        )

        assert len(results) == 2
        for r in results:
            assert r["metadata"]["category"] == "notes"

    def test_batch_upsert(self, lancedb_vector_store: LanceDBVectorStore):
        """Test batch upsert for efficient bulk inserts."""
        if lancedb_vector_store is None:
            pytest.skip("LanceDB not available")

        items = [
            ("batch_1", [0.1, 0.2, 0.3], {"index": 1}),
            ("batch_2", [0.4, 0.5, 0.6], {"index": 2}),
            ("batch_3", [0.7, 0.8, 0.9], {"index": 3}),
        ]

        lancedb_vector_store.upsert_batch(items)

        assert lancedb_vector_store.count() == 3
        for item_id, _, metadata in items:
            result = lancedb_vector_store.get(item_id)
            assert result is not None
            assert result["metadata"]["index"] == metadata["index"]

    def test_delete_vector(self, lancedb_vector_store: LanceDBVectorStore):
        """Test deleting a vector."""
        if lancedb_vector_store is None:
            pytest.skip("LanceDB not available")

        lancedb_vector_store.upsert("to_delete", [0.5, 0.5, 0.5])

        deleted = lancedb_vector_store.delete("to_delete")
        assert deleted is True

        result = lancedb_vector_store.get("to_delete")
        assert result is None

    def test_count_vectors(self, lancedb_vector_store: LanceDBVectorStore):
        """Test counting vectors."""
        if lancedb_vector_store is None:
            pytest.skip("LanceDB not available")

        for i in range(5):
            lancedb_vector_store.upsert(f"v_{i}", [float(i), 0.0, 0.0])

        count = lancedb_vector_store.count()
        assert count == 5

    def test_update_vector(self, lancedb_vector_store: LanceDBVectorStore):
        """Test updating an existing vector."""
        if lancedb_vector_store is None:
            pytest.skip("LanceDB not available")

        lancedb_vector_store.upsert("update_me", [0.1, 0.2, 0.3], {"version": 1})
        lancedb_vector_store.upsert("update_me", [0.3, 0.4, 0.5], {"version": 2})

        result = lancedb_vector_store.get("update_me")
        assert result is not None
        assert len(result["vector"]) == 3
        assert abs(result["vector"][0] - 0.3) < 0.001
        assert result["metadata"]["version"] == 2


class TestVectorStoreFactory:
    """Tests for create_vector_store factory function."""

    def test_factory_creates_store(self, temp_dir):
        """Test factory function creates a working store."""
        store = create_vector_store(temp_dir / "test_vectors", prefer_lancedb=False)

        store.upsert("test", [0.1, 0.2, 0.3], {"key": "value"})
        result = store.get("test")

        assert result is not None
        assert result["metadata"]["key"] == "value"
        store.close()

    def test_factory_prefers_lancedb_when_available(self, temp_dir):
        """Test factory prefers LanceDB when available."""
        store = create_vector_store(temp_dir / "prefer_lance", prefer_lancedb=True)

        if LANCEDB_AVAILABLE:
            assert isinstance(store, LanceDBVectorStore)
        else:
            assert isinstance(store, SQLiteVectorStore)

        store.close()

    def test_factory_falls_back_to_sqlite(self, temp_dir):
        """Test factory falls back to SQLite when LanceDB not preferred."""
        store = create_vector_store(temp_dir / "sqlite_only", prefer_lancedb=False)

        assert isinstance(store, SQLiteVectorStore)
        store.close()
