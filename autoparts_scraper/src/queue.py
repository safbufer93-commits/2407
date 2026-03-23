"""
Task queue with global and per-domain concurrency limits.
Supports exponential backoff with jitter for retries.
"""
from __future__ import annotations
import asyncio
import random
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Callable, Awaitable
from collections import defaultdict

from .adapters.base import TaskStatus, ScrapeResult
from .logger import CorrelatedLogger, Metrics


@dataclass
class ScrapeTask:
    run_id: str
    site_id: str
    site_base_url: str
    sku_raw: str
    sku_norm: str
    brand_hint: str = ""
    attempt: int = 1
    status: TaskStatus = TaskStatus.CREATED
    scheduled_at: float = field(default_factory=time.time)
    result: Optional[ScrapeResult] = None
    duration_ms: float = 0.0


class DomainSemaphore:
    """Per-domain asyncio.Semaphore factory."""

    def __init__(self, default_limit: int = 1) -> None:
        self._default = default_limit
        self._sems: Dict[str, asyncio.Semaphore] = {}

    def get(self, domain: str, limit: Optional[int] = None) -> asyncio.Semaphore:
        if domain not in self._sems:
            self._sems[domain] = asyncio.Semaphore(limit or self._default)
        return self._sems[domain]


def _jitter(base: float, jitter_frac: float = 0.5) -> float:
    return base + random.uniform(0, base * jitter_frac)


def _backoff(attempt: int, base: float = 2.0, max_sec: float = 30.0) -> float:
    delay = min(base ** attempt, max_sec)
    return _jitter(delay)


class TaskRunner:
    """
    Runs ScrapeTask items with concurrency control and retry logic.
    """

    def __init__(
        self,
        adapters: dict,                         # site_id -> BaseSiteAdapter
        config,                                  # config module
        metrics: Metrics,
        log: CorrelatedLogger,
        artifacts_dir: str,
        domain_limits: Optional[Dict[str, int]] = None,
    ) -> None:
        self.adapters = adapters
        self.config = config
        self.metrics = metrics
        self.log = log
        self.artifacts_dir = artifacts_dir
        self._global_sem = asyncio.Semaphore(config.GLOBAL_CONCURRENCY)
        self._domain_sem = DomainSemaphore(default_limit=1)
        # Initialise per-domain limits from config
        for site in config.SITES:
            self._domain_sem.get(site["id"], site.get("per_domain_concurrency", 1))

    async def run_all(self, tasks: List[ScrapeTask]) -> List[ScrapeTask]:
        """Execute all tasks concurrently respecting limits. Returns completed tasks."""
        coros = [self._run_one(task) for task in tasks]
        completed = await asyncio.gather(*coros, return_exceptions=False)
        return list(completed)

    async def _run_one(self, task: ScrapeTask) -> ScrapeTask:
        adapter = self.adapters.get(task.site_id)
        if adapter is None:
            task.status = TaskStatus.PARSE_ERROR
            task.result = ScrapeResult(
                status=TaskStatus.PARSE_ERROR,
                error_message=f"No adapter registered for site_id={task.site_id}",
            )
            return task

        domain_sem = self._domain_sem.get(task.site_id)
        max_attempts = self.config.MAX_ATTEMPTS

        for attempt in range(1, max_attempts + 1):
            task.attempt = attempt
            tlog = self.log.bind(
                run_id=task.run_id,
                sku=task.sku_norm,
                site=task.site_id,
                attempt=attempt,
            )

            async with self._global_sem:
                async with domain_sem:
                    t0 = time.monotonic()
                    task.status = TaskStatus.IN_PROGRESS
                    self.metrics.tasks_total += 1

                    try:
                        from playwright.async_api import async_playwright
                        async with async_playwright() as pw:
                            browser = await pw.chromium.launch(
                                headless=self.config.HEADLESS,
                                args=["--no-sandbox", "--disable-dev-shm-usage"],
                            )
                            context = await browser.new_context(
                                user_agent=self.config.USER_AGENT,
                                locale=self.config.LOCALE,
                            )
                            # Block images and fonts to speed up page load
                            await context.route(
                                "**/*",
                                lambda route: (
                                    route.abort()
                                    if route.request.resource_type in ("image", "font", "media")
                                    else route.continue_()
                                ),
                            )
                            page = await context.new_page()
                            page.set_default_timeout(self.config.SELECTOR_TIMEOUT_MS)
                            page.set_default_navigation_timeout(self.config.NAVIGATION_TIMEOUT_MS)

                            result = await adapter.scrape(
                                page=page,
                                sku_norm=task.sku_norm,
                                sku_raw=task.sku_raw,
                                config=self.config,
                                artifacts_dir=self.artifacts_dir,
                                log=tlog,
                            )
                            await browser.close()
                    except Exception as exc:
                        import traceback
                        result = ScrapeResult(
                            status=TaskStatus.PARSE_ERROR,
                            error_message=f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}",
                        )

                    elapsed_ms = (time.monotonic() - t0) * 1000
                    task.duration_ms = elapsed_ms
                    task.result = result
                    task.status = result.status
                    self.metrics.record_duration(elapsed_ms)

                    tlog.info(
                        f"Task done: status={result.status} offers={len(result.offers)}",
                        duration_ms=round(elapsed_ms, 1),
                        result_count=len(result.offers),
                    )

            # Decide whether to retry
            if result.status in (TaskStatus.COMPLETED, TaskStatus.NOT_FOUND,
                                  TaskStatus.BLOCKED_CAPTCHA, TaskStatus.LOGIN_REQUIRED,
                                  TaskStatus.REQUIRES_PHONE, TaskStatus.INVALID_SKU):
                # Terminal states — no retry
                break

            if result.status == TaskStatus.TIMEOUT and result.retry_after:
                delay = result.retry_after
            else:
                delay = _backoff(attempt, self.config.BACKOFF_BASE_SEC, self.config.BACKOFF_MAX_SEC)

            if attempt < max_attempts:
                tlog.warning(f"Retrying in {delay:.1f}s (status={result.status})")
                await asyncio.sleep(delay)

        # Update metrics counters
        s = task.status
        if s == TaskStatus.COMPLETED:
            self.metrics.tasks_success += 1
            self.metrics.offers_parsed_total += len(task.result.offers if task.result else [])
        elif s in (TaskStatus.BLOCKED_CAPTCHA,):
            self.metrics.tasks_blocked += 1
        elif s == TaskStatus.TIMEOUT:
            self.metrics.tasks_timeout += 1
        elif s == TaskStatus.NOT_FOUND:
            self.metrics.tasks_not_found += 1
        else:
            self.metrics.tasks_error += 1

        return task
