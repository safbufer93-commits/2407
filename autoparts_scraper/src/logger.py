"""
Structured JSON logger with correlation fields: run_id, sku, site, attempt.
"""
from __future__ import annotations
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from typing import Optional


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        base = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "msg": record.getMessage(),
            "logger": record.name,
        }
        # Extra correlation fields attached via LoggerAdapter
        for key in ("run_id", "sku", "site", "attempt", "duration_ms", "result_count"):
            val = getattr(record, key, None)
            if val is not None:
                base[key] = val
        if record.exc_info:
            base["exc"] = self.formatException(record.exc_info)
        return json.dumps(base, ensure_ascii=False)


def setup_logging(log_dir: str, run_id: str, level: str = "INFO") -> None:
    os.makedirs(log_dir, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    fmt = _JsonFormatter()

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    root.addHandler(sh)

    log_path = os.path.join(log_dir, f"run_{run_id}.jsonl")
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)


class CorrelatedLogger:
    """Thin wrapper that injects correlation fields into every log call."""

    def __init__(
        self,
        name: str,
        run_id: str = "",
        sku: str = "",
        site: str = "",
        attempt: int = 0,
    ):
        self._log = logging.getLogger(name)
        self.run_id = run_id
        self.sku = sku
        self.site = site
        self.attempt = attempt

    def _extra(self, **kw) -> dict:
        return {
            "run_id": self.run_id,
            "sku": self.sku,
            "site": self.site,
            "attempt": self.attempt,
            **kw,
        }

    def info(self, msg: str, **kw) -> None:
        self._log.info(msg, extra=self._extra(**kw))

    def warning(self, msg: str, **kw) -> None:
        self._log.warning(msg, extra=self._extra(**kw))

    def error(self, msg: str, **kw) -> None:
        self._log.error(msg, extra=self._extra(**kw))

    def debug(self, msg: str, **kw) -> None:
        self._log.debug(msg, extra=self._extra(**kw))

    def bind(self, **kw) -> "CorrelatedLogger":
        c = CorrelatedLogger(
            self._log.name,
            run_id=kw.get("run_id", self.run_id),
            sku=kw.get("sku", self.sku),
            site=kw.get("site", self.site),
            attempt=kw.get("attempt", self.attempt),
        )
        return c


class Metrics:
    """Simple in-memory counters."""

    def __init__(self) -> None:
        self.tasks_total = 0
        self.tasks_success = 0
        self.tasks_blocked = 0
        self.tasks_timeout = 0
        self.tasks_not_found = 0
        self.tasks_error = 0
        self.offers_parsed_total = 0
        self._durations: list[float] = []

    def record_duration(self, ms: float) -> None:
        self._durations.append(ms)

    @property
    def avg_task_duration_ms(self) -> float:
        if not self._durations:
            return 0.0
        return sum(self._durations) / len(self._durations)

    def as_dict(self) -> dict:
        return {
            "tasks_total": self.tasks_total,
            "tasks_success": self.tasks_success,
            "tasks_blocked": self.tasks_blocked,
            "tasks_timeout": self.tasks_timeout,
            "tasks_not_found": self.tasks_not_found,
            "tasks_error": self.tasks_error,
            "offers_parsed_total": self.offers_parsed_total,
            "avg_task_duration_ms": round(self.avg_task_duration_ms, 1),
        }
