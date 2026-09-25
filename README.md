# Room X · The Observer's Ledger

**Live:** https://fractalyouniverse.org/Observer_ledger/ (also https://travisbergen2.github.io/Observer_ledger/)

A trading scaffold built on IMM Paper 18 (*Observer Requirements*): two bounded observers — one on a
euro-like tape, one on a gold-like tape — **estimate** a drifting world, **detect** the moment it changes,
and **commit** only when both agree, while a third system narrates every decision and pays every cost.
The room runs that scaffold, its arithmetic unmodified, on seeded synthetic markets — including a market
with no signal at all — keeps the ledger with friction on every fill, prints the scaffold's own
"anticipated" return beside what the tape then did, and measures the places where its blocks are not
coupled the way Paper 18 says a bounded observer's must be.

**Synthetic tapes only. No real prices, no backtest, no edge claim.** Paper 18 §11(8): "estimation is not
edge, and no profitability claim is made or implied."

## Files

| file | what |
|---|---|
| `index.html` | the room — self-contained; one external request (Google Fonts); no analytics; built from the template below |
| `receipts/ea/` | the scaffold, **verbatim** (`eurusd_reality.py`, `xauusd_reality.py`, `observer_ea.py`, `walk_forward.py`, `broker_adapter.py`, `make_smoke_data.py`, `requirements.txt`, its `README_scaffold.md`); sha256 of each file is recorded in `backtrader_receipt.json` |
| `receipts/backtrader_receipt.py` → `backtrader_receipt.json` | runs the unmodified scaffold under Backtrader 1.9.78.123 on its own 180-bar smoke tape and records the decision per bar, the 22 final model states, and Backtrader's own summary |
| `receipts/observer_twin.py` → `observer_twin.json` | the Python twin (pure Python): imports the reality models and `SyntheticBroker` from `ea/` verbatim, ports the Commit block of `observer_ea.py` line by line, generates the seeded worlds, runs the 24-seed census, the drift sweep and the five-pass replay; `--selftest` asserts the smoke run matches Backtrader bar for bar |
| `receipts/index.template.html`, `receipts/build_page.py` | the page is the template with the two JSON files injected |
| `receipts/node_check.js` | runs the page's JavaScript core headless under Node against the twin (no browser needed) |
| `receipts/prng_check.mjs` | the page's PRNG under Node, to confirm bit-identity with the twin's |
| `receipts/smoke/` | the smoke tape CSVs as `make_smoke_data.py` writes them |

Rebuild:

```
pip install -r receipts/ea/requirements.txt          # backtrader, only for the receipt
python3 receipts/backtrader_receipt.py
python3 receipts/observer_twin.py --selftest          # ~20 s
python3 receipts/build_page.py
node receipts/node_check.js                           # optional; ~1 s
```

## What the page computes, and checks

Everything on the page is recomputed in the browser — the mulberry32 PRNG, the tapes, both reality
models, the commit rule, the synthetic broker, the 24-seed × 4-world × 2-rule census, the 11-point
drift sweep and the five-pass replay — and compared with `observer_twin.json` (tolerance 10⁻⁹ relative;
decisions exact). The smoke-tape run is additionally compared with the scaffold under Backtrader: the
confirmed decision sequence (360 values) is identical and all 22 final model states agree to the last
bit, because in the scaffold decisions and learning never read equity and are therefore
broker-independent.

Worlds (all seeded, all named): **no signal** (log-price random walk, the kill control), **regime drift**
(Paper 18's model iii: drift ±s·σ per bar, sign re-drawn at rate 1/L; gold optionally copies the euro's
sign), **jumps** (no signal plus ±k·σ jumps at p = 0.02), **steady trend**, and the scaffold's own
deterministic **smoke tape**. Shocks are Irwin–Hall(12) sums so the tapes are platform-independent up to
the last bit; `exp`/`log` calls are covered by the tolerance.

## The brief, and what was delivered

