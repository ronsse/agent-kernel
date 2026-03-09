"""PostgreSQL connection pool management for Supabase.

Provides a shared connection pool that all Postgres store implementations use.
Uses psycopg2's ThreadedConnectionPool for thread-safe concurrent access.
"""

from __future__ import annotations

import threading
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

try:
    import psycopg2
    import psycopg2.extras
    import psycopg2.pool

    PSYCOPG2_AVAILABLE = True
except ImportError:
    PSYCOPG2_AVAILABLE = False


class PostgresConnectionPool:
    """Thread-safe PostgreSQL connection pool.

    Wraps psycopg2's ThreadedConnectionPool with convenience methods.
    All Postgres store implementations share a single pool instance.
    """

    _instance: PostgresConnectionPool | None = None
    _lock = threading.Lock()

    def __init__(
        self,
        database_url: str,
        *,
        min_connections: int = 1,
        max_connections: int = 10,
    ) -> None:
        if not PSYCOPG2_AVAILABLE:
            msg = (
                "psycopg2 is not installed. Install with: "
                "pip install psycopg2-binary"
            )
            raise ImportError(msg)

        self._database_url = database_url

        # Auto-enable SSL for Supabase connections
        dsn = database_url
        if ".supabase.co" in dsn and "sslmode" not in dsn:
            separator = "&" if "?" in dsn else "?"
            dsn = f"{dsn}{separator}sslmode=require"

        self._pool = psycopg2.pool.ThreadedConnectionPool(
            minconn=min_connections,
            maxconn=max_connections,
            dsn=dsn,
        )
        logger.info(
            "postgres_connection_pool_initialized",
            min_connections=min_connections,
            max_connections=max_connections,
        )

    @classmethod
    def get_instance(
        cls,
        database_url: str | None = None,
        **kwargs: Any,
    ) -> PostgresConnectionPool:
        """Get or create the singleton pool instance."""
        with cls._lock:
            if cls._instance is None:
                if database_url is None:
                    msg = "database_url required for first initialization"
                    raise ValueError(msg)
                cls._instance = cls(database_url, **kwargs)
            return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset the singleton (for testing)."""
        with cls._lock:
            if cls._instance is not None:
                cls._instance.close()
                cls._instance = None

    def get_connection(self) -> Any:
        """Get a connection from the pool."""
        conn = self._pool.getconn()
        conn.autocommit = False
        return conn

    def return_connection(self, conn: Any) -> None:
        """Return a connection to the pool."""
        self._pool.putconn(conn)

    def close(self) -> None:
        """Close all connections in the pool."""
        self._pool.closeall()
        logger.info("postgres_connection_pool_closed")


class PostgresConnection:
    """Context manager for pool connections.

    Usage:
        pool = PostgresConnectionPool.get_instance(url)
        with PostgresConnection(pool) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
    """

    def __init__(self, pool: PostgresConnectionPool) -> None:
        self._pool = pool
        self._conn: Any = None

    def __enter__(self) -> Any:
        self._conn = self._pool.get_connection()
        return self._conn

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if self._conn is not None:
            if exc_type is not None:
                self._conn.rollback()
            else:
                self._conn.commit()
            self._pool.return_connection(self._conn)
            self._conn = None
