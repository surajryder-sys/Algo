---
name: feedback-consistency-over-net
description: "When comparing backtest options, user prioritises zero losing months + lowest drawdown over highest net profit"
metadata:
  node_type: memory
  type: feedback
  originSessionId: 55f6ad35-c61d-42ac-81be-09537ab7a106
  modified: 2026-09-30T11:33:14.927Z
---

When choosing between tested options, the user ranks by consistency first: zero losing months and lowest worst-drop, then net profit.

**Why:** v2.14 session TP (S2) was picked because it had 0 losing months and the lowest drop (7.0 %), which the user called "part of consistency" — it also happened to have the top net, but net was not the stated reason.

**How to apply:** In every test comparison table, always show losing months and worst drop next to net, and recommend the most consistent option, not just the highest net. See [[project-v6s-ict-ea]].
