"""Ultra-realistic Walk-Forward Deriv Multiplier Simulator & Data Loaders.

Models:
- Exact Deriv MULTUP/MULTDOWN opening commission & 2-decimal rounding
- Exact bid-ask spread (adverse on entry and exit)
- Exact individual contract stop-out checked intrabar on sub-bars (M1 for BTC, M5 for FX/R_75 M15)
- Conservative intrabar ordering: stop-out & SL evaluated BEFORE TP on each sub-bar
- +1 bar / latency sensitivity stress test
- Strict chronological 70% Dev / 30% Virgin Holdout Walk-Forward split
- Automated Crash Stress Test isolating worst -3% to -8%+ drawdown episodes
"""

from __future__ import annotations

import asyncio
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from strategy_core import (
    BasketCycle,
    LayerPosition,
    StrategyConfig,
    compute_dynamic_dca_step,
    compute_indicators,
)


@dataclass(frozen=True)
class AssetSpec:
    """Real broker/contract parameters for Deriv Multipliers (or counterfactual)."""
    name: str
    symbol: str
    multiplier: float
    commission_pct_of_stake: float  # e.g. 0.07 for cryBTCUSD 100x ($0.07 per $1.00)
    spread_bps: float  # full round-trip spread in bps (e.g. 0.31 bps for BTC)
    round_comm_cents: bool = True
    min_comm_usd: float = 0.01
    is_counterfactual: bool = False
    notes: str = ""

    def compute_commission(self, stake: float) -> float:
        raw = stake * self.commission_pct_of_stake
        if self.round_comm_cents:
            # Deriv rounds commission to 2 decimal places (ceiling/nearest cent, min $0.01)
            comm = max(self.min_comm_usd, round(raw + 1e-9, 2))
            # Match observed Deriv live API: $1.00 stake on EURUSD 50x -> $0.02 when raw=$0.0104
            if raw > comm and (raw - comm) > 1e-6:
                comm = math.ceil(raw * 100.0 - 1e-9) / 100.0
            return comm
        return raw

    def stop_out_distance(self, stake: float) -> float:
        """Exact fractional price move to trigger individual Deriv stop-out."""
        comm = self.compute_commission(stake)
        return max(0.0005, (1.0 - comm / stake) / self.multiplier)


# Empirically probed live Deriv MULTUP specs (2026-11-18 via wss://api.derivws.com/trading/v1/options/ws/public)
SPEC_BTC_100X = AssetSpec(
    name="cryBTCUSD_100x_DerivReal",
    symbol="cryBTCUSD",
    multiplier=100.0,
    commission_pct_of_stake=0.07,  # $0.07 per $1.00 stake = 7.00 bps notional
    spread_bps=0.31,
    notes="Live Deriv API: comm=7.00% stake (7 bps notl), stop-out=-0.9300%",
)
SPEC_BTC_20X_CF = AssetSpec(
    name="cryBTCUSD_20x_Counterfactual",
    symbol="cryBTCUSD",
    multiplier=20.0,
    commission_pct_of_stake=0.014,  # same 7.00 bps notional -> 1.40% of stake
    spread_bps=0.31,
    round_comm_cents=False,
    is_counterfactual=True,
    notes="Counterfactual 20x (not offered on Deriv): 7 bps notl (1.4% stake), stop-out=-4.93%",
)
SPEC_BTC_20X_LOWFEE_CF = AssetSpec(
    name="cryBTCUSD_20x_LowFee_CF",
    symbol="cryBTCUSD",
    multiplier=20.0,
    commission_pct_of_stake=0.008,  # 4.00 bps notional -> 0.80% of stake
    spread_bps=0.31,
    round_comm_cents=False,
    is_counterfactual=True,
    notes="Counterfactual 20x low-fee exchange (4 bps notl, 0.8% stake), stop-out=-4.96%",
)
SPEC_EURUSD_50X = AssetSpec(
    name="frxEURUSD_50x_DerivReal",
    symbol="frxEURUSD",
    multiplier=50.0,
    commission_pct_of_stake=0.0104,  # Live Deriv: $1.04 per $100 stake = 2.08 bps notional
    spread_bps=0.80,
    notes="Live Deriv API: comm=1.04% stake (2.08 bps notl), stop-out=-1.98%",
)
SPEC_EURUSD_100X = AssetSpec(
    name="frxEURUSD_100x_DerivReal",
    symbol="frxEURUSD",
    multiplier=100.0,
    commission_pct_of_stake=0.0208,  # Live Deriv: $2.08 per $100 stake = 2.08 bps notional
    spread_bps=0.80,
    notes="Live Deriv API: comm=2.08% stake (2.08 bps notl), stop-out=-0.98%",
)
SPEC_R75_50X = AssetSpec(
    name="R_75_50x_DerivReal",
    symbol="R_75",
    multiplier=50.0,
    commission_pct_of_stake=0.0146,  # Live Deriv: $1.46 per $100 stake = 2.92 bps notional
    spread_bps=0.50,
    notes="Live Deriv API: comm=1.46% stake (2.92 bps notl), stop-out=-1.97%",
)
SPEC_R75_100X = AssetSpec(
    name="R_75_100x_DerivReal",
    symbol="R_75",
    multiplier=100.0,
    commission_pct_of_stake=0.0291,  # Live Deriv: $2.91 per $100 stake = 2.91 bps notional
    spread_bps=0.50,
    notes="Live Deriv API: comm=2.91% stake (2.91 bps notl), stop-out=-0.97%",
)


