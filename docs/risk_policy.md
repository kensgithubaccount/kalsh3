# Risk Policy

Hard limits: $1,000 bankroll; $700 protected reserve; $300 initial active capital; $100 aggregate open risk; $10 loss per market; $25 related-event risk. Loss stops are $20 daily, $50 weekly, $100 monthly, and $200 total experiment drawdown.

The active-capital allowance is the lesser of $300 and reconciled experiment equity above the $700 reserve, less committed positions, resting orders, and live risk reservations. Profit does not expand this allowance. Exposure checks assume complete fills and include maximum fees; expected fill probability and forecast quality never reduce hard risk.

Daily windows follow calendar days in `America/New_York`; weeks begin Monday and months use the local calendar month. Daily holds may clear at the next risk day, while weekly/monthly human-review states and the experiment drawdown halt are durable governance states. The experiment high-water mark is never silently reset.

Deterministic controls override every strategy and learning component. Learners, models, environment variables, requests, and dashboards cannot increase financial limits. Missing or stale financial information rejects risk. Global halt and compliance hold are independent durable controls; restart preserves safety state. M13 performs no Kalshi mutation and a pass means only `RISK CHECK PASSED` / `PASS_NEXT_GATE`.
