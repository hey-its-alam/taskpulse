from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.celery import CeleryInstrumentor
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from .config import settings
from .database import engine


def setup_tracing(service_name: str):
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    exporter = OTLPSpanExporter(endpoint=settings.otlp_endpoint, insecure=True)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    SQLAlchemyInstrumentor().instrument(engine=engine)
    CeleryInstrumentor().instrument()  # carries the trace from API -> queue -> worker


def instrument_fastapi(app: FastAPI):
    # /metrics and /health are polled constantly, so we keep them out of Tempo
    FastAPIInstrumentor.instrument_app(app, excluded_urls="metrics,health")