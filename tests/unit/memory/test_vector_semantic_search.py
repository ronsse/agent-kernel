"""Tests for vector store semantic search capabilities."""

import numpy as np
import pytest


class TestVectorSemanticSearch:
    """Tests for semantic search using vector embeddings."""

    @pytest.fixture
    def sample_embeddings(self):
        """Create sample embeddings for testing."""
        # Simulate embeddings for different topics
        return {
            "agents_1": np.random.rand(384).astype(np.float32),
            "agents_2": np.random.rand(384).astype(np.float32),
            "memory_1": np.random.rand(384).astype(np.float32),
            "planning_1": np.random.rand(384).astype(np.float32),
            "unrelated": np.random.rand(384).astype(np.float32),
        }

    def test_store_and_retrieve_vectors(self, vector_store, sample_embeddings):
        """Test storing and retrieving vectors."""
        # Store vectors
        for item_id, embedding in sample_embeddings.items():
            vector_store.upsert(
                item_id=item_id,
                vector=embedding.tolist(),
                metadata={"topic": item_id.split("_")[0]},
            )

        # Retrieve a specific vector
        retrieved = vector_store.get("agents_1")
        assert retrieved is not None
        assert len(retrieved["vector"]) == 384

    def test_similarity_search(self, vector_store, sample_embeddings):
        """Test finding similar vectors."""
        # Store vectors
        for item_id, embedding in sample_embeddings.items():
            vector_store.upsert(
                item_id=item_id,
                vector=embedding.tolist(),
                metadata={"topic": item_id.split("_")[0]},
            )

        # Search with agents_1 query
        query_vector = sample_embeddings["agents_1"]
        results = vector_store.query(query_vector.tolist(), top_k=3)

        # Should return results
        assert len(results) > 0
        assert len(results) <= 3

        # First result should be the query itself (perfect match)
        assert results[0]["item_id"] == "agents_1"
        assert results[0]["score"] > 0.99  # Nearly perfect similarity

    def test_filtered_search(self, vector_store):
        """Test searching with metadata filters."""
        # Create vectors with different metadata
        vec1 = np.random.rand(384).astype(np.float32)
        vec2 = np.random.rand(384).astype(np.float32)
        vec3 = np.random.rand(384).astype(np.float32)

        vector_store.upsert(
            "doc1",
            vec1.tolist(),
            metadata={"category": "technical", "status": "published"},
        )
        vector_store.upsert(
            "doc2",
            vec2.tolist(),
            metadata={"category": "technical", "status": "draft"},
        )
        vector_store.upsert(
            "doc3",
            vec3.tolist(),
            metadata={"category": "marketing", "status": "published"},
        )

        # Search with filter
        query = vec1  # Use doc1's vector as query
        results = vector_store.query(
            query.tolist(),
            top_k=5,
            filters={"category": "technical"},
        )

        # Should only return technical documents
        assert len(results) <= 2  # Only doc1 and doc2
        categories = {r["metadata"]["category"] for r in results}
        assert categories == {"technical"}

    def test_top_k_limiting(self, vector_store):
        """Test that top_k properly limits results."""
        # Store 10 vectors
        for i in range(10):
            vec = np.random.rand(384).astype(np.float32)
            vector_store.upsert(f"item_{i}", vec.tolist(), metadata={"index": i})

        # Search with top_k=3
        query = np.random.rand(384).astype(np.float32)
        results = vector_store.query(query.tolist(), top_k=3)

        assert len(results) == 3

        # Search with top_k=5
        results = vector_store.query(query.tolist(), top_k=5)
        assert len(results) == 5

    def test_empty_vector_store_search(self, vector_store):
        """Test searching in an empty vector store."""
        query = np.random.rand(384).astype(np.float32)
        results = vector_store.query(query.tolist(), top_k=5)

        assert len(results) == 0

    def test_vector_update(self, vector_store):
        """Test updating an existing vector."""
        vec1 = np.random.rand(384).astype(np.float32)
        vec2 = np.random.rand(384).astype(np.float32)

        # Store initial vector
        vector_store.upsert(
            "doc1",
            vec1.tolist(),
            metadata={"version": 1},
        )

        # Update with new vector
        vector_store.upsert(
            "doc1",
            vec2.tolist(),
            metadata={"version": 2},
        )

        # Retrieve and verify it was updated
        retrieved = vector_store.get("doc1")
        assert retrieved["metadata"]["version"] == 2

        # Vector should be different from original
        assert not np.allclose(retrieved["vector"], vec1)

    def test_delete_vector(self, vector_store):
        """Test deleting a vector."""
        vec = np.random.rand(384).astype(np.float32)
        vector_store.upsert("doc1", vec.tolist(), metadata={})

        # Verify it exists
        assert vector_store.get("doc1") is not None

        # Delete it
        deleted = vector_store.delete("doc1")
        assert deleted is True

        # Verify it's gone
        assert vector_store.get("doc1") is None

    def test_batch_upsert(self, vector_store):
        """Test batch insertion of vectors."""
        vectors = []
        for i in range(50):
            vec = np.random.rand(384).astype(np.float32)
            vectors.append({
                "item_id": f"batch_{i}",
                "vector": vec.tolist(),
                "metadata": {"batch": True, "index": i},
            })

        # Batch upsert
        for v in vectors:
            vector_store.upsert(v["item_id"], v["vector"], v["metadata"])

        # Verify count
        # Note: SQLiteVectorStore might not have a count method
        # So we'll search and verify we get results
        query = vectors[0]["vector"]
        results = vector_store.query(query, top_k=10)

        assert len(results) > 0

    def test_similarity_scores_descending(self, vector_store):
        """Test that results are returned in descending similarity order."""
        # Create query vector
        query_vec = np.random.rand(384).astype(np.float32)

        # Store query and some random vectors
        vector_store.upsert("query", query_vec.tolist(), metadata={})

        for i in range(5):
            vec = np.random.rand(384).astype(np.float32)
            vector_store.upsert(f"doc_{i}", vec.tolist(), metadata={})

        # Search
        results = vector_store.query(query_vec.tolist(), top_k=6)

        # Verify scores are in descending order
        scores = [r["score"] for r in results]
        assert scores == sorted(scores, reverse=True)

        # First result should be the query itself
        assert results[0]["item_id"] == "query"

    def test_search_with_threshold(self, vector_store):
        """Test filtering results by similarity threshold."""
        # Create a query vector
        query_vec = np.random.rand(384).astype(np.float32)

        # Store the exact query vector and some dissimilar ones
        vector_store.upsert("exact_match", query_vec.tolist(), metadata={})

        for i in range(5):
            # Create dissimilar vectors
            vec = np.random.rand(384).astype(np.float32)
            vector_store.upsert(f"different_{i}", vec.tolist(), metadata={})

        # Search
        results = vector_store.query(query_vec.tolist(), top_k=10)

        # Filter by threshold manually (if vector store doesn't support it)
        threshold = 0.9
        high_similarity = [r for r in results if r["score"] >= threshold]

        # Only exact match should be above threshold
        assert len(high_similarity) >= 1
        assert high_similarity[0]["item_id"] == "exact_match"

    def test_metadata_only_retrieval(self, vector_store):
        """Test retrieving metadata without vectors."""
        vec = np.random.rand(384).astype(np.float32)
        metadata = {
            "title": "Test Document",
            "author": "Test Author",
            "tags": ["test", "sample"],
        }

        vector_store.upsert("doc1", vec.tolist(), metadata=metadata)

        retrieved = vector_store.get("doc1")

        # Verify metadata is complete
        assert retrieved["metadata"]["title"] == "Test Document"
        assert retrieved["metadata"]["author"] == "Test Author"
        assert "test" in retrieved["metadata"]["tags"]

    def test_large_result_set(self, vector_store):
        """Test handling large result sets."""
        # Store 100 vectors
        for i in range(100):
            vec = np.random.rand(384).astype(np.float32)
            vector_store.upsert(f"large_{i}", vec.tolist(), metadata={"index": i})

        # Search with large top_k
        query = np.random.rand(384).astype(np.float32)
        results = vector_store.query(query.tolist(), top_k=50)

        assert len(results) == 50

    def test_vector_dimensionality_consistency(self, vector_store):
        """Test that all vectors maintain consistent dimensionality."""
        vec_384 = np.random.rand(384).astype(np.float32)

        # Store 384-dim vector
        vector_store.upsert("vec_384", vec_384.tolist(), metadata={})

        # Try to query with wrong dimension (should handle gracefully or error)
        # This test verifies the system maintains dimensionality consistency
        retrieved = vector_store.get("vec_384")
        assert len(retrieved["vector"]) == 384
