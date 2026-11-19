"""Entrypoint for the multi-user quantitative Deriv DCA worker container."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys

# Ensure `/app` (shared) and `/app/worker` (engine) are importable when run locally or in Docker
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(CURRENT_DIR)
for p in (ROOT_DIR, CURRENT_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

from engine.coordinator import WorkerCoordinator


def configure_logging() -> None:
    level_name = os.getenv("WORKER_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # Reduce noisy third-party transport logs
    logging.getLogger("websockets").setLevel(logging.WARNING)


async def run_worker() -> None:
    configure_logging()
    coordinator = WorkerCoordinator()
    loop = asyncio.get_running_loop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, coordinator.request_shutdown)
        except NotImplementedError:
            # Windows event loops do not support add_signal_handler
            pass

    await coordinator.run_forever()


if __name__ == "__main__":
    try:
        asyncio.run(run_worker())
    except KeyboardInterrupt:
        pass
