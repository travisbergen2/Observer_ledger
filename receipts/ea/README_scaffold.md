# Three-File Backtrader Observer EA

This is a **research and paper-backtest scaffold**, not a live-trading bot. It translates the attached Observer Requirements paper into three coupled components:

| File | Block | Responsibility |
|---|---|---|
| `eurusd_reality.py` | Estimate + Detect | Builds and updates an EURUSD reality model with adaptive update elasticity, volatility-normalized change detection, and resettable evidence. |
| `xauusd_reality.py` | Estimate + Detect | Builds and updates an independent XAUUSD reality model with separate parameters and learning state. |
| `observer_ea.py` | Commit | Backtrader strategy that requires both models, applies cross-market confirmation, sizes positions, applies costs and drawdown controls, records actions, and feeds realized outcomes back into each model. |
| `walk_forward.py` | Validation | Creates chronological train/test windows, learns only on the training segment, and evaluates the next segment with learning feedback disabled. |
| `broker_adapter.py` | Synthetic execution boundary | Defines a broker-neutral interface and a deterministic synthetic broker with cash, equity, margin, positions, spread, slippage, and commission. No live broker is connected. |

## Windows installation and run

Open **PowerShell** in this folder:

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
py -m pip install -r requirements.txt

py observer_ea.py `
  --eurusd data\eurusd.csv `
  --xauusd data\xauusd.csv `
  --checkpoint state\observer_state.json `
  --experience-log state\experience.jsonl `
  --transcript-log state\observer_transcript.txt
```

If PowerShell blocks activation, run the command without activation using `.venv\Scripts\python.exe`, or adjust the execution policy only according to your own Windows administration rules.

## Data contract

Each input CSV must contain a header and six columns in this order:

```text
datetime,open,high,low,close,volume
```

The default datetime format is `YYYY-MM-DD HH:MM:SS`. Both feeds should use the same timeframe and aligned timestamps. Missing or asynchronous data must be cleaned before a run; this build does not silently forward-fill market data.

## Risk and market-friction controls

The default paper backtest now includes:

- **Spread:** `--spread-bps 1.5`; half the spread is added to each simulated execution side.
- **Commission:** `--commission-pct 0.00002`; applied to notional value.
- **Slippage:** `--slippage-pct 0.00005`; applied through Backtrader's percentage slippage model.
- **Leverage:** `--max-leverage 3`; position notional is capped relative to equity.
- **Position sizing:** `--risk-fraction 0.005`; quantity is sized so a volatility-based stop risks approximately 0.5% of current equity.
- **Drawdown control:** `--max-drawdown 0.20`; once peak-to-equity drawdown reaches 20%, the strategy flattens positions and halts new exposure.
- **Stop-distance proxy:** `--stop-vol-multiple 2.5` and `--min-stop-pct 0.002`; these are used for sizing, not submitted as live stop orders.

These are generic research defaults. Replace them with values from the intended broker, account currency, contract size, spread schedule, commission schedule, and execution venue before treating results as meaningful.

## Walk-forward validation

Run chronological training and evaluation windows from PowerShell:

```powershell
py walk_forward.py `
  --eurusd data\eurusd.csv `
  --xauusd data\xauusd.csv `
  --train-bars 20000 `
  --test-bars 5000 `
  --step-bars 5000 `
  --output state\\walk_forward_results.json
```

Each window trains on its earlier segment and saves a checkpoint. The following test segment uses that checkpoint, but disables gain/criterion feedback learning. Short-term state estimation still processes test bars because that is inference, not outcome-based retraining. Results include train/test return, final equity, trade count, and drawdown-halt status per window.

Do not select parameters on the final test period. Use a separate development period for tuning, then report the untouched test period once.

## Broker adapter boundary

`broker_adapter.py` contains:

- `BrokerAdapter`: the minimum interface the decision layer may depend on.
- `PaperBrokerAdapter`: deterministic in-memory behavior for integration tests.
- `LiveBrokerAdapterNotConfigured`: an explicit failure instead of accidental live execution.

A concrete adapter cannot be safely invented without knowing the broker, symbol format, account currency, contract size, margin rules, order types, authentication, rate limits, and failure/reconciliation behavior. The `--execute` flag therefore refuses to run.

## Mapping to the observer requirements

The architecture follows the paper's conditional synthesis: **estimate**, **detect**, and **commit**. The adaptive `alpha` is the update-elasticity control; `integration_window` is its effective temporal integration; normalized innovation and `criterion` implement the signal-gain/filter-threshold channel; evidence accumulation and thresholded actions implement the discrete commitment channel. A detected change resets evidence and temporarily increases the update rate.

This is an identification of market features with observer variables, not a proof that markets obey the paper's assumptions. The paper's own limits remain active: the regime-switching interpretation is an assumption, the signal is endogenous here, and the matching laws must be tested out of sample.

## Validation before external execution

1. Use time-ordered train/validation/test periods and never use final-test outcomes to update the checkpoint.
2. Compare the observer against buy-and-hold, fixed-window EMA, and no-cross-confirmation ablations.
3. Use venue-specific spread, commission, slippage, rollover, latency, and contract-size assumptions.
4. Track maximum drawdown, turnover, exposure, margin utilization, trade expectancy, calibration, and performance by volatility/regime—not only net profit.
5. Add broker-side reconciliation, idempotent client order IDs, connection-loss handling, a manual kill switch, and persistent audit logs before live use.
6. Paper trade for a sustained period and verify that simulated fills and account equity reconcile with the broker's demo environment.

The strategy is **not a promise of profitability** and is not ready for unattended live trading merely because a backtest is positive.

## Demonstration transcript

The third system writes two synchronized outputs for every bar. `experience.jsonl` contains machine-readable state, actions, positions, and forecasts. `observer_transcript.txt` contains a human-readable explanation such as:

```text
[2024-01-04T18:00:00] EURUSD: BUY because evidence 4.812 cleared threshold 3.101; confirmed by the other market. Anticipated next-bar gross return +0.0180%, estimated spread/slippage/commission 0.0210%, and anticipated net return -0.0030%.
```

Each forecast is explicitly labeled **anticipated**, not realized. It reports the model's one-bar directional expectation, subtracts the configured synthetic spread/slippage/commission estimate, and later bars provide the actual feedback used for learning. This makes the third room suitable for showing both the observer's reasoning and the difference between an anticipated return and the realized result.
