from fastapi import FastAPI

from .database import Base, engine
from .routers import items, shelves, pantries, reservations

# Prototype-speed table creation. Swap for Alembic migrations once the
# schema stabilizes and you have real data you can't afford to drop.
Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="NourishNet API",
    description="Backend for the NourishNet smart-shelf food recovery system.",
    version="0.1.0",
)

app.include_router(items.router)
app.include_router(shelves.router)
app.include_router(pantries.router)
app.include_router(reservations.router)


@app.get("/health")
def health_check():
    return {"status": "ok"}
