"""
Database engine + session setup.

Reads DATABASE_URL from the environment (see .env.example).
For local dev without Postgres installed, you can temporarily set:
    DATABASE_URL=sqlite:///./nourishnet.db
and everything below still works — SQLAlchemy abstracts the driver.
"""
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg2://nourishnet:nourishnet@localhost:5432/nourishnet",
)

# SQLite needs this connect_arg; Postgres ignores it if present, so we branch instead.
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    """FastAPI dependency — yields a session, always closes it after the request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
