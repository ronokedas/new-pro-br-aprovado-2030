"""
Auditoria Quantitativa Rigorosa: Deriv cryBTCUSD vs Binance Spot/Perp
- Coleta ~180.000 ticks reais da Deriv cryBTCUSD (~50 horas / ~3.000 velas M1) com split temporal estrito (DEV vs HOLDOUT)
- Corrige timestamp da Binance 1s (2.880 velas M1) com split temporal estrito (DEV vs HOLDOUT)
- Testa confluencias 15s x 4 (Q1..Q4) + primeiros 5s (S5) com:
  * Quantis 100% CAUSAIS (rolling 240 velas, shift(1) - ZERO vazamento)
  * Execucao realista: p5 (+5s exato), p6 (+1s latencia de clique), p7 (+2s latencia)
  * Comparacao WinEntry REAL (5->60s) vs WinCor (0->60s) + teste com spread real da Deriv!
- Benchmark ao vivo (40s): Binance Perp + Binance Spot vs Deriv cryBTCUSD (Lead-Lag em ms + Spread real)
"""
import asyncio
import json
import math
import os
import time
import warnings
import numpy as np
import pandas as pd
import websockets

warnings.filterwarnings("ignore")

ROOT = r"D:\backup importantes\ficar-rico\btc-proxima-vela"
DATA_1S_BIN = r"C:\trading-agents\btc_color_research\data\BTCUSDT_1s_2d.parquet"
DERIV_WS = "wss://api.derivws.com/trading/v1/options/ws/public"
BIN_SPOT_WS = "wss://stream.binance.com:9443/ws/btcusdt@bookTicker"
BIN_PERP_WS = "wss://fstream.binance.com/ws/btcusdt@bookTicker"


def wilson_ci(k: int, n: int, z: float = 1.959963984540054):
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    denom = 1.0 + z * z / n
    center = (p + z * z / (2.0 * n)) / denom
    half = (z * math.sqrt((p * (1.0 - p) + z * z / (4.0 * n)) / n)) / denom
    return max(0.0, center - half), min(1.0, center + half)


def eval_sig(name: str, sig: np.ndarray, entry_px: np.ndarray,
             open_t: np.ndarray, close_t: np.ndarray, half_spread_bps: float = 0.0):
    m = sig != 0
    n_tot = len(sig)
    n = int(m.sum())
    if n == 0:
        return dict(name=name, n=0, cov=0.0, win_e=0.0, w_lo=0.0, w_hi=0.0, win_c=0.0, bps=0.0, win_sp=0.0)
    s = sig[m]
    e = entry_px[m]
    o = open_t[m]
    c = close_t[m]

    we = np.where(s == 1, c > e, c < e)
    wc = np.where(s == 1, c > o, c < o)
    # Com half-spread (se corretora aplica spread/barrier no preco de entrada)
    e_sp = np.where(s == 1, e * (1.0 + half_spread_bps / 10000.0), e * (1.0 - half_spread_bps / 10000.0))
    wsp = np.where(s == 1, c > e_sp, c < e_sp)

    k = int(we.sum())
    acc_e = k / n
    w_lo, w_hi = wilson_ci(k, n)
    bps = float((s * (c - e) / e * 10000.0).mean())
    return dict(
        name=name, n=n, cov=n / n_tot, win_e=acc_e,
        w_lo=w_lo, w_hi=w_hi, win_c=float(wc.mean()), bps=bps, win_sp=float(wsp.mean())
    )


def print_table(title: str, rows: list, show_sp: bool = False):
    print(f"\n{'='*118}")
    print(f" {title}")
    print(f"{'='*118}")
    sp_hdr = " | {'Win+Sp':>6s}" if show_sp else ""
    hdr = (f"{'Regra':40s} | {'N':>5s} | {'Cov':>5s} | "
           f"{'WinEntry':>8s} | {'Wilson 95%':>15s} | {'WinCor':>6s} | {'BpsEnt':>7s}")
    if show_sp:
        hdr += f" | {'Win+Sp':>6s}"
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        ci = f"[{r['w_lo']*100:4.1f}%, {r['w_hi']*100:4.1f}%]"
        flag = " **>55.6%" if (r["n"] >= 30 and r["w_lo"] > 0.556) else (" *>50%" if (r["n"] >= 30 and r["w_lo"] > 0.50) else "")
        line = (
            f"{r['name']:40s} | {r['n']:5d} | {r['cov']*100:4.1f}% | "
            f"{r['win_e']*100:7.2f}% | {ci:>15s} | {r['win_c']*100:5.2f}% | "
            f"{r['bps']:+7.2f}"
        )
        if show_sp:
            line += f" | {r['win_sp']*100:5.2f}%"
        print(line + flag)


