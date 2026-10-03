---
name: feedback-other-chat-v6s-v2
description: "In V6S_ICT_3 work, don't look into V6S_ICT_EA_v2.x (non-tester) files/tester runs -- they belong to the user's other chat"
metadata:
  node_type: memory
  type: feedback
  originSessionId: c57e7cda-2d34-480b-aaf5-f6aaa335e66b
  modified: 2026-10-02T17:33:04.690Z
---

When working on V6S_ICT_3 (v6s_ict_3/), do not read, analyze or report on `V6S_ICT_EA_v2.21` (and the other
V6S_ICT_EA_v2.x copies / their Strategy Tester runs) -- user 2026-10-02: "dont look into this at all, it belongs to
other chat". A separate Claude chat owns V6S-ICT v2.x (e.g. it made v2.22).

**Why:** that work is owned and tracked in another session; mixing its runs into V6S_ICT_3 reports confuses both.
**How to apply:** when summarising tester logs or results for V6S_ICT_3, filter to `V6S_ICT_3\` runs only; porting
already-verified rules from v2.21 source happened once on explicit request -- ask before touching v2.x again.
Related: [[project-ob-detector-ea]].
