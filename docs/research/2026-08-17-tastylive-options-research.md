# tastylive / tastytrade options research — 2026-08-17

Source: Apify `streamers/youtube-scraper` runs `puFl7fdbFVl42goCA` (30 most-popular @tastyliveshow videos, dataset Wwbh5ha14817vjkyx) and `BMH0l5geevP3z3fwU` (search: tastytrade options strategy / strangle 45 DTE manage 21 / iron condor IV rank, dataset ed2FrL1sSgWWAXwlf). Auto-transcripts distilled by two analyst agents. Strategy spec derived from this lives in the Phase 8 plan and `rules_options.json`.

---

# Part A — most-popular channel videos

# tastylive Top-30 — distilled mechanical trading rules (batch A)

Source: Apify dataset Wwbh5ha14817vjkyx (auto-transcripts). Notes are paraphrased; numbers as stated by speakers.

### How He Trades Full Time with ONE Strategy (217,893 views, 2,900 likes) https://www.youtube.com/watch?v=9TEN6Q2BzGc
- Strategy: ONLY short strangles to open (never bought an option to open). Product-indifferent — any liquid, volatile underlying (futures/commodities/ETFs/stocks), aims for uncorrelated mix.
- ENTRY: default 20-delta strangle; goes to 30-35 delta when more aggressive (after a "volatility pop"). Duration ~35-40 DTE normally; extended to ~60 DTE when IV is low everywhere. Enters full size at once (no legging in); may leg out.
- MANAGEMENT: no action until a break-even is breached, then roll up the untested side. Also roll untested side earlier when it decays to ~5 delta (bring it back to ~10-12 delta) — a 5-delta leg is dead capital, not protection. Profit target: sell for $2 → close ~$1 (~50% of credit), evaluated on annualized return-on-capital. Half the time exits ~15-20 DTE, otherwise holds to 5-10 DTE. Losers: roll out ~30 days in the last 5-10 DTE and keep rolling for as long as it takes; biggest regret = NOT rolling out (cattle blow-up 2017 hit max pain and closed).
- SIZING: 10-20 positions at once (40 too many). Buying power usage averages ~50% of capital (0-100% range). Max instantaneous portfolio risk ~3%; if that feels like more than you can stomach mentally (e.g. 1%), you're too big. Position size = the single most important success factor.
- STATS quoted: 2016 (Apr-Dec) +72%; 2017 +17% after a cattle drawdown; lost ~25% of account in 2017 to directional futures scalping ("directional hero") — cut scalping to ~5% of activity.
- WARNINGS: undefined risk from day one; every position becomes directional by day 5; commodity blowups (cattle); don't be a directional hero.
- Quotable: "If your heart starts beating faster and you're hoping — you're trading too big."

### These 4 Lessons Allowed Me to Stop Trading Directionally (207,372 views, 4,000 likes) https://www.youtube.com/watch?v=Zy_046YjuvQ
- Strategy: framework video (Jim Schultz crash course) — trade volatility/time first, direction last. Structures shown: short call spread, short strangle (strikes just outside the expected move), short put spread, naked short put; low-IV alternatives: ATM long put vertical, calendar spread.
- ENTRY: use liquid pre-screened watchlist (tasty default) sorted by IV Rank high→low; IVR ≥ 30 = reference line for selling premium (traders range 20-25 up to 35-40); IVR < 30 → mix in debit strategies (long verticals, calendars, diagonals). Example cycle ~41 DTE. Strangle strikes anchored at the expected move (~16 delta). Undefined risk (naked put/strangle) carries full short Vega; spreads water it down (net −2/−3 Vega).
- MANAGEMENT: not covered in depth (references P50 metric ~77% on short put spread; higher P50 for undefined risk).
- SIZING: n/a
- STATS: Vol regimes: expansion 10% / contraction 20% / lull 70% of days → ~90% of days favor short vol. VIX study (2000+): random day down 53/47; after consecutive VIX up-days next-day probabilities shift to ~60/40 in favor of contraction (mean reversion strongest after spikes). Aug-2023 study: 16-delta strangles, IVR<30 avg $0.25/day vs IVR>30 avg $1.15/day (>3x) with essentially the same win rate; profitable in both regimes.
- WARNINGS: undefined risk hurts more on the downside; earnings inside the cycle; IVR outside 0-100 means IV is beyond the 52-wk range.
- Quotable: "Trade volatility first, not direction."

### How do we choose our trading strategy? (222,065 views, 3,100 likes) https://www.youtube.com/watch?v=MOxQqT_s-Eg
- Strategy: Tom/Tony market-measures segment on strategy selection by IV environment. High IV → short strangles, iron condors, credit (short) verticals, covered calls, naked puts. Low IV → diagonals, pairs trades, bearish directional debit spreads (ITM put debit spread, one strike in / one strike out), VIX call spreads as near-long-vol hedge.
- ENTRY: IV is the primary factor. Compare stock IV to market IV (e.g. market 25% vs stock 40% → sell the stock's premium). Prefer indexes/liquid ETFs (SPY) when their IV is rich (2008: SPY IV ~60%, IVP 71 → trade SPY, not single names); use single stocks only when index IV is thin. Avoid illiquid/inefficiently priced names (ISRG) and names with no edge (MSFT). High IV → wider/further-OTM strikes for all structures. Only take trades where ROC is meaningful (won't sell a put for 1.5% ROC).
- MANAGEMENT: n/a (segment is selection-focused).
- SIZING: smaller in single names (TSLA/CMG) than SPY-type ETFs; portfolio-weighted POP target 60-70% (65-75 in richer IV).
- STATS: none beyond regime examples.
- WARNINGS: single stocks are binary/undiversified (earnings); low IV → protect against sudden IV expansion; pairs trades give no statistical edge; even "good" earnings picks contain luck.
- Quotable: "High probability of profit trades while maximizing return on capital — for the trade AND the portfolio."

### How to Hedge Your Positions | Options Trading Concepts (192,117 views, 1,800 likes) https://www.youtube.com/watch?v=1iYQ0XOLHNU
- Strategy: beginner whiteboard (Mike) — hedging = covered calls, delta hedging, cross-correlated hedges (short call spread / short call in QQQ or SPY against long AAPL; long put spread in low IV; short 15-20 shares of SPY as small notional hedge).
- ENTRY: covered call example: long 100 sh @50, sell 55 call for $1.50; 30-delta short call = under-hedge (net +70 delta); "perfect hedge" = 200 sh + 4 ATM (~50-delta) calls; over-hedge = 100 sh + 3 ATM calls (net −50). Sell short call spreads in high IV, long put spreads in low IV.
- MANAGEMENT: hedge is dynamic — the short call's delta shrinks as stock falls (30 → 15 delta → net +85), so re-hedge as underlying moves.
- SIZING: each short call must map to 100 shares — extra contracts are naked calls.
- STATS: none.
- WARNINGS: over-hedging is rare — usually just exit; extra short calls create undefined upside risk.
- Quotable: "Deploying opposite strategies in correlated underlyings is the go-to hedge."

### Don't Make These Beginner Options Mistakes (333,783 views, 4,600 likes) https://www.youtube.com/watch?v=g_SYzdK6g0k
- Strategy: "trade traps" segment for small ("tastyBite", $2.5k-$25k) accounts — general premium-selling mechanics rather than one structure.
- ENTRY: sell enough premium (don't sell in too-low IV, don't sit too close to ATM); avoid weeklies with little premium — need duration to be right ("duration over direction"); go longer duration only when IV is very high (lock up expensive premium); be aware of earnings/dividends (theta decay flattens ~2 weeks before earnings → close before that or you lose ROC).
- MANAGEMENT: manage winners — don't let winners become losers; exit when the assumption changes; don't wait until forced ("take the money when you can, not when you have to"); avoid exiting into illiquidity.
- SIZING: #1 trap = not enough occurrences — target minimum 20-30 trades/month even in a small account; consistent size — never get big on one "too good" trade (one oversized loss negates many small winners); diversify strategies (don't repeat what worked last time).
- STATS: none.
- WARNINGS: retail has no house edge → no luxury of being wrong; small-account mistakes are magnified; unplannable gap risk (fertilizer names −30%).
- Quotable: "The number-one trap is not creating enough occurrences."

### Trading Options In A Small Account | Options Trading Strategies (171,638 views, 1,800 likes) https://www.youtube.com/watch?v=IQ4LcLHU1sA
- Strategy: Mike's $10k follow-page account — naked puts in low-priced underlyings ($5-6 stocks e.g. GPRO/RIG/CHK), short vertical spreads (3- or 5-wide for ~$1-1.50 credit in AAPL/AMZN/GOOG, max loss ~$200-300), diagonals / poor-man's covered call/put (directional, benefits from IV expansion in low IV). Pick 1-3 strategies and master them.
- ENTRY: defined risk to keep BPR small; naked puts only where notional is small (5-strike put for $0.20-0.30 → max loss ~$470/contract even at zero).
- MANAGEMENT: stop loss at 2x credit received (net loss = 2x credit → buy back at 3x gross: $2 credit → close at $6 debit), place as resting order; roll for credit is far easier on naked options than on defined-risk spreads (spread only rolls for credit when price sits near short strike close to expiry); redeploy BP to higher-probability trades instead of babysitting losers.
- SIZING: 1-2% of net liq per trade in BPR (max 5%): $100k → ≤$5k BPR; $10k → ~$500 BPR per trade.
- STATS: GPRO short 30 put (stock 33 → 13-14) → ~$1,400-1,500 loss, ~10% of account, rolled 2-3x collecting ~$500 extrinsic; a 2x-credit stop would have saved ~$1,200-1,300.
- WARNINGS: small accounts can't hold naked losers "until right"; one oversized position can paralyze the account.
- Quotable: "Trade small, stay small — 1-5% buying power per trade."

### Trader Makes 50% Returns Using Vertical Spreads & Iron Condors (361,252 views, 5,800 likes) https://www.youtube.com/watch?v=Cm2gkiT5bV8
- Strategy: Rising-star Shandra ($250k portfolio-margin acct) — 6-7 strategies chosen by market position: market far from mean → put spreads (short verticals); market mid-range → iron condors; occasional strangles/straddles; iron flies for earnings; PMCC/covered calls. Bulk of trades in SPX and SPY.
- ENTRY: started with 5-10 DTE far-OTM "junk" spreads for $0.05-0.15 → abandoned; now enters at ~45 DTE; gets more aggressive when IV is high ("don't get scared, premium is high"). Written trading plan/workflow: first check "is theta ≈ 1/100 of net liq?" (i.e. portfolio theta ~0.1% of NLV per day) — if yes, don't touch anything; if less, add trades.
- MANAGEMENT: close at 50% of max profit; losers: never close before at least 2 rolls (roll out for duration); many put spreads close next day when the market moves.
- SIZING: 2015 ~950 closing trades (~2,000-2,500 total incl. rolls); 2016 pace ~300 closes in 2 months. Was told size too big (10-lot SPX on $250k) → cut size; "one step away from wiping out the account" mindset.
- STATS: 2015 +49.5% (~$125k on $250k); got burnt on oil (USO) Oct-Nov 2015; ~15 prior years flat.
- WARNINGS: continued one-way moves are the hardest; don't set $ goals (greed); keep fixed costs low before going full-time.
- Quotable: "Enter at 45 days, close at 50%, don't get big, see where IV is."

