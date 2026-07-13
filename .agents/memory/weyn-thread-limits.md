---
name: WEYN scanner thread limits
description: Why WEYN's start button could revert instantly, and the safe worker-count ceiling for this container
---

# Container thread/pid budget is much lower than `ulimit` reports

`weyn.py`'s scan methods spawn worker pools via `ThreadPoolExecutor`. Defaults
of 800-1000 threads per pool (up to ~1800 total across scanner+lookup pools)
raise `RuntimeError: can't start new thread` almost immediately. Since that
exception escapes the pool-creation/submit code inside a `try/finally`, it
hits `finally` and sets `_web_state['running'] = False` right away — which is
what makes the frontend's start button look like it instantly reverts to idle
without any user-visible error.

**Why:** `ulimit -a` inside this container reports huge limits (max user
processes ~31857) but the real ceiling is much lower — measured empirically
around 600-1000 total concurrently-created OS threads across pools in one
process before `can't start new thread` fires. `ulimit` does not reflect the
actual (likely cgroup pids) constraint here.

**How to apply:** Keep total simultaneous worker threads (summed across all
pools created by one scan) well under ~300-400 as a safe margin (confirmed
600 total works, 1000 total reliably fails). Current safe defaults in
`weyn.py`: M1 scanner=150 + lookup=150, M2=150, M3=150. If someone bumps these
via `M1_SCANNER_WORKERS`/`M1_LOOKUP_WORKERS`/`M2_WORKERS`/`M3_WORKERS` env
vars, warn them of this ceiling. Any future thread-pool code in this project
should also guard `pool.submit()` calls against `RuntimeError` instead of
letting it silently kill the run.
