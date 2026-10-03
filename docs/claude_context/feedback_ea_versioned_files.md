---
name: feedback_ea_versioned_files
description: Every EA change = a NEW file with the version at the end of the filename (e.g. V6S_ICT_EA_v1.51.mq5); never overwrite the previous version
metadata:
  node_type: memory
  type: feedback
  originSessionId: 55f6ad35-c61d-42ac-81be-09537ab7a106
  modified: 2026-10-03T17:22:15.733Z
---

When changing an MQL5 EA, create a new file named `<EA>_v<version>.mq5` (for example `V6S_ICT_EA_v1.51.mq5`), bump `#property version` to match, and install and compile it next to the older ones in the terminals. Previous versions stay untouched.

**Why:** the user asked for this on 2026-09-27 ("add a new ea file everytime with version in the end of the filename, to understand"). They backtest versions side by side in MT5 and need to know which build produced which result.

**How to apply:** it applies to V6S-ICT ([[project_v6s_ict_ea]]) and any future EA. The first versioned file is `v6s_ict/V6S_ICT_EA_v1.50.mq5`. Mention the new filename in every reply that changes an EA.

**Exception (2026-10-03):** pure BUG FIXES that don't change tester results go into the SAME version file (user: "dont change versions ... same version, apply changes" for the V6S_ICT_OB_3.25 / v5.00 restart-safety fix). New versions are for strategy/logic changes. Add a dated "Fix" note in the file header instead.
