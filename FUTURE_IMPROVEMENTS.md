# Future Improvements

## Portfolio fit score with empty portfolio

Observed behavior:

- When the portfolio is empty or almost empty, `portfolio_fit_score` tends to be nearly identical across assets.
- This happens because all assets receive similar bonuses:
  - asset underweight
  - sector underweight
  - asset type underweight
  - no cash penalty

Why this matters:

- The score is technically consistent with the current rules.
- However, it becomes less informative in the initial state of the portfolio.

Potential future improvements:

- Differentiate more strongly by asset class target weights even when the portfolio is empty.
- Apply a small structural penalty or lower base fit for higher-risk buckets such as crypto.
- Add a diversification-aware bonus that depends on how much a candidate improves sector balance.
- Add a "cold start" mode for `portfolio_fit_score` when there are no positions yet.
- Prevent near-uniform scores in the first-run experience so the ranking is more useful.

Implementation note:

- This should be addressed in `services/rebalance_service.py` without changing the rest of the architecture.
- Keep the logic explainable and configurable from `config/portfolio_rules.yaml`.
