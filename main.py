"""Deep-drop bounce bot for make.fun btc-race-24h-20261115-0828.

Trades cbBTC/USDC on 1-minute bars, long only, starting from $1,000 paper.
Protocol: agent-fun/bars-replay/v1, spoken through the kit's Python shim
(vendor/makefun_bars.py, a byte-identical copy of sdk/makefun_bars.py).

Idea (from the frozen dev window, 24h ending at the open: BTC fell 4% with
one sharp waterfall and a +5.5% recovery; every fast trend system tested
lost money there, EMA-cross 8/21 lost $135 on 63 trades):
  * Intraday BTC mean-reverts after sharp drops: after a 60-bar drop of
    150+ bps the next 60 minutes averaged +139 bps (median +118 bps).
    So the bot waits for a DEEP multi-hour drop, then buys only once the
    tape turns up (15-bar return >= +30 bps) -- never into a falling knife.
  * Two triggers: a 240-bar drop of 300+ bps (the waterfall bottom) and a
    sharper 120-bar drop of 250+ bps (a fast flush).
  * Exits are mechanical: take profit +300 bps over cost, stop -250 bps,
    a 100 bps trailing stop once the peak is +150 bps over cost, and a
    240-bar maximum hold. A full-size round trip costs ~26 bps in fees,
    slippage and impact, so the +300 bps target clears it several times over.
  * Rider: after a bounce exit on take-profit or trailing stop, if the
    recovery keeps running (60-bar return >= +80 bps within 60 bars) the
    bot re-enters once to ride the continuation, with a tighter stop.
  * No new entries in the last 120 bars: a trade needs room to play out
    before the window flattens everything at the last close.
  * At most one open position, full cash per entry, one order per reply.

Everything is integer arithmetic on the bars' closes (basis points via
integer division); no wall clock, no environment, no network, no
randomness -- two runs on the same data produce the same orders.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
import makefun_bars

SYMBOL = "CBBTC"

# Entry: deep drop over a long window, confirmed by a short turn-up.
DROP_LONG_BARS = 240
DROP_LONG_BPS = -300
DROP_FAST_BARS = 120
DROP_FAST_BPS = -250
TURN_BARS = 15
TURN_BPS = 30

# Rider: continuation after a profitable bounce exit.
RIDE_WINDOW_BARS = 60
RIDE_MOM_BARS = 60
RIDE_MOM_BPS = 80

# Exits, in basis points over the position's cost (the engine's cost is the
# buy notional, fee included).
BOUNCE_TP_BPS = 300
BOUNCE_STOP_BPS = 250
BOUNCE_TRAIL_BPS = 100
BOUNCE_TRAIL_ARM_BPS = 150
RIDE_TP_BPS = 300
RIDE_STOP_BPS = 150
RIDE_TRAIL_BPS = 120
MAX_HOLD_BARS = 240
COOLDOWN_BARS = 60
NO_ENTRY_LAST_BARS = 120
WINDOW_BARS = 1440

MAX_SLIPPAGE_BPS = 150
# Buys get a wider cap: after a sharp drop the bounce itself can gap the
# next open more than 1.5% above the close the signal was decided on, and a
# rejected buy misses the bounce entirely; the stop caps the cost of a
# worse fill. Sells keep the tighter cap: a sell past it is refused and
# sent again next bar while the exit reason still holds.
BUY_MAX_SLIPPAGE_BPS = 300
SELL_MAX_SLIPPAGE_BPS = 150


class Bot:
    """Holds the closes and the position state machine."""

    def __init__(self):
        self.closes = []
        self.leg = None          # "bounce" | "ride" | None
        self.peak = None         # best position value (quote units) while holding
        self.hold = 0            # bars held
        self.cool = 0            # bars left of cooldown after a losing exit
        self.ride_window = 0     # bars left in which a rider entry is allowed
        self.last_exit = None    # "tp" | "trail" | "stop" | "hold"
        self.had_position = False

    def ret_bps(self, lb):
        """Return over the last lb bars, in basis points (integer)."""
        if len(self.closes) < lb + 1:
            return None
        now = self.closes[-1]
        ago = self.closes[-1 - lb]
        return (now - ago) * 10000 // ago

    def on_batch(self, state, batch):
        if state.warming_up:
            for bar in batch["bars"]:
                if bar["symbol"] == SYMBOL:
                    self.closes.append(int(bar["c"]))
            return []

        for bar in batch["bars"]:
            if bar["symbol"] == SYMBOL:
                self.closes.append(int(bar["c"]))

        holding = state.positions.get(SYMBOL)

        # The position just closed: apply the exit transition once.
        if holding is None and self.leg is not None:
            if self.had_position:
                if self.last_exit in ("tp", "trail") and self.leg == "bounce":
                    self.ride_window = RIDE_WINDOW_BARS
                    self.cool = 0
                else:
                    self.cool = COOLDOWN_BARS
            self.leg = None
            self.peak = None
            self.hold = 0
            self.last_exit = None
            self.had_position = False

        if self.cool > 0:
            self.cool -= 1
        if self.ride_window > 0:
            self.ride_window -= 1

        if holding is not None:
            self.had_position = True
            self.hold += 1
            value = state.value(SYMBOL, holding.qty)
            if self.peak is None or value > self.peak:
                self.peak = value
            cost = holding.cost
            leg = self.leg if self.leg is not None else "bounce"
            if leg == "bounce":
                tp_bps, stop_bps = BOUNCE_TP_BPS, BOUNCE_STOP_BPS
            else:
                tp_bps, stop_bps = RIDE_TP_BPS, RIDE_STOP_BPS
            reason = None
            if value >= cost * (10000 + tp_bps) // 10000:
                reason = "tp"
            elif value <= cost * (10000 - stop_bps) // 10000:
                reason = "stop"
            elif leg == "bounce" and self.peak is not None and \
                    self.peak >= cost * (10000 + BOUNCE_TRAIL_ARM_BPS) // 10000 and \
                    value <= self.peak * (10000 - BOUNCE_TRAIL_BPS) // 10000:
                reason = "trail"
            elif leg == "ride" and self.peak is not None and \
                    value > cost and \
                    value <= self.peak * (10000 - RIDE_TRAIL_BPS) // 10000:
                reason = "trail"
            elif self.hold >= MAX_HOLD_BARS:
                reason = "hold"
            if reason is not None:
                # Keep leg/peak until the fill is confirmed gone; if the
                # sell is rejected the same exit is sent again next bar.
                self.last_exit = reason
                return [{"side": "sell", "symbol": SYMBOL, "qty": holding.qty,
                         "max_slippage_bps": SELL_MAX_SLIPPAGE_BPS}]
            return []

        # Flat: look for an entry.
        if self.cool > 0 or self.leg is not None:
            return []
        if state.seq >= WINDOW_BARS - NO_ENTRY_LAST_BARS:
            return []
        r240 = self.ret_bps(DROP_LONG_BARS)
        r120 = self.ret_bps(DROP_FAST_BARS)
        r60 = self.ret_bps(RIDE_MOM_BARS)
        r15 = self.ret_bps(TURN_BARS)
        if r240 is None or r15 is None:
            return []
        leg = None
        if r240 <= DROP_LONG_BPS and r15 >= TURN_BPS:
            leg = "bounce"
        elif r120 is not None and r120 <= DROP_FAST_BPS and r15 >= TURN_BPS:
            leg = "bounce"
        elif self.ride_window > 0 and r60 is not None and r60 >= RIDE_MOM_BPS:
            leg = "ride"
        if leg is None:
            return []
        cash = state.cash
        if cash < int(state.hello["fees"]["min_notional_quote_units"]):
            return []
        self.leg = leg
        self.peak = None
        self.hold = 0
        self.had_position = False
        return [{"side": "buy", "symbol": SYMBOL,
                 "notional_quote_units": cash,
                 "max_slippage_bps": BUY_MAX_SLIPPAGE_BPS}]


makefun_bars.run(Bot().on_batch)
