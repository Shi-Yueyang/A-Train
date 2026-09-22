"""Pytest fixtures: the real application and a controllable TCP test ATP server.

Per the Phase 0 passing criteria, the fixture starts the complete application
(real ``SimulationCore``, FastAPI lifespan, ATP manager) and a controllable
TCP test ATP server, without mocking any production module.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest_asyncio

from a_train.domain.train import TrainConfig
from tests.support.app import running_app
from tests.support.atp_server import TestAtpServer

TEST_TRAIN = TrainConfig(
    train_id="TRAIN001",
    cab_ids=(1, 2),
    initial_active_cab=1,
    max_traction_accel=1.5,
    max_decel=2.0,
)


@pytest_asyncio.fixture
async def app_client() -> AsyncIterator:
    async with running_app([TEST_TRAIN]) as client:
        yield client


@pytest_asyncio.fixture
async def atp_server() -> AsyncIterator[TestAtpServer]:
    server = TestAtpServer()
    await server.start()
    try:
        yield server
    finally:
        await server.stop()