# ==============================================================================
# 1. FETCH ~180k TICKS REAIS DA DERIV (~50 HORAS / ~3.000 VELAS M1)
# ==============================================================================
async def fetch_deriv_ticks(max_pages: int = 180):
    ticks_p, ticks_t = [], []
    async with websockets.connect(DERIV_WS, ping_interval=None, max_size=2**24) as ws:
        end_ts = "latest"
        for pg in range(max_pages):
            await ws.send(json.dumps({
                "ticks_history": "cryBTCUSD",
                "style": "ticks",
                "end": str(end_ts),
                "count": 1000
            }))
            r = json.loads(await ws.recv())
            if "error" in r:
                print(f"[DERIV TICKS] parou na pag {pg}: {r['error']}")
                break
            h = r.get("history", {})
            pr = h.get("prices", [])
            tm = h.get("times", [])
            if not pr:
                break
            ticks_p.extend(pr)
            ticks_t.extend(tm)
            new_end = int(tm[0]) - 1
            if str(new_end) == str(end_ts):
                break
            end_ts = new_end
            if (pg + 1) % 30 == 0:
                await asyncio.sleep(0.15)
            else:
                await asyncio.sleep(0.03)
    df_t = pd.DataFrame({"epoch": ticks_t, "price": ticks_p}).drop_duplicates("epoch").sort_values("epoch").reset_index(drop=True)
    return df_t


