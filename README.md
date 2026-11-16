# P/E Dashboard

Daily valuation table for the stocks in the TradingView "Stocks" watchlist, published at
**https://dunbok.github.io/pe-dashboard/**. Built every NYSE trading day at 9:45 AM ET by GitHub Actions.

See the original plan for the full spec. How it works:

| Piece | File |
|---|---|
| Watchlist scrape, symbol mapping, equity filter | `src/watchlist.py`, `symbol_overrides.json` |
| SEC EDGAR GAAP EPS cache | `src/edgar.py` -> `data/eps_edgar/` |
| Prices + splits + FX | `src/prices.py` -> `data/prices/` |
| Trailing P/E history (point-in-time, split-adjusted) | `src/trailing.py` |
| Alpha Vantage adjusted EPS, 25 calls/day budget | `src/alphavantage.py` -> `data/eps_av/`, `data/av_backfill_state.json` |
| Approximate forward P/E history | `src/forward.py` |
| Table assembly | `src/pipeline.py` -> `data/daily/*.csv`, `data/fwd_pe_history.csv`, `docs/data/` |
| Run-time gating (DST-safe, NYSE calendar, waits for 9:45) | `src/schedule.py`, `tests/test_schedule.py` |
| Dashboard | `docs/index.html` |
| Workflow | `.github/workflows/daily.yml` |

## Run locally

```powershell
pip install -r requirements.txt
$env:SEC_USER_AGENT = "pe-dashboard you@example.com"      # required by the SEC
$env:ALPHAVANTAGE_API_KEY = "..."
python -m src.run --force --only NVDA,AAPL                # test run (flagged as a test, never blocks the real run)
python tests/test_schedule.py                             # cron / DST / holiday simulations
```

## Repo settings it relies on

- Secrets: `ALPHAVANTAGE_API_KEY`, `SEC_USER_AGENT`
- Pages source: **GitHub Actions**
- GitHub disables scheduled workflows after 60 days without repository activity. The daily data commits normally
  count, but if the schedule ever stops, re-enable it from the Actions tab.
