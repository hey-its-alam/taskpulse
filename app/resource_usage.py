
import os
import psutil


_process = psutil.Process(os.getpid())


def get_process_memory_mb() -> float:
    """Return current Python process memory usage in MB."""
    return round(_process.memory_info().rss / (1024 * 1024), 2)


def get_system_memory_percent() -> float:
    """Return total system memory utilization percentage."""
    return round(psutil.virtual_memory().percent, 2)


def get_memory_snapshot() -> dict:
    """Return a snapshot of process and system memory usage."""
    return {
        "process_rss_mb": get_process_memory_mb(),
        "system_memory_percent": get_system_memory_percent(),
    }
