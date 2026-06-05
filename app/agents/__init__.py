"""LangGraph decision agent — should we pay, hold, or schedule a bill?

Three-state machine:

  start ──► make_decision ──► ( pay_now | schedule | hold )

The decision rule (from the MVP `app/agents/nodes.py:32-35`):

  if balance < (amount + fee)            → hold
  elif days_until_due <= 3               → pay_now
  else                                    → schedule

Layout:
  * `state.py`   — typed state, decision enum
  * `nodes.py`   — pure function `decide()` + LangGraph node
  * `graphs.py`  — the StateGraph build
"""
