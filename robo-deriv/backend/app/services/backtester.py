import asyncio
import json
import logging
import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import websockets
from app.core.redis_client import redis_get_json, redis_set_json

logger = logging.getLogger("backtester")

DERIV_PUBLIC_WS = "wss://api.derivws.com/trading/v1/options/ws/public"

CANDIDATE_POOLS = [
    # Forex Majors & Minors
    {"symbol": "frxEURUSD", "name": "EUR/USD", "category": "Forex", "multiplier": 500},
    {"symbol": "frxUSDJPY", "name": "USD/JPY", "category": "Forex", "multiplier": 500},
    {"symbol": "frxGBPUSD", "name": "GBP/USD", "category": "Forex", "multiplier": 500},
    {"symbol": "frxAUDUSD", "name": "AUD/USD", "category": "Forex", "multiplier": 500},
    {"symbol": "frxUSDCAD", "name": "USD/CAD", "category": "Forex", "multiplier": 500},
    {"symbol": "frxUSDCHF", "name": "USD/CHF", "category": "Forex", "multiplier": 500},
    {"symbol": "frxEURJPY", "name": "EUR/JPY", "category": "Forex", "multiplier": 500},
    {"symbol": "frxGBPJPY", "name": "GBP/JPY", "category": "Forex", "multiplier": 500},
    {"symbol": "frxEURGBP", "name": "EUR/GBP", "category": "Forex", "multiplier": 500},
    {"symbol": "frxAUDJPY", "name": "AUD/JPY", "category": "Forex", "multiplier": 500},

    # Criptomoedas
    {"symbol": "cryBTCUSD", "name": "Bitcoin / USD", "category": "Cripto", "multiplier": 200},
    {"symbol": "cryETHUSD", "name": "Ethereum / USD", "category": "Cripto", "multiplier": 200},

    # Índices Sintéticos Deriv 24/7 (Com suporte oficial a Multiplicador)
    {"symbol": "JD75", "name": "Jump 75 Index", "category": "Sintéticos", "multiplier": 100},
    {"symbol": "R_100", "name": "Volatility 100", "category": "Sintéticos", "multiplier": 100},
    {"symbol": "1HZ100V", "name": "Volatility 100 (1s)", "category": "Sintéticos", "multiplier": 100},
    {"symbol": "JD50", "name": "Jump 50 Index", "category": "Sintéticos", "multiplier": 100},
    {"symbol": "R_50", "name": "Volatility 50", "category": "Sintéticos", "multiplier": 100},
    {"symbol": "JD25", "name": "Jump 25 Index", "category": "Sintéticos", "multiplier": 100},
    {"symbol": "R_25", "name": "Volatility 25", "category": "Sintéticos", "multiplier": 100},
    {"symbol": "R_10", "name": "Volatility 10", "category": "Sintéticos", "multiplier": 100},
]

_LATEST_BACKTEST_CACHE: Optional[Dict[str, Any]] = None


def calculate_sma(prices: List[float], period: int) -> Optional[float]:
    if len(prices) < period:
        return None
    return sum(prices[-period:]) / period


def calculate_rsi(prices: List[float], period: int = 5) -> float:
    if len(prices) < period + 1:
        return 50.0
    changes = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    gains = [max(c, 0.0) for c in changes[-period:]]
    losses = [max(-c, 0.0) for c in changes[-period:]]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


async def fetch_candles_for_symbol(
    ws: websockets.WebSocketClientProtocol, symbol: str, count: int = 1000
) -> List[Dict[str, Any]]:
    req = {
        "ticks_history": symbol,
        "adjust_start_time": 1,
        "count": count,
        "end": "latest",
        "style": "candles",
        "granularity": 60,
    }
    await ws.send(json.dumps(req))
    resp_raw = await ws.recv()
    resp = json.loads(resp_raw)
    return resp.get("candles", [])


