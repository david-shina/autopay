"""LangGraph state graph build.

Currently a single-node graph (the rule is the only decision). Future
expansion: an LLM sanity-check before/after the rule.
"""
from __future__ import annotations

from decimal import Decimal

from langgraph.graph import END, StateGraph

from app.agents.nodes import make_decision_node
from app.agents.state import AgentState, Decision, DecisionResult


def build_graph():
    """Build the LangGraph state graph."""
    g = StateGraph(AgentState)
    g.add_node("decide", make_decision_node)
    g.set_entry_point("decide")
    g.add_edge("decide", END)
    return g.compile()


def run_agent(
    *,
    user_balance: Decimal,
    bill_amount: Decimal,
    fee: Decimal,
    days_until_due: int,
) -> DecisionResult:
    """Invoke the graph. Equivalent to calling `decide()` directly —
    exists so callers can be graph-aware without rewriting later."""
    graph = build_graph()
    out = graph.invoke(
        {
            "user_balance": str(user_balance),
            "bill_amount": str(bill_amount),
            "fee": str(fee),
            "days_until_due": days_until_due,
        }
    )
    return DecisionResult(
        decision=Decision(out["decision"]),
        reason=out["reason"],
    )
