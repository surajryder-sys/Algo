---
name: feedback-algo-trading-collab
description: "How this user likes to collaborate on the Algo trading bot project - verification rigor, GUI boundaries, discuss-before-build"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: d3eb867f-7c44-41c8-a89e-659f140c8e60
  modified: 2026-08-15T17:04:52.823Z
---

For the OB MTF trading bot project ([[project-ob-mtf-bot-strategy]]):

- User wants trading strategy design fully discussed and explicitly confirmed, rule by rule, before any of it becomes executable code - especially anything that could place real orders. When told "let's discuss more before we implement," that means stop proposing code and keep working through open decision points conversationally instead.
- User values genuine critique, not just validation - when asked "what are your thoughts," give an honest assessment of gaps/risks rather than agreement. They engaged constructively with pushback (accepted the whipsaw-gap and staleness-check concerns as real open items rather than dismissing them).
- User cares a lot about correctness/no-mistakes in data pulled from the bridge - proactively verify (automated sanity checks, cross-checking numbers against real chart screenshots) rather than just asserting a pipeline works because it ran without error.
- Never perform MT5 terminal GUI actions (attaching indicators, changing chart/indicator properties, clicking AutoTrading) - user does all GUI steps themselves every time. Compile and hand off with clear instructions instead; user reports back when done, then verify via a script.
- User is comfortable giving broad file/directory access once a task requires it, despite an early "don't access files unless I say" instruction - that rule was about unprompted exploration, not about work they've explicitly asked for since.
- Claude never runs the actual live-trading process itself (`python -m ob_mtf_bot.reversal_trader`), even when the user explicitly says "I authorize" - executing financial trades isn't something authorization lifts, even against a demo account, since it's a real call to the broker's order-placement API. Prepare/verify everything, then hand the exact run command to the user to execute themselves. This held firm under direct pushback once already.
- When something looks wrong in a live trade (unexpected direction, odd SL, a big loss), the user often already suspects the real mechanism and is testing/confirming understanding by asking pointed questions ("why did we fire short?", "are we not using candle close logic?") rather than genuinely not knowing - verify against real MT5/log data and follow their diagnostic instinct seriously rather than reassuring or deflecting.
- User is fine with periodic autonomous monitoring (used `/loop` for a 20-min live-status check) but explicitly wants it stopped rather than left running when no longer needed, and does not by default want a durable cross-session replacement (`/schedule`) set up without being asked - confirm before assuming they want monitoring to persist.
- When designing a big/complex strategy change, user wants ONE question asked at a time, not a batched list - "this is too much to read and too much to answer all at once, ask me step by step." They answer with concrete worked examples (specific prices, specific zone sequences) rather than abstract rules - restate each answer back in plain terms with a concrete example to confirm understanding before moving to the next question, and only ask the next question once the current one is actually resolved (don't stack unresolved questions).
- User wants explicit confirmation before EVERY code change, not just once at the start of a task - "let me know before you make any changes, ask me, dont do without my consent." Said this after a long stretch (tv_scraper/scraper.py, retest_tracker.py, first_seen_store.py, zone_store.py, the OBD_SecretTrader.pine indicator) where an initial "yes go ahead" on one diagnosed bug got treated as standing license to keep finding-and-fixing related bugs across several follow-up turns without re-asking each time. Going forward: propose the fix and wait for a fresh yes/confirmation before editing, every single time, even mid-investigation and even when the previous fix was just approved.

**Why**: this project involves live order-placing automation against a real (currently demo) MT5 account, so getting the collaboration process right - rigor, explicit confirmation, no unauthorized GUI/order actions - matters as much as the code itself.

**How to apply**: carry this working style into any future session touching `C:\Users\ARK\projects\Algo` - don't skip the discuss-first pattern on strategy questions, don't touch the MT5 GUI, and verify data claims before presenting them as fact.
