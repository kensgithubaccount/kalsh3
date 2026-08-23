# M28D forecast-vintage and historical economics boundary

M28D adds two pure evaluation layers:

1. **Forecast vintage evidence** — only source publications at or before a predeclared decision cutoff may become model features. Later retrieval is allowed; later publication is not.
2. **Historical checkpoint economics** — one-contract TAKER economics are reconstructed from same-checkpoint public quotes plus an explicitly reviewed fee formula.

M28D deliberately does **not** claim historical fill truth. Queue position, depth, slippage, passive-fill probability, and final charged exchange fees remain separate execution-learning evidence.

This milestone has no network transport, credential access, account reads, production-state mutation, risk authorization, approval, execution authorization, burn, or order path.
