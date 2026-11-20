
import json
import logging
import sys
from datetime import datetime, timezone

from opentelemetry import trace


class JsonFormatter(logging.Formatter):
    STANDARD_FIELDS = {
        "name", "msg", "args", "levelname", "levelno",
        "pathname", "filename", "module", "exc_info",
        "exc_text", "stack_info", "lineno", "funcName",
        "created", "msecs", "relativeCreated", "thread",
        "threadName", "processName", "process",
        "message", "asctime",
    }

    def format(self, record):
        ctx = trace.get_current_span().get_span_context()

        data = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "service": getattr(record, "service", "taskpulse"),
            "logger": record.name,
            "event": getattr(record, "event", "log"),
            "message": record.getMessage(),
            "trace_id": (
                format(ctx.trace_id, "032x") if ctx.is_valid else None
            ),
        }

        for key, value in record.__dict__.items():
            if key not in self.STANDARD_FIELDS and key not in data:
                data[key] = value

        if record.exc_info:
            data["error_type"] = record.exc_info[0].__name__
            data["error_message"] = str(record.exc_info[1])
            data["stack"] = self.formatException(record.exc_info)

        if record.stack_info:
            data["stack"] = self.formatStack(record.stack_info)

        return json.dumps(data, default=str)


def setup_logging():
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)