# ==============================================================================
# 2. FEATURE MATRIX 15s (Q1..Q4) + 5s (S5) + EXECUCAO +1s/+2s
# ==============================================================================
def build_15s_5s_features(df_sec: pd.DataFrame, price_col: str, time_col: str, is_epoch: bool):
    df = df_sec.copy()
    if is_epoch:
        ts = df[time_col].to_numpy(dtype=np.int64)
    else:
        # Conversao segura independente de datetime64[ms], [us] ou [ns]
        ts = pd.to_datetime(df[time_col], utc=True).astype("datetime64[s, UTC]").astype("int64").to_numpy()
    df["minute"] = (ts // 60) * 60
    df["sec"] = ts % 60

    recs = []
    for minute, g in df.groupby("minute", sort=True):
        if len(g) < 25:
            continue
        secs = g["sec"].to_numpy()
        px = g[price_col].to_numpy(dtype=np.float64)
        o = float(px[0])
        c = float(px[-1])

        def last_before(s_lim, fallback):
            idx = np.where(secs <= s_lim)[0]
            return float(px[idx[-1]]) if len(idx) > 0 else fallback

        p0 = o
        p5 = last_before(5, p0)
        p6 = last_before(6, p5)
        p7 = last_before(7, p6)
        p15 = last_before(14, p0)
        p30 = last_before(29, p15)
        p45 = last_before(44, p30)
        p60 = c

        recs.append({
            "minute": int(minute), "n_ticks": len(px),
            "open": o, "close": c, "p5": p5, "p6": p6, "p7": p7,
            "q1": (p15 - p0) / p0 * 10000.0,
            "q2": (p30 - p15) / p15 * 10000.0,
            "q3": (p45 - p30) / p30 * 10000.0,
            "q4": (p60 - p45) / p45 * 10000.0,
            "r5": (p5 - p0) / p0 * 10000.0,
        })

    m1 = pd.DataFrame(recs).sort_values("minute").reset_index(drop=True)
    # Quantis estritamente CAUSAIS (rolling 240 velas anteriores, shift(1) para nao usar a vela atual!)
    m1["q4_thr70"] = m1["q4"].abs().rolling(240, min_periods=60).quantile(0.70).shift(1)
    m1["r5_thr70"] = m1["r5"].abs().rolling(240, min_periods=60).quantile(0.70).shift(1)
    return m1


def run_split_audit(m1: pd.DataFrame, source_label: str, half_spread_bps: float = 0.0):
    dt_ok = (m1["minute"].diff() == 60) & m1["r5_thr70"].notna()
    valid_idx = np.where(dt_ok.to_numpy())[0]
    if len(valid_idx) < 100:
        print(f"[{source_label}] Amostra insuficiente: N={len(valid_idx)}")
        return

    mid_cut = valid_idx[len(valid_idx) // 2]
    splits = [
        ("DEV (1a metade)", dt_ok & (m1.index < mid_cut)),
        ("HOLDOUT (2a metade)", dt_ok & (m1.index >= mid_cut)),
    ]

    for sp_name, mask in splits:
        prev = m1.shift(1).loc[mask].reset_index(drop=True)
        curr = m1.loc[mask].reset_index(drop=True)
        N = len(curr)

        o_t = curr["open"].to_numpy()
        c_t = curr["close"].to_numpy()
        p5_t = curr["p5"].to_numpy()
        p6_t = curr["p6"].to_numpy()
        p7_t = curr["p7"].to_numpy()
        r5_t = curr["r5"].to_numpy()
        r5_thr = curr["r5_thr70"].to_numpy()

        c_p = prev["close"].to_numpy()
        o_p = prev["open"].to_numpy()
        q1 = prev["q1"].to_numpy()
        q2 = prev["q2"].to_numpy()
        q3 = prev["q3"].to_numpy()
        q4 = prev["q4"].to_numpy()
        q4_thr = prev["q4_thr70"].to_numpy()

        s_col = np.sign(c_p - o_p).astype(int)
        s_q1 = np.sign(q1).astype(int)
        s_q2 = np.sign(q2).astype(int)
        s_q3 = np.sign(q3).astype(int)
        s_q4 = np.sign(q4).astype(int)
        s_5 = np.sign(r5_t).astype(int)

        s_q34 = np.where((s_q3 != 0) & (s_q3 == s_q4), s_q4, 0)
        s_q234 = np.where((s_q2 != 0) & (s_q2 == s_q3) & (s_q3 == s_q4), s_q4, 0)
        s_4x15 = np.where((s_q1 != 0) & (s_q1 == s_q2) & (s_q2 == s_q3) & (s_q3 == s_q4), s_q4, 0)
        s_5f = np.where(np.abs(r5_t) >= r5_thr, s_5, 0)
        s_q4f = np.where(np.abs(q4) >= q4_thr, s_q4, 0)

        s_q4_s5 = np.where((s_q4 != 0) & (s_q4 == s_5), s_5, 0)
        s_q34_s5 = np.where((s_q34 != 0) & (s_q34 == s_5), s_5, 0)
        s_q234_s5 = np.where((s_q234 != 0) & (s_q234 == s_5), s_5, 0)
        s_4x15_s5 = np.where((s_4x15 != 0) & (s_4x15 == s_5), s_5, 0)
        s_q34_s5f = np.where((s_q34 != 0) & (s_q34 == s_5f), s_5f, 0)
        s_col_q4_s5 = np.where((s_col != 0) & (s_col == s_q4) & (s_q4 == s_5), s_5, 0)

        rows = [
            # Produto A (:00s)
            eval_sig("[A :00s] B2_cor_anterior (Cont)", s_col, o_t, o_t, c_t, half_spread_bps),
            eval_sig("[A :00s] Q4 ultimos 15s (Cont)", s_q4, o_t, o_t, c_t, half_spread_bps),
            eval_sig("[A :00s] Q3+Q4 ultimos 30s (Cont)", s_q34, o_t, o_t, c_t, half_spread_bps),
            eval_sig("[A :00s] 4x15s TODOS iguais (Cont)", s_4x15, o_t, o_t, c_t, half_spread_bps),
            # Produto B (+5s) — WinEntry = 5->60s!
            eval_sig("[B +5s] S5 sozinho (Cont 5->60s)", s_5, p5_t, o_t, c_t, half_spread_bps),
            eval_sig("[B +5s] S5_Forte Q70 causal (Cont)", s_5f, p5_t, o_t, c_t, half_spread_bps),
            eval_sig("[B +5s] Q4 + S5 (Cont)", s_q4_s5, p5_t, o_t, c_t, half_spread_bps),
            eval_sig("[B +5s] Q3 + Q4 + S5 (Cont @p5)", s_q34_s5, p5_t, o_t, c_t, half_spread_bps),
            eval_sig("[B +6s] Q3 + Q4 + S5 (+1s clique @p6)", s_q34_s5, p6_t, o_t, c_t, half_spread_bps),
            eval_sig("[B +7s] Q3 + Q4 + S5 (+2s clique @p7)", s_q34_s5, p7_t, o_t, c_t, half_spread_bps),
            eval_sig("[B +5s] Q2+Q3+Q4 + S5 (Cont)", s_q234_s5, p5_t, o_t, c_t, half_spread_bps),
            eval_sig("[B +5s] 4x15s + S5 TODOS (Cont)", s_4x15_s5, p5_t, o_t, c_t, half_spread_bps),
            eval_sig("[B +5s] Q3+Q4 + S5_Forte (@p5)", s_q34_s5f, p5_t, o_t, c_t, half_spread_bps),
            eval_sig("[B +6s] Q3+Q4 + S5_Forte (+1s @p6)", s_q34_s5f, p6_t, o_t, c_t, half_spread_bps),
            eval_sig("[B +5s] CorPrev + Q4 + S5 (Cont)", s_col_q4_s5, p5_t, o_t, c_t, half_spread_bps),
        ]
        print_table(f"{source_label} [{sp_name}] (N={N}, spread_half={half_spread_bps:.2f}bp)",
                    rows, show_sp=(half_spread_bps > 0))


# ==============================================================================
# 3. BENCHMARK AO VIVO (40s): BINANCE PERP & SPOT vs DERIV cryBTCUSD
# ==============================================================================
async def run_live_leadlag(duration_s: int = 40):
    print(f"\n[AO VIVO] Gravando Binance Perp, Binance Spot e Deriv cryBTCUSD por {duration_s}s...")
    t_end = time.monotonic() + duration_s
    recs = {"perp": [], "spot": [], "deriv": []}
    deriv_spreads = []
    spot_spreads = []

    async def listen_bin(uri, key, sp_list=None):
        try:
            async with websockets.connect(uri, ping_interval=None) as ws:
                while time.monotonic() < t_end:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
                    except asyncio.TimeoutError:
                        continue
                    d = json.loads(raw)
                    if "b" in d and "a" in d:
                        b, a = float(d["b"]), float(d["a"])
                        mid = 0.5 * (b + a)
                        recs[key].append((time.monotonic(), mid))
                        if sp_list is not None:
                            sp_list.append((a - b) / mid * 10000.0)
        except Exception as e:
            print(f"[WS {key} err] {e}")

    async def listen_deriv():
        try:
            async with websockets.connect(DERIV_WS, ping_interval=None) as ws:
                await ws.send(json.dumps({"ticks": "cryBTCUSD", "subscribe": 1}))
                while time.monotonic() < t_end:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=3.0)
                    except asyncio.TimeoutError:
                        continue
                    d = json.loads(raw)
                    tk = d.get("tick")
                    if tk and "quote" in tk:
                        q = float(tk["quote"])
                        recs["deriv"].append((time.monotonic(), q))
                        if "bid" in tk and "ask" in tk:
                            deriv_spreads.append((float(tk["ask"]) - float(tk["bid"])) / q * 10000.0)
        except Exception as e:
            print(f"[WS deriv err] {e}")

    await asyncio.gather(
        listen_bin(BIN_PERP_WS, "perp"),
        listen_bin(BIN_SPOT_WS, "spot", spot_spreads),
        listen_deriv(),
    )

    print(f"Ticks recebidos em {duration_s}s: Perp={len(recs['perp'])} ({len(recs['perp'])/duration_s:.1f}/s) | "
          f"Spot={len(recs['spot'])} ({len(recs['spot'])/duration_s:.1f}/s) | "
          f"Deriv={len(recs['deriv'])} ({len(recs['deriv'])/duration_s:.2f}/s)")
    avg_sp_der = float(np.mean(deriv_spreads)) if deriv_spreads else 0.0
    if deriv_spreads and spot_spreads:
        print(f"Spread medio (ask-bid): Deriv cryBTCUSD = {avg_sp_der:.2f} bps | "
              f"Binance Spot = {np.mean(spot_spreads):.4f} bps")

    if len(recs["deriv"]) < 15 or len(recs["spot"]) < 50:
        print("Poucos ticks ao vivo para cross-correlacao.")
        return avg_sp_der

    t0 = max(recs["perp"][0][0], recs["spot"][0][0], recs["deriv"][0][0]) + 1.0
    t1 = min(recs["perp"][-1][0], recs["spot"][-1][0], recs["deriv"][-1][0]) - 0.5
    grid = np.arange(t0, t1, 0.1)  # 100ms

    def resample_step(arr):
        ts = np.array([x[0] for x in arr])
        vs = np.array([x[1] for x in arr])
        idx = np.searchsorted(ts, grid, side="right") - 1
        idx = np.clip(idx, 0, len(vs) - 1)
        return vs[idx]

    p_perp = resample_step(recs["perp"])
    p_spot = resample_step(recs["spot"])
    p_der = resample_step(recs["deriv"])

    diff_usd = p_der - p_spot
    print(f"Diferenca preco (Deriv - Binance Spot): media={diff_usd.mean():+.2f} USD | std={diff_usd.std():.2f} USD")

    # Autocorrelação interna 1s da propria Deriv vs Binance (detectar suavizacao!)
    w = 10  # 1.0s
    r_perp_1s = (p_perp[w:] - p_perp[:-w]) / p_perp[:-w]
    r_spot_1s = (p_spot[w:] - p_spot[:-w]) / p_spot[:-w]
    r_der_1s = (p_der[w:] - p_der[:-w]) / p_der[:-w]

    ac_spot_1s = np.corrcoef(r_spot_1s[:-10], r_spot_1s[10:])[0, 1]
    ac_der_1s = np.corrcoef(r_der_1s[:-10], r_der_1s[10:])[0, 1]
    print(f"Autocorrelacao lag-1s (detecta suavizacao): Binance Spot={ac_spot_1s:+.3f} | Deriv cryBTCUSD={ac_der_1s:+.3f}")

    print("\n--- CROSS-CORRELACAO LEAD-LAG (lag > 0 => BINANCE lidera DERIV em +lag ms) ---")
    best_p = (-99, 0)
    best_s = (-99, 0)
    for lag_steps in range(-20, 51, 2):  # -2000ms a +5000ms
        lag_ms = lag_steps * 100
        if lag_steps > 0:
            cp = np.corrcoef(r_perp_1s[:-lag_steps], r_der_1s[lag_steps:])[0, 1]
            cs = np.corrcoef(r_spot_1s[:-lag_steps], r_der_1s[lag_steps:])[0, 1]
        elif lag_steps < 0:
            k = -lag_steps
            cp = np.corrcoef(r_perp_1s[k:], r_der_1s[:-k])[0, 1]
            cs = np.corrcoef(r_spot_1s[k:], r_der_1s[:-k])[0, 1]
        else:
            cp = np.corrcoef(r_perp_1s, r_der_1s)[0, 1]
            cs = np.corrcoef(r_spot_1s, r_der_1s)[0, 1]
        if cp > best_p[0]:
            best_p = (cp, lag_ms)
        if cs > best_s[0]:
            best_s = (cs, lag_ms)
        if lag_ms in [-1000, 0, 200, 400, 600, 800, 1000, 1400, 2000, 3000, 4000]:
            print(f"  lag={lag_ms:+5d}ms | r(Perp -> Deriv)={cp:+.3f} | r(Spot -> Deriv)={cs:+.3f}")
    print(f"PICO LEAD-LAG: Perp -> Deriv: r={best_p[0]:+.3f} em lag={best_p[1]:+d}ms | "
          f"Spot -> Deriv: r={best_s[0]:+.3f} em lag={best_s[1]:+d}ms")
    return avg_sp_der


async def main():
    # Primeiro mede spread/lag ao vivo para ja usar o spread real da Deriv na tabela!
    avg_sp_der = await run_live_leadlag(duration_s=40)
    half_sp = avg_sp_der / 2.0

    print("\nBaixando ~180.000 ticks reais da Deriv cryBTCUSD (~50 horas)...")
    df_dticks = await fetch_deriv_ticks(max_pages=180)
    t0 = pd.to_datetime(df_dticks["epoch"].min(), unit="s", utc=True)
    t1 = pd.to_datetime(df_dticks["epoch"].max(), unit="s", utc=True)
    print(f"Deriv Ticks coletados: {len(df_dticks):,} ticks ({t0} -> {t1})")

    out_p = os.path.join(ROOT, "audit_data", "DERIV_cryBTCUSD_ticks.parquet")
    df_dticks.to_parquet(out_p, index=False)

    # Auditoria DEV vs HOLDOUT na DERIV real (com e sem half-spread)
    m1_deriv = build_15s_5s_features(df_dticks, "price", "epoch", is_epoch=True)
    run_split_audit(m1_deriv, "DERIV cryBTCUSD REAL (50h)", half_spread_bps=half_sp)

    # Auditoria DEV vs HOLDOUT na BINANCE 1s real (48h)
    df_bin1s = pd.read_parquet(DATA_1S_BIN)
    m1_bin = build_15s_5s_features(df_bin1s, "close", "open_time", is_epoch=False)
    run_split_audit(m1_bin, "BINANCE Spot 1s REAL (48h)", half_spread_bps=0.0)


if __name__ == "__main__":
    asyncio.run(main())
