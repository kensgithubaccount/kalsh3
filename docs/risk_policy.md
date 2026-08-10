# Risk Policy

Hard limits: $1,000 bankroll; $700 protected reserve; $300 initial active capital; $100 aggregate open risk; $10 loss per market; $25 related-event risk. Loss stops are $20 daily, $50 weekly, $100 monthly, and $200 total experiment drawdown.

The first 50 live fills default to one contract per order, at most ten simultaneous positions, with no martingale, averaging down, or automatic scaling. Deterministic controls override all strategies and learning. Learners may never increase financial limits. Missing information rejects risk. Global halt and compliance hold are independent, durable controls; restart is disarmed.
