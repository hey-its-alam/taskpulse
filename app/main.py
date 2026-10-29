from contextlib import asynccontextmanager

from fastapi import FastAPI

from . import models  # noqa: F401
from .database import Base, engine
from .routes import router


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(engine)
    yield


app = FastAPI(title="TaskPulse", version="0.2.0", lifespan=lifespan)
app.include_router(router)


@app.get("/health", tags=["system"])
def health():
    return {"status": "ok"}