# Dashboard roadmap (built slowly, one question a day)

Goal: screen for value-investing opportunities and avoid stocks that aren't shareholder friendly or carry red flags
that lose money. Changes are small and incremental.

## Decisions so far
- P/E basics live: trailing and forward P/E, 5y/10y mean and median, "current vs median/mean" % columns,
  colour on the current P/E (green = below all four history figures, red = above all four), "cheaper than all history"
  filter, max trailing / max forward P/E filters. Frozen columns: Ticker through Forward.

## Candidate metrics (not yet chosen; ask about these one at a time)
- Quality: ROE / ROIC, gross and operating margin trend, FCF margin
- Balance sheet: net debt / EBITDA, debt / equity, interest coverage, current ratio
- Shareholder friendliness: net buyback yield, share-count change over 3-5y, dividend yield and payout ratio,
  stock-based comp as % of revenue (dilution)
- Red flags: Altman Z, Piotroski F-score, accruals (earnings vs cash flow), insider selling, rising receivables or
  inventory vs sales, going-concern or restatement mentions
- Other valuation: EV/EBITDA, P/FCF, PEG, FCF yield

## Questions asked (date - question - answer - what we did)
- 2026-11-18 - What to add next? (balance-sheet safety / shareholder friendliness / quality / red-flag score) - Balance-sheet safety (user took my recommendation) - No debt data in the pipeline yet, so user then said ok to the EDGAR plan. Built (not yet committed/pushed): src/leverage.py (SEC companyfacts -> net debt/EBITDA, interest cover), 2 new columns + "max net debt / EBITDA" filter + colours in docs/index.html. Local test: 51 of 96 SEC tickers get a value.

- 2026-11-19 - Data health recs (n/m for huge P/E, cap interest cover, flag SEC/Yahoo mismatch) - "do all 3" - n/m (>200) and the ≠ flag were already live; added ">100" display cap for interest cover in docs/index.html (not yet committed/pushed).

- 2026-11-22 - Huge trailing P/E (e.g. CRWD 3519): (a) n/m and exclude, (b) grey out, (c) leave? - Took recommendation (a); kept NM_PE threshold at 200 (not 500, which would let LSCC 338 through) - already live.
- 2026-11-20 - Next metric: net buyback yield / share-count change, SBC %, or dividends? - Option 1: buyback yield / share-count change (user, 2026-11-22) - Built 2026-11-22 (uncommitted): 'Shares 3y %' column (src/leverage.py share_change, SCHEMA 2 refetches all; pipeline column; green <= -2%, red >= +5%; >40% hidden as likely split). Spot-checked AAPL -6.9, MSFT -0.3, META -4.7, INTC +9.9, NVDA -2.2. No filter yet.

## Data quality (built 2026-11-18)
- src/health.py runs before outputs are written: aborts the run (Action goes red, yesterday's data stays) on a >10% row drop or
  >30% fewer trailing P/Es; otherwise puts warnings (odd P/Es, big price jumps, SEC-vs-Yahoo net debt/EBITDA mismatch,
  tickers added/removed) in latest.json and a banner on the page. ZTS left the table 11-18 because it was removed from the TradingView list.

## Backlog / ideas
- Data health 2026-11-19: trailing P/E for CRWD (3519), PANW (668), SPCX (617), LSCC (338) vs forward P/E is flagged every run (tiny GAAP EPS, not a bug); consider showing "n/m" or "n/a" for P/E > ~500 so it can't distort filters/colours. FN interest cover 1286.7 implausible (cap at ">100"). SEC-vs-Yahoo net debt/EBITDA mismatches (COHR, INTC, TSLA, CNC) recur: likely differing debt/cash definitions (leases, short-term investments).
- Data health 2026-11-21 (latest run 11-20, OK/fresh, 104 rows): SNDK price 86.33 flagged -90% vs last run with trailing P/E 0.7 / forward 2.4 and GAAP-fallback EPS (likely bad EPS basis or spin-off history; check it). Same recurring CRWD/PANW/SPCX/LSCC/FN/RKLB (fwd 687) and COHR/INTC/TSLA/CNC mismatches as 11-19. Scheduled runs appear at ~16:00-16:13 ET, not 9:45 AM ET as labelled in asof; worth checking the cron/time basis.
- Balance-sheet safety follow-ups: blanks for ~45 tickers (non-US filers, banks/REITs skipped, or debt tagged unusually); captive-finance arms (GM, F) look too safe; consider Yahoo fallback for blanks. Original plan: Add net debt/EBITDA and interest coverage columns plus a "max net debt/EBITDA" filter
  (suggested default 4). Needs new pipeline work: pull debt, cash, operating income + D&A, interest expense from SEC
  XBRL (src/edgar.py already fetches EPS); non-US tickers will show blank. Colour: green <2, red >4 (to confirm).
- Share-count change (chosen 2026-11-22), planned small build: in src/leverage.py compute() (or a new shares.py) take annual diluted weighted-average shares (us-gaap WeightedAverageNumberOfDilutedSharesOutstanding, FY facts from 10-K, units 'shares') and output share_chg_3y_pct = latest FY vs 3 FY earlier (annualised % optional). Bump SCHEMA to refetch. Add column "Shares 3y %" to pipeline COLUMNS + docs/index.html: green if <= -2% (buying back), red if >= +5% (diluting), plus an optional filter. Beware splits (SEC data is not always split-adjusted: skip/flag if |change| > 40%). Non-US tickers blank. Later: true net buyback yield from cash-flow PaymentsForRepurchaseOfCommonStock minus StockIssued.
