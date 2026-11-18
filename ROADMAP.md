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

## Data quality (built 2026-11-18)
- src/health.py runs before outputs are written: aborts the run (Action goes red, yesterday's data stays) on a >10% row drop or
  >30% fewer trailing P/Es; otherwise puts warnings (odd P/Es, big price jumps, SEC-vs-Yahoo net debt/EBITDA mismatch,
  tickers added/removed) in latest.json and a banner on the page. ZTS left the table 11-18 because it was removed from the TradingView list.

## Backlog / ideas
- Balance-sheet safety follow-ups: blanks for ~45 tickers (non-US filers, banks/REITs skipped, or debt tagged unusually); captive-finance arms (GM, F) look too safe; consider Yahoo fallback for blanks. Original plan: Add net debt/EBITDA and interest coverage columns plus a "max net debt/EBITDA" filter
  (suggested default 4). Needs new pipeline work: pull debt, cash, operating income + D&A, interest expense from SEC
  XBRL (src/edgar.py already fetches EPS); non-US tickers will show blank. Colour: green <2, red >4 (to confirm).