# ---------------------------------------------------------------------------
# Data Loaders (Read-Only Local Parquets + Deriv Public WS Candle Fetcher)
# ---------------------------------------------------------------------------

BTC_PARQUET_PATHS = [
    Path(r"D:\backup importantes\ficar-rico\btc-proxima-vela\audit_data\BTCUSDT_spot_1m_virgin45d.parquet"),
    Path(r"C:\trading-agents\btc_color_research\data\BTCUSDT_1m_65d.parquet"),
]


def load_btc_1m_data() -> pd.DataFrame:
    """Load and concatenate the 110 days of real 1-minute BTCUSDT spot OHLCV data (read-only)."""
    dfs = []
    for p in BTC_PARQUET_PATHS:
        if not p.exists():
            raise FileNotFoundError(f"Required BTC parquet missing: {p}")
        df = pd.read_parquet(p)
        dfs.append(df)
    full = pd.concat(dfs, ignore_index=True)
    full["open_time"] = pd.to_datetime(full["open_time"], utc=True)
    full = full.sort_values("open_time").drop_duplicates(subset=["open_time"]).reset_index(drop=True)
    full = full.set_index("open_time")
    for col in ("open", "high", "low", "close", "volume", "taker_buy_base"):
        if col in full.columns:
            full[col] = full[col].astype(np.float64)
    return full


