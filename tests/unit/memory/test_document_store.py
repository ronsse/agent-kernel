"""Tests for document store."""


from agent_kernel.memory.document_store import SQLiteDocumentStore


class TestSQLiteDocumentStore:
    """Tests for SQLiteDocumentStore."""

    def test_put_and_get(self, document_store: SQLiteDocumentStore):
        """Test storing and retrieving a document."""
        doc_id = document_store.put(
            doc_id="doc_123",
            content="This is test content for the document.",
            metadata={"title": "Test Doc", "tags": ["test"]},
        )

        assert doc_id == "doc_123"

        doc = document_store.get("doc_123")
        assert doc is not None
        assert doc["content"] == "This is test content for the document."
        assert doc["metadata"]["title"] == "Test Doc"

    def test_put_generates_id(self, document_store: SQLiteDocumentStore):
        """Test that put generates an ID if not provided."""
        doc_id = document_store.put(
            doc_id=None,
            content="Auto-generated ID document",
        )

        assert doc_id is not None
        assert len(doc_id) == 26  # ULID length

        doc = document_store.get(doc_id)
        assert doc is not None

    def test_update_document(self, document_store: SQLiteDocumentStore):
        """Test updating an existing document."""
        document_store.put(
            doc_id="doc_update",
            content="Original content",
            metadata={"version": 1},
        )

        document_store.put(
            doc_id="doc_update",
            content="Updated content",
            metadata={"version": 2},
        )

        doc = document_store.get("doc_update")
        assert doc["content"] == "Updated content"
        assert doc["metadata"]["version"] == 2

    def test_delete_document(self, document_store: SQLiteDocumentStore):
        """Test deleting a document."""
        document_store.put(doc_id="doc_delete", content="To be deleted")

        deleted = document_store.delete("doc_delete")
        assert deleted is True

        doc = document_store.get("doc_delete")
        assert doc is None

    def test_delete_nonexistent(self, document_store: SQLiteDocumentStore):
        """Test deleting non-existent document."""
        deleted = document_store.delete("nonexistent")
        assert deleted is False

    def test_search_documents(self, document_store: SQLiteDocumentStore):
        """Test full-text search."""
        document_store.put(
            doc_id="doc_1",
            content="Python programming language tutorial",
            metadata={"title": "Python Tutorial"},
        )
        document_store.put(
            doc_id="doc_2",
            content="JavaScript web development guide",
            metadata={"title": "JS Guide"},
        )
        document_store.put(
            doc_id="doc_3",
            content="Python data science with pandas",
            metadata={"title": "Data Science"},
        )

        results = document_store.search("Python")
        assert len(results) == 2

        results = document_store.search("JavaScript")
        assert len(results) == 1
        assert results[0]["doc_id"] == "doc_2"

    def test_list_documents(self, document_store: SQLiteDocumentStore):
        """Test listing documents with pagination."""
        for i in range(10):
            document_store.put(
                doc_id=f"doc_{i}",
                content=f"Document number {i}",
            )

        docs = document_store.list_documents(limit=5)
        assert len(docs) == 5

        docs = document_store.list_documents(limit=5, offset=5)
        assert len(docs) == 5

    def test_count_documents(self, document_store: SQLiteDocumentStore):
        """Test counting documents."""
        for i in range(3):
            document_store.put(doc_id=f"count_{i}", content=f"Doc {i}")

        count = document_store.count()
        assert count == 3

    def test_search_with_sql_metadata_filter(
        self, document_store: SQLiteDocumentStore
    ):
        """Test that metadata filters are pushed to SQL in search."""
        document_store.put(
            doc_id="d1",
            content="Python programming guide",
            metadata={"category": "tutorial", "language": "python"},
        )
        document_store.put(
            doc_id="d2",
            content="Python data science",
            metadata={"category": "reference", "language": "python"},
        )
        document_store.put(
            doc_id="d3",
            content="JavaScript programming guide",
            metadata={"category": "tutorial", "language": "javascript"},
        )

        # Search with metadata filter
        results = document_store.search(
            "programming", filters={"category": "tutorial"}
        )
        assert len(results) == 2
        result_ids = {r["doc_id"] for r in results}
        assert result_ids == {"d1", "d3"}

        # Search with multiple metadata filters
        results = document_store.search(
            "programming",
            filters={"category": "tutorial", "language": "python"},
        )
        assert len(results) == 1
        assert results[0]["doc_id"] == "d1"
