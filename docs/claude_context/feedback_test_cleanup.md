---
name: feedback-test-cleanup
description: "Keep verification lean and DELETE all throwaway test/verification files once the work is finished, without fail; don't pile up many test runs or test code"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 1a28a4fc-a986-47a1-8ca7-10339f148773
  modified: 2026-09-19T21:42:00.790Z
---

User (2026-09-20, during TM-STR): "why have you created so many test cycles and
test related code -- you shouldn't forget to delete them without fail, once the
work is finished, only those test things."

**Why:** by the end of the TM-STR rules work I had run the suite dozens of times,
grown the saved suite from 53 to 95 tests, and left ~12 throwaway scripts (edit
scripts, mutation harness, live-check, time-base check, dry-run logs, fixtures)
in the session scratchpad. The user saw that as clutter and noise.

**How to apply:**
- Verify proportionately: a few targeted checks per change, not a test per
  sub-case and not a mutation harness for every small rule.
- Anything I create only to verify (scratch scripts, logs, fixture copies, /tmp
  output, __pycache__ from test runs) is deleted as soon as that piece of work
  is finished -- do it in the same turn, don't wait to be told. Only delete
  files I created; leave everything else alone.
- The repo `tests/` suite (saved at the user's request 2026-09-20) was DELETED by the user's
  decision the same day ("delete it"), still uncommitted at that point (git shows it as
  deleted; it exists in commit b809556). Do not recreate a tests/ folder or add test files to
  the repo unless the user asks for one.

See [[feedback-algo-trading-collab]] for the rest of how this user likes to work.
