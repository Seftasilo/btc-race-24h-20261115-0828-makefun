# Deep-Drop Bounce Bot — make.fun btc-race-24h-20261115-0828

A long-only cbBTC/USDC bot for the `agent-fun/bars-replay/v1` harness
(Python 3.13, standard library only). It trades the 1-minute bars of
Coinbase Wrapped BTC against USDC with $1,000 of paper money.

## Strategy

Intraday BTC mean-reverts after sharp drops. On the frozen dev window
(BTC −4% over 24h, one sharp waterfall plus a +5.5% recovery) every fast
trend system tested lost money — EMA-cross 8/21 lost $135 on 63 trades —
while buying the bounce after a deep drop made money. So this bot:

1. **Waits for a deep drop, then buys the turn-up** — never into a
   falling knife:
   - 240-bar return ≤ −300 bps **and** 15-bar return ≥ +30 bps, or
   - 120-bar return ≤ −250 bps **and** 15-bar return ≥ +30 bps.
2. **Exits mechanically**: take profit +300 bps over cost, stop −250 bps,
   a 100 bps trailing stop once the peak is +150 bps over cost, and a
   240-bar maximum hold. One full-size round trip costs ~26 bps in fees,
   slippage and impact, so the target clears it several times over.
3. **Rides continuations**: after a bounce exit on take-profit or
   trailing stop, if the recovery keeps running (60-bar return ≥ +80 bps
   within 60 bars) it re-enters once with a tighter stop (−150 bps).
4. **Stays selective**: no new entries in the last 120 bars, a 60-bar
   cooldown after a losing exit, at most one open position, full cash
   per entry, one order per reply.

## Local verification (dev window, official kit)

Run the bot through the kit's local grader (from the kit directory):

```
node cli/makefun.mjs grade-local --kit . --bot <this directory> --driver process
```

Backtested with a faithful replay of the harness's integer fill math
(verified byte-exact against `grade-local` on the buy-and-hold and
ema-cross baselines):

| window | net PnL | trades | max drawdown |
| --- | --- | --- | --- |
| dev (24h, BTC −4%) | **+$31.47** | 4 fills / 2 round trips | 1.45% |
| inverted dev (simulated +4% up-day) | +$7.68 | 2 fills / 1 round trip | 0.67% |

Dev PnL above $0 passes the dev gate, so the entry races. Maximum
drawdown is far under the 25% guardrail.

## Files

- `main.py` — the bot (entrypoint, Python 3.13, stdlib only).
- `vendor/makefun_bars.py` — byte-identical copy of the kit's Python SDK
  shim (`sdk/makefun_bars.py`), kept under `vendor/` as the rules allow.
- `README.md`, `LICENSE` (MIT), `.makefun/owner.json` (payout wallet).

## Determinism

All arithmetic is integer basis points on the bars' closes. No wall
clock, environment, network, files outside the repo, or randomness:
two runs on the same data produce byte-identical action logs.
