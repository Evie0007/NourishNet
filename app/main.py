import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .database import Base, engine
from .routers import auth, items, shelves, pantries, reservations

# Prototype-speed table creation. Swap for Alembic migrations once the
# schema stabilizes and you have real data you can't afford to drop.
Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="NourishNet API",
    description="Backend for the NourishNet smart-shelf food recovery system.",
    version="0.2.0",
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
app.include_router(items.router)
app.include_router(shelves.router)
app.include_router(pantries.router)
app.include_router(reservations.router)


@app.get("/health")
def health_check():
    return {"status": "ok"}
