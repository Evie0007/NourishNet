import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import expiration, scheduler
from .database import Base, SessionLocal, engine
from .routers import auth, catalog, intake, items, shelves, pantries, reservations

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))

# Creates missing tables only — it will not add a column to a table that
# already exists. An existing database therefore needs
# `python -m scripts.upgrade_schema` once, which is also the script that
# backfills expiration deadlines onto items created before the rules
# existed. Alembic remains the real answer (NFR-4.6.4, C-5).
Base.metadata.create_all(bind=engine)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Seed the expiration policy before the first request, so no item can
    # be created into a database where "no rule" silently means "never
    # expires."
    db = SessionLocal()
    try:
        created = expiration.ensure_default_rules(db)
        if created:
            logging.getLogger(__name__).info("seeded %d default expiration rules", created)
    finally:
        db.close()

    scheduler.start()
    try:
        yield
    finally:
        await scheduler.stop()


app = FastAPI(
    title="NourishNet API",
    description="Backend for the NourishNet smart-shelf food recovery system.",
    version="0.3.0",
    lifespan=lifespan,
)

# NFR-4.1.3: an explicit allowlist, not "*". With credentialed requests,
# a wildcard would let any website act on a signed-in user's behalf.
# Set CORS_ORIGINS in the environment to a comma-separated list — your
# Vercel URL in production, plus any preview domains you actually use.
_DEFAULT_ORIGINS = "http://localhost:5173,http://127.0.0.1:5173"
ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv("CORS_ORIGINS", _DEFAULT_ORIGINS).split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(catalog.router)
app.include_router(intake.router)
app.include_router(items.router)
app.include_router(shelves.router)
app.include_router(pantries.router)
app.include_router(reservations.router)


@app.get("/health")
def health_check():
    """Reports whether the automatic rules are actually running, not only
    that the process is up — a server answering requests while its sweep is
    dead looks healthy and is not."""
    return {
        "status": "ok",
        "database": "postgres" if engine.dialect.name == "postgresql" else engine.dialect.name,
        "sweep_enabled": scheduler.SWEEP_ENABLED,
        "sweep_interval_seconds": scheduler.SWEEP_INTERVAL_SECONDS,
    }