def simulate_m1_strategy(
    symbol: str,
    name: str,
    category: str,
    multiplier: int,
    candles: List[Dict[str, Any]],
    stake_initial: float = 1.0,
    target_pnl: float = 0.50,
    max_loss_pnl: float = 12.0,
) -> Dict[str, Any]:
    if len(candles) < 260:
        return {
            "symbol": symbol,
            "name": name,
            "category": category,
            "multiplier": multiplier,
            "candles_tested": len(candles),
            "trades_count": 0,
            "wins": 0,
            "losses": 0,
            "winrate_pct": 0.0,
            "total_pnl_usd": 0.0,
            "profit_factor": 0.0,
            "max_drawdown_usd": 0.0,
            "error": "Dados insuficientes (mín. 260 velas M1)",
        }

    closes = [float(c["close"]) for c in candles]
    highs = [float(c["high"]) for c in candles]
    lows = [float(c["low"]) for c in candles]

    trades = []
    in_trade = False
    direction = None
    entry_price = 0.0
    avg_price = 0.0
    layers = 0
    total_stake = 0.0
    trade_start_i = 0
    commission = 0.08  # Deriv standard commission approx

    equity = 0.0
    peak_equity = 0.0
    max_drawdown = 0.0

    # Start after 250 candles warmup (for MA 250 trend)
    for i in range(250, len(candles)):
        c_curr = candles[i]
        close_curr = closes[i]

        hist_curr = closes[: i + 1]
        hist_prev = closes[:i]

        ma250 = calculate_sma(hist_curr, 250)
        ma14_curr = calculate_sma(hist_curr, 14)
        ma14_prev = calculate_sma(hist_prev, 14)
        ma6_curr = calculate_sma(hist_curr, 6)
        ma6_prev = calculate_sma(hist_prev, 6)
        rsi5_curr = calculate_rsi(hist_curr, 5)

        # -------------------------------------------------------------
        # IF CURRENTLY IN AN OPEN BASKET:
        # -------------------------------------------------------------
        if in_trade:
            # Smart DCA: If candle closed adverse, add Layer 2 with double stake
            if layers == 1:
                is_adverse = (direction == "CALL" and close_curr < entry_price) or (
                    direction == "PUT" and close_curr > entry_price
                )
                if is_adverse:
                    layers = 2
                    stake2 = stake_initial * 2.0
                    avg_price = (stake_initial * entry_price + stake2 * close_curr) / (
                        stake_initial + stake2
                    )
                    total_stake = stake_initial + stake2

            # Evaluate high/low excursion within candle
            if direction == "CALL":
                pnl = (
                    total_stake * multiplier * ((highs[i] - avg_price) / avg_price)
                    - (layers * commission)
                )
                loss_pnl = (
                    total_stake * multiplier * ((lows[i] - avg_price) / avg_price)
                    - (layers * commission)
                )
            else:
                pnl = (
                    total_stake * multiplier * ((avg_price - lows[i]) / avg_price)
                    - (layers * commission)
                )
                loss_pnl = (
                    total_stake * multiplier * ((avg_price - highs[i]) / avg_price)
                    - (layers * commission)
                )

            # Exit Conditions:
            # 1. Target profit reached
            if pnl >= target_pnl:
                trade_res = {"win": True, "pnl": round(target_pnl, 2), "layers": layers}
                trades.append(trade_res)
                equity += target_pnl
                in_trade = False
            # 2. Stop loss / max hold reached (12 minutes max hold)
            elif loss_pnl <= -max_loss_pnl or (i - trade_start_i >= 12):
                final_move = (
                    (close_curr - avg_price) / avg_price
                    if direction == "CALL"
                    else (avg_price - close_curr) / avg_price
                )
                final_pnl = round(
                    total_stake * multiplier * final_move - (layers * commission), 2
                )
                is_win = final_pnl > 0
                trade_res = {"win": is_win, "pnl": final_pnl, "layers": layers}
                trades.append(trade_res)
                equity += final_pnl
                in_trade = False

            # Update drawdown
            if equity > peak_equity:
                peak_equity = equity
            dd = peak_equity - equity
            if dd > max_drawdown:
                max_drawdown = dd
            continue

        # -------------------------------------------------------------
        # CHECK ENTRY TRIGGERS ON CANDLE CLOSE (Anti-Repaint):
        # -------------------------------------------------------------
        # LONG (MULTUP):
        # 1. Preço acima da MA 250 (tendência institucional de alta)
        # 2. MA 6 cruza acima da MA 14
        # 3. RSI 5 em momento altista (>= 60)
        call_cross = (
            ma6_prev is not None
            and ma14_prev is not None
            and ma6_prev <= ma14_prev
            and ma6_curr is not None
            and ma14_curr is not None
            and ma6_curr > ma14_curr
        )
        if ma250 is not None and close_curr > ma250 and call_cross and rsi5_curr >= 60:
            in_trade = True
            direction = "CALL"
            entry_price = close_curr
            avg_price = close_curr
            layers = 1
            total_stake = stake_initial
            trade_start_i = i
            continue

        # SHORT (MULTDOWN):
        # 1. Preço abaixo da MA 250 (tendência institucional de baixa)
        # 2. MA 6 cruza abaixo da MA 14
        # 3. RSI 5 em momento baixista (<= 40)
        put_cross = (
            ma6_prev is not None
            and ma14_prev is not None
            and ma6_prev >= ma14_prev
            and ma6_curr is not None
            and ma14_curr is not None
            and ma6_curr < ma14_curr
        )
        if ma250 is not None and close_curr < ma250 and put_cross and rsi5_curr <= 40:
            in_trade = True
            direction = "PUT"
            entry_price = close_curr
            avg_price = close_curr
            layers = 1
            total_stake = stake_initial
            trade_start_i = i
            continue

    total_trades = len(trades)
    wins = sum(1 for t in trades if t["win"])
    losses = total_trades - wins
    winrate_pct = round((wins / total_trades) * 100.0, 1) if total_trades > 0 else 0.0
    total_pnl = round(sum(t["pnl"] for t in trades), 2)

    gross_profit = sum(t["pnl"] for t in trades if t["pnl"] > 0)
    gross_loss = abs(sum(t["pnl"] for t in trades if t["pnl"] < 0))
    profit_factor = (
        round(gross_profit / gross_loss, 2)
        if gross_loss > 0
        else (round(gross_profit, 2) if gross_profit > 0 else 0.0)
    )

    return {
        "symbol": symbol,
        "name": name,
        "category": category,
        "multiplier": multiplier,
        "candles_tested": len(candles),
        "trades_count": total_trades,
        "wins": wins,
        "losses": losses,
        "winrate_pct": winrate_pct,
        "total_pnl_usd": total_pnl,
        "profit_factor": profit_factor,
        "max_drawdown_usd": round(max_drawdown, 2),
    }