The scaffold was produced by an AI agent (Manus) in September 2026 from this brief, in the author's words
(lightly punctuated; the extension list was the agent's own suggestion, accepted):

> "I am wanting a Python EA with Backtrader based on my Observer Requirements, set up where a three-file
> system is set up that two of the systems are forced to build a reality model of EURUSD and XAUUSD for
> the third one to function and make decisions in, and it learns from experience — and I want to let it
> learn the market by feeding it back data until it catches up and figures it out. I am running it on
> Windows … it should be extended with realistic spread, commission, slippage, leverage, position sizing,
> walk-forward validation, drawdown controls, and a broker-specific execution adapter."

| asked | delivered | measured (facts about the committed code) | standing |
|---|---|---|---|
| A Backtrader EA built on the Observer Requirements | a strategy whose variables are named for the paper's knobs (alpha = UE, integration_window = TI, gain = SG, criterion = FT); README: "follows the paper's conditional synthesis" | the three couplings the synthesis consists of are absent (below); the README's "a detected change resets evidence and temporarily increases the update rate" is true of a line of code and false of every run, because the detection never comes | names delivered; couplings not |
| two systems forced to build a reality model of each market | two files, one algorithm with different constants; a drift estimate and a volatility per bar | the estimate block works; the model is never asked whether it is right — feedback scores the trade, not the forecast; no forecast distribution, calibration or skill measure | shape delivered |
| …for the third to function and decide | the third raises unless exactly two feeds exist and lets each market veto the other | a veto is a gate, not a use of the models; the transcript's "blocked" clause is unreachable | gate delivered |
| it learns from experience | gain and criterion move on each acted bar's outcome | 0 of 360 decisions changed on its own tape; criterion ratchets to its ceiling below 80 % accuracy and is read only by a detector that never fires | inert |
| feed it back data until it catches up | a JSON checkpoint carried between runs; walk-forward with learning off in test | replayed five times with the checkpoint carried: euro criterion 3.575 → 4.500 → 4.500 (gold 5.0, ceiling), α 0.064 → 0.041 → 0.025 (floor; window 20 → 80 bars), detections 0, **decisions changed 0**, equity unchanged to the cent; "catching up" has no definition in the code | inverted |
| Windows | PowerShell, `py` launcher, venv | — | delivered |
| realistic spread, commission, slippage | half-spread in Backtrader's slippage model, commission on notional, a `SyntheticBroker` charging both | present with generic defaults the README says to replace | delivered (defaults) |
| leverage, position sizing | risk-fraction sizing against a volatility stop proxy, 3× cap | the cap held in sizing and failed in execution (stacked orders → −580 oz short, 14× equity); the stop exists as a number, never as an order | delivered on paper |
| walk-forward validation | chronological train/test windows | returns only; no baseline, no forecast metric, no exposure accounting | minimal |
| drawdown controls | 20 % peak-to-equity halt | fired on its own tape because of the execution fault; no daily limit | minimal |
| a broker-specific execution adapter | an interface, a paper adapter, and a stub that refuses | the one place the build declined to pretend | correctly declined |

Credit where it is earned: the refusal in the last row, the "anticipated, not realized" labelling in the
transcript, and the fee mechanics are sound. The rest is a set of correctly named parts whose names
describe the paper and not the behaviour. The build's React display layer was not ported: its broker
figures, forecast/realised bars and dial labels were hardcoded, and its "realised" outcomes were
generated from the anticipated ones by a formula (`generate_observer_archive.py`); the room replaces all
of it with live computation. The design that would deliver the brief is a separate document.

## What the room found in the scaffold (facts about the committed code)

1. **The commit threshold never binds.** `state.evidence` already accumulates `return_mean / vol`; the
   commit rule divides it by volatility again before comparing with a criterion of 2.5–4.5, so the
   observer acts on any nonzero evidence (smoke tape: 282/282 eligible bars over threshold, peak ratio
   1582×). The transcript prints the same doubly-normalised number ("evidence 1680.491 cleared threshold
   2.625"). A labelled **dimension-corrected variant** (compare `state.evidence` directly) is on the page;
   it is a variant, not the scaffold.
2. **The detector is blind by construction.** The innovation is standardised by a true-range volatility
   (≈ 1.8 σ of the close-to-close move) that has already absorbed the bar being judged: a 5 σ jump lands
   as ≈ 1.9 against a criterion of 2.5. Median across the census: 0.5–1 detections per 1,000 bar-steps.
   Since a detection is the only thing that resets the evidence accumulator, the observer holds a
   direction for as long as it took to build it (smoke tape: long on all 141 post-warm-up bars of a tape
   that reverses every 18 bars; never short).
3. **The update rate is a clock.** While no alarm fires the window is 2/α, so the rule
   α ← 0.995·α + 0.005/window is exactly α ← 0.9975·α: a geometric run-down to the floor 0.025 with no
   term that reads the data (smoke tape: 0.1 × 0.9975¹⁷⁹ = 0.0639, the ledger value to four digits).
   The "adaptive" estimate adapts only through the detector — see 2.
4. **Learning raises a bar the observer never uses.** Feedback moves the criterion +0.02 per wrong call
   and −0.005 per right one (break-even 80 % accuracy). The criterion is read only by the detector, so
   losing makes alarms rarer and direction locks longer. On the smoke tape learning changed 0 decisions.
5. **Replay does nothing but deafen.** Five passes with the checkpoint carried change 0 decisions
   (smoke tape and regime world alike); criterion → ceiling, α → floor.
6. **The transcript misreports blocked commits.** The "blocked because the other market disagrees" clause
   is unreachable (a blocked action is 0, and the 0-branch is tested first).
7. **By its own arithmetic the observer trades against its forecast:** on the smoke tape 227 of 282
   commits had a negative anticipated net return after the configured friction.
8. **Under Backtrader the ledger is not the documented cage.** Backtrader held the per-bar target orders
   from bar 49 to bar 72 and filled all of them at bar 73's open (22 stacked "sell 26 oz" orders → a
   −580 oz gold short, 14× equity against a 3× cage), then −19.5 % and a drawdown halt at bar 85; the
   scaffold never cancels a still-pending order before submitting the next. The page's ledger uses the
   scaffold's own `SyntheticBroker` with each target filled at the next open (+0.78 % on the same
   decisions). Both numbers are shown.

None of this is evidence about a market. Repairing the scaffold is a design (a separate document);
testing a repair would be a registered experiment; neither happens on this page.

## Operating characteristic (from `observer_twin.json`, 24 seeds × 500 bars)

Median return: no signal −1.02 % (friction), jumps −0.39 %, regime drift at 0.25 σ/bar +1.49 %, steady
trend +26.5 % (that is the world, not the observer). The regime-world median return crosses zero near
0.15 σ per bar — a within-regime Sharpe ratio of roughly 2.4 if bars were days; a liquid daily market
with annual Sharpe 0.4 sits at 0.4/√252 ≈ 0.025, and even that is unconditional drift, not a regime the
observer may count on.

Companion room: [Bergen's Game of Life](https://travisbergen2.github.io/Bergen-s_Game_of_Life/)
(Room IV). Built 2026-09-25 by Travis Bergen with the Riemann agent. Instruments, not proofs.