### Former Engineer/Lawyer Becomes Full Time Trader with 3 Simple Criteria (176,175 views, 3,300 likes) https://www.youtube.com/watch?v=5cu3PTHSDOM
- Strategy: Les (~$500k acct, Austin) — 3 phenomena / 5 strategies: (1) earnings IV crush → 1-SD short strangles (smaller names) or iron condors (larger names); (2) plain theta decay (short premium in 5 ETFs only: SPY, QQQ, IWM, TLT, GLD); (3) overbought/oversold mean reversion → bull put / bear call spread fading big post-earnings gaps (only time he's directional). Almost 100% options; puts assigned → covered calls. Delta neutral overall.
- ENTRY: earnings: watchlist of ~240 names, ~250 earnings trades/yr, ~900 trades/yr total (3-4/day); shorts at 1 standard deviation; enter at 2:00pm CT (top of the last hour) the day before; exit ~5 min after next open (9:40) over 10-15 min — never let them linger. Trades only first 45-60 min and last hour; alerts otherwise; ~2-3 hrs/day.
- MANAGEMENT: winners: 50% of credit OR 21 DTE, whichever first (made "quite a lot of difference"); earnings losers (~25% of trades): half small → eat them; half with mean-reversion history → roll. Written cheat-sheet/flowchart; tweaks only tested via backtest/paper first.
- SIZING: 20% cash always; up to 80% BP used when defined risk, less when undefined (margin fluctuates); consistent size and occurrence count for 3 years. Prior blow-up: 500 contracts of $1-wide SPY credit spreads (SEC large-trader threshold) with no exit plan.
- STATS: ~55% aggregate over 3 years on ~$500k; drawdowns "not bad" since going small/often.
- WARNINGS: liquidity — getting in is easy, getting out isn't; overconfidence after wins leads to size creep; need a plan B for blow-ups.
- Quotable: "50% of credit or 21 DTE — clear the boards and redeploy at higher vol with more time."

### Gamma Explained: What is it & How to Trade it (216,341 views, 4,400 likes) https://www.youtube.com/watch?v=t2ty3MuQ68Q
- Strategy: beginner Greeks explainer (Mike) — mechanical takeaway only.
- ENTRY: prefer ~45 DTE — ATM gamma ~0.05 (delta 50 → 55 on $1 move) vs 2 DTE gamma ~0.25 (delta 50 → 75; option 0.65 → 1.15 → ~1.90 on a $2 move, tripling; short seller is a loser instantly).
- MANAGEMENT: gamma risk grows into expiration week → roll out to ~45 DTE before expiration week to reset to a stable delta.
- SIZING: n/a. STATS: none.
- WARNINGS: expiration-week short options flip winner/loser on random intraday noise.
- Quotable: "Gamma is the stability or instability of an option's probability."

### Basic Option Trading: Verticals (181,194 views, 2,400 likes) https://www.youtube.com/watch?v=HaoM4nqxYhU
- Strategy: tastyBites (<$25k) segment on vertical spreads as stock replacement; verticals remain the largest share of Tom/Tony's option trades. Credit spreads when IV is rich; debit spreads when IV is cheap or directionally inclined.
- ENTRY: credit vertical — collect ≥ 1/3 the width of the spread ($0.33 on $1-wide; $1.67-1.75 on $5-wide; example IBM $5-wide sold $1.74, closed $1.00); widen strikes so the trade makes transactional sense. Debit vertical — ATM, one strike ITM / one strike OTM, pays ~50% of width in any stock (e.g. AAPL $5-wide ≈ $2.40-2.60) → cheap defined-risk directional exposure. Naked options in a small account only in low-priced, high-IV, very liquid names.
- MANAGEMENT: example took a $1.74 credit spread off at $1.00 (~43% of credit).
- SIZING: verticals equalize a $500 stock and a $20 stock; delta-equivalent stock replacement using ~1/4 the capital.
- STATS: none.
- WARNINGS: don't fall in love with one strategy — diversify strategies AND products; debit spreads don't profit tick-for-tick; transaction costs on tiny credits.
- Quotable: "One strike in, one strike out costs 50% of the width — in any stock."

### Successful Option Trader reveals his Trading Strategies with Tom Sosnoff (207,910 views, 1,200 likes) https://www.youtube.com/watch?v=p64dqYZqpPY
- Strategy: Rob (ex-pilot, portfolio-margin accounts, trades with family) — naked options from the start; ratio spreads and jade lizards; trades AAPL/AMZN/GOOG/NFLX. Mostly anecdotal — few hard numbers.
- ENTRY: hunts quick "crisp" moves via a sorted quote/watchlist; act fast — more analysis = talked out or late ("late costs dimes, nickels, quarters"). Needs enough IV in the underlying to repair (WMT calendar couldn't be fixed; NFLX short 205 calls repaired in 3 days; AMZN strangle took 3 months to repair).
- MANAGEMENT: repair/roll rather than puke out losing undefined-risk trades ("warrior attitude"); always act on short puts before expiration week.
- SIZING: n/a. STATS: one down month since Oct 2011 (small drawdown while ill); first trade 1987 OEX puts +500%.
- WARNINGS: low-IV names (WMT) can't be repaired; ownership — never trade off CNBC/analysts.
- Quotable: "The quicker I get to confirm-and-send, the better my trades."

### Options for Beginners [2026] (214,584 views, 5,000 likes) https://www.youtube.com/watch?v=89NHBLDTQyk
- Strategy: Jim Schultz beginner crash course (~108KB, skimmed via keyword search — mostly Greeks/expected-move/bid-ask basics). Structures shown: short put spread, iron condor (POP ~55% on DIA example), short put (GLD, +28 delta, ~$3k BPR), short strangle.
- ENTRY: sell premium in high-IVR names (IVR = IV percentile over prior 12 months); trade only the most liquid underlyings; expected move = price × IV × sqrt(DTE/365); core edge = IV consistently > realized vol.
- MANAGEMENT: winners: GTC order at ~50% of max profit; losers: predetermined exit at 2x or 3x credit received (pick one, stick with it); "manage early": close/roll at 21 DTE (theta more reliable in first half of the cycle, eliminates gamma risk).
- SIZING: defined risk 1-5% of net liq per position; undefined risk 3-10% of net liq per position (bigger accounts → low end of range).
- STATS: none beyond IV>RV claim.
- WARNINGS: undefined risk must absorb occasional 3-5 SD moves; bid-ask is a real cost — stick to liquid products.
- Quotable: "Manage winners, extend duration on losers, manage early at 21 DTE."

### What are Calendar Spread Strategies? (194,963 views, 2,600 likes) https://www.youtube.com/watch?v=BgP72h9V52I
- Strategy: beginner explainer — calendar spread (long back-month, short front-month, same strike; debit; pure IV-expansion play). Skimmed.
- ENTRY: use only when IV is very low (expecting mean-reverting expansion); example buy Apr 100 call $7 / sell Feb 100 call $2 = $5 debit = max loss; put vs call calendar priced ~the same — pick by which side you're comfortable being OTM.
- MANAGEMENT: max profit near strike at front expiry; both far up and far down are losses; IV change affects the long back-month more (that's the edge).
- SIZING/STATS: n/a. WARNINGS: cannot know max profit/breakeven precisely (back-month IV unknown).
- Quotable: "A calendar spread is an IV expansion play."

### How this Software Engineer Turned $2,500 into $200k with Options (227,221 views, 4,600 likes) https://www.youtube.com/watch?v=FhBXXzRrL84
- Strategy: Vishant — PRICE mean-reversion swing trades using ATM debit verticals (mostly bull call spreads; occasionally credit spreads); ETFs (SPY/QQQ/DIA/IWM) + ~200-stock watchlist (started with 20). Explicitly NOT the tasty playbook (Tom flags: debit spreads regardless of IV, directional, no rolling).
- ENTRY: premarket routine 6am MT: futures, VIX, index ETFs, ~15-16 sectors → find overextended sectors/stocks; enter on TA confirmation of reversal (higher highs/higher lows); all entries in the first 2 hours; ~30 DTE every trade; buy ATM $1-wide spread for ~$0.50 and immediately place a closing order at $0.97 (target ~94% ROI). ~700-1,000 trades/yr. Selection criteria: liquidity first, price second (10-item checklist); avoid earnings.
- MANAGEMENT: no rolling. Cut loser at 50% of debit paid; if by day ~14-15 the trade isn't working, close; you know within the first 10 days. Hedge: if futures gap deep red, "retrofit" the expiration with an equal number of bear put spreads (offsets, then reassess). After a 10% correction, wait for TA turn, then use reserves to add one new trade per apparent loser.
- SIZING: only 30% of portfolio deployed; 70% cash reserve always. Withdraws profits at target ($25k) and resets to $2.5k to protect against corrections.
- STATS: $2,500 → $25k repeatedly (~1 yr); 2019 $2.5k → Nov 2021 $210k by scaling contracts; blew up accounts many times early (pink sheets, <$10 stocks); 2022 rough.
- WARNINGS: Tom: not a repeatable expectation; one bear market can zero paper gains; needs positive expectancy per expiration (winners > losers).
- Quotable: "Any methodology works if you have rules and stick to them."

### How 0 DTE and 45-day Strategies Took This Trader To 24 Mil (231,137 views, 4,700 likes) https://www.youtube.com/watch?v=LpMopMs4CJM
- Strategy: "Doc" (pain physician, ~$24-28M acct) — (a) SPX 0DTE short call spreads (majority of trades, ~60% of profits); (b) 45-DTE short premium (short puts / put spreads for long delta, ~40%); ~100 underlyings, ~60% naked; 60-70% equity, 20-30% futures/futures options; liquid top-50 names, Mag-7, crude.
- ENTRY (0DTE): SPX 10-wide call spreads placed in the FIRST MINUTE after open (max IV → most premium); strikes above the day's expected move AND above resistance / large open-interest levels (dealer/gamma levels act as resistance); legs in over time (thousands of contracts). Rationale: market drops 100 pts in 5 min but rarely melts up 100 in 5 min. In sell-offs: call spreads are the "safer" premium sale but beware melt-ups.
- MANAGEMENT: 0DTE closed within ~2 hours (never held into the afternoon gamma); 45-DTE positions managed early (admits managing "too early"); rolls queued overnight; nightly review/replay of every order.
- SIZING: 30% dry powder minimum; 40-50% dry powder when VIX > 25 (prefers 50% BP available); ~100 underlyings with multiple positions each.
- STATS: 2022 $3M → $6M then lost $4M in one Fed-speech day (−1.5% market, oversized, no defense) → net −$1M; deposited $10M; 2023 +40% (~$5M on $12M); 2024 ~+$8M (~40%); 2025 positive; worst drawdown since 2023 only a "couple hundred thousand".
- WARNINGS: size + no preemptive defense = blow-up; account growth makes % returns harder; results not typical (Tom).
- Quotable: "The market can drop 100 points in 5 minutes; it does not melt up 100 points in 5 minutes."

### Software Engineer Creates 10x Returns with This Strategy (169,626 views, 3,400 likes) https://www.youtube.com/watch?v=DcwGoubTwUM
- Strategy: Brad (~$20k → $280k in 2022) — SPX 0DTE ATM credit spreads (call OR put, "50/50 shot"), 10-20 wide, defined risk, always a seller; occasional skewed iron condor on Mondays; butterflies in quiet names; earlier: 30 positions × 45 DTE (too much work), NQ futures blew up an account.
- ENTRY: morning trade → target $1.00 of credit captured then out ("go for a buck, don't hit a home run"); afternoon session entered ~12-1pm ET and held to ~3:50pm; on big-move days sells further OTM for $6-7 and lets it come in. Watches DXY, gold, bonds, VIX; checks the economic calendar daily; skips 0DTE ahead of NFP by rolling out to Wednesday-style "skip day" for extra theta.
- MANAGEMENT: 1-2 contracts only so rolls stay manageable; ALWAYS roll for a credit → must double the width (10→20 wide); rolls out AND toward the market (untested side, closer to ATM), max 2-3 rolls; rolls at the last minute (~3:50) because a 30-min-early roll can be 10-20 handles wrong; ~2-3 opening trades/day; often carries a rolled position overnight (esp. calls).
- SIZING: 1-2 lots per trade; "risk one make one" ATM; staying small is the hardest part after a big year; future idea: 10-20 delta wider spreads for less stress with a bigger account.
- STATS: 2022 SPX P&L $306k on ~$20k start (mostly 0DTE, market weak → sold call spreads); prior 17k→50k then blew up on NQ.
- WARNINGS: 0DTE — you don't know where you'll finish until the last minutes; NQ liquidity/velocity; over-leverage after success.
- Quotable: "If I trade in the morning I go for a buck — don't try to hit a home run."

### Tom Sosnoff's Complete Guide to Options (full talk) (172,046 views, 4,800 likes) https://www.youtube.com/watch?v=TyUuB7z8z3o
- Strategy: build a diversified $40k portfolio (Aug-14-2020 prices) from 5 "food groups": strangles, credit spreads, ratio spreads, iron condors, naked options (+ reverse jade lizard). 8 trades: SLV 1x2 call ratio (buy 40Δ / sell 2×30Δ, 35 DTE, $0.58 cr, BPR $1.1k, POP 88%); AAPL iron condor (25Δ strangle + $20 wings, $6.60 cr, BPR $1,340, $9/day theta); FB call credit spread ($3.10 cr, BPR $690, POP 65%); MSFT 30Δ strangle 35 DTE ($8.85 cr, BPR $3.2k, POP 58% → real ~70%, $20/day); QQQ reverse jade lizard (naked 30Δ call + OTM put spread, BPR $4.7k, POP 73%); GDX naked call (BPR $500, $1.29 cr, POP 76%); XLE 35Δ short put 35 DTE ($1.25 cr, BPR $1.6k, POP 67%); IWM 30Δ strangle 35 DTE (BPR $2.5k, POP 57%).
- ENTRY: IVR + liquidity are THE two gates: IVR ≥ 20 (post-COVID; normally "high"), penny-wide/tight bid-ask; 35 DTE; dynamic delta-based strikes (25-30Δ shorts, 35Δ for single short put); enter each structure as ONE order (never leg into strangles/condors/ratios); price 1-3¢ off mid; stagger entries over time, not all in one day; diversify by underlying, strategy, duration, volatility; mix defined + undefined risk.
- MANAGEMENT: manage early at 21 DTE; expected monthly P&L ≈ 25% of credit received; theta grows from ~$62/day toward ~$90 by 21 DTE.
- SIZING (capital allocation by VIX): VIX 10-15 → 25%; 15-20 → 30%; 20-30 → 35%; 30-40 → 40%; >40 → 50% of net liq deployed. Portfolio: $15,360 BPR of $40k (~38%), total credit $3,183, portfolio POP ~67% (target 2/3), beta-weighted delta −34 SPY shares, S&P correlation 0.56 (vs 0.7-0.8 for a stock portfolio).
- STATS: expected 5.2%/month on capital used ≈ 2%/month on account ≈ 24% annualized ("perfect world", not a promise).
- WARNINGS: fat-tail risk on undefined trades is what management at 21 DTE is for; POP on undefined risk understates true win rate; defined-risk POP is static.
- Quotable: "IV rank and liquidity are the single most important requirements to open a trade."

### How We Trade 0DTE Vertical Spreads (without over spending) (233,784 views, 3,100 likes) https://www.youtube.com/watch?v=YH_hL1oRjU0
- Strategy: Tom/Tony research segment — short vertical spreads (SPY/SPX) in 0DTE vs 45 DTE. Rationale: naked SPX strangle ties up ~$80k (~20% of notional); verticals normalize capital to a few hundred / thousand dollars.
- ENTRY: sell verticals for ~1/3 the width. 45 DTE example SPY 399/390 put spread ($9 wide) → $2.17 credit, max loss $683, POP 62%. 0DTE: to get 1/3 width you're ~$1 wide with short strike ~$1 OTM in SPY (404/403 for $0.27; SPX 10-wide ≈ $2.70/$270 credit, risk $730, POP ~66%). Study spec: short 35Δ / long 25Δ put spread, ~10 wide.
- MANAGEMENT/STATS (study): 0DTE spreads carry ~10x the directional (gamma) exposure of 45-DTE spreads as % of credit — SPY +$2: 0DTE +70% vs 45DTE +7%; +$1: +40% vs +3%; unchanged: +25% vs +1%; −$1: −15% vs −3%; −$2: −111% vs −7%. 0DTE spread collects ~1/8 the credit ($27 vs $217) but 10x the speed → ONE 0DTE spread ≈ dollar exposure of ONE 45-DTE spread. Tom took a 0DTE spread off intraday because he wouldn't accept 10x directional exposure without conviction.
- SIZING: size 0DTE 1:1 with what you'd do in 45 DTE (not more contracts because they're cheap); can't go tighter than ~$10 wide in SPX — tighter strikes are "worthless options" with no room.
- WARNINGS: 0DTE = "feast and famine — you can't afford to be wrong"; 99% of tasty trades are 45 DTE precisely to avoid 10x risk.
- Quotable: "1/8 the credit, 10 times the speed of the risk."

### How 0 DTE Turned This Uber Driver Into A Millionaire (654,692 views, 11,000 likes) https://www.youtube.com/watch?v=-kLqp_4JLmU
- Strategy: Mark (30, ex-construction) — SPX ONLY, 0DTE credit spreads entered as iron condors (each side managed separately); shorts are effectively synthetic naked options with nickel wings for capital; plus long-dated long options (7-6-5-4-3-2-1 DTE ladder, legged in ~1 week out) held to expiry as tail/vega/gamma hedge and margin offset; some calendarized structures. Long "tails" + short 0DTE spreads = "locally concave for income, globally convex for drawdowns".
- ENTRY: starts 9:32 ET (after opening price discovery) and lays out new spreads every ~2 minutes until ~3:50 → 100-200 trades/day, 15-200 positions/day, ~25-50k trades/yr; short deltas 7-20 (mostly 7-16); credit ~$1-3 per side (up to ~$4.50), leans heavier on put side, uses longer-dated calls as buffer; leaves ~99% of account in cash overnight (only long options carried). Skips FOMC days (negative expected return, high slippage).
- MANAGEMENT: almost always lets 0DTE spreads expire (saves ~2-5% of premium in exit slippage — "2% alpha") UNLESS threatened → close at a predetermined risk ratio set at entry; when threatened rolls only the short leg (up/down), long leg stays; all spreads in cash by 4pm. Screens in grayscale to remove emotion. Trade → track → be curious.
- SIZING: uses a "significant percentage" of the account daily; sized down after early success so no drawdown forces paying a volatility tax to exit; scalable to ~5% of open interest.
- STATS: ~$200k → $1.5M in 2 years (mostly profits); worst drawdown 20% (90-day gold/TLT/SPX structure during Ukraine invasion), ~8% since; SPX bid-ask cost ~2% of premium on entry, ~5% on exit (10% in high vol); market-tanking days are among the best days to sell 0DTE (vol demand). Tom: statistically far less edge than 45 DTE — zero-sum, "a lot of bodies out there".
- WARNINGS: FOMC days; huge delta risk with 2 minutes to go if you let things expire; needs SPX (cash-settled, no assignment) — not SPY.
- Quotable: "Risk managers first, capital allocators second."

### The Worst Day In Market History Explained (1,970,924 views, 17,000 likes) https://www.youtube.com/watch?v=jLfjEMDJubg
- Strategy: documentary on the Oct-1987 crash (Dow −508 / −22.6% in one day; MMI futures buying on Oct 20 halted the meltdown). No trading rules — skimmed only.
- Relevant takeaways: portfolio insurance = "illusion of liquidity" (couldn't sell futures when needed); post-87 the market permanently priced volatility SKEW (puts richer than calls) — the reason short-put premium is structurally rich; tail gaps of 20% in vol/price can happen; liquidity disappears exactly when you need it (markets $5-100 wide).
- Quotable: "Markets have pure context around risk — nothing else in life does."

### Volume & Open Interest Explained | Options Trading Concepts (208,514 views, 3,800 likes) https://www.youtube.com/watch?v=7K3navPkzlU
- Strategy: liquidity primer (Mike) — thresholds only.
- ENTRY (liquidity filter): stock volume > 1,000,000 shares/day; option strike open interest OR daily volume > 1,000 contracts at the strikes you trade; most liquidity clusters near ATM strikes; cash-settled indexes (SPX) exempt from the share-volume test.
- MANAGEMENT/SIZING/STATS: n/a. WARNINGS: thin strikes → wide bid-ask, unfair fills.
- Quotable: "Over 1,000 open interest or volume at the strike, over 1M shares a day in the stock."

### What Is A Covered Call & How Do I Trade It? (414,422 views, 5,000 likes) https://www.youtube.com/watch?v=q_6qqdMpFqQ
- Strategy: beginner explainer — covered call (same risk profile as a short put). Skimmed.
- ENTRY: example 100 sh @100, sell 115 call for $2 → cost basis $98, max profit $1,700; sell closer/ATM call ($5) when stock is at range highs for more cost-basis reduction (basis $95, less upside).
- MANAGEMENT: adjust short call strike to assumption; stock blowing past the strike = max profit (not a problem).
- SIZING/STATS: n/a. WARNINGS: caps upside.
- Quotable: "Reduce cost basis; profitable even if the stock doesn't move."

### Put Options Explained: Buying & Selling Put Options (552,443 views, 6,700 likes) https://www.youtube.com/watch?v=FAwDrUqpGUI
- Strategy: pure beginner explainer (long put vs short put). Skimmed — no mechanical rules.
- Only takeaway: short put profits 2 of 3 ways (up / flat), high POP, capped profit, loss to zero; long put 1 of 3 ways, low POP; longer DTE = higher premium.

### Iron Condor Adjustments Tutorial | Options Trading Concepts (280,152 views, 4,300 likes) https://www.youtube.com/watch?v=Uv1VcRMnKWo
- Strategy: iron condor defense (Mike). Example: 75/72 put spread + 85/88 call spread ($3 wide) sold for $1.00 → max profit $100, max loss $200, breakevens 74 / 86; non-equidistant wings → risk = wider side.
- MANAGEMENT: (1) stock rallies (IV falls) → roll the UNTESTED put spread up, all the way to an iron fly at the short call strike (85/82) for +$0.40 → total credit $1.40, max loss $160, breakevens 83.60/86.40 (profit range narrows to $2.80 wide). (2) stock drops (IV rises → far OTM premium richer) → roll untested call spread down only partway (80/83) for +$0.30, keeping a wide 75-80 max-profit zone. (3) blown out far past strikes → roll only if the credit clears a minimum: personal threshold ~$0.25-0.30 per adjustment (a $0.10 roll is eaten by ~$0.09 of commissions at $1.50/leg). Never adjust beyond the iron fly (don't invert/overlap short strikes).
- ENTRY/SIZING/STATS: n/a. WARNINGS: rolling to iron fly makes max profit require a pin; commissions matter on small credits.
- Quotable: "We seldom have to accept max loss — and we don't adjust further than the iron fly."

### 48 Minutes of Trading Advice You Wish You Knew Yesterday (182,202 views, 4,900 likes) https://www.youtube.com/watch?v=y0PNn89qw7U
- Strategy: Tom Sosnoff webinar on monitoring / adjusting / closing-rolling / redeploying a short-premium portfolio (equities + futures options).
- ENTRY / SETUP: watchlist = front-month index futures (ES, NQ, YM, RTY) → other liquid futures grouped → most liquid equities (top ~75-200); separate positions tab; sort by IV Rank + liquidity stars. Native (non-beta-weighted) Greeks per position, futures shown as ETF equivalents (ES→SPY, CL→USO), then beta-weight the whole book to SPY. Size relationships: SPX = 10× SPY; ES = 5× SPY; MES = 50 SPY shares. Daily P&L per underlying flags out-of-whack deltas.
- MANAGEMENT: portfolio theta / net liq = 0.1%-0.3% per day (e.g. $100k → $100-300/day; $1,000/day = way too big). Deltas must match your assumption. Default: "earlier is better." Manage-early vs hold-to-expiry stats: POP ~1-2% higher holding, but avg daily P&L higher managing early, P&L volatility 1/3, CVaR/outlier risk −2/3. The 3 R's: (1) Roll the UNTESTED side up/down to cut delta (never touch the tested side); (2) Re-enter — buy back the guts, sell new wings in one click to cut delta+gamma; (3) Roll out in time ("calendarize") — cuts risk ~30%, kills most outlier risk. Increase credits, lower delta; never add money/contracts to losers; don't leg; hedging defensively = overpaying insurance (offensive adjustments OK: roll calls down / roll out when IVR is high). Close when: optimal decay point reached, IVR collapsed (single digits → close, not roll), better use of capital, assumption changed, or underlying hit the expected move. Roll to extend duration on positions with high IVR (>50). Gamma of 16Δ put ~5x higher at 3 DTE vs 53 DTE; longer duration synthetically raises IV; theoretical edge same at any DTE — only mechanics/optimization differ.
- SIZING: theta 10-30 bps of NLV/day; be product-indifferent (futures uncorrelated: bonds/gold/oil/natgas vs SPY ~0) and capital-efficient (ES short put max profit ~6-7x SPY for same $7k BPR); need 600-1,000+ occurrences before results narrow, 2,000-5,000 → very predictable.
- STATS / EXPECTATIONS: minimum objective ≈ 3× risk-free (~15-18%/yr); keep ~25% of daily theta ($200/day theta on $100k → $6k/mo → keep $1.5k = 1.5%/mo ≈ 18%/yr).
- WARNINGS: sometimes the stock wins; egos add to losers; short-duration books (1-7 DTE) are unmanageable at scale.
- Quotable: "Earlier is better. Roll the untested side. Don't add to losers. Trust the research."

### Options - OTM vs ITM Explained (184,482 views, 2,800 likes) https://www.youtube.com/watch?v=zXBo-ebwAvo
- Pure beginner explainer (ITM = intrinsic value; sellers want OTM at expiry). Skimmed — no mechanical rules.

### Selling Put Options in $10,000 (or less) Trading Accounts (505,568 views, 6,600 likes) https://www.youtube.com/watch?v=7XgXvyg9mOQ
- Strategy: tastyBites research — naked short puts in SPY for small accounts; 1-SD (~16Δ, "84% OTM") put vs nearest-OTM (~ATM, 55-60% POP) put.
- ENTRY: Tom/Tony's habitual short-put delta is 30-35 (POP ~65-75%) — the "sweet spot" between 1SD and ATM. BPR rule of thumb for a naked put ≈ 17-18% of (short strike + credit) × 100 → SPY 1SD put ~$650 vs ATM ~$1,600. Future improvement: only sell when IV percentile > 50 (shown elsewhere to "vastly improve" ROC and win rate).
- MANAGEMENT: study held to expiration with no management; managing winners is easier further OTM.
- SIZING: 1-SD put is the most BP-efficient choice for limited capital.
- STATS (SPY monthly, 2009-Apr 2013, ~52 cycles, bull market): 1SD put 49 winners (94%; longer data → ~85%) P&L $1,878, std dev of returns $456; ATM put 41 winners (79%; longer data → ~60%) P&L $5,9xx (3.14x more) with 3.08x the variance ($1,407). Realized win rates beat theoretical POP (84% / 55%) — IV overstates realized vol.
- WARNINGS: bull-market data; ATM puts = higher return but ~3x variance — bad for small accounts; use the mirror (short calls) in a bear market.
- Quotable: "It proved out that you're picking up nickels — but not in front of a steamroller."

### Standard Deviation: Short Put 1 SD below Stock Price = 84% Probability of Closing OTM (184,878 views, 2,300 likes) https://www.youtube.com/watch?v=RMRWlwcKmJA
- Strategy: market-measure primer — 1 SD short put; VIX call spread / butterfly anecdotes.
- ENTRY: implied vol = 1 SD expected move; stock closes within 1 SD 68.2%, 2 SD 95.4%, 3 SD 99.7% of the time; a put sold 1 SD below = 84% chance of expiring OTM, and the credit pushes breakeven further out; broker BPR is set at ~2 SD, so BPR ≈ reasonable max-risk expectation. Sell cheap puts on cheap, high-IV stocks (limited downside in $) with very small size.
- MANAGEMENT: example VIX call spread sold $0.80 → closed $0.20; sell premium when IV is rich regardless of direction (TLT calls sold on high IV won despite bonds rallying).
- SIZING: keep size very small; a bankruptcy (PCX) is your ~1%.
- STATS: 84% POP; a listener's back test "worked ~95% of the time" ≈ 2 SD.
- WARNINGS: over enough samples you get exactly the probability you put on — no free lunch; tail-chasing (risk 1 to make 10-20) doesn't work.
- Quotable: "It's easier to manage nine wins against one loss than one win against nine losses."

### Call Options Explained: Options Trading for Beginners (405,501 views, 5,000 likes) https://www.youtube.com/watch?v=kmQ20J_3K7Q
- Pure beginner explainer (long call vs short call). Skimmed — no mechanical rules. Only takeaway: tasty sells OTM calls (2 of 3 ways to win, high POP, undefined upside risk).

### How To Trade Options in 2 Hours 12 Minutes and 4 Seconds | Live Bash Series (184,634 views, 4,600 likes) https://www.youtube.com/watch?v=441HWKDot0Y
- Strategy: 2h12m multi-speaker tutorial (~131KB, skimmed via keyword search — not read in full). Covers equity options, futures options, strangles, defined vs undefined risk, futures scalping by SD.
- ENTRY: open at ~45 DTE (optimal per research); sell premium when IVR > 30 (same rule for futures options); use 30/45-day IVR; strike selection by delta/POP (25Δ put ≈ 75% POP; spread POP ≈ 1 − max loss/width); 0-1 DTE = "minimal edge trades." Skew: at 45 DTE a 16Δ put is ~8% OTM vs 16Δ call ~4.9% OTM. Strangle stats (per study): 16Δ POP 81%, avg P&L $44, P&L SD ~$614; 20Δ POP 76%, higher avg P&L, higher SD; ~30Δ POP 68%, avg P&L ~$54, CVaR ~$1,673. Futures: same IV/mechanics as equities; ES strangle credit ~$1,800 vs BPR ~$17k (better margin model); roll futures options to the SAME DELTA (not strike) at 21 DTE; futures scalping: fade a 1-SD intraday move (68/32) targeting ~½ SD, stop ~½ SD (symmetric); 2-SD move → ~95% reversion odds; beware stop orders in futures.
- MANAGEMENT: manage at 50% of max profit OR 21 DTE, whichever comes first (defined risk can go 25% or wait a bit longer); scale "manage early" to any duration: 45d→21d, 30d→15d, 10d→5d, 1d→3 hours. Manage-early vs hold: POP unchanged, ~60% lower CVaR, ~60% lower daily P&L volatility, ~30% higher avg daily P&L. Reasonable expectation: keep ~25% of the extrinsic premium sold. Deltas move slowly at 45 DTE, faster at 21, "all over the place" at 2 DTE.
- SIZING: defined risk 0.5-2% of NET LIQ per position (never BPR); undefined risk 3-7%; max 15-20% of net liq in any one underlying; binary events ≤ 15-20% of BP; portfolio BP usage: large accts 25% (low) to 50-60% (high), small accts 50-80%; futures allocation ≤ 20-25% of net liq; single futures margin < 5% of NLV; mix ~75% undefined / 25% defined risk (Tom); one speaker: 15-20% futures, 35% stock options, rest core; futures options can add 35-50% of portfolio returns via leverage.
- STATS: as above; P50 always > POP.
- WARNINGS: futures leverage (S&P 13:1) — size by BPR; 0DTE minimal edge; don't play for moves beyond the expected move.
- Quotable: "50% of max profit or 21 DTE — whichever is easier."

## SYNTHESIS

Coverage: all 30 videos (offsets 1-30) fetched and processed; 2 long tutorials (Options for Beginners [2026], Live Bash 2h12m) were keyword-skimmed rather than read in full; 6 pure beginner explainers (Call/Put/OTM-ITM/Covered Call/Calendar/Hedging) contain no mechanical rules and were noted in 1-3 lines. 21 videos carried concrete rules.

### Top-10 most-repeated concrete rules (ranked by number of supporting videos)

1. **Enter short premium at ~45 DTE (30-60); duration over direction** — ONE Strategy (35-40, 60 in low IV), Trader 50% (45), Engineer/Lawyer, Gamma Explained (45 vs 2 DTE), Options for Beginners, Doc 24M (45-day book), Sosnoff Guide (35), 0DTE Verticals (99% of tasty trades are 45 DTE), 48 Minutes, Live Bash (45 optimal), Beginner Mistakes ("duration over direction"), $2.5k→$200k (30 DTE). [12]
2. **Manage winners at ~50% of max profit (GTC order); never let winners become losers** — ONE Strategy ($2→$1), Trader 50%, Engineer/Lawyer, Options for Beginners, Live Bash, 48 Minutes (keep ~25% of theta), Sosnoff Guide (expected monthly P&L = 25% of credit), Small Account, Beginner Mistakes, Verticals ($1.74→$1.00), 10x Engineer ("go for a buck"). [11]
3. **Size small: defined risk 1-2% (0.5-5%) of NET LIQ per position, undefined 3-7% (3-10%); position size is the #1 success factor** — ONE Strategy (3% instantaneous portfolio risk; "size is most important"), Small Account (1-2%, <5%), Options for Beginners (1-5 / 3-10), Live Bash (0.5-2 / 3-7; ≤15-20% per underlying), 48 Minutes (theta 10-30 bps of NLV/day), Trader 50% (10-lot SPX too big), Doc (oversized → −$4M day), Engineer/Lawyer (500-lot blow-up), Beginner Mistakes ("too big in one trade"), Sosnoff Guide, 10x Engineer (1-2 lots). [11]
4. **Manage early: close or roll at 21 DTE (or ~half the trade's duration) to kill gamma/outlier risk** — Engineer/Lawyer, Options for Beginners, Sosnoff Guide, 48 Minutes (POP −1-2% but P&L vol −2/3, CVaR −2/3), Live Bash (45→21, 30→15, 10→5, 1d→3h; CVaR −60%, avg P&L +30%), Gamma Explained (roll before expiration week), Doc ("manage early"), ONE Strategy (half of exits at 15-20 DTE), Beginner Mistakes. [9]
5. **Trade only liquid underlyings; liquidity + IVR are the two entry gates** — Sosnoff Guide (penny-wide), Volume & OI (>1M shares/day, >1,000 OI or volume at the strike), Engineer/Lawyer (5 ETFs; "getting out needs liquidity"), ONE Strategy, Doc (top-50 liquid), Uber 0DTE (SPX only; bid-ask ~2% in / 5% out of premium), $2.5k→$200k ("liquidity first, price second"), 4 Lessons (pre-screened watchlist), 48 Minutes (sort by liquidity stars). [9]
6. **Sell premium when IV Rank is high (≥30 default; ≥20 in a post-crash regime; IVP>50 best); mean reversion of vol is the edge** — 4 Lessons (IVR 30 line; >30 = 3x P&L/day at same win rate), Sosnoff Guide (IVR ≥20), Live Bash (IVR >30 for equities and futures), Strategy Selection (stock IV vs market IV), Selling Puts $10k (IVP >50 "vastly improves" ROC), 48 Minutes (roll positions with IVR>50, close single-digit IVR), Beginner Mistakes ("not enough premium"), Standard Deviation (sell when vol is rich regardless of direction). [8]
7. **Defend by rolling the UNTESTED side toward the money for a credit and rolling out in time; never touch the tested side, never add money to losers, never leg** — ONE Strategy (roll untested at breakeven; roll out last 5-10 DTE), 48 Minutes (3 R's: roll untested / re-enter guts / calendarize −30% risk), Iron Condor Adjustments (untested up to iron fly max; ≥$0.25-0.30 credit per roll), Small Account (naked rolls for credit easier than spreads), 10x Engineer (always roll for credit → double width; max 2-3 rolls), Trader 50% (≥2 rolls before closing), Rob (repair vs puke), Uber 0DTE (roll only the short leg). [8]
8. **Create many occurrences — trade small, trade often (≥20-30 trades/month; pros 700-1,000/yr; results only converge after ~600-1,000+ trades)** — Beginner Mistakes (#1 trap = not enough occurrences), Trader 50% (~950 closes/yr), Engineer/Lawyer (~900/yr), $2.5k→$200k (700-1,000/yr), Uber 0DTE (25-50k/yr), 48 Minutes (convergence chart), Sosnoff Guide, ONE Strategy (10-20 positions), Doc (~100 underlyings). [9]
9. **Keep dry powder; scale buying-power usage to VIX (25% at VIX 10-15 → 35% at 20-30 → 50% at >40) and hold 20-50% cash** — Sosnoff Guide (allocation ladder), ONE Strategy (~50% BP), Engineer/Lawyer (20% cash always), Doc (30% min, 40-50% dry when VIX>25), $2.5k→$200k (70% cash), Live Bash (25-60% large / 50-80% small accounts; futures ≤20-25% NLV), Uber (99% cash overnight). [7]
10. **Delta-based strike selection: strangles 16-30Δ (20Δ default, 16Δ = 1 SD/84% OTM, 30Δ "dynamic"), short puts 30-35Δ (16Δ for small accounts), condors 25Δ shorts; credit verticals ≥1/3 width** — ONE Strategy (20Δ; 30-35 aggressive), Sosnoff Guide (30Δ strangles, 25Δ IC, 35Δ put), Selling Puts $10k (30-35Δ sweet spot; 1SD most BP-efficient), Standard Deviation (1SD put = 84% POP), Engineer/Lawyer (1SD earnings), Live Bash (16/20/30Δ strangle stats), 4 Lessons (expected-move strikes), Verticals + 0DTE Verticals (1/3 width), Uber (7-20Δ 0DTE). [9]

Honourable mentions: enter multi-leg structures as ONE order / no legging (Sosnoff Guide, 48 Min, ONE Strategy); diversify by underlying, strategy, duration AND volatility incl. uncorrelated futures (Sosnoff Guide, 48 Min, Live Bash, Strategy Selection, Verticals, Doc); stop-loss at 2x-3x credit for undefined risk in small accounts (Small Account, Options for Beginners, Uber "predetermined risk ratio") — the veterans prefer roll/extend duration over hard stops; earnings: 1SD strangle/IC at 2pm CT day before, out ~9:40 next open (Engineer/Lawyer), binary events ≤15-20% of BP (Live Bash), avoid or close 2 weeks pre-earnings (Beginner Mistakes, $2.5k→$200k); 0DTE cohort: SPX only, 10-wide, first-minute or continuous 9:32-3:50 entries, close within ~2h (Doc) or let expire (Uber), 1/8 credit for 10x gamma → size 1:1 with 45-DTE (0DTE Verticals), skip FOMC/NFP (Uber, 10x Engineer).

### Numeric parameter table (consensus and range across videos)

| Parameter | Consensus | Range seen | Notes / sources |
|---|---|---|---|
| Entry DTE | 45 | 30-60 (0DTE cohort separate) | 35 (Sosnoff Guide), 30 (Vishant), 60 in low IV (Lewis) |
| Short-strike delta — strangle | 20Δ | 16-35Δ | 16Δ=1SD; 30Δ "dynamic" (Sosnoff); 30-35 aggressive (Lewis) |
| Short-strike delta — naked put | 30-35Δ | 16-35Δ | 1SD (16Δ) most BP-efficient for small accts |
| Iron condor shorts | 25Δ | 16-30Δ | wings $20 in AAPL example; 10-wide SPX 0DTE |
| 0DTE short delta | 7-20Δ / above expected move | ATM (Brad) → 7Δ (Mark) | Doc: above resistance + expected move |
| IV Rank threshold to sell | ≥30 | 20-50 (IVP>50 best) | 20 post-COVID (Sosnoff); traders vary 20-40 |
| Credit on short vertical | ≥1/3 width | 1/3 (0DTE needs ~$1 OTM/$1 wide SPY) | ATM debit vertical ≈ 50% of width |
| Profit target | 50% of max profit | 25-50% (94% ROI on debit spreads — Vishant outlier) | keep ~25% of theta/credit expected |
| Manage-early point | 21 DTE | 15-21 DTE; ~half of duration | 1-day trade → 3 hours |
| Stop / loss multiple (undefined) | 2x credit | 2x-3x credit; 50% of debit (debit spreads); ½ SD (futures scalps) | veterans prefer roll/extend over stops |
| Position size (defined risk) | 1-2% of net liq | 0.5-5% | never on BPR, on NLV |
| Position size (undefined risk) | 3-7% of net liq | 3-10% | ≤15-20% NLV in one underlying |
| Portfolio theta / NLV per day | 0.1-0.3% (~0.2%) | 0.1-0.3% ($100-300/day on $100k) | 1%/day = "way too big" |
| Buying-power usage | 35% (VIX 20-30) | 25% (VIX 10-15) → 50% (VIX>40); 50-80% small accts | keep 20-50% dry powder; futures ≤20-25% NLV |
| Portfolio POP target | 65-70% (2/3) | 60-75% | higher when IV is richer |
| Occurrences | 20-30 trades/month min | 700-1,000/yr pros; 25-50k/yr 0DTE | results converge >600-1,000 trades |
| Positions carried | 10-20 | 5 ETFs (Les) → ~100 underlyings (Doc) | 40 = too many (Lewis) |
| Liquidity gates | >1M shares/day; >1,000 OI or vol at strike; penny-wide | SPX bid-ask cost 2% in / 5% out of premium | |
| Return expectation | ~18%/yr (3x risk-free) | 15-18% min; 24% "perfect world"; 2%/month | keep 25% of credit → 1.5%/mo on 0.2%/day theta |
| Earnings play | 1SD strangle/IC, in 2pm CT, out 9:40 next day | binary events ≤15-20% BP | avoid / close 2 wks pre-earnings otherwise |

---

# Part B — Market Measures research segments + critical outside views

# tastylive research notes (batch B) — Apify dataset ed2FrL1sSgWWAXwlf

Processed: channelUsername == tastyliveshow (16 items) + 2 outside critiques.
Skipped: projectoption, skyviewtrading, MarkHawaiiantrader, Navigationtrading, IncomeOptionsTrading, BusinessWithBrian, smbcapital, LearnAsYouGrowOfficial, SaminYasar_, clearvaluetax9382, HaiKhuuTrading, VolatilityVibes, scottreesetradinginvesting7936.

### Mastering Your Strangle Mechanics | Market Measures (49,856 views, 831 likes) https://www.youtube.com/watch?v=DMEKpnD0_gM
- Structure: 20Δ short strangle (20Δ call / 20Δ put), 45 DTE.
- Setup: SPY, IWM, AAPL, META; ~7 years of data. Three management variants: hold to expiration, close at 21 DTE, close at 14 DTE. Filter: IVR<30 vs any IVR.
- Results: win rates barely move across management (any IVR: 21 DTE ~78%, 14 DTE ~75%, expiration ~76%). In low IVR win rates "drop dramatically". SPY: 21 DTE in low IVR was poor; extending to 14 DTE sometimes helped a little in ETFs. IWM low IVR + shorter management → avg daily P&L net loser and CVaR rose. AAPL: win rate ~68-70% at any IVR; positive P&L only when managed at 21 DTE; 14 DTE bad, expiration bad, low IVR "a disaster" — the only profitable AAPL scenario was high IVR + 21 DTE. META was more forgiving of holding longer. In equities, CVaR at 14 DTE was almost as high as holding to expiration with no P&L improvement vs 21 DTE, in either IVR regime.
- Rule: keep standard mechanics — 45 DTE entry, 20Δ, manage at 21 DTE, prefer high IVR; in ETFs you can occasionally hold to 14 DTE in low IVR without a big CVaR penalty, but "no situations in equities where it paid to hold longer". Stay small, manage early, many trades.
- Caveats: hard to make money selling premium in low vol at all; single-stock outliers dominate results (AAPL); results are underlying-specific.
- Quote: "managing early, staying small, putting on lots of trades — still the best policy."

### 0DTE vs 45DTE | Options Backtest (30,333 views, 753 likes) https://www.youtube.com/watch?v=P2eZOTxsEX4
- Structure: 20Δ short strangle, entered at market open. Compares 0DTE (hours) vs 45 DTE (weeks) — 1 hour of a 0DTE ≈ 1 week of a 45 DTE.
- Setup: SPX. 0DTE: 1 year of data. 45 DTE: 15 years of data. P&L measured as % of initial credit per segment.
- Results (45 DTE, per 2-week segment): weeks 1-2 avg +11% of credit (stdev of P&L change ~6%); weeks 3-4 +24% (stdev ~12%) — "the meat"; weeks 5-6 −2% — "the death trade"; final week +16% but with ~3-4x the risk. I.e., first ~4 weeks (≈24-28 days) you make more than your stdev of risk; after that risk/reward flips. 0DTE: first 2 hours weak; hours 3-4 the "payday" with reasonable stdev; hours 5-6 troublesome; last 2 hours the biggest decay but stdev of P&L change ~5x the expected gain — wild swings.
- Rule: this is THE reason for managing 45 DTE trades at 21 DTE — profit is front-loaded, risk is back-loaded. Start small when switching between DTE regimes.
- Caveats: 20Δ SPX strangle credit assumed ~$5 → 11% is only ~$0.50; 0DTE takes far more risk per unit of premium; both regimes are "not the same animal".
- Quote: "why would you ever take so much more risk in the second half."

### What is the Probability of Reaching 50% of Max Profit? (27,024 views, 747 likes) https://www.youtube.com/watch?v=wkCZgESHdOs
- Structure: 45 DTE short strangles at 10Δ / 16Δ / 20Δ / 30Δ; question = P(hit 50% of initial credit before 21 DTE).
- Setup: 2005-2023 (18 yrs). SPY as benchmark plus GLD, SLV, AAPL, GOOGL, AMZN.
- Results (SPY): P(50% max profit before 21 DTE) ≈ 63% (10Δ), 52% (16Δ), 45% (20Δ), 26% (30Δ). Avg days to hit 50%: 17 d (10Δ), 18 d (16Δ), 19 d (20Δ), ~24 d (30Δ, i.e. right at the 21-DTE exit for a 45-DTE trade held 24 days). Inverse for a fixed $100/contract goal: higher-Δ reach it sooner (10Δ ~23 d vs 30Δ ~19 d) because they start with more credit. All Δs reach the fixed $ goal before 21 DTE. Across SPY/GLD/SLV/AAPL/GOOGL/AMZN the P(50%) and days-held were "roughly the same" (~50%) — SPY is a valid proxy.
- Rule: tasty "lives" at 16-20Δ; 50% profit target; manage early; if you're at 45% and asking whether to wait for 50% — take it. Higher-Δ strangles are fine only in high IV; be careful with them in low IV.
- Caveats: probability of a $ target vs % target invert with delta; low-Δ makes little absolute money.
- Quote: "the default is always to take profits."

### It Took Us 15,000 Trades To Find Our Preferred Delta (64,238 views, 1,400 likes) https://www.youtube.com/watch?v=-2TM3Jaz9dQ
- Structure: short OTM puts and calls (strangle legs) — probability of touch (POT) study across 10-45Δ.
- Setup: SPY, ~10 years (bull market), 45 DTE entry, exit at 21 DTE vs hold to expiration. Theoretical POT ≈ 2×delta.
- Results (20Δ): theoretical POT 40%/40%; realized held-to-expiration: puts ~20%, calls ~40%; managed at 21 DTE: puts ~10-11%, calls ~14%. Ratio realized-POT@21DTE / delta ≈ 0.3 for 20Δ (i.e. <1/3 of theoretical). Across deltas, POT@21DTE for calls: 10Δ ~3%, 15Δ ~7%, 20Δ ~14%, 25Δ ~24%. Gap between theoretical and realized narrows as delta rises; ATM ~100% touch. Puts have lower realized POT than calls (bull market); the put/call difference becomes marginal once managed at 21 DTE.
- Rule: sell 10-25Δ (tasty lives at 16-20Δ; they "don't do 40-45Δ"), enter 45 DTE, manage at 21 DTE — this alone cuts realized touch to a fraction of theoretical. Managing early + 45 DTE flattens the P&L curve.
- Caveats: bull-market sample; realized POT for puts is regime-dependent; study is for OTM only, meaningless for ATM.
- Quote: "you always remember when you don't manage early."

### We Studied 15 Years of 21 DTEs Results To See If There Was a Better Way (90,163 views, 2,000 likes) https://www.youtube.com/watch?v=v_gyQeYxOys
- Structure: 20Δ short strangle, 45 DTE entry; swept EVERY fixed holding duration from 0 days (close immediately) to expiration.
- Setup: SPY, IWM, QQQ; 15-16 years of data. Metrics: avg P&L per trade and CVaR (avg of worst 5%) vs days held.
- Results (SPY): holding 0-20 days → avg P&L wanders −$10 to +$10 (coin flip); holding ~20-24 days → +$20 to +$40 per contract, the peak; holding beyond ~25 days to 45 the avg P&L declines — you make more at day 21-24 than at day 45. CVaR: near zero on day 1, rises slowly, then "goes off the charts" in days 25-45 (SPY CVaR ~$1,400 at 40+ days); sharp CVaR increases after ~30 days held, mostly in the last two weeks. IWM even more pronounced (P&L drops sharply, CVaR much higher in last 25 days); QQQ same shape.
- Rule: 45 DTE in, manage at 21 DTE (≈24 days held) — "the sweet spot" that top-ticks P&L-per-day and eliminates the outlier tail; then redeploy capital. Trades held <20 days have very low avg P&L, so don't close too early either.
- Caveats: strangles held to expiration in all three ETFs carry outsized CVaR; the "magic" is really a sweet-spot window (~21-25 days), not literally 21.
- Quote: "leaving positions on for 24 days was the sweet spot — good profits without exposure to large losses."

### Why 21 DTE May Change How You Manage Options (3,471 views, 82 likes) https://www.youtube.com/watch?v=xccHQzd8fLk
- Structure: part 2 of a capital-allocation study — 20Δ SPY short naked puts, 45 DTE (part 1 was 1-SD strangles). Three exits: hold to expiration, manage at 50% max profit, exit at 21 DTE. Portfolio capital committed: 25% vs 35%.
- Setup: SPY, decade-long bull-market sample, portfolio-level daily return + daily volatility.
- Results: managing winners at 50% gave the highest returns (bull market tailwind), holding to expiration and 50%-target both raised portfolio daily volatility (with 35% capital, "way higher" — ~2%+ daily swings); exiting at 21 DTE kept portfolio volatility in a tight range at both 25% and 35% capital, for both puts and strangles, with similar daily return. Puts vs strangles: puts a few ticks more daily vol except under 21 DTE where they matched.
- Rule: exit at 21 DTE for volatility control regardless of size; keep committed capital ≈25% (35% is "a big uptick" in BP usage that amplifies swings); be mechanical, not emotional.
- Caveats: naked put returns are flattered by a decade of bull market ("scares me just as much"); ramping size raises returns AND volatility.
- Quote: "21 DTE stands out as the best approach to control volatility, regardless of capital allocation."

### Most Traders Stop Selling Strangles When the Market Drops 3%. The Data Says That's the Wrong Move. (1,482 views, 31 likes) https://www.youtube.com/watch?v=VRelP3ORlrA
- Structure: 16Δ short strangle, 45 DTE, managed at 21 DTE; entries bucketed by that day's SPY move: any day / down ≤1% / down 2% / down ≥3%.
- Setup: SPY, 2020 (post-COVID crash) to ~2026, ~6 years, entered every available 45-DTE expiration (~100-200 occurrences/yr, not just monthlies).
- Results: all entries avg P&L ≈ $105/contract; avg P&L rises monotonically with sell-off size; on down ≥3% days avg P&L is more than double the all-days basket. Return on capital roughly 1% (any day) → ~2.5-3% (big down days). Premium/BP ≈ 5% normally (≈$50 per $1,000 BP) vs ≈ 8% after big down moves; largest loss as % of BP also decreases. Win rate declines only modestly as sell-off size grows (bigger losers, but bigger avg wins → better reward/risk).
- Rule: sell strangles INTO 2-3% down days (vol expansion → paid more, BP lower, strikes further OTM); manage at 21 DTE; often take the money the next day when vol contracts ("be nimble, no piggies"); keep size in check because sell-offs can continue.
- Caveats: only ~6 years, an unusually vol-rich sample; positions put on BEFORE the vol expansion suffer both delta and vega; down days can be the start of larger moves.
- Quote: "when volatility expands you're getting paid more and putting up less capital."

### Why We Manage at 21 DTE (4,730 views, 171 likes) https://www.youtube.com/watch?v=sqh0u63Zw6o
- Structure: conceptual (Jim Schultz) — applies to short premium generally, esp. undefined risk (short puts, strangles, ratio spreads).
- Setup: no new backtest; explanation of the mechanism behind the 45-in / 21-out rule.
- Results/logic: options P&L comes from direction, time, or volatility; tasty wants to weight time+vol over direction. Short options = short gamma; gamma is naturally low at 45-21 DTE and "grows teeth" around 14-10 DTE, so inside 21 DTE direction becomes the dominant driver of P&L. This is why every tasty study shows 45→21 more reliable than 21→expiration.
- Rule: manage undefined-risk positions at or before 21 DTE. Defined-risk (verticals, iron condors) have long options capping gamma, so 21 DTE is a "take it if you have a profit" nudge, not an urgent exit.
- Caveats: DTE isn't the only gamma driver (moneyness matters); 21 vs 17-18 DTE isn't a big difference — 21 is where "the tide begins to turn".
- Quote: "greater emphasis on time and volatility, less emphasis on direction."

### I Wish I Knew About This 0 DTE Strategy 4000 Trades Ago (49,354 views, 843 likes) https://www.youtube.com/watch?v=2P6IPdypZTE
- Structure: 0DTE SPX iron condors with wide wings as a synthetic strangle (naked 0DTE SPX strangle needs ~$120k BP per 1-lot; SPX gets no duration benefit in margin).
- Setup: SPX, 2 years of 0DTE data; short strikes at 20Δ / 30Δ / 40Δ; wings fixed-width 30 to 100 points; close at 25% of credit else hold to close.
- Results (20Δ shorts): $40-wide wings cost ~$1.00, BP ~$3,400; $50-wide ~$0.67; $60-wide ~$0.50, BP ~$5,300; $90-wide ~$0.24, BP ~$8,300. Wings actually provided protection ~0.2% of the time (≈20 bps) — irrelevant across widths; avg daily P&L ≈ $650 regardless of width. 30Δ: wings cost more, BP less, protection slightly more; 40Δ: BP ~$2,500 but wings come into play far more (~13% of the time). Cutoff: wings ≤50 SPX points (~5 SPY/QQQ/IWM points) behave like an iron condor; 60-90 wide behaves like a strangle at ~$0.30/trade cost.
- Rule: tasty's wheelhouse is 20Δ shorts (they "don't even really do 30Δ"); pick wing width purely by how much BP you want to commit — no width is theoretically better; wider = cheaper wings but more BP.
- Caveats: wings are for BP reduction, not protection; 0DTE outcomes still hinge on the stock cooperating.
- Quote: "narrower than 50 [points] is an iron condor; 80-90 wide is essentially strangle behavior."

### What Is The Right DTE? … We Tested It To Find Out (16,665 views, 443 likes) https://www.youtube.com/watch?v=fFaW9OENf8c
- Structure: 16Δ short strangle, iron condor, ATM straddle; DTE swept 1 / 15 / 30 / 45 / 60 / 90.
- Setup: 14 years of data (SPY implied); managed at 50% max profit (couldn't use 21 DTE across all durations); metrics: stdev of P&L, CVaR, win rate, CVaR/DTE (risk per day), correlation DTE↔win rate.
- Results: CVaR rises with DTE (more time for big moves) but CVaR/DTE falls smoothly — risk per day is lowest at long DTE; win rate roughly flat across DTE (strangle high-70s to low-80s, ~81%; iron condor ~79%; straddle high-50s/low-60s, ~57% at 1 DTE, ~59% overall). Strangle CVaR/DTE ≈ 40 at 45 DTE (their chosen sweet spot); iron condor CVaR/DTE ≈ 17 at 45 DTE with a big drop from 30→45 DTE. 1 DTE is the riskiest per day ("crazy"); 15 and 30 DTE still carry significant per-day risk. Straddle: ~2x the stdev and ~2x CVaR/DTE of strangle, lowest win rate.
- Rule: 45 DTE is the sweet spot for strangles AND iron condors — CVaR/DTE has dropped a lot, win rate still max, and you avoid the last 2-3 weeks; 60-90 DTE not enough extra bang, <45 too much risk per day. Straddles only in very high IV.
- Caveats: results based on 50%-profit-target management, not 21 DTE; longer DTE lowers daily risk but eventually removes you from the decay-curve sweet spot; "no edge either way" — it's risk-per-unit-time.
- Quote: "45 days is the perfect point in the decay curve where you still make money when you're right but avoid the last two or three weeks when things get ugly."

### Jim Schultz on Short Strangles vs Iron Condors, and When to Use Each. (3,909 views, 263 likes) https://www.youtube.com/watch?v=rF0baGqUk30
- Structure: short strangle (undefined) vs iron condor (defined-risk strangle) for neutral bias. No backtest — platform walkthrough (GDXJ example).
- Setup/mechanics shown: sort watchlist by IV rank high→low; avoid names with earnings inside the cycle (unless deliberately trading earnings); IVR ~50+ (XLU 53, GDXJ 51.5) called "really good"; expiration 30-60 DTE, ideally ~45; short strikes at ~1 SD / expected move ≈ 15-17Δ put, ~20-23Δ call (skew). Example: GDXJ strangle credit $4.00, BP ≈ $1,000, theta ≈ $9-10/day, net delta ≈ −7; $5-wide iron condor version credit $1.50, theta ≈ $2/day, much lower BP, capped max loss.
- Rule: beginners and accounts ≤ $15-25k → iron condors only; $30-50k+ and experienced → strangles; strangle gives full theta/vega exposure and many adjustment options but undefined risk both sides; iron condor is "set it and forget it" but hard to make money and hard to adjust.
- Caveats: wide neutral ranges are "a bit of a disillusion" — you WILL get tested; have adjustment mechanics ready before entry; short put is his #1, strangle #2.
- Quote: "if you're just starting out — your goal is not to make money, your goal is to learn."

### How to Read IV Rank After Market Volatility (1,517 views, 46 likes) https://www.youtube.com/watch?v=fOXWJqjN8yw
- Structure: IVR as an entry gate for any short premium (condor, strangle, put) — how a vol spike (COVID, tariff tantrum) compresses subsequent IVR readings.
- Setup: SPY IVR + VIX, 2015-present; buckets IVR 10-20 / 20-30 / 30+; measured P(VIX contracts ≥5% and ≥10% within the next 45-day window). IVR = (IV − 52w low)/(52w high − 52w low).
- Results: IVR > 30 → ~66-68% probability of ≥5% VIX contraction within 45 days, and still a high probability of a 10% contraction; IVR 10-20 → very low probability (vol would have to go lower still); IVR 20-30 → decent, "not a bad entry", and far more occurrences than 30+. When the trailing-year max was a huge spike, contraction becomes likely even at lower IVRs (a 30-40 IVR reading is "really an 80-90" ex-spike). 80-90 IVR readings are rare; 30-40 is the practical top of the range.
- Rule: IVR ≥ 30 = go zone for short premium; IVR 20-30 = relative high end, acceptable; IVR 10-20 = pump the brakes. Lower the threshold to ~20+ if a large vol event sits inside the trailing 52 weeks. (Live example: SPY IVR 22 = "in go zone".)
- Caveats: IVR is skewed by outliers and can't be "fixed"; be dynamic — vol can come in fast (GLD IVR 114-117 example) and pricing moves around; watch fills.
- Quote: "a 30 IV rank is a good spot to start looking at short premium positions."

### I Don't Only Focus On The Stock … I Look At This (7,768 views, 256 likes) https://www.youtube.com/watch?v=Oq4B-4QLwXg
- Structure: short OTM puts, 45 DTE, managed at 21 DTE; deltas 15→50 in 5Δ steps; IVR as the "market opportunity indicator".
- Setup: SPY, 12 years (tastytrade options backtesting engine); IVR split at 50 (low ≤50, high >50); premium standardized at 1% of underlying (SPY $624.77 → ~$6.24 credit; live example 604 strike, 27Δ, IVR 13.4).
- Results: to collect 1% premium you need ~30Δ in low IVR vs median ~15Δ in high IVR (15Δ-point advantage → same reward, less risk/lower touch). Every delta column performs better in high IVR; 50Δ puts in low IVR earned LESS than 15Δ puts in high IVR. 20Δ put avg P&L ≈ $108 (low IVR) vs $367 (high IVR) — >3x. Win rate cost: ~79% (low IVR) vs ~71% (high IVR) at comparable delta. Tom's interpolation: with an IVR-30 cutoff, expect ~2x rather than 3x.
- Rule: use IVR ≥30 (not 50 — SPY IVR >50 only a couple times a year; 30 keeps you "in the game" ~60-65% of the time). Sell 16-25Δ puts, 30-35Δ max. High IVR → go further OTM and size up; low IVR (<30) → stay selective, size smaller, don't be short lots of S&P strangles.
- Caveats: IVR "doesn't predict outcomes, it sets expectations"; study is puts-only, bull-market period.
- Quote: "IV rank tells you how far out of the money you can go."

### [OUTSIDE VIEW] I Traded the Tastytrade 45 DTE Strategy for a Year — Here's the Problems Nobody Mentions (3,385 views, 59 likes) https://www.youtube.com/watch?v=Y4AtuWIEJWI
- Channel: SmallAccountOptionsTrading (Dr. Ashik Raman). Structure: tasty 45 DTE credit spreads / short premium, exit ~21 DTE (≈24 days in), on a tiny account ($500 → ~$1,170 funded; positions in GLD, SLV, PLTR, MSTR).
- No backtest; one year of live experience. Verdict: "the strategy works… still the only strategy that ever made me money", net positive despite repeated CVaR-sized losers.
- Problems he flags (what breaks in practice):
  1. Implicit market timing: a 45-day window IS a bet on the next 45 days; the moment you look at a chart / support you're timing, and it doesn't work (MSTR entered "at support", −12% next day; PLTR down right after entry).
  2. Directional credit spreads are momentum bets dressed as premium selling.
  3. Small-account psychology: every trade "counts in your head"; mechanical on paper, not in feel; you're forced to pick and choose because BP is limited — no real edge in the picking.
  4. Diversification is an illusion: GLD/SLV are highly correlated (both dumped together); individual stocks correlate with SPY, plus earnings block you (IV rises into earnings, can't enter within ~2 weeks). Hard to find truly uncorrelated trades; as the account grows you run out of baskets, so daily swings scale (e.g. $50k fully deployed → ±$10k days).
  5. Sitting on cash: at all-time highs / low IV, tasty says stay small/selective — psychologically hard to hold cash on a large account.
  6. Tail events: "you WILL eventually hit CVaR" (worst 5% of trades); overnight gaps (MSTR gap risk); it "makes you question your whole strategy".
  7. Public accountability (posting daily) worsens decision quality; risk management > entries.
- His rule going forward: ETFs only (uncorrelated ones), no single stocks until account ≥ $5k; small size in many baskets; trust the numbers, don't check daily.
- Quote: "risk management matters more than the entries."

### [OUTSIDE VIEW] What no one tells you about Option Selling? (Harsh Truth) @thetradingscholarr (502,402 views, 7,900 likes) https://www.youtube.com/watch?v=M4bK4rRCtWQ
- Channel: punchHQofficial (Punch, Indian broker), guest Kundan (@thetradingscholarr). NO TRANSCRIPT in dataset (subtitles=null; Hindi/Hinglish, 58 min). Notes below are from the video description/timestamps only.
- Framing: pro-option-selling, not anti-tasty. Buyer win rate <20% vs seller ≥60% POP; sellers win in 3 of 5 market scenarios (sideways, slow-against, with-trend); "unlimited risk" is a myth IF systematically hedged; selling scales, buying doesn't; use G-Secs (bonds) as collateral to lift return on margin.
- Strategy shown: "Monthly No-Brainer" Nifty ratio spread — fixed entry/exit timings, targets, stop loss, no adjustments; filters based on net debit/credit of the structure to skip bad months; live backtests for a normal month, a high-vol (Budget) month, and a losing-start month that recovers.
- Relevance/critiques (inferred from timestamps): margin/collateral efficiency is the real return driver; must hedge (defined tails) — undefined-risk selling is the harsh part; the "6 golden rules" for strategy design emphasise mechanical entry/exit and skipping trades when the credit/debit filter fails.
- Caveat: Indian index (Nifty) context — weekly/monthly expiry, SPAN margin, lot sizes differ from US; numbers unavailable without transcript.
- Quote (from description): "profit even when the market goes sideways or moves slowly against you."

### How 0 DTE Turned This Uber Driver Into A Millionaire (654,692 views, 11,000 likes) https://www.youtube.com/watch?v=-kLqp_4JLmU
- Structure: Rising Star interview (Mark). SPX-only 0DTE credit spreads laid on as iron condors every ~2 minutes from 9:32 to ~3:50, 15-200 positions/day, 100-200 trades/day (25-50k/yr); short strikes ~7-20Δ, ~$1-3 credit per side (up to ~$4.50); nickel wings 200+ pts out ("synthetically naked"); separately holds a ladder of long options 1-7 DTE bought a week out and held to expiration as tail/gap hedge; ends every day ~99% cash, long gamma/vega/delta into the close.
- Not a backtest — anecdote: ~$200k → $1.5M in ~2 years (2022-24), max drawdown ~8% on 0DTE (20% earlier on a 90-DTE gold/TLT/SPX structure during Ukraine invasion).
- Practical numbers: SPX bid/ask friction ≈ 2% of premium on entry, ~5% on exit (up to 10% in high vol) → he lets spreads expire for the ~2% "alpha" unless threatened; manages only the short leg (rolls) at a predetermined risk ratio, never lifts wings; skips Fed days (negative expectancy, slippage); trades only SPX (cash-settled, no assignment/pin risk, tight spreads, low fees); market-crash days are "some of the best" for selling 0DTE vol.
- Rule/philosophy: "risk manager first, capital allocator second"; "locally concave for income, globally convex" via long tails; sized down after profits so he never has to "pay a volatility tax to get out"; greyscale screens; test-trade-track.
- Caveats (Tom's own): "significantly less edge" in 0DTE, zero-sum-ish, "a lot of bodies out there", uses a significant % of net liq daily — not for the faint of heart; results are one trader's, not a study.
- Quote: "I want to limit the degradation of adjusting… but use risk management to shape the distribution."

### These 4 Lessons Allowed Me to Stop Trading Directionally (207,372 views, 4,000 likes) https://www.youtube.com/watch?v=Zy_046YjuvQ
- Structure: Jim Schultz "trade volatility, not direction" crash course; covers VIX/VX, IV index, IVR definition, Vega sign, short put spread / short put / strangle / long put vertical / calendar examples on AAPL, AMZN, SBUX, QQQ.
- Studies cited: (a) tasty Aug-2022 VIX study (data from 2000): random day VIX down 53% / up 47%; after consecutive VIX up-days, P(next day down) shifts to ~60/40 in favour of contraction from the very first up-day — mean reversion is strongest after expansions; after down-day streaks, only a slight tilt toward the next day being up. (b) Vol regimes (Unlucky Investor's Guide fig 2.2): expansion ~10% of days, contraction ~20%, lull ~70% → vol is falling or flat ~90% of the time. (c) tasty Aug-2023 study: 16Δ strangles, IVR<30 vs IVR>30 — win rates essentially the same; avg daily P&L $0.25/day (IVR<30) vs ~$1.15/day (IVR>30), >3x.
- Mechanics shown: IVR = (IV − 52w low)/(52w high − low), can exceed 0-100; use tasty-default / Tom's watchlist (pre-screened for liquidity), sort IVR high→low; ~41-45 DTE cycle; strikes at the expected move (~1 SD); strangle has full short Vega (e.g. −18/−19 per leg), spreads net only −2 to −3 Vega; SBUX 95/90 put spread: $1.74 credit, POP 62%, P50 77%.
- Rule: IVR 30 as the high/low reference line (some use 20-25, others 35-40 — adjust to style/size); sell premium when IVR high, mix in debit/calendar/diagonal when IVR low; short premium was profitable in BOTH regimes but far more so above 30.
- Caveats: day-to-day direction is random; earnings inside the cycle complicate single-stock entries; vol/price inverse relation is reliable but not 1:1.
- Quote: "trade volatility first, not direction."

### Selling Put Options in $10,000 (or less) Trading Accounts (505,569 views, 6,600 likes) https://www.youtube.com/watch?v=7XgXvyg9mOQ
- Structure: short SPY puts — 1-SD put (~84% OTM prob, ~16Δ) vs nearest-OTM put (~50-60% POP); held to expiration, no management, no IV filter (older Tasty Bites segment).
- Setup: SPY monthly cycles 2009 → early 2013 (~52 occurrences, bull market).
- Results: 1-SD put 49 winners (94% win rate) vs ATM put 41 winners (79%); P&L: ATM put ≈ 3.14x the 1-SD put's total (1-SD ~$1,878 total mentioned) but stdev of returns 3.08x greater ($456 vs $1,407 per-trade stdev). Realized win rates beat theoretical (84% / 55%) — "IV overstates realized vol" edge, though the team notes longer samples would land nearer 85% / ~60%. Capital: BP ≈ 17-18% of (short strike + credit) → ~$1,600 (ATM) vs ~$650 (1-SD) per SPY put.
- Rule: for limited-capital / IRA accounts the 1-SD put is the most BP-efficient and easiest to manage as a winner; tasty's own default sits at ~30-35Δ (65-75% POP) — this study "validates" living between the two extremes. Managing winners and entering only when IV percentile > 50 "vastly improves ROC and POP" (referenced from other studies).
- Caveats: bull-market sample; no management, no IV filter; higher-delta puts = ~3x returns for ~3x variance — no free lunch; short-call analog exists for bear markets.
- Quote: "the most efficient use of buying power in accounts with limited capital is selling the one standard deviation put."


## SYNTHESIS

### A. tastylive "mechanics" as a rule set (with the numeric evidence behind each)

| # | Rule | Value | Evidence (from the videos above) |
|---|------|-------|-----------------------------------|
| 1 | Entry DTE | 45 DTE (acceptable band 30-60) | "Right DTE" study: CVaR/DTE falls smoothly with DTE; at 45 DTE strangle CVaR/DTE ≈40, condor ≈17 with win rate still ~80%; 60-90 DTE "not enough extra bang", 1-30 DTE too much risk/day. 15-yr sweep: P&L peaks at ~21-24 days held. |
| 2 | Delta per structure | Strangle: 16-20Δ per leg (never >30Δ except high IV). Short put: 16-25Δ, 30-35Δ max (1-SD/16Δ for small accounts). Iron condor: same short strikes ~16-20Δ; wings ≥ 5 pts (SPY/QQQ/IWM) — <50 SPX pts behaves as condor, 60-90 pts ≈ synthetic strangle. 0DTE: 7-20Δ. | POT study: realized touch @21DTE for 20Δ ≈ 10-14% (≈0.3× delta), 25Δ ~24%; P50 study: P(50% profit before 21 DTE) 63/52/45/26% for 10/16/20/30Δ; $10k study: 1-SD put 94% win, ATM 79% win but 3.08× variance. |
| 3 | IV gate | IVR ≥ 30 (go zone); 20-30 acceptable "relative high end"; <20 pump the brakes; lower threshold to ~20 if a vol spike sits inside trailing 52w. IVR>50 rare in SPY (a couple times/yr). | IVR>30 → 66-68% chance VIX contracts ≥5% within 45d; 16Δ strangle avg daily P&L $0.25 (IVR<30) vs ~$1.15 (IVR>30) with same win rate; 20Δ put avg P&L $108 (low IVR) vs $367 (high IVR); need 30Δ vs 15Δ to collect 1% premium in low vs high IVR. |
| 4 | Profit target | 50% of max profit (credit) — take it; "if at 45% and asking, take it" | P50 study; 50%-management drove highest returns in bull market (naked put study) but with more portfolio vol than 21-DTE exit. |
| 5 | Time stop | Close/roll at 21 DTE regardless (≈24 days held). Undefined risk: mandatory. Defined risk: take profit if there, less urgent. | 15-yr sweep: SPY avg P&L +$20-40 at 20-24 days vs −$10..+$10 <20 days and declining after; CVaR explodes after day ~25-30 (SPY CVaR ~$1,400 at 40+ days). 0DTE vs 45DTE: weeks 1-4 earn +11%/+24% of credit at stdev 6%/12%; weeks 5-6 −2%; last week +16% at 3-4× risk. Gamma "grows teeth" ~14-10 DTE. Vol-control study: 21-DTE exit keeps portfolio daily vol tight at 25% AND 35% capital. |
| 6 | Loss / stop rule | tasty rarely uses a hard P&L stop on strangles/puts; the "stop" is the 21-DTE exit + small size + rolling untested side/rolling out in time. (Outside 0DTE trader: predetermined risk ratio on the short leg, roll only the short leg.) | Mechanics study: only profitable AAPL scenario was high IVR + 21 DTE; no equity scenario benefited from holding longer. |
| 7 | Sizing (% net liq) | Small: many occurrences; ~25% of portfolio committed as baseline; 35% raises returns AND daily swings sharply. Size up modestly in high IVR, down in low IVR. Per-trade: 1-2% of net liq at risk is the tasty norm implied by "stay small". | Capital-allocation study (25% → 35%): 21-DTE exit is the only variant whose vol stayed tight; 50%/expiration variants "way higher" vol at 35%. |
| 8 | Max BP usage | ~25-35% of net liq in short premium; keep dry powder to sell into 2-3% down days. Iron condors (not strangles) if account ≤ $15-25k; strangles at $30-50k+. | 3%-drop study: premium/BP 5% normally → 8% after big down days, ROC 1% → 2.5-3%, avg P&L >2× — only usable if BP is free. |
| 9 | Underlying filters | Highly liquid, tasty-default watchlist names/ETFs (SPY, IWM, QQQ, GLD, SLV, TLT…); tight bid/ask; avoid earnings inside the cycle (IV rises into earnings, ~2-week blackout); SPY is a valid proxy for other liquid underlyings (P50 & days-held ~same across SPY/GLD/SLV/AAPL/GOOGL/AMZN). Prefer index/ETF over single stocks (single-stock outliers dominate). SPX preferred for 0DTE (cash settled, no assignment/pin risk, ~2% entry / 5% exit friction). | Mechanics study (AAPL vs SPY), P50 study, Uber-driver interview. |
| 10 | Regime rule | Sell INTO vol expansion (2-3% SPY down days), not before it; be nimble — often close next day when vol contracts. Vol contracts/lulls ~90% of days; after VIX up-days P(next day down) ~60%. | 3%-drop study; VIX streak study. |

### B. Strongest criticisms / failure modes an automated bot must guard against

1. **Tail risk / CVaR is real and WILL be hit** — undefined-risk strangles/puts held past ~25-30 days show CVaR "off the charts"; even with 21-DTE exits, the worst 5% (gaps, MSTR-type −12% overnight moves) arrive. Bot must: hard-enforce 21-DTE exit, cap per-position and portfolio delta/vega, and never rely on the win rate (76-80%) as safety.
2. **Bull-market sample bias** — nearly every tasty study (2005-2023, 2009-2013, "last 10 years") is a rising-market sample; put-side realized touch (~10%) and 94% put win rates are regime-dependent. Bot should stress-test on 2008/2020/2022 and treat call-side risk symmetrically.
3. **Low-IVR premium selling is a loser in single stocks** — AAPL low-IVR was "a disaster"; SPY/IWM low-IVR + short holds → negative daily P&L, higher CVaR. Bot needs a hard IVR gate (≥30, or ≥20 post-spike) and should reduce size/skip rather than "get cute".
4. **IVR misuse** — IVR is skewed by outliers (post-COVID a 30-40 reads like 80-90 ex-spike; GLD IVR 114-117); IVR ≠ prediction. Bot must compute IVR consistently (52w range), handle >100 / <0, and prefer IV percentile or dual gates; don't set the gate at 50 (rarely triggers in SPY).
5. **Margin / BP blow-ups** — naked SPX 0DTE ≈ $120k BP/lot; strangle BP expands as vol rises and price moves against you; 35% committed capital roughly doubles daily swings. Bot must model BP dynamically, keep committed capital ≤ ~25-30% of net liq, and keep reserve for sell-into-fear entries; wings on condors buy BP reduction, NOT protection (help ~0.2% of the time).
6. **Slippage / friction** — SPX ≈ 2% of premium on entry, ~5% on exit, up to 10% in high vol; 20Δ SPY strangle credit ~$5 so "+11%" = $0.50 — commissions and spread can eat the early-cycle edge; Fed-day/first-minute price discovery has negative expectancy. Bot must use limit orders at/near mid, avoid the open and event minutes, and require minimum credit per unit BP (~5%).
7. **Assignment / pin risk on ETFs & stocks** — SPY/single-stock options settle in shares; early assignment on ITM short puts (dividends) and expiration-day pin risk. Bot must close before expiration week (21-DTE rule handles this) and monitor ITM short legs; prefer cash-settled index for very short DTE.
8. **Correlation illusion** — "diversified" baskets (GLD+SLV, single stocks vs SPY) move together in a sell-off; as the account grows you run out of uncorrelated underlyings and daily P&L swings scale (e.g. ±$10k on $50k fully deployed). Bot needs a correlation/beta-weighted delta cap, not just a position count.
9. **Implicit market timing & momentum bets** — a 45-day short premium trade is a bet on the next 45 days; directional credit spreads are momentum bets; chart-based "support" entries added no edge (MSTR/PLTR). Bot should stay mechanical: no discretionary technical filters, enter on schedule/IVR triggers, accept the 21-DTE exit even when it "would have recovered".
10. **Earnings & event risk** — IV rises into earnings, single-stock ranges are "a disillusion" (you WILL get tested). Bot must screen earnings dates inside the cycle and skip or size down.
11. **Cash-drag / over-trading in low vol** — the strategy demands sitting on cash at market highs; humans (and naive bots) fill idle capital with poor trades. Bot should treat "no trade" as the default when IVR gate fails.
12. **Structure-specific pitfalls** — straddles: half the win rate, 2× stdev, 2× CVaR/DTE (only in very high IV); iron condors: hard to adjust, low theta ($2/day vs $9-10 for the strangle in the GDXJ example), 21-DTE exit less urgent; 0DTE: far less edge, wild last-2-hour swings (stdev ≈5× expected gain), needs long tail hedges and end-of-day flat.
13. **Psychology → automation risk** — the human failure modes (checking daily, blaming picks, size creep after wins) become bot failure modes if size/limits aren't hard-coded; the 0DTE trader's fix was to size DOWN after profits so he never pays a "volatility tax" to exit.
