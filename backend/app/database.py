"""
Database engine + session setup.

PostgreSQL is the target (NFR-4.6.2). The intake pipeline made that
concrete rather than aspirational: the expiration sweep and the
reservation claim both write while dashboards read, and SQLite takes a
lock over the whole file to do it. One sweep would stall every request in
flight.

SQLite still works for local dev with no server to install — set
DATABASE_URL=sqlite:///./nourishnet.db — and everything below adapts. The
one thing it will not reproduce is concurrency behaviour, so anything
about locking or races has to be tested against Postgres.

Local Postgres in one command:
    docker run --name nourishnet-db -e POSTGRES_PASSWORD=nourishnet \
        -e POSTGRES_USER=nourishnet -e POSTGRES_DB=nourishnet \
        -p 5432:5432 -d postgres:16
"""
import os

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker, declarative_base
from dotenv import load_dotenv

load_dotenv()

_RAW_DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg2://nourishnet:nourishnet@localhost:5432/nourishnet",
)


def _normalize_url(url: str) -> str:
    """
    Accept the URL forms hosting providers hand out.

    Render, Heroku and friends set DATABASE_URL to `postgres://...`, a
    scheme SQLAlchemy 2.x dropped. Rewriting it here is the difference
    between a deploy that works and a NoSuchModuleError with no obvious
    cause, and it costs nothing to be tolerant about.
    """
    if url.startswith("postgres://"):
        return "postgresql+psycopg2://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        return "postgresql+psycopg2://" + url[len("postgresql://"):]
    return url


DATABASE_URL = _normalize_url(_RAW_DATABASE_URL)
IS_POSTGRES = DATABASE_URL.startswith("postgresql")
IS_SQLITE = DATABASE_URL.startswith("sqlite")

if IS_SQLITE:
    engine_kwargs = {"connect_args": {"check_same_thread": False}}
else:
    connect_args = {"application_name": os.getenv("DB_APPLICATION_NAME", "nourishnet-api")}

    # A query that hangs holds a connection and a row lock with it, and the
    # sweep queues up behind it. A statement timeout bounds that.
    #
    # Off unless asked for, because this deployment connects through
    # Supabase's pooler and a connection pooler in transaction mode can
    # reject the libpq `options` startup parameter outright — which fails
    # every connection, not just slow ones. Turn it on against a direct
    # Postgres connection, or set the timeout on the database role instead:
    #     ALTER ROLE nourishnet SET statement_timeout = '15s';
    _statement_timeout_ms = os.getenv("DB_STATEMENT_TIMEOUT_MS")
    if _statement_timeout_ms:
        connect_args["options"] = f"-c statement_timeout={_statement_timeout_ms}"

    engine_kwargs = {
        # Sized for a small web dyno plus the background sweep, which holds
        # its own connection. Keep pool_size + max_overflow under the
        # server's max_connections, remembering every worker has its own
        # pool: a 4-worker deploy at these numbers can reach 60 connections.
        "pool_size": int(os.getenv("DB_POOL_SIZE", "5")),
        "max_overflow": int(os.getenv("DB_MAX_OVERFLOW", "10")),
        # Managed Postgres drops idle connections; recycling under that
        # window avoids handing out a socket the server already closed.
        "pool_recycle": int(os.getenv("DB_POOL_RECYCLE_SECONDS", "1800")),
        "connect_args": connect_args,
    }

engine = create_engine(DATABASE_URL, pool_pre_ping=True, **engine_kwargs)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


@event.listens_for(Engine, "connect")
def _set_sqlite_pragmas(dbapi_connection, connection_record):
    """
    NFR-4.7.4: SQLite ignores foreign keys unless told not to, per
    connection. Without this the referential integrity the schema declares
    is not actually enforced in development — an item can point at a shelf
    that does not exist and nothing complains until production, on
    Postgres, where it does.

    WAL lets readers work while a write is in flight, which is the closest
    SQLite gets to the concurrency the sweep assumes.
    """
    if not IS_SQLITE:
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


def get_db():
    """FastAPI dependency — yields a session, always closes it after the request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
