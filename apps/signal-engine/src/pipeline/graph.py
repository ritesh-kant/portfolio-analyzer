"""LangGraph StateGraph — 9-node pipeline with parallel analysis and guard conditional.

Graph topology:
  news_agent → sector_agent → stock_selector → parallel_analysis (technical + market concurrently)
  → guard_agent → [any_passed] → signal_agent → order_agent → audit_agent → END
                → [all_blocked] → audit_agent → END
"""

import asyncio
import logging
import time
from datetime import date as _date
from typing import Any

from langgraph.graph import StateGraph, END

_graph_logger = logging.getLogger(__name__)

from .state import TradingState
from .agents import (
    news_agent,
    sector_agent,
    stock_selector,
    technical_agent,
    market_agent,
    guard_agent,
    signal_agent,
    order_agent,
    audit_agent,
)
from ..providers.llm_factory import get_llm


def _after_guard(state: TradingState) -> str:
    passed = state.guard_result.get("passed", [])
    return "signal_agent" if passed else "audit_agent"


async def _parallel_analysis(state: TradingState) -> TradingState:
    """Run technical_agent and market_agent concurrently, then merge results.

    Calls agent.run() (not _execute) so each agent writes its own logs and
    handles internal errors via the BaseAgent error-safe wrapper.
    """
    start = time.monotonic()

    # BaseAgent.run never raises — exceptions are caught and appended to state.errors
    tech_result, market_result = await asyncio.gather(
        technical_agent.run(state),
        market_agent.run(state),
    )

    timings = {**state.agent_timings, **tech_result.agent_timings, **market_result.agent_timings}
    errors = list(state.errors)
    for e in tech_result.errors + market_result.errors:
        if e not in errors:
            errors.append(e)

    timings["parallel_analysis"] = (time.monotonic() - start) * 1000

    return state.model_copy(update={
        "technical_data": tech_result.technical_data,
        "market_data": market_result.market_data,
        "errors": errors,
        "agent_timings": timings,
    })


def build_graph() -> Any:
    graph = StateGraph(TradingState)

    for agent in (news_agent, sector_agent, stock_selector, guard_agent, signal_agent, order_agent, audit_agent):
        graph.add_node(agent.name, agent.run)

    # technical_agent and market_agent run concurrently inside one node
    graph.add_node("parallel_analysis", _parallel_analysis)

    graph.set_entry_point("news_agent")
    graph.add_edge("news_agent", "sector_agent")
    graph.add_edge("sector_agent", "stock_selector")
    graph.add_edge("stock_selector", "parallel_analysis")
    graph.add_edge("parallel_analysis", "guard_agent")
    graph.add_conditional_edges("guard_agent", _after_guard, {
        "signal_agent": "signal_agent",
        "audit_agent": "audit_agent",
    })
    graph.add_edge("signal_agent", "order_agent")
    graph.add_edge("order_agent", "audit_agent")
    graph.add_edge("audit_agent", END)

    return graph.compile()


_compiled_graph: Any = None


def _get_graph() -> Any:
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = build_graph()
    return _compiled_graph


# NSE trading holidays — update annually from nseindia.com/resources/exchange-communication-holidays
_NSE_HOLIDAYS: frozenset[str] = frozenset({
    # 2025
    "2025-01-14", "2025-01-26", "2025-02-19", "2025-03-14", "2025-03-31",
    "2025-04-10", "2025-04-14", "2025-04-18", "2025-05-01", "2025-08-15",
    "2025-08-27", "2025-10-02", "2025-10-21", "2025-10-22", "2025-11-05",
    "2025-12-25",
    # 2026 — verify against NSE circular before each year begins
    "2026-01-26",  # Republic Day
    "2026-03-20",  # Holi
    "2026-04-03",  # Good Friday
    "2026-04-14",  # Dr. Ambedkar Jayanti
    "2026-05-01",  # Maharashtra Day
    "2026-08-15",  # Independence Day
    "2026-08-25",  # Ganesh Chaturthi
    "2026-10-02",  # Gandhi Jayanti / Dussehra
    "2026-10-20",  # Diwali (Laxmi Puja)
    "2026-11-24",  # Gurunanak Jayanti
    "2026-12-25",  # Christmas
})


def _warn_if_holidays_stale() -> None:
    """Log a warning if the current year has fewer than 5 holidays listed."""
    current_year = str(_date.today().year)
    count = sum(1 for d in _NSE_HOLIDAYS if d.startswith(current_year))
    if count < 5:
        _graph_logger.warning(
            "nse_holidays_stale: only %d holidays found for %s — "
            "update _NSE_HOLIDAYS from nseindia.com/resources/exchange-communication-holidays",
            count, current_year,
        )


_warn_if_holidays_stale()


async def run_pipeline(run_id: str, date: str, ai_provider: str) -> TradingState:
    """Execute the full pipeline and return the final TradingState."""
    if date in _NSE_HOLIDAYS:
        import logging
        logging.getLogger(__name__).info(
            "pipeline_skipped run_id=%s reason=nse_holiday date=%s", run_id, date
        )
        from ..db.client import get_db
        from ..db.repositories.pipeline_runs import PipelineRunsRepository
        repo = PipelineRunsRepository(get_db())
        await repo.finalize(run_id, "skipped", {"reason": "nse_holiday", "date": date}, None)
        return TradingState(run_id=run_id, date=date, ai_provider=ai_provider)

    llm = get_llm(provider=ai_provider)

    initial_state = TradingState(
        run_id=run_id,
        date=date,
        ai_provider=ai_provider,
        llm=llm,
    )

    graph: Any = _get_graph()
    final_state: TradingState = await graph.ainvoke(initial_state)
    return final_state

