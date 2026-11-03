from contextlib import asynccontextmanager

from fastapi import FastAPI
from prometheus_fastapi_instrumentator import Instrumentator

from . import models  # noqa: F401
from .database import Base, engine
from .logging_config import setup_logging
from .metrics import register_db_collector
from .routes import router

setup_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(engine)
    yield


app = FastAPI(title="TaskPulse", version="0.6.0", lifespan=lifespan)
Instrumentator().instrument(app).expose(app, include_in_schema=False)
register_db_collector()
app.include_router(router)


@app.get("/health", tags=["system"])
def health():
    return {"status": "ok"}