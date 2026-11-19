"""
Backtest 100% Causal da Logica Exata do Video (YouTube uetcJ9tlQp8 — Regressao Linear 50 +/-2sigma + ATR 10):
- Canal de Regressao Linear rolling causal (window=50, +/-2.0*sigma) -> SEM repaint!
- Contencao: not HasPosition e High < Upper e Low > Lower
- Direcao: Close < Mid -> Compra limite em Lower (executa se barra seguinte toca low <= Lower)
           Close > Mid -> Venda limite em Upper (executa se barra seguinte toca high >= Upper)
- Saida exata do video: TP = 0.75 * ATR(10), SL = 1.90 * ATR(10) (e variacoes TP/SL + DCA 3 camadas)
- Custos REAIS verificados na API Deriv:
  * cryBTCUSD 100x: comissao 7.00 bps nocional (7% do stake), stop-out -0.93%, spread 0.31 bps
  * R_75 50x:       comissao 2.92 bps nocional (1.46% do stake), stop-out -1.97%, spread 0.30 bps
  * frxEURUSD 50x:  comissao 2.08 bps nocional (1.04% do stake), stop-out -1.98%, spread 0.50 bps
- Split estrito 70% DEV / 30% HOLDOUT VIRGEM
"""
import math
import sys
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from strategy_core import compute_linear_regression_channel
from simulator import load_btc_1m_data, resample_ohlcv, fetch_deriv_candles


def calc_atr(df: pd.DataFrame, period: int = 10) -> np.ndarray:
    h = df["high"].to_numpy(dtype=np.float64)
    l = df["low"].to_numpy(dtype=np.float64)
    c = df["close"].to_numpy(dtype=np.float64)
    prev_c = np.roll(c, 1)
    prev_c[0] = c[0]
    tr = np.maximum(h - l, np.maximum(np.abs(h - prev_c), np.abs(l - prev_c)))
    return pd.Series(tr).rolling(period, min_periods=period).mean().to_numpy(dtype=np.float64)


