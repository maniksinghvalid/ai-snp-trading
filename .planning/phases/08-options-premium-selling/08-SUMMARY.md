---
phase: 08
name: Options Premium Selling (tasty_credit_spreads)
status: built — live paper UAT pending (operator)
date: 2026-08-17
design: ~/.claude/plans/scrape-highly-rated-options-velvety-naur.md
research: docs/research/2026-08-17-tastylive-options-research.md
---

# Phase 8 — Options Premium Selling (`tasty_credit_spreads`)

## Why
Trend Join Long was validated as no-edge on 2026-08-13 (226 trades, −0.019R/trade, full S&P universe). The operator asked for a successor built from the highest-rated tastytrade/tastylive research content, and for the bot to be able to trade options.

## What was researched
Apify `streamers/youtube-scraper`, two runs (`puFl7fdbFVl42goCA`: 30 most-popular @tastyliveshow videos; `BMH0l5geevP3z3fwU`: top-viewed strategy/strangle-45DTE/IVR searches — 16 *Market Measures* segments + 2 critical outside reviews), full auto-transcripts distilled to `docs/research/2026-08-17-tastylive-options-research.md`. Consensus mechanics → config keys (every number is in `rules_options.json`).

## What was built (all on `develop`)
| Wave | Commits | Content |
|---|---|---|
| 1 | 14d9ab5 b5f1c85 10aef4e 47a47dd | migration 0006 (`option_positions`, `option_legs`); `bot/options/{schema,config,strategy}.py` (pure core); `rules_options.json`; liquidity abs floor |
| 2–3 | f5c6612 ac614eb 7624ea9 | `MoomooGateway.get_stock_ids/screen_options/get_option_positions`; `OptionsStore(StateStore)`; `LegExecutor` (fill_leg / open_position / close_legs) |
| 4 | ce3edb7 bb059f1 96ad6ac e34e252 35194ed | `OptionsBot` (entry scan 10:00/14:30, manage every 5 min RTH, EOD 16:10; reconcile; breaker; alerts; HTML report); `bot/main.py` `strategy_name` dispatch; `python -m bot --rules` |
| 5 | ad41cd5 + docs | `scripts/uat_options_probe.py`; live read-only UAT fixes (below); ROADMAP/README/CLAUDE.md |

Tests: 731 → **961 passed / 1 skipped**.

## Live read-only UAT findings (2026-08-17 ~01:30 ET, market closed — stale Friday quotes)
Verified against OpenD 10.07.6708 / paper acc 1727266 (`STOCK_AND_OPTION`):
- Option screen returns per-underlying `iv_rank`/`iv_percentile`/`change_ratio` as **fractions** → ×100 once in service (`_ivr_pct/_ivp_pct/_change_pct`).
- Screen `strike_date` is `"N/A"` unless `STRIKE_DATE_TIMESTAMP` is retrieved → expiry now derived from the option code (`US.XLE260918P50000` → 2026-09-18).
- SDK returns literal `"N/A"` for bid/ask of untraded strikes → `_as_float` fails closed (0.0).
- `position_list_query`: SHORT legs come back with **negative qty** and `position_side="SHORT"`; `get_option_positions()` → `{-2, +1, -1}` correct.
- `pick_expiry` originally chose Wed 2026-09-30 (dte 44) over the monthlies; its wings had OI 41–74 → `prefer_monthly` now restricts to monthlies inside [min,max] DTE.
- 1% wings on cheap ETFs (XLE $62 → one $0.50 strike → 34-lot condors) → `min_wing_width_usd: 2.0`.
- Defaults retuned on structural grounds: `short_delta 0.20`, `min_credit_to_width 0.25` (⅓-width is tastylive's *vertical* rule; a 16–20Δ condor rarely reaches it).
- Regime today: SPY IVR 6.6, QQQ 22.9, IWM 1.3, GLD 27, TLT 25, **XLE 61** → only XLE passes the IVR gate; SPY rejected at 18% credit/width; TLT would build a 6-lot IC at 25% but fails IVR. "No trade" is the default and correct.

## Operator run-book
```bash
# read-only probe (run during RTH first — weekend quotes are stale/wide)
set -a; source .env; set +a; PAPER_TRADING=true FUTU_TRD_ENV=SIMULATE python3 scripts/uat_options_probe.py
# 1-lot paper round trip (places + closes ONE SPY put credit spread; proves SIMULATE fill model)
PAPER_TRADING=true FUTU_TRD_ENV=SIMULATE python3 scripts/uat_options_probe.py --live-1lot --symbol US.SPY --confirm
# run the options bot (own DB data/options_state.db, kill file .bot_kill_options, reports/options/)
PAPER_TRADING=true FUTU_TRD_ENV=SIMULATE FUTU_ACC_ID=1727266 python3 -m bot --rules rules_options.json
```
Only ONE options-bot instance at a time; it may run alongside the equity bot (equity reconcile ignores option legs; options bot ignores anything not in its `option_legs`).

## Open items
- [ ] RTH read-only probe: confirm liquidity gates (`max_spread_pct_of_mid 5`, `max_spread_abs_usd 0.05`, `min_open_interest 500`) don't reject genuinely liquid monthly wings on live quotes; adjust in `rules_options.json` if they do (evidence-based, not for excitement).
- [ ] `--live-1lot` paper round trip (SIMULATE option limit fill model unverified).
- [ ] Phase 9: options backtester (Massive option daily aggregates — entitlement verified 200; chain snapshot 403).
- Known limits: no rolling; restart mid-open → NEEDS_ATTENTION (manual close); human's same-code contracts on the shared account → reconcile mismatch alert.
