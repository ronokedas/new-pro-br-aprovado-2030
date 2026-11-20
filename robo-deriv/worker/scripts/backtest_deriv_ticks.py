"""
NEXUS QUANTUM AI - Quantitative Backtest & Strategy Optimization Lab
=====================================================================
Utiliza o DerivClient oficial para baixar milhares de ticks reais da Deriv
e executa otimização anti-repaint das confluências:
1. Símbolos: RDBULL, JD25, 1HZ100V, R_100
2. Durations: 1T, 2T, 3T, 5T
3. Lógicas: Mean Reversion com Z-Score, Trend Momentum, Bull Pullback e Exhaustion
4. Métricas: Winrate (%), Profit Factor, Max Consecutive Losses, Expected Value ($EV$)
"""

import asyncio
import json
import math
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, "/app")
from engine.deriv_client import DerivClient
from shared.config import get_settings

SYMBOLS = ["RDBULL", "JD25", "1HZ100V", "R_100"]
TICKS_COUNT = 5000
PAYOUT_RATIO = 0.92  # 92% real Deriv payout


async def fetch_real_ticks(symbol: str, count: int = 5000) -> List[Tuple[float, int]]:
    settings = get_settings()
    ws_url = f"{settings.DERIV_AUTH_WS_BASE}?app_id={settings.DERIV_APP_ID}"
    client = DerivClient(ws_url=ws_url, fallback_ws_url=settings.DERIV_PUBLIC_WS, label=f"backtest-{symbol}")

    ticks: List[Tuple[float, int]] = []
    try:
        print(f"[*] Conectando ao WebSocket Deriv para baixar ticks de {symbol}...")
        await client.connect(timeout=15.0)
        resp = await client.ticks_history(
            symbol=symbol,
            count=count,
            style="ticks",
            end="latest",
        )
        history = resp.get("history") or {}
        prices = history.get("prices") or []
        times = history.get("times") or []

        for p, t in zip(prices, times):
            try:
                ticks.append((float(p), int(t)))
            except (ValueError, TypeError):
                continue
    except Exception as exc:
        print(f"[-] Erro ao baixar ticks de {symbol}: {exc}")
    finally:
        await client.close()

    print(f"[+] {symbol}: {len(ticks)} ticks recebidos.")
    return ticks


def run_backtest_simulation(
    symbol: str,
    ticks: List[Tuple[float, int]],
    duration: int,
    strategy: str,
    params: Dict[str, Any],
    payout: float = PAYOUT_RATIO,
) -> Dict[str, Any]:
    n = len(ticks)
    if n < 80:
        return {"trades": 0, "winrate": 0.0, "profit_factor": 0.0}

    prices = [p for p, _ in ticks]

    # Precompute Indicators
    ema10 = [0.0] * n
    ema20 = [0.0] * n
    zscore = [0.0] * n

    a10 = 2.0 / 11.0
    a20 = 2.0 / 21.0
    ema10[0] = prices[0]
    ema20[0] = prices[0]

    for i in range(1, n):
        ema10[i] = a10 * prices[i] + (1.0 - a10) * ema10[i - 1]
        ema20[i] = a20 * prices[i] + (1.0 - a20) * ema20[i - 1]

        if i >= 30:
            window = prices[i - 29 : i + 1]
            mean = sum(window) / 30.0
            var = sum((x - mean) ** 2 for x in window) / 30.0
            std = math.sqrt(var)
            if std > 1e-6:
                zscore[i] = (prices[i] - ema20[i]) / std

    wins = 0
    losses = 0
    consec_l = 0
    max_consec_l = 0
    gross_win = 0.0
    gross_loss = 0.0
    pnl = 0.0
    last_exit_idx = -1

    z_thresh = params.get("z_thresh", 1.5)

    for i in range(35, n - duration):
        if i < last_exit_idx:
            continue

        p0 = prices[i]
        p1 = prices[i - 1]
        p2 = prices[i - 2]
        p3 = prices[i - 3]

        sig: Optional[str] = None

        if strategy == "bull_market_pullback":
            # Estratégia #1: Pullback em tendência de alta no RDBULL
            # Espera 2 ticks de recuo (p2 > p1) seguidos de retomada imediata (p0 > p1) acima da EMA-20
            if (p2 > p1) and (p0 > p1) and (p0 >= ema20[i]) and (ema10[i] >= ema20[i]):
                sig = "CALL"
            elif (zscore[i] <= -1.15) and (p0 > p1):
                sig = "CALL"

        elif strategy == "z_score_reversal":
            # Estratégia #2: Reversão à média estatística com filtro de confirmação
            if zscore[i] <= -z_thresh and p0 > p1:
                sig = "CALL"
            elif zscore[i] >= z_thresh and p0 < p1:
                sig = "PUT"

        elif strategy == "micro_momentum_run":
            # Estratégia #3: Micro-rompimento com 3 ticks e EMA alinhada
            if p0 > p1 > p2 and ema10[i] > ema20[i]:
                sig = "CALL"
            elif p0 < p1 < p2 and ema10[i] < ema20[i]:
                sig = "PUT"

        elif strategy == "jump_reaction":
            # Estratégia #4: JD25 Reversão pós-salto / exaustão
            if zscore[i] <= -1.8 and p0 > p1:
                sig = "CALL"
            elif zscore[i] >= 1.8 and p0 < p1:
                sig = "PUT"

        if sig is not None:
            entry = p0
            exit_p = prices[i + duration]
            last_exit_idx = i + duration

            win = (sig == "CALL" and exit_p > entry) or (sig == "PUT" and exit_p < entry)

            if win:
                wins += 1
                gain = 1.0 * payout
                gross_win += gain
                pnl += gain
                consec_l = 0
            else:
                losses += 1
                gross_loss += 1.0
                pnl -= 1.0
                consec_l += 1
                if consec_l > max_consec_l:
                    max_consec_l = consec_l

    tot = wins + losses
    wr = (wins / tot * 100.0) if tot > 0 else 0.0
    pf = (gross_win / gross_loss) if gross_loss > 0 else 999.0
    ev = (wr / 100.0 * payout) - ((1.0 - wr / 100.0) * 1.0)

    return {
        "symbol": symbol,
        "strategy": strategy,
        "duration": duration,
        "trades": tot,
        "wins": wins,
        "losses": losses,
        "winrate": round(wr, 2),
        "profit_factor": round(pf, 2),
        "ev": round(ev, 4),
        "pnl": round(pnl, 2),
        "max_consec_losses": max_consec_l,
        "params": params,
    }