def run_video_sim(
    df: pd.DataFrame,
    mult: int,
    comm_notional_bps: float,
    spread_bps: float,
    stake: float = 1.0,
    ch_window: int = 50,
    ch_sigma: float = 2.0,
    atr_period: int = 10,
    tp_atr: float = 0.75,
    sl_atr: float = 1.90,
    max_layers: int = 1,
    dca_step_atr: float = 0.80,
    start_idx: int = 0,
    end_idx: int = None,
):
    close = df["close"].to_numpy(dtype=np.float64)
    high = df["high"].to_numpy(dtype=np.float64)
    low = df["low"].to_numpy(dtype=np.float64)
    opn = df["open"].to_numpy(dtype=np.float64)

    mid, upper, lower, r2, slope_bps, _ = compute_linear_regression_channel(close, ch_window, ch_sigma)
    atr = calc_atr(df, atr_period)

    n = len(df)
    if end_idx is None:
        end_idx = n
    half_sp = (spread_bps / 2.0) / 10000.0
    comm_per_dollar_stake = (comm_notional_bps / 10000.0) * mult  # e.g., 7bp * 100 = 0.07 ($0.07 por $1)
    stop_out_dist = (1.0 - comm_per_dollar_stake) / mult          # e.g., 0.93 / 100 = 0.0093 (-0.93%)

    # Estado do trade
    in_pos = False
    direction = 0
    layers = []  # list of (entry_px, stake_k, comm_k, so_px)
    tp_px = 0.0
    sl_px = 0.0
    atr_at_entry = 0.0
    next_dca_px = 0.0

    pnls = []
    comms = []
    wins = 0
    stop_outs = 0
    reasons = {}

    for i in range(max(start_idx, ch_window + 5), end_idx):
        # 1) Se NAO estamos posicionados, verificar se barra i-1 armou ordem limite (exato do video!)
        if not in_pos:
            u_p, m_p, l_p, a_p = upper[i - 1], mid[i - 1], lower[i - 1], atr[i - 1]
            if np.isnan(u_p) or np.isnan(a_p) or a_p <= 0:
                continue
            # Condicao exata do video: High[i-1] < Upper[i-1] e Low[i-1] > Lower[i-1]
            if not (high[i - 1] < u_p and low[i - 1] > l_p):
                continue

            # Close < Mid -> BuyLimit(Lower); Close > Mid -> SellShortLimit(Upper)
            if close[i - 1] < m_p:
                # Tocou limite de compra na barra i? (ordem limite executa em l_p se low[i] <= l_p e open[i] > l_p)
                if opn[i] > l_p and low[i] <= l_p:
                    entry = l_p * (1.0 + half_sp)
                    c1 = stake * comm_per_dollar_stake
                    so1 = entry * (1.0 - stop_out_dist)
                    in_pos = True
                    direction = 1
                    layers = [(entry, stake, c1, so1)]
                    atr_at_entry = a_p
                    tp_px = entry + tp_atr * a_p
                    sl_px = entry - sl_atr * a_p
                    next_dca_px = entry - dca_step_atr * a_p
                    # Avaliar saida conservadora na mesma barra (pior caso: stop/SO antes de TP)
                    worst = low[i] * (1.0 - half_sp)
                    best = high[i] * (1.0 - half_sp)
                    if worst <= so1:
                        pnls.append(-stake); comms.append(c1); stop_outs += 1
                        reasons["SO"] = reasons.get("SO", 0) + 1; in_pos = False; continue
                    if max_layers == 1 and worst <= sl_px:
                        net = stake * mult * ((sl_px - entry) / entry) - c1
                        net = max(-stake, net)
                        pnls.append(net); comms.append(c1)
                        reasons["SL"] = reasons.get("SL", 0) + 1; in_pos = False; continue
                    # Nota conservadora: na mesma barra do toque nao assumimos TP antes de nova barra (evita viés OHLC!)
                    continue
            elif close[i - 1] > m_p:
                if opn[i] < u_p and high[i] >= u_p:
                    entry = u_p * (1.0 - half_sp)
                    c1 = stake * comm_per_dollar_stake
                    so1 = entry * (1.0 + stop_out_dist)
                    in_pos = True
                    direction = -1
                    layers = [(entry, stake, c1, so1)]
                    atr_at_entry = a_p
                    tp_px = entry - tp_atr * a_p
                    sl_px = entry + sl_atr * a_p
                    next_dca_px = entry + dca_step_atr * a_p
                    worst = high[i] * (1.0 + half_sp)
                    if worst >= so1:
                        pnls.append(-stake); comms.append(c1); stop_outs += 1
                        reasons["SO"] = reasons.get("SO", 0) + 1; in_pos = False; continue
                    if max_layers == 1 and worst >= sl_px:
                        net = stake * mult * ((entry - sl_px) / entry) - c1
                        net = max(-stake, net)
                        pnls.append(net); comms.append(c1)
                        reasons["SL"] = reasons.get("SL", 0) + 1; in_pos = False; continue
                    continue
        else:
            # 2) Ja estamos posicionados: checar reentrada DCA (se max_layers > 1), Stop-Out, SL e TP
            if direction == 1:
                worst = low[i] * (1.0 - half_sp)
                best = high[i] * (1.0 - half_sp)

                # Reentrada DCA se tocou next_dca_px antes de stop-out da camada 1
                if len(layers) < max_layers and low[i] <= next_dca_px:
                    e_new = next_dca_px * (1.0 + half_sp)
                    s_new = stake * (1.0 if len(layers) == 1 else 1.5)
                    c_new = s_new * comm_per_dollar_stake
                    so_new = e_new * (1.0 - stop_out_dist)
                    layers.append((e_new, s_new, c_new, so_new))
                    # Recalcular preco medio ponderado e novo alvo acima do preco medio!
                    tot_s = sum(x[1] for x in layers)
                    avg_e = sum(x[0] * x[1] for x in layers) / tot_s
                    tp_px = avg_e + tp_atr * atr_at_entry
                    sl_px = layers[0][0] - sl_atr * atr_at_entry
                    next_dca_px = e_new - dca_step_atr * atr_at_entry

                # Checar Stop-Out individual primeiro (conservador)
                realized_so = 0.0
                alive = []
                for e_k, s_k, c_k, so_k in layers:
                    if worst <= so_k:
                        realized_so -= s_k
                        stop_outs += 1
                    else:
                        alive.append((e_k, s_k, c_k, so_k))
                if not alive:
                    tot_c = sum(x[2] for x in layers)
                    pnls.append(realized_so); comms.append(tot_c)
                    reasons["SO"] = reasons.get("SO", 0) + 1; in_pos = False; continue

                # Checar SL (conservador antes de TP)
                if worst <= sl_px:
                    net = realized_so + sum(max(-s_k, s_k * mult * ((sl_px - e_k) / e_k) - c_k) for e_k, s_k, c_k, _ in alive)
                    tot_c = sum(x[2] for x in layers)
                    pnls.append(net); comms.append(tot_c)
                    if net > 0: wins += 1
                    reasons["SL"] = reasons.get("SL", 0) + 1; in_pos = False; continue

                # Checar TP
                if best >= tp_px:
                    net = realized_so + sum(max(-s_k, s_k * mult * ((tp_px - e_k) / e_k) - c_k) for e_k, s_k, c_k, _ in alive)
                    tot_c = sum(x[2] for x in layers)
                    pnls.append(net); comms.append(tot_c)
                    if net > 0: wins += 1
                    reasons["TP"] = reasons.get("TP", 0) + 1; in_pos = False; continue
            else:
                worst = high[i] * (1.0 + half_sp)
                best = low[i] * (1.0 + half_sp)

                if len(layers) < max_layers and high[i] >= next_dca_px:
                    e_new = next_dca_px * (1.0 - half_sp)
                    s_new = stake * (1.0 if len(layers) == 1 else 1.5)
                    c_new = s_new * comm_per_dollar_stake
                    so_new = e_new * (1.0 + stop_out_dist)
                    layers.append((e_new, s_new, c_new, so_new))
                    tot_s = sum(x[1] for x in layers)
                    avg_e = sum(x[0] * x[1] for x in layers) / tot_s
                    tp_px = avg_e - tp_atr * atr_at_entry
                    sl_px = layers[0][0] + sl_atr * atr_at_entry
                    next_dca_px = e_new + dca_step_atr * atr_at_entry

                realized_so = 0.0
                alive = []
                for e_k, s_k, c_k, so_k in layers:
                    if worst >= so_k:
                        realized_so -= s_k
                        stop_outs += 1
                    else:
                        alive.append((e_k, s_k, c_k, so_k))
                if not alive:
                    tot_c = sum(x[2] for x in layers)
                    pnls.append(realized_so); comms.append(tot_c)
                    reasons["SO"] = reasons.get("SO", 0) + 1; in_pos = False; continue

                if worst >= sl_px:
                    net = realized_so + sum(max(-s_k, s_k * mult * ((e_k - sl_px) / e_k) - c_k) for e_k, s_k, c_k, _ in alive)
                    tot_c = sum(x[2] for x in layers)
                    pnls.append(net); comms.append(tot_c)
                    if net > 0: wins += 1
                    reasons["SL"] = reasons.get("SL", 0) + 1; in_pos = False; continue

                if best <= tp_px:
                    net = realized_so + sum(max(-s_k, s_k * mult * ((e_k - tp_px) / e_k) - c_k) for e_k, s_k, c_k, _ in alive)
                    tot_c = sum(x[2] for x in layers)
                    pnls.append(net); comms.append(tot_c)
                    if net > 0: wins += 1
                    reasons["TP"] = reasons.get("TP", 0) + 1; in_pos = False; continue

    n_tr = len(pnls)
    if n_tr == 0:
        return dict(n=0, wr=0, net=0, comm=0, pf=0, exp=0, so=0, dd=0)
    arr = np.array(pnls)
    gp = arr[arr > 0].sum()
    gl = -arr[arr < 0].sum()
    pf = gp / gl if gl > 0 else (99.0 if gp > 0 else 0.0)
    eq = np.cumsum(arr)
    dd = float((np.maximum.accumulate(np.concatenate([[0.0], eq])) - np.concatenate([[0.0], eq])).max())
    return dict(n=n_tr, wr=wins / n_tr * 100.0, net=float(arr.sum()), comm=float(sum(comms)),
                pf=float(pf), exp=float(arr.mean()), so=stop_outs, dd=dd)


