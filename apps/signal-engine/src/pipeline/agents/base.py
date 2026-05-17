"""BaseAgent - error-safe wrapper pattern for every pipeline node."""

import time
from abc import ABC, abstractmethod
from typing import Any

import structlog

from src.db.client import get_db
from src.db.repositories.agent_logs import AgentLogsRepository
from src.db.repositories.pipeline_runs import PipelineRunsRepository

from ..state import TradingState

logger = structlog.get_logger()


class BaseAgent(ABC):
    name: str = "base"

    async def run(self, state: TradingState) -> TradingState:
        start = time.monotonic()
        log = logger.bind(agent=self.name, run_id=state.run_id)

        await self._update_agent_status(state.run_id, "running")

        try:
            result = await self._execute(state)
            duration_ms = (time.monotonic() - start) * 1000
            result.agent_timings[self.name] = duration_ms

            await self._update_agent_status(state.run_id, "completed")
            await self._write_log(state.run_id, "info", "completed", duration_ms)
            log.info("agent_completed", duration_ms=round(duration_ms, 1))
            return result

        except Exception as exc:
            duration_ms = (time.monotonic() - start) * 1000
            state.agent_timings[self.name] = duration_ms
            state.errors.append(f"{self.name}: {exc!s}")

            await self._update_agent_status(state.run_id, "failed")
            await self._write_log(
                state.run_id, "error", str(exc), duration_ms, {"exception": repr(exc)}
            )
            log.error("agent_failed", error=str(exc), duration_ms=round(duration_ms, 1))
            return state

    async def _update_agent_status(self, run_id: str, status: str) -> None:
        try:
            repo = PipelineRunsRepository(get_db())
            await repo.update_agent_status(run_id, self.name, status)
        except Exception as exc:
            logger.warning("failed_to_update_agent_status", agent=self.name, error=str(exc))

    @abstractmethod
    async def _execute(self, state: TradingState) -> TradingState:
        ...

    async def _write_log(
        self,
        run_id: str,
        level: str,
        message: str,
        duration_ms: float | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        try:
            repo = AgentLogsRepository(get_db())
            await repo.log(run_id, self.name, level, message, duration_ms, context)
        except Exception as exc:
            logger.warning("failed_to_write_agent_log", agent=self.name, error=str(exc))
