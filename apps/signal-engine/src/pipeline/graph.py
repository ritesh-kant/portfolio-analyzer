"""LangGraph StateGraph — 9-node pipeline with parallel analysis and guard conditional.

Graph topology:
  news_agent → sector_agent → stock_selector → parallel_analysis (technical + market concurrently)
  → guard_agent → [any_passed] → signal_agent → order_agent → audit_agent → END
                → [all_blocked] → audit_agent → END
"""

import asyncio
import time

from langgraph.graph import StateGraph, END

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
from ..db.client import get_db
from ..db.repositories.pipeline_runs import PipelineRunsRepository


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


def build_graph() -> StateGraph:
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


_compiled_graph = None


def _get_graph():
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = build_graph()
    return _compiled_graph


async def run_pipeline(run_id: str, date: str, ai_provider: str) -> TradingState:
    """Execute the full pipeline and return the final TradingState."""
    repo = PipelineRunsRepository(get_db())
    llm = get_llm(provider=ai_provider)

    initial_state = TradingState(
        run_id=run_id,
        date=date,
        ai_provider=ai_provider,
        llm=llm,
    )

    graph = _get_graph()
    final_state: TradingState = await graph.ainvoke(initial_state)
    return final_state