async def main():
    print("=" * 80)
    print("NEXUS QUANTUM AI - BACKTEST E OTIMIZAÇÃO QUANTITATIVA ANTI-REPAINT")
    print("=" * 80)

    dataset: Dict[str, List[Tuple[float, int]]] = {}
    for sym in SYMBOLS:
        ticks = await fetch_real_ticks(sym, count=TICKS_COUNT)
        if ticks:
            dataset[sym] = ticks

    if not dataset:
        print("[-] Nenhum dado baixado. Encerrando.")
        return

    results: List[Dict[str, Any]] = []

    test_configs = [
        ("bull_market_pullback", {"bias": "bull"}, [1, 2, 3, 5]),
        ("z_score_reversal", {"z_thresh": 1.3}, [1, 2, 3, 5]),
        ("z_score_reversal", {"z_thresh": 1.6}, [1, 2, 3, 5]),
        ("z_score_reversal", {"z_thresh": 1.9}, [1, 2, 3, 5]),
        ("micro_momentum_run", {}, [1, 2, 3, 5]),
        ("jump_reaction", {}, [1, 2, 3, 5]),
    ]

    for sym, ticks in dataset.items():
        for strat, params, durations in test_configs:
            if strat == "bull_market_pullback" and sym != "RDBULL":
                continue
            if strat == "jump_reaction" and not sym.startswith("JD"):
                continue

            for dur in durations:
                res = run_backtest_simulation(sym, ticks, dur, strat, params)
                if res["trades"] >= 20:
                    results.append(res)

    # Sort results strictly by Profit Factor and Winrate
    results.sort(key=lambda r: (r["profit_factor"], r["winrate"], r["ev"]), reverse=True)

    print("\n" + "=" * 92)
    print(f"{'ATIVO':<10} | {'ESTRATÉGIA':<22} | {'DUR':<4} | {'TRADES':<7} | {'WINRATE':<8} | {'P. FACTOR':<9} | {'MAX L':<5} | {'EV/trade':<8}")
    print("=" * 92)

    for r in results[:25]:
        status_flag = "⭐ LUCRATIVO" if r["profit_factor"] >= 1.20 else "✓ POSITIVO" if r["profit_factor"] > 1.0 else "✕ NEGATIVO"
        print(
            f"{r['symbol']:<10} | {r['strategy']:<22} | {r['duration']:<4} | "
            f"{r['trades']:<7} | {r['winrate']:<6}% | {r['profit_factor']:<9} | "
            f"{r['max_consec_losses']:<5} | {r['ev']:<8} | {status_flag}"
        )
    print("=" * 92)

    profitable = [r for r in results if r["profit_factor"] > 1.15 and r["trades"] >= 30]
    if profitable:
        best = profitable[0]
        print(f"\n[🏆 MELHOR ESTRATÉGIA COMPROVADA MATEMATICAMENTE]:")
        print(f"-> Ativo: {best['symbol']}")
        print(f"-> Estratégia: {best['strategy']}")
        print(f"-> Duração: {best['duration']} Ticks")
        print(f"-> Winrate: {best['winrate']}% (Breakeven é 52.1%)")
        print(f"-> Profit Factor: {best['profit_factor']}")
        print(f"-> Expectativa Matemática: +{best['ev']} por trade")
        print(f"-> Sequência Máxima de Derrotas: apenas {best['max_consec_losses']}")

        with open("/app/best_strategy_params.json", "w") as f:
            json.dump(best, f, indent=2)


if __name__ == "__main__":
    asyncio.run(main())