def resample_ohlcv(df_sub: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Strictly causal left-labeled OHLCV resampling (bar at 12:00 covers [12:00, 12:05))."""
    agg = {
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
    }
    if "volume" in df_sub.columns:
        agg["volume"] = "sum"
    if "taker_buy_base" in df_sub.columns:
        agg["taker_buy_base"] = "sum"
    res = df_sub.resample(rule, label="left", closed="left").agg(agg).dropna(subset=["open", "high", "low", "close"])
    return res


async def _fetch_deriv_candles_async(
    symbol: str,
    granularity: int,
    max_pages: int = 24,
) -> pd.DataFrame:
    """Paginate real historical candles from Deriv public WebSocket."""
    import websockets

    url = "wss://api.derivws.com/trading/v1/options/ws/public"
    all_candles: List[dict] = []
    end: str | int = "latest"
    async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
        for _ in range(max_pages):
            req = {
                "ticks_history": symbol,
                "adjust_start_time": 1,
                "count": 5000,
                "end": str(end),
                "granularity": granularity,
                "style": "candles",
            }
            await ws.send(json.dumps(req))
            raw = await ws.recv()
            resp = json.loads(raw)
            if "error" in resp:
                break
            candles = resp.get("candles", [])
            if not candles:
                break
            all_candles.extend(candles)
            earliest = int(candles[0]["epoch"])
            if end != "latest" and earliest >= int(end):
                break
            end = earliest - 1
            await asyncio.sleep(0.12)

    if not all_candles:
        raise RuntimeError(f"Failed to fetch candles for {symbol} granularity={granularity}")

    df = pd.DataFrame(all_candles)
    df["open_time"] = pd.to_datetime(df["epoch"].astype(np.int64), unit="s", utc=True)
    for col in ("open", "high", "low", "close"):
        df[col] = df[col].astype(np.float64)
    df = df[["open_time", "open", "high", "low", "close"]].sort_values("open_time").drop_duplicates("open_time")
    df = df.set_index("open_time")
    return df


def fetch_deriv_candles(
    symbol: str,
    granularity: int = 300,
    max_pages: int = 24,
    cache_dir: Optional[Path] = None,
) -> pd.DataFrame:
    """Fetch Deriv historical candles with local parquet cache inside C:\\robo-deriv\\backtest\\cache."""
    if cache_dir is None:
        cache_dir = Path(__file__).resolve().parent / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"{symbol}_{granularity}s.parquet"
    if cache_file.exists():
        df = pd.read_parquet(cache_file)
        if not isinstance(df.index, pd.DatetimeIndex):
            df.index = pd.to_datetime(df.index, utc=True)
        return df

    df = asyncio.run(_fetch_deriv_candles_async(symbol, granularity=granularity, max_pages=max_pages))
    df.to_parquet(cache_file)
    return df


# ---------------------------------------------------------------------------
# Simulation Engine
# ---------------------------------------------------------------------------

@dataclass
class CycleRecord:
    """Completed Smart DCA cycle record for auditing and metrics."""
    cycle_id: int
    direction: int
    open_time: pd.Timestamp
    close_time: pd.Timestamp
    bars_held: int
    layers_opened: int
    layers_stopped_out: int
    total_stake_usd: float
    total_commission_usd: float
    gross_pnl_usd: float
    net_pnl_usd: float
    close_reason: str


@dataclass
class SimulationResult:
    """Full quantitative metrics output from a simulation run."""
    label: str
    split_name: str
    asset_spec: AssetSpec
    timeframe: str
    start_time: pd.Timestamp
    end_time: pd.Timestamp
    n_signal_bars: int
    reference_capital_usd: float
    cycles: List[CycleRecord]
    crash_guard_blocks: int
    raw_signals: int

    # Aggregated metrics
    n_cycles: int = 0
    win_rate_pct: float = 0.0
    expectancy_usd: float = 0.0
    total_net_pnl_usd: float = 0.0
    total_gross_pnl_usd: float = 0.0
    total_commissions_usd: float = 0.0
    profit_factor: float = 0.0
    max_drawdown_usd: float = 0.0
    max_drawdown_pct: float = 0.0
    worst_losing_streak: int = 0
    avg_layers: float = 0.0
    layer_counts: Dict[int, int] = field(default_factory=dict)
    exit_reasons: Dict[str, int] = field(default_factory=dict)
    total_individual_stop_outs: int = 0

    def compute_metrics(self) -> None:
        self.n_cycles = len(self.cycles)
        if self.n_cycles == 0:
            return
        pnls = np.array([c.net_pnl_usd for c in self.cycles], dtype=np.float64)
        gross = np.array([c.gross_pnl_usd for c in self.cycles], dtype=np.float64)
        comms = np.array([c.total_commission_usd for c in self.cycles], dtype=np.float64)

        wins = pnls[pnls > 0.0]
        losses = pnls[pnls <= 0.0]

        self.win_rate_pct = float((pnls > 0.0).mean() * 100.0)
        self.total_net_pnl_usd = float(pnls.sum())
        self.total_gross_pnl_usd = float(gross.sum())
        self.total_commissions_usd = float(comms.sum())
        self.expectancy_usd = float(pnls.mean())

        sum_wins = float(wins.sum()) if len(wins) else 0.0
        sum_losses = float(abs(losses.sum())) if len(losses) else 0.0
        if sum_losses > 1e-12:
            self.profit_factor = sum_wins / sum_losses
        else:
            self.profit_factor = float("inf") if sum_wins > 0 else 0.0

        # Equity curve & Max Drawdown (cycle-by-cycle)
        eq = self.reference_capital_usd + np.cumsum(pnls)
        eq_full = np.concatenate([[self.reference_capital_usd], eq])
        running_peak = np.maximum.accumulate(eq_full)
        dd_usd = running_peak - eq_full
        self.max_drawdown_usd = float(dd_usd.max())
        self.max_drawdown_pct = float((self.max_drawdown_usd / self.reference_capital_usd) * 100.0)

        # Worst losing streak
        streak = 0
        max_streak = 0
        for p in pnls:
            if p <= 0.0:
                streak += 1
                if streak > max_streak:
                    max_streak = streak
            else:
                streak = 0
        self.worst_losing_streak = max_streak

        self.avg_layers = float(np.mean([c.layers_opened for c in self.cycles]))
        self.total_individual_stop_outs = int(sum(c.layers_stopped_out for c in self.cycles))

        lc: Dict[int, int] = {}
        er: Dict[str, int] = {}
        for c in self.cycles:
            lc[c.layers_opened] = lc.get(c.layers_opened, 0) + 1
            er[c.close_reason] = er.get(c.close_reason, 0) + 1
        self.layer_counts = dict(sorted(lc.items()))
        self.exit_reasons = dict(sorted(er.items()))


def _build_subbar_index_map(
    signal_index: pd.DatetimeIndex,
    sub_df: Optional[pd.DataFrame],
    signal_freq_seconds: int,
) -> Tuple[np.ndarray, np.ndarray]:
    """Map each signal bar `i` (covering [T_i, T_i + freq)) to a slice [start[i], end[i]) in `sub_df`."""
    n = len(signal_index)
    if sub_df is None or len(sub_df) == 0:
        return np.zeros(n, dtype=np.int64), np.zeros(n, dtype=np.int64)
    sub_ns = sub_df.index.astype("datetime64[ns, UTC]").asi8
    sig_start_ns = signal_index.astype("datetime64[ns, UTC]").asi8
    sig_end_ns = sig_start_ns + np.int64(signal_freq_seconds) * np.int64(1_000_000_000)
    starts = np.searchsorted(sub_ns, sig_start_ns, side="left")
    ends = np.searchsorted(sub_ns, sig_end_ns, side="left")
    return starts, ends


def _solve_exact_pnl_price(
    open_layers: List[LayerPosition],
    realized_pnl: float,
    target_total_pnl: float,
) -> Optional[float]:
    """Solve linearly for the exact mark_exit_price P where total basket PnL equals target_total_pnl.

    For open layers (all same direction d in {+1, -1}):
      sum_k [ stake_k * mult_k * d * (P / E_k - 1) - comm_k ] + realized_pnl = target_total_pnl
      A * P - B = target_total_pnl - realized_pnl
      where A = sum_k (stake_k * mult_k * d / E_k),
            B = sum_k (stake_k * mult_k * d + comm_k)
    """
    if not open_layers:
        return None
    a_coef = 0.0
    b_const = 0.0
    for lp in open_layers:
        sm = lp.stake * lp.multiplier * lp.direction
        a_coef += sm / lp.entry_price
        b_const += sm + lp.commission
    if abs(a_coef) < 1e-18:
        return None
    return (target_total_pnl - realized_pnl + b_const) / a_coef


def run_simulation(
    signal_df: pd.DataFrame,
    cfg: StrategyConfig,
    spec: AssetSpec,
    timeframe: str,
    signal_freq_seconds: int,
    sub_df: Optional[pd.DataFrame] = None,
    split_name: str = "FULL",
    label: str = "",
    latency_sub_bars: int = 0,
    latency_signal_bars: int = 0,
    latency_extra_slippage_bps: float = 0.0,
    eval_start_idx: int = 0,
    eval_end_idx: Optional[int] = None,
) -> SimulationResult:
    """Run strictly causal simulation with exact intrabar sub-bar stop-out & TP/SL checking.

    Parameters
    ----------
    signal_df : DataFrame with OHLC(V) on signal timeframe (M5 or M15).
                Indicators are computed ONCE causally on signal_df (so warmup before eval_start_idx
                is preserved without ever leaking future bars!).
    sub_df : Optional higher-resolution OHLC DataFrame (e.g. M1 for BTC, M5 for M15 FX/R_75).
             If None, signal_df itself acts as single sub-bar per step.
    latency_sub_bars : Number of sub-bars to delay fill after signal close (0 = next sub-bar open;
                       1 = +1 sub-bar delay).
    latency_extra_slippage_bps : Additional adverse entry/exit slippage in bps for stress testing.
    eval_start_idx, eval_end_idx : Slice of signal_df bars to actually trade (enables clean 70/30
                                   Walk-Forward split with proper EMA200/channel warmup!).
    """
    ind_df = compute_indicators(signal_df, cfg)
    n_total = len(ind_df)
    if eval_end_idx is None:
        eval_end_idx = n_total
    eval_start_idx = max(0, eval_start_idx)
    eval_end_idx = min(n_total, eval_end_idx)

    # Extract NumPy arrays for speed
    sig_times = ind_df.index
    sig_open = ind_df["open"].to_numpy(dtype=np.float64)
    sig_high = ind_df["high"].to_numpy(dtype=np.float64)
    sig_low = ind_df["low"].to_numpy(dtype=np.float64)
    sig_close = ind_df["close"].to_numpy(dtype=np.float64)
    sig_atr = ind_df["atr"].to_numpy(dtype=np.float64)

    entry_long = ind_df["entry_long"].to_numpy(dtype=bool)
    entry_short = ind_df["entry_short"].to_numpy(dtype=bool)
    raw_entry_long = ind_df["raw_entry_long"].to_numpy(dtype=bool)
    raw_entry_short = ind_df["raw_entry_short"].to_numpy(dtype=bool)
    blk_long = ind_df["blocked_by_crash_guard_long"].to_numpy(dtype=bool)
    blk_short = ind_df["blocked_by_crash_guard_short"].to_numpy(dtype=bool)
    cg_long = ind_df["crash_guard_long"].to_numpy(dtype=bool)
    cg_short = ind_df["crash_guard_short"].to_numpy(dtype=bool)
    re_long = ind_df["reentry_confirm_long"].to_numpy(dtype=bool)
    re_short = ind_df["reentry_confirm_short"].to_numpy(dtype=bool)
    cb_long = ind_df["channel_break_long"].to_numpy(dtype=bool)
    cb_short = ind_df["channel_break_short"].to_numpy(dtype=bool)

    use_sub = sub_df is not None and len(sub_df) > 0
    if use_sub:
        sb_starts, sb_ends = _build_subbar_index_map(sig_times, sub_df, signal_freq_seconds)
        sb_times = sub_df.index
        sb_open = sub_df["open"].to_numpy(dtype=np.float64)
        sb_high = sub_df["high"].to_numpy(dtype=np.float64)
        sb_low = sub_df["low"].to_numpy(dtype=np.float64)
        sb_close = sub_df["close"].to_numpy(dtype=np.float64)
    else:
        sb_starts = sb_ends = np.empty(0, dtype=np.int64)
        sb_times = sig_times
        sb_open = sig_open
        sb_high = sig_high
        sb_low = sig_low
        sb_close = sig_close

    half_spread_frac = (0.5 * spec.spread_bps + latency_extra_slippage_bps) * 1e-4
    max_basket_stake = cfg.base_stake_usd * sum(cfg.layer_multipliers)
    global_sl_usd = cfg.global_sl_pct_of_max_basket * max_basket_stake
    ref_capital = 100.0 * cfg.base_stake_usd  # e.g., $100 for $1 stake; $1000 for $10 stake

    cycles: List[CycleRecord] = []
    active_basket: Optional[BasketCycle] = None
    cycle_counter = 0
    cooldown_left = 0

    # Pending actions decided on closed bar `i-1` to execute at start of bar `i` (or delayed by latency_signal_bars)
    pending_open_dir: int = 0  # +1 LONG, -1 SHORT, 0 none
    pending_open_atr: float = 0.0
    pending_open_due_bar: int = -1
    pending_add_layer: bool = False
    pending_add_due_bar: int = -1
    pending_close_reason: str = ""
    pending_close_due_bar: int = -1

    raw_signals = 0
    crash_guard_blocks = 0

    def _entry_fill(raw_px: float, direction: int) -> float:
        return raw_px * (1.0 + half_spread_frac) if direction == 1 else raw_px * (1.0 - half_spread_frac)

    def _exit_mark(raw_px: float, direction: int) -> float:
        return raw_px * (1.0 - half_spread_frac) if direction == 1 else raw_px * (1.0 + half_spread_frac)

    def _make_layer(layer_idx: int, direction: int, raw_px: float, ts: pd.Timestamp) -> LayerPosition:
        stake = cfg.base_stake_usd * cfg.layer_multipliers[layer_idx - 1]
        comm = spec.compute_commission(stake)
        so_dist = spec.stop_out_distance(stake)
        fill_px = _entry_fill(raw_px, direction)
        so_px = fill_px * (1.0 - so_dist) if direction == 1 else fill_px * (1.0 + so_dist)
        return LayerPosition(
            layer_idx=layer_idx,
            direction=direction,
            stake=stake,
            multiplier=spec.multiplier,
            commission=comm,
            entry_price=fill_px,
            stop_out_price=so_px,
            entry_time=ts,
        )

    def _finalize_basket(basket: BasketCycle, exit_mark_px: Optional[float], ts: pd.Timestamp, reason: str, bar_i: int) -> None:
        nonlocal active_basket, cooldown_left
        for lp in basket.layers:
            if lp.is_open:
                assert exit_mark_px is not None
                lp.realized_pnl = lp.mark_pnl(exit_mark_px)
                lp.exit_price = exit_mark_px
                lp.exit_time = ts
                lp.is_open = False
                lp.exit_reason = reason
        basket.close_time = ts
        basket.close_reason = reason

        total_comm = basket.total_commission
        total_net = sum(lp.realized_pnl for lp in basket.layers)
        total_gross = total_net + total_comm
        n_so = sum(1 for lp in basket.layers if lp.exit_reason == "STOP_OUT")

        cycles.append(
            CycleRecord(
                cycle_id=basket.cycle_id,
                direction=basket.direction,
                open_time=basket.open_time,
                close_time=ts,
                bars_held=max(1, bar_i - basket.open_bar_idx),
                layers_opened=basket.num_layers,
                layers_stopped_out=n_so,
                total_stake_usd=basket.total_stake_deployed,
                total_commission_usd=total_comm,
                gross_pnl_usd=total_gross,
                net_pnl_usd=total_net,
                close_reason=reason,
            )
        )
        active_basket = None
        cooldown_left = cfg.cooldown_bars

    # Main chronological loop strictly inside [eval_start_idx, eval_end_idx)
    for i in range(eval_start_idx, eval_end_idx):
        # Gather sub-bars belonging to signal bar `i`
        if use_sub:
            s0 = int(sb_starts[i])
            s1 = int(sb_ends[i])
            if s1 <= s0:
                sub_indices = [-1]  # fallback to signal bar i itself
            else:
                sub_indices = list(range(s0, s1))
        else:
            sub_indices = [-1]

        exec_sub_pos = min(latency_sub_bars, len(sub_indices) - 1)

        for pos, sidx in enumerate(sub_indices):
            if sidx == -1:
                t_s = sig_times[i]
                o_s, h_s, l_s, c_s = sig_open[i], sig_high[i], sig_low[i], sig_close[i]
            else:
                t_s = sb_times[sidx]
                o_s, h_s, l_s, c_s = sb_open[sidx], sb_high[sidx], sb_low[sidx], sb_close[sidx]

            # 1. Execute any pending action queued at close of signal bar `i-1` (or earlier if latency_signal_bars > 0)
            if pos == exec_sub_pos:
                if pending_close_reason and i >= pending_close_due_bar and active_basket is not None:
                    mark_open = _exit_mark(o_s, active_basket.direction)
                    _finalize_basket(active_basket, mark_open, t_s, pending_close_reason, i)
                    pending_close_reason = ""
                    pending_add_layer = False
                elif pending_add_layer and i >= pending_add_due_bar and active_basket is not None:
                    if active_basket.num_layers < len(cfg.layer_multipliers):
                        new_lp = _make_layer(active_basket.num_layers + 1, active_basket.direction, o_s, t_s)
                        active_basket.layers.append(new_lp)
                    pending_add_layer = False
                elif pending_open_dir != 0 and i >= pending_open_due_bar and active_basket is None:
                    cycle_counter += 1
                    l1 = _make_layer(1, pending_open_dir, o_s, t_s)
                    so_dist = spec.stop_out_distance(l1.stake)
                    step = compute_dynamic_dca_step(cfg, so_dist, pending_open_atr, o_s)
                    active_basket = BasketCycle(
                        cycle_id=cycle_counter,
                        direction=pending_open_dir,
                        open_time=t_s,
                        open_bar_idx=i,
                        dca_step_frac=step,
                        layers=[l1],
                    )
                    pending_open_dir = 0

            # 2. Intrabar monitoring of active basket on sub-bar `s`
            if active_basket is None:
                continue

            d = active_basket.direction
            open_mark = _exit_mark(o_s, d)
            worst_mark = _exit_mark(l_s, d) if d == 1 else _exit_mark(h_s, d)
            best_mark = _exit_mark(h_s, d) if d == 1 else _exit_mark(l_s, d)
            close_mark = _exit_mark(c_s, d)

            # Step A: Check individual contract stop-outs FIRST (conservative worst-case)
            for lp in active_basket.layers:
                if not lp.is_open:
                    continue
                hit_so = (worst_mark <= lp.stop_out_price) if d == 1 else (worst_mark >= lp.stop_out_price)
                if hit_so:
                    lp.is_open = False
                    lp.exit_price = lp.stop_out_price
                    lp.exit_time = t_s
                    lp.realized_pnl = -lp.stake
                    lp.exit_reason = "STOP_OUT"

            if not active_basket.open_layers:
                _finalize_basket(active_basket, None, t_s, "ALL_STOP_OUT", i)
                continue

            # Step B: Check Global Basket SL SECOND
            worst_pnl = active_basket.net_pnl_at(worst_mark)
            if worst_pnl <= -global_sl_usd:
                open_pnl = active_basket.net_pnl_at(open_mark)
                if open_pnl <= -global_sl_usd:
                    fill_exit = open_mark  # gapped past SL
                else:
                    realized_so = sum(lp.realized_pnl for lp in active_basket.layers if not lp.is_open)
                    solved = _solve_exact_pnl_price(active_basket.open_layers, realized_so, -global_sl_usd)
                    fill_exit = solved if solved is not None else worst_mark
                _finalize_basket(active_basket, fill_exit, t_s, "GLOBAL_SL", i)
                continue

            # Step C: If trailing was ALREADY armed on a prior sub-bar, check pullback against worst_mark
            if active_basket.trailing_active:
                trail_floor = active_basket.peak_net_pnl * (1.0 - cfg.trailing_pullback_frac)
                if worst_pnl <= trail_floor:
                    open_pnl = active_basket.net_pnl_at(open_mark)
                    if open_pnl <= trail_floor:
                        fill_exit = open_mark
                    else:
                        realized_so = sum(lp.realized_pnl for lp in active_basket.layers if not lp.is_open)
                        solved = _solve_exact_pnl_price(active_basket.open_layers, realized_so, trail_floor)
                        fill_exit = solved if solved is not None else worst_mark
                    _finalize_basket(active_basket, fill_exit, t_s, "TRAILING_TP", i)
                    continue

            # Step D: Check Basket TP / Trailing activation on best_mark
            tp_target_usd = cfg.tp_net_pct_of_deployed * active_basket.total_stake_deployed
            best_pnl = active_basket.net_pnl_at(best_mark)
            if not cfg.use_trailing:
                if best_pnl >= tp_target_usd:
                    open_pnl = active_basket.net_pnl_at(open_mark)
                    if open_pnl >= tp_target_usd:
                        fill_exit = open_mark
                    else:
                        realized_so = sum(lp.realized_pnl for lp in active_basket.layers if not lp.is_open)
                        solved = _solve_exact_pnl_price(active_basket.open_layers, realized_so, tp_target_usd)
                        fill_exit = solved if solved is not None else best_mark
                    _finalize_basket(active_basket, fill_exit, t_s, "BASKET_TP", i)
                    continue
            else:
                if best_pnl > active_basket.peak_net_pnl:
                    active_basket.peak_net_pnl = best_pnl
                if not active_basket.trailing_active and best_pnl >= tp_target_usd:
                    active_basket.trailing_active = True
                # If trailing is active, check whether bar's close already retraced past floor
                if active_basket.trailing_active:
                    trail_floor = active_basket.peak_net_pnl * (1.0 - cfg.trailing_pullback_frac)
                    close_pnl = active_basket.net_pnl_at(close_mark)
                    if close_pnl <= trail_floor:
                        _finalize_basket(active_basket, close_mark, t_s, "TRAILING_TP", i)
                        continue

        # 3. Signal bar `i` is NOW CLOSED — evaluate causal signals for bar `i + 1`
        # Tally Crash Guard blocks across evaluation window
        if cfg.allow_long:
            if raw_entry_long[i]:
                raw_signals += 1
            if blk_long[i]:
                crash_guard_blocks += 1
        if cfg.allow_short:
            if raw_entry_short[i]:
                raw_signals += 1
            if blk_short[i]:
                crash_guard_blocks += 1

        # Don't queue new actions on the very last bar
        if i == eval_end_idx - 1:
            break

        if active_basket is not None:
            d = active_basket.direction
            # Timeout check
            if (i - active_basket.open_bar_idx) >= cfg.max_hold_bars:
                if not pending_close_reason:
                    pending_close_reason = "TIMEOUT"
                    pending_close_due_bar = i + 1 + latency_signal_bars
                continue
            # Structural channel break check
            is_ch_break = cb_long[i] if d == 1 else cb_short[i]
            if is_ch_break:
                active_basket.reentries_suspended = True
                if cfg.stop_on_channel_break:
                    if not pending_close_reason:
                        pending_close_reason = "CHANNEL_BREAK"
                        pending_close_due_bar = i + 1 + latency_signal_bars
                    continue
            # Smart DCA re-entry check
            if (
                not active_basket.reentries_suspended
                and not pending_add_layer
                and active_basket.num_layers < len(cfg.layer_multipliers)
            ):
                last_ep = active_basket.last_entry_price()
                adverse_move = (last_ep - sig_close[i]) / last_ep if d == 1 else (sig_close[i] - last_ep) / last_ep
                cg_now = cg_long[i] if d == 1 else cg_short[i]
                re_ok = (re_long[i] if d == 1 else re_short[i]) if cfg.dca_require_confirmation else True
                if adverse_move >= active_basket.dca_step_frac and (not cg_now) and re_ok:
                    pending_add_layer = True
                    pending_add_due_bar = i + 1 + latency_signal_bars
        else:
            if cooldown_left > 0:
                cooldown_left -= 1
            elif pending_open_dir == 0:
                if cfg.allow_long and entry_long[i]:
                    pending_open_dir = 1
                    pending_open_atr = float(sig_atr[i]) if np.isfinite(sig_atr[i]) else 0.0
                    pending_open_due_bar = i + 1 + latency_signal_bars
                elif cfg.allow_short and entry_short[i]:
                    pending_open_dir = -1
                    pending_open_atr = float(sig_atr[i]) if np.isfinite(sig_atr[i]) else 0.0
                    pending_open_due_bar = i + 1 + latency_signal_bars

    # Force-close any open basket at end of split (honest mark-to-market, zero hidden drawdown)
    if active_basket is not None:
        last_i = eval_end_idx - 1
        last_mark = _exit_mark(sig_close[last_i], active_basket.direction)
        _finalize_basket(active_basket, last_mark, sig_times[last_i], "END_OF_SPLIT", last_i)

    res = SimulationResult(
        label=label or f"{spec.name}_{timeframe}",
        split_name=split_name,
        asset_spec=spec,
        timeframe=timeframe,
        start_time=sig_times[eval_start_idx],
        end_time=sig_times[eval_end_idx - 1],
        n_signal_bars=eval_end_idx - eval_start_idx,
        reference_capital_usd=ref_capital,
        cycles=cycles,
        crash_guard_blocks=crash_guard_blocks,
        raw_signals=raw_signals,
    )
    res.compute_metrics()
    return res


# ---------------------------------------------------------------------------
# Walk-Forward 70/30 Split & Crash Stress Test
# ---------------------------------------------------------------------------

def walk_forward_70_30(
    signal_df: pd.DataFrame,
    cfg: StrategyConfig,
    spec: AssetSpec,
    timeframe: str,
    signal_freq_seconds: int,
    sub_df: Optional[pd.DataFrame] = None,
    label: str = "",
) -> Dict[str, SimulationResult]:
    """Run strict chronological 70% Dev / 30% Virgin Holdout + Latency Stress on Holdout.

    Indicators are strictly causal, so computing on `signal_df` and evaluating `[0, split)`
    vs `[split, N)` preserves EMA(200)/channel warmup cleanly at the Holdout boundary
    with ZERO future leakage.
    """
    n = len(signal_df)
    split_idx = int(n * 0.70)

    dev_res = run_simulation(
        signal_df=signal_df, cfg=cfg, spec=spec, timeframe=timeframe,
        signal_freq_seconds=signal_freq_seconds, sub_df=sub_df,
        split_name="DEV_70%", label=label,
        eval_start_idx=0, eval_end_idx=split_idx,
    )
    oos_res = run_simulation(
        signal_df=signal_df, cfg=cfg, spec=spec, timeframe=timeframe,
        signal_freq_seconds=signal_freq_seconds, sub_df=sub_df,
        split_name="HOLDOUT_30%", label=label,
        eval_start_idx=split_idx, eval_end_idx=n,
    )
    # +1 bar / latency sensitivity check on Virgin Holdout:
    # - When sub_df exists (e.g., M1 inside M5/M15), delay by +1 sub-bar + 1.0 bps adverse slippage
    # - When sub_df is None (e.g., Deriv M5 native), delay by +1 full signal bar + 1.0 bps adverse slippage
    use_sig_delay = 0 if (sub_df is not None and len(sub_df) > 0) else 1
    use_sub_delay = 1 if (sub_df is not None and len(sub_df) > 0) else 0
    oos_lat = run_simulation(
        signal_df=signal_df, cfg=cfg, spec=spec, timeframe=timeframe,
        signal_freq_seconds=signal_freq_seconds, sub_df=sub_df,
        split_name="HOLDOUT_LATENCY+1", label=label,
        latency_sub_bars=use_sub_delay,
        latency_signal_bars=use_sig_delay,
        latency_extra_slippage_bps=1.0,
        eval_start_idx=split_idx, eval_end_idx=n,
    )
    return {"dev": dev_res, "holdout": oos_res, "holdout_latency": oos_lat}


@dataclass
class CrashWindow:
    """Isolated sharp drawdown / crash episode in historical prices."""
    rank: int
    peak_time: pd.Timestamp
    trough_time: pd.Timestamp
    peak_price: float
    trough_price: float
    drawdown_pct: float  # negative, e.g. -6.4%
    duration_hours: float


def detect_crash_windows(
    signal_df: pd.DataFrame,
    min_drop_pct: float = 3.0,
    lookback_hours: float = 24.0,
    max_windows: int = 6,
) -> List[CrashWindow]:
    """Identify distinct worst -3% to -8%+ peak-to-trough price crashes (non-overlapping)."""
    close = signal_df["close"]
    times = signal_df.index
    if len(close) < 20:
        return []

    bar_sec = max(60, int((times[1] - times[0]).total_seconds()))
    win_bars = max(4, int((lookback_hours * 3600) / bar_sec))

    roll_peak = close.rolling(win_bars, min_periods=2).max()
    dd_pct = ((close - roll_peak) / roll_peak) * 100.0

    # Greedily select worst non-overlapping crash troughs
    found: List[CrashWindow] = []
    used_mask = np.zeros(len(close), dtype=bool)
    dd_arr = dd_pct.to_numpy(dtype=np.float64)
    c_arr = close.to_numpy(dtype=np.float64)

    for _ in range(max_windows * 3):
        if len(found) >= max_windows:
            break
        masked = np.where(used_mask | ~np.isfinite(dd_arr), 0.0, dd_arr)
        trough_idx = int(np.argmin(masked))
        drop = float(masked[trough_idx])
        if drop > -min_drop_pct:
            break

        # Find peak inside [trough_idx - win_bars + 1, trough_idx]
        p_start = max(0, trough_idx - win_bars + 1)
        peak_rel = int(np.argmax(c_arr[p_start : trough_idx + 1]))
        peak_idx = p_start + peak_rel

        dur_h = (times[trough_idx] - times[peak_idx]).total_seconds() / 3600.0
        found.append(
            CrashWindow(
                rank=len(found) + 1,
                peak_time=times[peak_idx],
                trough_time=times[trough_idx],
                peak_price=float(c_arr[peak_idx]),
                trough_price=float(c_arr[trough_idx]),
                drawdown_pct=drop,
                duration_hours=dur_h,
            )
        )
        # Mask out +-12h around this crash so windows don't overlap
        pad = max(4, int((12.0 * 3600) / bar_sec))
        used_mask[max(0, peak_idx - pad) : min(len(close), trough_idx + pad)] = True

    found.sort(key=lambda w: w.drawdown_pct)
    for idx, w in enumerate(found, 1):
        w.rank = idx
    return found


@dataclass
class CrashStressStats:
    window: CrashWindow
    cycles_active: int
    bot_net_pnl_usd: float
    stop_outs: int
    raw_signals_in_crash: int
    blocked_by_crash_guard: int


def evaluate_crash_stress(
    signal_df: pd.DataFrame,
    cfg: StrategyConfig,
    full_result: SimulationResult,
    crash_windows: List[CrashWindow],
) -> List[CrashStressStats]:
    """Measure exact bot PnL, stop-outs, and Crash Guard protection during isolated crash windows."""
    ind_df = compute_indicators(signal_df, cfg)
    stats: List[CrashStressStats] = []
    for w in crash_windows:
        # Include 2h buffer after trough so cycles opened during the crash and closed shortly after count
        end_buf = w.trough_time + pd.Timedelta(hours=2)
        sub_ind = ind_df.loc[(ind_df.index >= w.peak_time) & (ind_df.index <= w.trough_time)]
        raw_sig = int(sub_ind["raw_entry_long"].sum()) if cfg.allow_long else 0
        blk_sig = int(sub_ind["blocked_by_crash_guard_long"].sum()) if cfg.allow_long else 0

        # Cycles overlapping [peak_time, trough_time]
        overlapping = [
            c for c in full_result.cycles
            if (c.open_time <= w.trough_time) and (c.close_time >= w.peak_time)
        ]
        pnl = sum(c.net_pnl_usd for c in overlapping)
        sos = sum(c.layers_stopped_out for c in overlapping)
        stats.append(
            CrashStressStats(
                window=w,
                cycles_active=len(overlapping),
                bot_net_pnl_usd=pnl,
                stop_outs=sos,
                raw_signals_in_crash=raw_sig,
                blocked_by_crash_guard=blk_sig,
            )
        )
    return stats