def main():
    btc_1m = load_btc_1m_data()
    btc_m5 = resample_ohlcv(btc_1m, "5min")
    btc_m15 = resample_ohlcv(btc_1m, "15min")
    eur_m5 = fetch_deriv_candles("frxEURUSD", granularity=300)
    eur_m15 = resample_ohlcv(eur_m5, "15min")
    r75_m5 = fetch_deriv_candles("R_75", granularity=300)
    r75_m15 = resample_ohlcv(r75_m5, "15min")

    tests = [
        # (Label, df, mult, comm_bp, spread_bp, stake, tp_atr, sl_atr, max_layers, dca_step_atr)
        ("VIDEO EXATO | BTC 100x M1 ($1)",         btc_1m,  100, 7.00, 0.31, 1.0, 0.75, 1.90, 1, 0.8),
        ("VIDEO EXATO | BTC 100x M5 ($1)",         btc_m5,  100, 7.00, 0.31, 1.0, 0.75, 1.90, 1, 0.8),
        ("VIDEO EXATO | BTC 100x M15 ($1)",        btc_m15, 100, 7.00, 0.31, 1.0, 0.75, 1.90, 1, 0.8),
        ("VIDEO+DCA3  | BTC 100x M5 ($1)",         btc_m5,  100, 7.00, 0.31, 1.0, 0.75, 2.20, 3, 0.7),
        ("VIDEO+DCA3  | BTC 100x M15 ($1)",        btc_m15, 100, 7.00, 0.31, 1.0, 0.75, 2.20, 3, 0.6),
        ("VIDEO EXATO | BTC SEM TAXA M5 (diag)",   btc_m5,  100, 0.00, 0.00, 1.0, 0.75, 1.90, 1, 0.8),
        ("VIDEO EXATO | R_75 50x M5 ($1)",         r75_m5,   50, 2.92, 0.30, 1.0, 0.75, 1.90, 1, 0.8),
        ("VIDEO EXATO | R_75 50x M15 ($1)",        r75_m15,  50, 2.92, 0.30, 1.0, 0.75, 1.90, 1, 0.8),
        ("VIDEO+DCA3  | R_75 50x M5 ($1)",         r75_m5,   50, 2.92, 0.30, 1.0, 0.75, 2.50, 3, 0.8),
        ("VIDEO+DCA3  | R_75 50x M15 ($1)",        r75_m15,  50, 2.92, 0.30, 1.0, 0.75, 2.50, 3, 0.8),
        ("VIDEO EXATO | R_75 SEM TAXA M5 (diag)",  r75_m5,   50, 0.00, 0.00, 1.0, 0.75, 1.90, 1, 0.8),
        ("VIDEO EXATO | EURUSD 50x M5 ($1)",       eur_m5,   50, 2.08, 0.50, 1.0, 0.75, 1.90, 1, 0.8),
        ("VIDEO EXATO | EURUSD 50x M15 ($1)",      eur_m15,  50, 2.08, 0.50, 1.0, 0.75, 1.90, 1, 0.8),
        ("VIDEO+DCA3  | EURUSD 50x M15 ($1)",      eur_m15,  50, 2.08, 0.50, 1.0, 0.75, 2.50, 3, 0.8),
    ]

    print(f"{'Estrategia (Canal 50 +-2s + ATR10)':38s} | {'DEV70 N/WR/Net/PF':28s} | {'OOS30 N/WR/Net/PF/DD':34s}")
    print("-" * 108)
    for lbl, df, mult, cb, sp, st, tp, sl, ml, dstep in tests:
        cut = int(len(df) * 0.70)
        d = run_video_sim(df, mult, cb, sp, st, 50, 2.0, 10, tp, sl, ml, dstep, 0, cut)
        o = run_video_sim(df, mult, cb, sp, st, 50, 2.0, 10, tp, sl, ml, dstep, cut, len(df))
        ds = f"N={d['n']:4d} {d['wr']:4.1f}% ${d['net']:+7.2f} PF={d['pf']:4.2f}"
        os_ = f"N={o['n']:4d} {o['wr']:4.1f}% ${o['net']:+7.2f} PF={o['pf']:4.2f} DD=${o['dd']:.2f}"
        flag = " **PASS" if (o["n"] >= 20 and o["pf"] >= 1.10 and d["pf"] >= 1.05) else ""
        print(f"{lbl:38s} | {ds:28s} | {os_:34s}{flag}")


if __name__ == "__main__":
    main()
