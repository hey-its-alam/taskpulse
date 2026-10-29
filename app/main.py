from contextlib import asynccontextmanager

from fastapi import FastAPI

from . import models  # noqa: F401
from .database import Base, engine


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(engine)
    yield


app = FastAPI(title="TaskPulse", version="0.1.0", lifespan=lifespan)


@app.get("/health", tags=["system"])
def health():
    return {"status": "ok"}