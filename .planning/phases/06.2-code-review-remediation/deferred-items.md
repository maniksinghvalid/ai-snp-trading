# Deferred Items — Phase 06.2 Plan 02

Out-of-scope discoveries logged during execution (not fixed — outside the
current task's file/behavior boundary per the deviation-rules scope guard).

| Found during | Item | File | Reason deferred |
|---|---|---|---|
| Task 7 (finding 2.7) | `force_close` exit alert (line ~585) and `partial` profit exit alert (line ~833) in `bot/position/manager.py` both compute `exit_proxy = pos.avg_fill_price or entry`. `avg_fill_price` stores the **entry** fill price (D-06), so this is equivalent to `exit_proxy = entry`, producing R ≈ 0 in both alerts — the same class of bug finding 2.7 fixes in `_trigger_stop_out`. | `bot/position/manager.py:585,833` | Finding 2.7's scope (plan task 7 `<action>`) is explicitly `_trigger_stop_out` only ("~724 and ~747" in the original line numbering). Fixing the other two sites would require picking a different proxy per alert type (force_close: no natural "stop" reference since force_close exits at whatever the broker fills; partial: same, partial profit exit is a take-profit, not a stop) — a design decision beyond a mechanical fix, better suited to a follow-up finding/plan. |
