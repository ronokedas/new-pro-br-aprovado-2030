"""Comprehensive pytest unit tests for the Quantitative Multi-User Deriv DCA Worker.

Covers:
1. Causal Linear Regression Channel (R^2, slope, EMA50/200, Crash Guard ADX/ATR, zero lookahead).
2. Confirmations (wick rejection >= 0.40, RSI turn, MACD deceleration, Binance lead flow, DCA re-entry).
3. Smart DCA Basket Manager (step fitting inside -0.93% stop-out, conservative sizing vs Martingale,
   net PnL, avg entry, direct TP, 30% trailing pullback, global SL, channel break SL, timeout).
4. RiskGuard (daily loss stop, daily profit target, consecutive losses, AND Safe Subscription Expiration
   blocking new entries/reentries while still allowing exits of open baskets).
5. Mocked DerivClient Kill Switch closing all open contracts via `sell(price=0.0)` + DB persistence.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import math
from typing import Any
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from engine.binance_lead import BinanceLeadFilter
from engine.channel_detector import ChannelDetector, ChannelSnapshot
from engine.confirmations import (
    compute_macd,
    compute_rsi,
    dca_reentry_signal,
    entry_signal,
    wick_ratios,
)
from engine.dca_basket_manager import (
    BasketState,
    ContractSnapshot,
    SmartDCABasketManager,
)
from engine.deriv_client import _sanitize_payload, extract_stop_out_pct
from engine.risk_guard import RiskConfig, RiskGuard, UserAccessState
from engine.user_session import UserSession
from shared import database as shared_db
from shared.crypto_vault import decrypt_secret, encrypt_secret
from shared.models import Base, Basket, BotConfig, BotEventLog, Contract, DerivAccount, User


# ---------------------------------------------------------------------------
# Synthetic OHLC Generators (strictly deterministic)
# ---------------------------------------------------------------------------


def make_trending_candles(
    n: int = 240,
    start_price: float = 60000.0,
    step: float = 15.0,
    wave_amp: float = 25.0,
    wick: float = 10.0,
    start_epoch: int = 1700000000,
    granularity: int = 300,
) -> list[dict[str, Any]]:
    """Build deterministic trending candles with smooth sinusoidal oscillation inside channel."""
    candles: list[dict[str, Any]] = []
    for i in range(n):
        base = start_price + step * i + wave_amp * math.sin(i * 0.35)
        o = base - (step * 0.3)
        c = base
        h = max(o, c) + wick
        l = min(o, c) - wick
        ep = start_epoch + i * granularity
        candles.append(
            {
                "time": ep,
                "epoch": ep,
                "open": round(o, 4),
                "high": round(h, 4),
                "low": round(l, 4),
                "close": round(c, 4),
                "is_closed": True,
            }
        )
    return candles


# ---------------------------------------------------------------------------
# 1. Causal Linear Regression Channel & Crash Guard Tests
# ---------------------------------------------------------------------------


def test_causal_bull_and_bear_channel_detection_and_serialization():
    detector = ChannelDetector(window=60, band_std=2.0, min_r2=0.45)

    # Clean upward channel
    bull_candles = make_trending_candles(n=240, start_price=60000.0, step=18.0, wave_amp=20.0, wick=12.0)
    snap = detector.detect(bull_candles, symbol="cryBTCUSD", granularity=300)
    assert snap is not None
    assert snap.slope > 0
    assert snap.r2 >= 0.80
    assert snap.ema_50 > snap.ema_200
    assert snap.is_bull_channel is True
    assert snap.is_bear_channel is False
    assert snap.trend == "UP"
    assert snap.upper > snap.center > snap.lower

    payload = snap.to_dict()
    assert len(payload["series"]) == 60
    assert len(payload["upper_line"]) == 60
    assert len(payload["center_line"]) == 60
    assert len(payload["lower_line"]) == 60
    assert payload["upper_line"][-1]["value"] == payload["upper"]
    # Verify JSON-serializability
    encoded = json.dumps(payload)
    assert "upper_line" in encoded

    # Clean downward channel
    bear_candles = make_trending_candles(n=240, start_price=65000.0, step=-18.0, wave_amp=20.0, wick=12.0)
    bear_snap = detector.detect(bear_candles, symbol="cryBTCUSD", granularity=300)
    assert bear_snap is not None
    assert bear_snap.slope < 0
    assert bear_snap.r2 >= 0.80
    assert bear_snap.ema_50 < bear_snap.ema_200
    assert bear_snap.is_bear_channel is True
    assert bear_snap.is_bull_channel is False
    assert bear_snap.trend == "DOWN"


def test_channel_detector_strictly_causal_no_lookahead():
    detector = ChannelDetector(window=60)
    candles = make_trending_candles(n=220, start_price=60000.0, step=12.0, wave_amp=25.0)
    prefix_snap = detector.detect(candles[:180], symbol="cryBTCUSD", granularity=300)
    assert prefix_snap is not None

    # Adding an unclosed candle at the end must NOT affect the closed-candle channel
    with_open = list(candles[:180]) + [
        {
            "time": candles[180]["epoch"],
            "epoch": candles[180]["epoch"],
            "open": 62000.0,
            "high": 69000.0,
            "low": 50000.0,
            "close": 52000.0,
            "is_closed": False,
        }
    ]
    snap_with_open = detector.detect(with_open, symbol="cryBTCUSD", granularity=300)
    assert snap_with_open is not None
    assert snap_with_open.slope == pytest.approx(prefix_snap.slope)
    assert snap_with_open.r2 == pytest.approx(prefix_snap.r2)
    assert snap_with_open.lower == pytest.approx(prefix_snap.lower)
    assert snap_with_open.atr == pytest.approx(prefix_snap.atr)


def test_crash_guard_adx_opposing_direction_and_atr_spike():
    detector = ChannelDetector(window=60, adx_crash_threshold=35.0, atr_spike_mult=2.5)

    # 1. Strong sustained selloff -> ADX > 35 with -DI > +DI => blocks LONG
    crash_candles = make_trending_candles(n=120, start_price=60000.0, step=10.0, wave_amp=10.0, wick=8.0)
    price = crash_candles[-1]["close"]
    last_ep = crash_candles[-1]["epoch"]
    for i in range(1, 35):
        o = price
        c = price - 110.0
        crash_candles.append(
            {
                "time": last_ep + i * 300,
                "epoch": last_ep + i * 300,
                "open": o,
                "high": o + 5.0,
                "low": c - 10.0,
                "close": c,
                "is_closed": True,
            }
        )
        price = c

    snap_adx = detector.detect(crash_candles)
    assert snap_adx is not None
    assert snap_adx.adx > 35.0
    assert snap_adx.minus_di > snap_adx.plus_di
    assert snap_adx.crash_guard_long is True
    assert snap_adx.crash_guard_active is True

    # 2. Sudden volatility spike (ATR > 2.5x recent median) => blocks BOTH LONG and SHORT
    calm_candles = make_trending_candles(n=140, start_price=60000.0, step=5.0, wave_amp=5.0, wick=5.0)
    last_c = calm_candles[-1]["close"]
    last_ep = calm_candles[-1]["epoch"]
    for i in range(1, 10):
        calm_candles.append(
            {
                "time": last_ep + i * 300,
                "epoch": last_ep + i * 300,
                "open": last_c,
                "high": last_c + 400.0,
                "low": last_c - 400.0,
                "close": last_c + (20.0 if i % 2 == 0 else -20.0),
                "is_closed": True,
            }
        )

    snap_atr = detector.detect(calm_candles)
    assert snap_atr is not None
    assert snap_atr.atr_ratio > 2.5
    assert snap_atr.crash_guard_long is True
    assert snap_atr.crash_guard_short is True
    assert snap_atr.crash_guard_reason is not None


# ---------------------------------------------------------------------------
# 2. Causal Confirmations Tests (Wick Rejection, RSI Turn, MACD Decel, DCA)
# ---------------------------------------------------------------------------


def _make_bull_channel_snapshot(lower: float = 59800.0, upper: float = 60400.0, atr: float = 100.0) -> ChannelSnapshot:
    return ChannelSnapshot(
        symbol="cryBTCUSD",
        granularity=300,
        epoch=1700050000,
        window=60,
        slope=10.0,
        intercept=59500.0,
        r2=0.72,
        sigma=150.0,
        center=(lower + upper) / 2.0,
        upper=upper,
        lower=lower,
        ema_50=60150.0,
        ema_200=59900.0,
        atr=atr,
        atr_median=95.0,
        atr_ratio=1.05,
        slope_atr_norm=0.10,
        adx=21.0,
        plus_di=28.0,
        minus_di=14.0,
        trend="UP",
        is_bull_channel=True,
        is_bear_channel=False,
        crash_guard_active=False,
        crash_guard_long=False,
        crash_guard_short=False,
        crash_guard_reason=None,
    )


def test_entry_signal_wick_rejection_and_rsi_turn():
    # Build pullback sequence pushing RSI < 35, then a lower-wick rejection candle turning RSI up
    candles = make_trending_candles(n=60, start_price=60000.0, step=5.0, wave_amp=2.0, wick=5.0)
    price = candles[-1]["close"]
    ep = candles[-1]["epoch"]

    # 12 declining bars to push RSI(14) well below 35
    for i in range(1, 13):
        o = price
        c = price - 35.0
        candles.append(
            {
                "time": ep + i * 300,
                "epoch": ep + i * 300,
                "open": o,
                "high": o + 5.0,
                "low": c - 5.0,
                "close": c,
                "is_closed": True,
            }
        )
        price = c

    # Final rejection pinbar touching lower band with lower wick >= 55% and closing higher (RSI turns up)
    pin_open = price
    pin_close = price + 22.0
    pin_high = pin_close + 8.0
    pin_low = pin_open - 65.0  # Lower wick = 65 / (22+8+65) = 68% >= 40%
    candles.append(
        {
            "time": ep + 13 * 300,
            "epoch": ep + 13 * 300,
            "open": pin_open,
            "high": pin_high,
            "low": pin_low,
            "close": pin_close,
            "is_closed": True,
        }
    )

    lw, _uw = wick_ratios(candles[-1])
    assert lw >= 0.40

    channel = _make_bull_channel_snapshot(lower=pin_low + 20.0, upper=pin_low + 620.0, atr=100.0)
    decision = entry_signal("LONG_ONLY", candles, channel)

    assert decision.should_enter is True
    assert decision.direction == "MULTUP"
    assert decision.score >= 2
    assert decision.rsi_prev < 35.0
    assert decision.rsi > decision.rsi_prev
    assert any("Pavio inferior" in r for r in decision.reasons)
    assert any("RSI(14)" in r for r in decision.reasons)


def test_entry_signal_macd_deceleration_and_binance_boost():
    # Build pullback followed by 3 stabilizing/recovering bars so MACD histogram decelerates for 2 bars
    candles = make_trending_candles(n=60, start_price=60000.0, step=4.0, wave_amp=1.0, wick=5.0)
    price = candles[-1]["close"]
    ep = candles[-1]["epoch"]

    for i in range(1, 10):
        o = price
        c = price - 30.0
        candles.append(
            {"time": ep + i * 300, "epoch": ep + i * 300, "open": o, "high": o + 4.0, "low": c - 4.0, "close": c, "is_closed": True}
        )
        price = c

    # 3 progressively recovering candles -> MACD histogram rises for 2 consecutive bars from negative
    for j, delta in enumerate([+15.0, +25.0, +35.0], start=10):
        o = price
        c = price + delta
        candles.append(
            {
                "time": ep + j * 300,
                "epoch": ep + j * 300,
                "open": o,
                "high": c + 5.0,
                "low": o - 50.0 if j == 12 else o - 5.0,
                "close": c,
                "is_closed": True,
            }
        )
        price = c

    # Verify MACD histogram decelerated for 2 consecutive bars
    import numpy as np

    closes = np.array([c["close"] for c in candles], dtype=float)
    _, _, hist = compute_macd(closes)
    assert hist[-3] < 0.0
    assert hist[-3] < hist[-2] < hist[-1]

    # Test Binance lead flow filter
    bf = BinanceLeadFilter()
    now_ts = 1700050000.0
    bf.ingest_trade(price=60000.0, qty=5.0, is_buyer_maker=True, ts=now_ts - 40.0)   # heavy sell 40s ago
    bf.ingest_trade(price=60005.0, qty=3.0, is_buyer_maker=False, ts=now_ts - 5.0)   # aggressive buy 5s ago
    b_state = bf.get_state(now=now_ts)
    assert b_state["available"] is True
    assert b_state["delta_15s"] > 0.0
    assert b_state["absorption_bullish"] is True

    channel = _make_bull_channel_snapshot(lower=candles[-1]["low"] + 15.0, upper=candles[-1]["low"] + 615.0, atr=100.0)
    decision = entry_signal("LONG_ONLY", candles, channel, binance_state=b_state)
    assert decision.should_enter is True
    assert decision.score == 3
    assert any("MACD" in r for r in decision.reasons)
    assert any("Binance" in r for r in decision.reasons)


def test_dca_reentry_signal_blocks_panic_and_structural_break_but_allows_confirmed_reentry():
    candles = make_trending_candles(n=60, start_price=60000.0, step=3.0, wave_amp=2.0, wick=5.0)
    price = candles[-1]["close"]
    ep = candles[-1]["epoch"]

    for i in range(1, 10):
        o = price
        c = price - 25.0
        candles.append(
            {"time": ep + i * 300, "epoch": ep + i * 300, "open": o, "high": o + 3.0, "low": c - 3.0, "close": c, "is_closed": True}
        )
        price = c

    channel = _make_bull_channel_snapshot(lower=price - 20.0, upper=price + 580.0, atr=100.0)
    last_entry = price + 220.0  # > 0.35% above current price

    # 1. Panic red candle -> MUST block DCA re-entry
    panic_candles = list(candles) + [
        {
            "time": ep + 10 * 300,
            "epoch": ep + 10 * 300,
            "open": price,
            "high": price + 2.0,
            "low": price - 80.0,
            "close": price - 78.0,
            "is_closed": True,
        }
    ]
    res_panic = dca_reentry_signal("MULTUP", panic_candles, channel, last_entry_price=last_entry, min_step_pct=0.0025)
    assert res_panic.should_enter is False
    assert "pânico" in (res_panic.blocked_reason or "").lower()

    # 2. Structural break below lower_band - 1.0*ATR -> MUST block DCA re-entry
    broken_candles = list(candles) + [
        {
            "time": ep + 10 * 300,
            "epoch": ep + 10 * 300,
            "open": price - 150.0,
            "high": price - 140.0,
            "low": price - 220.0,
            "close": channel.lower - 1.5 * channel.atr,
            "is_closed": True,
        }
    ]
    res_break = dca_reentry_signal("MULTUP", broken_candles, channel, last_entry_price=last_entry, min_step_pct=0.0025)
    assert res_break.should_enter is False
    assert "rompimento" in (res_break.blocked_reason or "").lower()

    # 3. Valid drop >= 0.25% with strong lower wick rejection + RSI bounce -> ALLOWS DCA re-entry
    good_candles = list(candles) + [
        {
            "time": ep + 10 * 300,
            "epoch": ep + 10 * 300,
            "open": price,
            "high": price + 22.0,
            "low": price - 55.0,
            "close": price + 15.0,
            "is_closed": True,
        }
    ]
    res_ok = dca_reentry_signal("MULTUP", good_candles, channel, last_entry_price=last_entry, min_step_pct=0.0025)
    assert res_ok.should_enter is True
    assert res_ok.direction == "MULTUP"


# ---------------------------------------------------------------------------
# 3. Smart DCA & Basket Manager Tests
# ---------------------------------------------------------------------------


def test_smart_dca_dynamic_step_fits_inside_stop_out_and_rejects_martingale():
    mgr = SmartDCABasketManager(max_layers=3, layer_multipliers=[1.0, 1.0, 1.5])

    # cryBTCUSD 100x real stop-out = -0.93%
    step_btc = mgr.compute_dynamic_step_pct(stop_out_pct=-0.0093, max_layers=3)
    total_span = (3 - 1) * step_btc
    assert 0.0020 <= step_btc <= 0.00349
    assert total_span <= 0.75 * 0.0093 + 1e-9

    # Tighter stop-out (-0.50%) automatically shrinks step so total span <= 0.75 * 0.50%
    step_tight = mgr.compute_dynamic_step_pct(stop_out_pct=-0.0050, max_layers=3)
    assert (3 - 1) * step_tight <= 0.75 * 0.0050 + 1e-9

    # Conservative multipliers pass
    assert SmartDCABasketManager.validate_layer_multipliers([1.0, 1.3, 1.7], max_layers=3) == [1.0, 1.3, 1.7]

    # Exponential Martingale (>= 2.0x per step) MUST be rejected
    with pytest.raises(ValueError, match="Martingale"):
        SmartDCABasketManager.validate_layer_multipliers([1.0, 2.0, 4.0], max_layers=3)

    with pytest.raises(ValueError, match="Martingale"):
        SmartDCABasketManager.validate_layer_multipliers([1.0, 1.2, 2.5], max_layers=3)


def test_basket_pnl_avg_entry_tp_trailing_sl_channel_break_and_timeout():
    opened = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    c1 = ContractSnapshot(1, "MULTUP", stake_usd=1.0, multiplier=100, entry_spot=60000.0, current_profit_usd=-0.10, commission_usd=0.07)
    c2 = ContractSnapshot(2, "MULTUP", stake_usd=1.5, multiplier=100, entry_spot=59800.0, current_profit_usd=0.30, commission_usd=0.105)

    # Net PnL & stake-weighted average entry
    assert SmartDCABasketManager.compute_net_pnl([c1, c2]) == pytest.approx(0.20)
    expected_avg = (1.0 * 60000.0 + 1.5 * 59800.0) / 2.5
    assert SmartDCABasketManager.compute_avg_entry_price([c1, c2]) == pytest.approx(expected_avg)

    # 1. Direct TP (trailing disabled)
    mgr_tp = SmartDCABasketManager(basket_tp_usd=0.18, trailing_enabled=False, max_basket_loss_usd=2.50)
    b_tp = BasketState(symbol="cryBTCUSD", direction="MULTUP", opened_at=opened, contracts=[c1, c2])
    dec_tp = mgr_tp.evaluate_exit(b_tp, now=opened + timedelta(minutes=10))
    assert dec_tp.should_close is True
    assert dec_tp.reason == "TP"

    # 2. Trailing Profit: arms at >= $0.20, peaks at $0.50 (floor = $0.35), closes at $0.34 (30% pullback)
    mgr_trail = SmartDCABasketManager(basket_tp_usd=0.20, trailing_enabled=True, trailing_pullback_pct=0.30)
    t1 = ContractSnapshot(1, "MULTUP", 1.0, 100, 60000.0, current_profit_usd=0.25)
    b_tr = BasketState(symbol="cryBTCUSD", direction="MULTUP", opened_at=opened, contracts=[t1])

    dec_arm = mgr_trail.evaluate_exit(b_tr, now=opened + timedelta(minutes=5))
    assert dec_arm.should_close is False
    assert b_tr.trailing_armed is True
    assert b_tr.trailing_floor_usd == pytest.approx(0.25 * 0.70)

    t1.current_profit_usd = 0.50
    dec_peak = mgr_trail.evaluate_exit(b_tr, now=opened + timedelta(minutes=6))
    assert dec_peak.should_close is False
    assert b_tr.peak_pnl_usd == pytest.approx(0.50)
    assert b_tr.trailing_floor_usd == pytest.approx(0.35)

    t1.current_profit_usd = 0.34
    dec_trail_exit = mgr_trail.evaluate_exit(b_tr, now=opened + timedelta(minutes=7))
    assert dec_trail_exit.should_close is True
    assert dec_trail_exit.reason == "TRAILING"

    # 3. Global Basket Stop Loss
    sl1 = ContractSnapshot(1, "MULTUP", 1.0, 100, 60000.0, current_profit_usd=-1.30)
    sl2 = ContractSnapshot(2, "MULTUP", 1.5, 100, 59850.0, current_profit_usd=-1.25)
    b_sl = BasketState(symbol="cryBTCUSD", direction="MULTUP", opened_at=opened, contracts=[sl1, sl2])
    dec_sl = mgr_trail.evaluate_exit(b_sl, now=opened + timedelta(minutes=8))
    assert dec_sl.should_close is True
    assert dec_sl.reason == "STOP_LOSS"

    # 4. Channel Break Stop Loss (close < lower - 1.2 * ATR)
    cb1 = ContractSnapshot(1, "MULTUP", 1.0, 100, 60000.0, current_profit_usd=-0.50)
    b_cb = BasketState(symbol="cryBTCUSD", direction="MULTUP", opened_at=opened, contracts=[cb1])
    ch = _make_bull_channel_snapshot(lower=59800.0, upper=60400.0, atr=100.0)
    dec_cb = mgr_trail.evaluate_exit(b_cb, channel=ch, latest_closed_close=59670.0, now=opened + timedelta(minutes=9))
    assert dec_cb.should_close is True
    assert dec_cb.reason == "CHANNEL_BREAK"

    # 5. Timeout Exit (`max_hold_minutes`)
    mgr_to = SmartDCABasketManager(max_hold_minutes=60, trailing_enabled=True, basket_tp_usd=0.50)
    to1 = ContractSnapshot(1, "MULTUP", 1.0, 100, 60000.0, current_profit_usd=-0.05)
    b_to = BasketState(symbol="cryBTCUSD", direction="MULTUP", opened_at=opened, contracts=[to1])
    dec_to = mgr_to.evaluate_exit(b_to, now=opened + timedelta(minutes=61))
    assert dec_to.should_close is True
    assert dec_to.reason == "TIMEOUT"


# ---------------------------------------------------------------------------
# 4. RiskGuard & Safe Subscription Expiration Tests
# ---------------------------------------------------------------------------


def test_risk_guard_daily_limits_consecutive_losses_and_safe_expiration():
    now = datetime(2026, 5, 10, 14, 0, 0, tzinfo=timezone.utc)
    active_user = UserAccessState(status="ACTIVE", expires_at=now + timedelta(days=30))
    expired_user = UserAccessState(status="ACTIVE", expires_at=now - timedelta(minutes=5))
    suspended_user = UserAccessState(status="SUSPENDED", expires_at=now + timedelta(days=30))

    guard = RiskGuard(
        RiskConfig(
            max_daily_loss_usd=10.0,
            daily_profit_target_usd=15.0,
            max_consecutive_losses=3,
            cooldown_candles_after_close=0,
        )
    )

    # Normal allowed
    v_ok = guard.evaluate(active_user, has_open_basket=False, now=now)
    assert v_ok.code == "ALLOWED"
    assert v_ok.allow_new_entry is True
    assert v_ok.allow_exit is True

    # Daily loss stop (realized + floating)
    guard.load_daily_stats(realized_pnl_today=-7.0, consecutive_losses=1, now=now)
    guard.set_floating_pnl(-3.2)
    v_loss = guard.evaluate(active_user, has_open_basket=True, now=now)
    assert v_loss.code == "DAILY_LOSS_STOP"
    assert v_loss.allow_new_entry is False
    assert v_loss.allow_reentry is False
    assert v_loss.allow_exit is True

    # Daily profit target
    guard.load_daily_stats(realized_pnl_today=15.5, consecutive_losses=0, now=now)
    guard.set_floating_pnl(0.0)
    v_prof = guard.evaluate(active_user, has_open_basket=False, now=now)
    assert v_prof.code == "DAILY_PROFIT_STOP"
    assert v_prof.allow_new_entry is False

    # Consecutive loss stop
    guard.load_daily_stats(realized_pnl_today=-2.0, consecutive_losses=0, now=now)
    guard.record_closed_basket(-1.0, now=now)
    guard.record_closed_basket(-1.0, now=now)
    guard.record_closed_basket(-1.0, now=now)
    v_seq = guard.evaluate(active_user, has_open_basket=False, now=now)
    assert v_seq.code == "CONSECUTIVE_LOSS_STOP"
    assert v_seq.allow_new_entry is False

    # CRITICAL: Safe Subscription Expiration WITH open basket -> EXPIRED_DRAINING
    # Blocks new entries & re-entries, STILL ALLOWS EXIT until basket closes!
    v_exp_drain = guard.evaluate(expired_user, has_open_basket=True, now=now)
    assert v_exp_drain.code == "EXPIRED_DRAINING"
    assert v_exp_drain.allow_new_entry is False
    assert v_exp_drain.allow_reentry is False
    assert v_exp_drain.allow_exit is True
    assert v_exp_drain.auto_disable_after_close is True
    assert v_exp_drain.should_disable_bot is False

    # CRITICAL: Safe Subscription Expiration WITHOUT open basket -> EXPIRED_STOPPED
    v_exp_stop = guard.evaluate(expired_user, has_open_basket=False, now=now)
    assert v_exp_stop.code == "EXPIRED_STOPPED"
    assert v_exp_stop.allow_new_entry is False
    assert v_exp_stop.allow_reentry is False
    assert v_exp_stop.should_disable_bot is True

    # Suspended user with open basket -> also EXPIRED_DRAINING (never abandon position!)
    v_susp_drain = guard.evaluate(suspended_user, has_open_basket=True, now=now)
    assert v_susp_drain.code == "EXPIRED_DRAINING"
    assert v_susp_drain.allow_exit is True
    assert v_susp_drain.allow_new_entry is False


# ---------------------------------------------------------------------------
# 5. Mocked DerivClient Kill Switch & Deriv Payload Helpers
# ---------------------------------------------------------------------------


def test_deriv_helpers_stop_out_extraction_and_token_redaction():
    # Realistic Deriv cryBTCUSD 100x proposal response
    proposal_resp = {
        "proposal": {
            "id": "prop-123",
            "ask_price": 1.0,
            "spot": 60000.0,
            "commission": 0.07,
            "limit_order": {"stop_out": {"value": 59442.0, "order_amount": -1.0}},
        }
    }
    so_pct, so_spot = extract_stop_out_pct(proposal_resp, contract_type="MULTUP")
    assert so_spot == 59442.0
    assert so_pct == pytest.approx(0.0093, rel=1e-3)

    # Token redaction safety
    sanitized = _sanitize_payload({"authorize": "SUPER_SECRET_DERIV_TOKEN_123", "req_id": 1})
    assert sanitized["authorize"] == "***REDACTED***"
    assert "SUPER_SECRET" not in json.dumps(sanitized)

    # Vault round-trip from shared.crypto_vault
    enc = encrypt_secret("DEMO_TOKEN_XYZ")
    assert enc != "DEMO_TOKEN_XYZ"
    assert decrypt_secret(enc) == "DEMO_TOKEN_XYZ"


@pytest.mark.asyncio
async def test_mocked_deriv_client_kill_switch_closes_all_open_contracts(tmp_path, monkeypatch):
    # Setup isolated SQLite DB via shared.database monkeypatch
    db_file = tmp_path / "test_worker.db"
    test_engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=test_engine)
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

    monkeypatch.setattr(shared_db, "_engine", test_engine)
    monkeypatch.setattr(shared_db, "_SessionLocal", TestSessionLocal)

    # Seed User, DerivAccount, BotConfig, and OPEN Basket with 2 Contracts
    with shared_db.session_scope() as db:
        user = User(
            name="Trader Teste",
            email="trader@test.local",
            password_hash="hashed",
            role="USER",
            status="ACTIVE",
            expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        )
        db.add(user)
        db.flush()

        acct = DerivAccount(
            user_id=user.id,
            loginid="VRTC999999",
            account_type="DEMO",
            currency="USD",
            token_encrypted=encrypt_secret("FAKE_TEST_TOKEN"),
            balance=10000.0,
            is_active_selected=True,
        )
        db.add(acct)
        db.flush()

        cfg = BotConfig(
            user_id=user.id,
            enabled=True,
            symbol="cryBTCUSD",
            timeframe_sec=300,
            stake_usd=1.0,
            multiplier=100,
            max_layers=3,
            status_text="EM OPERAÇÃO",
        )
        db.add(cfg)

        basket = Basket(
            user_id=user.id,
            deriv_account_id=acct.id,
            account_type="DEMO",
            symbol="cryBTCUSD",
            direction="MULTUP",
            status="OPEN",
            layers_count=2,
            total_stake_usd=2.0,
            avg_entry_price=59925.0,
        )
        db.add(basket)
        db.flush()

        db.add_all(
            [
                Contract(
                    basket_id=basket.id,
                    user_id=user.id,
                    layer_index=1,
                    deriv_contract_id="1001",
                    contract_type="MULTUP",
                    symbol="cryBTCUSD",
                    stake_usd=1.0,
                    multiplier=100,
                    entry_spot=60000.0,
                    current_profit_usd=-0.05,
                    status="OPEN",
                ),
                Contract(
                    basket_id=basket.id,
                    user_id=user.id,
                    layer_index=2,
                    deriv_contract_id="1002",
                    contract_type="MULTUP",
                    symbol="cryBTCUSD",
                    stake_usd=1.0,
                    multiplier=100,
                    entry_spot=59850.0,
                    current_profit_usd=0.08,
                    status="OPEN",
                ),
            ]
        )
        user_id = int(user.id)
        basket_id = int(basket.id)

    # Mock DerivClient with 3 open contracts (2 in DB basket + 1 extra orphan on Deriv portfolio)
    mock_client = AsyncMock()
    mock_client.is_connected = True
    mock_client.portfolio = AsyncMock(
        return_value=[
            {"contract_id": 1001, "symbol": "cryBTCUSD", "contract_type": "MULTUP"},
            {"contract_id": 1002, "symbol": "cryBTCUSD", "contract_type": "MULTUP"},
            {"contract_id": 1003, "symbol": "cryBTCUSD", "contract_type": "MULTUP"},
        ]
    )
    mock_client.sell = AsyncMock(
        side_effect=lambda cid, price=0.0: {"sell": {"contract_id": int(cid), "sold_for": 1.05}}
    )

    mock_redis = AsyncMock()
    mock_redis.set = AsyncMock(return_value=True)
    mock_redis.publish = AsyncMock(return_value=1)

    session = UserSession(
        user_id=user_id,
        redis_client=mock_redis,
        deriv_client=mock_client,
    )
    assert session._load_state_from_db() is True
    assert session.has_open_basket is True

    result = await session.kill_switch()

    # Assert ALL 3 contracts were sold via sell(cid, price=0.0)
    assert sorted(result["closed_contract_ids"]) == ["1001", "1002", "1003"]
    assert result["errors"] == []
    assert mock_client.sell.await_count == 3
    sold_calls = {
        (str(call.args[0]), call.kwargs.get("price", 0.0))
        for call in mock_client.sell.await_args_list
    }
    assert sold_calls == {("1001", 0.0), ("1002", 0.0), ("1003", 0.0)}

    # Assert DB Basket closed with KILL_SWITCH and BotConfig disabled
    with shared_db.session_scope() as db:
        b = db.query(Basket).filter(Basket.id == basket_id).first()
        assert b is not None
        assert b.status == "CLOSED"
        assert b.close_reason == "KILL_SWITCH"
        assert all(c.status == "SOLD" for c in b.contracts)

        cfg_after = db.query(BotConfig).filter(BotConfig.user_id == user_id).first()
        assert cfg_after is not None
        assert cfg_after.enabled is False
        assert cfg_after.status_text == "KILL SWITCH"

        logs = db.query(BotEventLog).filter(BotEventLog.user_id == user_id).all()
        assert any("KILL SWITCH" in lg.message for lg in logs)

    # Assert Redis state & event publication happened
    assert mock_redis.set.await_count >= 1
    assert mock_redis.publish.await_count >= 1

    test_engine.dispose()


def test_linreg_atr_video_mode_arming_touch_and_atr_exits():
    """Verify exact causal LINREG_ATR_VIDEO mode: arming, containment, band touch, ATR(10) TP/SL & Crash Guard."""
    from engine.linreg_atr_video import (
        arm_video_band_order,
        check_band_touch,
        compute_atr_exit_levels,
        compute_atr_simple,
        compute_linreg_bands,
        evaluate_atr_price_exit,
    )

    # Build 60 oscillating candles around 1000.0 so High < LinC and Low > LinB
    candles = []
    ep = 1794900000
    for i in range(60):
        wave = 10.0 * math.sin(i * 0.45)
        c = 1000.0 + wave
        o = c - 0.5
        candles.append({
            "time": ep + i * 300,
            "epoch": ep + i * 300,
            "open": o,
            "high": max(o, c) + 2.0,
            "low": min(o, c) - 2.0,
            "close": c,
            "is_closed": True,
        })

    # Place last candle strictly inside channel and below mid -> must arm MULTUP at LinB
    bands_pre = compute_linreg_bands(candles, window=50, sigma_mult=2.0)
    assert bands_pre is not None
    candles[-1]["close"] = bands_pre["mid"] - 3.0
    candles[-1]["open"] = bands_pre["mid"] - 2.0
    candles[-1]["high"] = bands_pre["mid"]
    candles[-1]["low"] = bands_pre["mid"] - 4.0

    armed = arm_video_band_order(
        candles,
        direction_mode="BOTH",
        channel_window=50,
        channel_sigma=2.0,
        atr_period=10,
        use_crash_guard=False,
    )
    assert armed is not None
    assert armed.direction == "MULTUP"
    assert abs(armed.trigger_price - armed.lin_lower) < 1e-6
    assert armed.atr_value == pytest.approx(compute_atr_simple(candles, 10), rel=1e-6)

    # Touch check intrabar
    assert check_band_touch(armed, armed.trigger_price + 1.0) is False
    assert check_band_touch(armed, armed.trigger_price) is True
    assert check_band_touch(armed, armed.trigger_price - 0.5) is True

    # Crash Guard blocks when enabled
    crash_snap = _make_bull_channel_snapshot()
    crash_snap.crash_guard_long = True
    blocked = arm_video_band_order(
        candles, direction_mode="BOTH", channel_window=50, channel_sigma=2.0,
        atr_period=10, use_crash_guard=True, channel=crash_snap,
    )
    assert blocked is None

    # Containment guard blocks when bar extends outside LinB
    candles_outside = list(candles)
    candles_outside[-1] = dict(candles[-1], low=armed.lin_lower - 20.0)
    assert arm_video_band_order(candles_outside, direction_mode="BOTH", use_crash_guard=False) is None

    # ATR(10) dynamic TP (0.75x) & SL (1.90x) exact math (like video: ATR=134 -> TP=+100.5, SL=-254.6)
    tp_px, sl_px = compute_atr_exit_levels("MULTUP", 1000.0, 1000.0, 134.0, tp_atr_mult=0.75, sl_atr_mult=1.90)
    assert tp_px == pytest.approx(1100.5, abs=1e-6)
    assert sl_px == pytest.approx(745.4, abs=1e-6)
    assert evaluate_atr_price_exit("MULTUP", 1050.0, tp_px, sl_px) is None
    assert evaluate_atr_price_exit("MULTUP", 1101.0, tp_px, sl_px) == "TP_ATR"
    assert evaluate_atr_price_exit("MULTUP", 745.0, tp_px, sl_px) == "SL_ATR"