async def run_live_backtest(
    candidate_symbols: Optional[List[str]] = None,
    candle_count: int = 1000,
) -> Dict[str, Any]:
    """Execute live real-market backtest across candidate symbols via Deriv WS."""
    global _LATEST_BACKTEST_CACHE

    target_pool = CANDIDATE_POOLS
    if candidate_symbols:
        target_pool = [c for c in CANDIDATE_POOLS if c["symbol"] in candidate_symbols]
        if not target_pool:
            target_pool = CANDIDATE_POOLS

    logger.info(
        f"Iniciando backtest real em {len(target_pool)} pares com {candle_count} velas M1 cada..."
    )

    results: List[Dict[str, Any]] = []

    async with websockets.connect(
        DERIV_PUBLIC_WS, ping_interval=20, ping_timeout=15
    ) as ws:
        for item in target_pool:
            sym = item["symbol"]
            try:
                candles = await fetch_candles_for_symbol(ws, sym, count=candle_count)
                res = simulate_m1_strategy(
                    symbol=sym,
                    name=item["name"],
                    category=item["category"],
                    multiplier=item["multiplier"],
                    candles=candles,
                )
                results.append(res)
            except Exception as e:
                logger.error(f"Erro no backtest de {sym}: {e}")
                results.append({
                    "symbol": sym,
                    "name": item["name"],
                    "category": item["category"],
                    "multiplier": item["multiplier"],
                    "candles_tested": 0,
                    "trades_count": 0,
                    "wins": 0,
                    "losses": 0,
                    "winrate_pct": 0.0,
                    "total_pnl_usd": 0.0,
                    "profit_factor": 0.0,
                    "max_drawdown_usd": 0.0,
                    "error": str(e),
                })
            # Pequeno intervalo para respeitar taxa de requisições da Deriv
            await asyncio.sleep(0.08)

    # Sort candidates by Assertividade (Winrate %) and Total PnL
    results.sort(key=lambda x: (x.get("winrate_pct", 0), x.get("total_pnl_usd", 0)), reverse=True)

    # Mark Top 10
    top10_symbols: List[str] = []
    for idx, r in enumerate(results):
        r["rank"] = idx + 1
        is_top = idx < 10 and r.get("trades_count", 0) > 0
        r["is_top10"] = is_top
        if is_top:
            top10_symbols.append(r["symbol"])

    total_candidates = len(results)
    tested_trades = sum(r.get("trades_count", 0) for r in results)
    best_candidate = results[0] if results else None
    worst_candidate = results[-1] if results else None

    # Compute average winrate of Top 10
    top10_results = [r for r in results if r.get("is_top10")]
    avg_top10_winrate = (
        round(sum(r["winrate_pct"] for r in top10_results) / len(top10_results), 1)
        if top10_results
        else 0.0
    )

    report_payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "candle_count": candle_count,
        "total_pairs_tested": total_candidates,
        "total_trades_simulated": tested_trades,
        "avg_top10_winrate_pct": avg_top10_winrate,
        "best_pair": best_candidate,
        "worst_pair": worst_candidate,
        "top10_symbols": top10_symbols,
        "results": results,
    }

    _LATEST_BACKTEST_CACHE = report_payload
    redis_set_json("cache:latest_backtest", report_payload, ttl=86400)
    logger.info(
        f"Backtest concluído. Top 10 selecionado: {top10_symbols} (Média Winrate: {avg_top10_winrate}%)"
    )
    return report_payload


def get_latest_backtest() -> Optional[Dict[str, Any]]:
    global _LATEST_BACKTEST_CACHE
    if _LATEST_BACKTEST_CACHE:
        return _LATEST_BACKTEST_CACHE
    cached = redis_get_json("cache:latest_backtest")
    if cached:
        _LATEST_BACKTEST_CACHE = cached
        return cached
    return None
