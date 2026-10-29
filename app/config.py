from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg2://taskpulse:taskpulse@localhost:5432/taskpulse"
    redis_url: str = "redis://localhost:6379/0"
    otlp_endpoint: str = "http://localhost:4317"

    rate_limit_submit: int = 30
    rate_limit_read: int = 300
    rate_limit_retry: int = 10
    rate_limit_window_seconds: int = 60
    max_queue_depth: int = 500
    task_rate_limit: str = "120/m"

    failure_rate: float = 0.15
    extra_latency_seconds: float = 0.0


settings = Settings()