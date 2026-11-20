"""
research/study_confluence.py
============================
Estudo OFFLINE e CAUSAL, em dados novos (>= 2026-09-14), das confluencias:
  - Camada M5 (regime)               -> engine.context.m5_context
  - Camada 15s (ultimas 4 velas 15s) -> engine.context.micro15
aplicadas sobre os sinais A (basis z60 Q80) e B (SNR 5s >= 0.50) do sistema.

Metodologia (para nao se enganar):
  * Tudo causal: cada decisao usa apenas velas fechadas antes da abertura do minuto previsto.
  * Metade antiga (H1) e metade recente (H2) reportadas separadas: efeito real precisa aparecer nas DUAS.
  * Intervalo de confianca de Wilson 95% em toda taxa; sem n suficiente, nao conclua nada.
  * Produto B e medido de 2 jeitos: cor da vela (open->close, como o painel antigo) e ENTRADA REAL (preco +5s -> close).

Uso:  python research/study_confluence.py
"""
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from engine.context import m5_context, micro15  # noqa: E402

DATA = ROOT / "audit_data"


def wilson(k: int, n: int, z: float = 1.96):
    if n == 0:
        return 0.0, 0.0
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    a = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (c - a) / d, (c + a) / d


def rep(name: str, win: np.ndarray, mask: np.ndarray, half: np.ndarray):
    """win: bool (acertou). mask: amostras validas. half: 0/1."""
    def one(m):
        n = int(m.sum())
        k = int(win[m].sum())
        if n == 0:
            return "   --   (n=0)"
        lo, hi = wilson(k, n)
        return f"{100*k/n:5.2f}% (n={n:>5}, IC {100*lo:4.1f}-{100*hi:4.1f})"
    print(f"{name:<34} ALL {one(mask)} | H1 {one(mask & (half == 0))} | H2 {one(mask & (half == 1))}", flush=True)


# ----------------------------------------------------------------------------- 1m: A + M5
def study_1m():
    spot = pd.read_parquet(DATA / "fresh_spot_1m.parquet")
    perp = pd.read_parquet(DATA / "fresh_perp_1m.parquet")
    df = spot.merge(perp[["open_time", "close"]], on="open_time", suffixes=("", "_p")).sort_values("open_time").reset_index(drop=True)
    n = len(df)
    print(f"\n=== DADOS 1m: {n} minutos  {pd.to_datetime(df.open_time.iloc[0], unit='ms')} -> {pd.to_datetime(df.open_time.iloc[-1], unit='ms')}")

    ot = df.open_time.values
    o, c = df.open.values, df.close.values
    basis = (df.close_p.values - c) / c
    bs = pd.Series(basis)
    z = ((bs - bs.rolling(60).mean()) / bs.rolling(60).std()).values
    absz = pd.Series(np.abs(z))
    th = absz.shift(1).rolling(440, min_periods=60).quantile(0.80).values

    sigA = np.zeros(n, dtype=int)
    ok = (~np.isnan(z)) & (~np.isnan(th)) & (np.abs(z) >= th)
    sigA[ok] = np.sign(z[ok]).astype(int)

    # resultado do minuto SEGUINTE (i+1)
    nxt_color = np.zeros(n, dtype=int)
    nxt_color[:-1] = np.sign(c[1:] - o[1:]).astype(int)
    contiguous = np.zeros(n, dtype=bool)
    contiguous[:-1] = (ot[1:] - ot[:-1]) == 60_000
    half = (np.arange(n) >= n // 2).astype(int)

    valid = contiguous & (nxt_color != 0)
    win_A = (sigA == nxt_color)
    maskA = valid & (sigA != 0)
    print("\n--- Produto A (basis z60 Q80) -- replicacao em dados novos")
    print(f"cobertura: {100*maskA.sum()/valid.sum():.1f}% dos minutos")
    rep("A todos os sinais", win_A, maskA, half)

    # ---- contexto M5 para minutos-sinal de A + amostra geral (1 em 4)
    recs = df[["open_time", "open", "high", "low", "close", "volume", "taker_buy_base"]].to_dict("records")
    feat = {}
    idx_all = [i for i in range(330, n - 1) if sigA[i] != 0 or i % 4 == 0]
    for i in idx_all:
        feat[i] = m5_context(recs[i - 320:i + 1], int(ot[i + 1]), trend_min_bps=0.0)
    print(f"contexto M5 calculado para {len(feat)} minutos", flush=True)

    ii = np.array(sorted(feat.keys()))
    trend = np.array([feat[i].get("trend_bps", 0.0) if feat[i]["ready"] else np.nan for i in ii])
    pos = np.array([feat[i]["pos_in_m5"] for i in ii])
    vreg = np.array([feat[i].get("vol_regime", "NA") if feat[i]["ready"] else "NA" for i in ii])
    partial = np.array([feat[i].get("partial_ret_bps", 0.0) if feat[i]["ready"] else np.nan for i in ii])
    lastm5 = np.array([feat[i].get("last_m5_ret_bps", 0.0) if feat[i]["ready"] else np.nan for i in ii])
    okf = ~np.isnan(trend)

    nc = nxt_color[ii]
    hv = half[ii]
    vv = valid[ii] & okf
    sa = sigA[ii]

    print("\n--- M5 sozinho: a tendencia M5 prediz a cor do proximo minuto? (continuacao = acerto)")
    for tau in (0.0, 1.0, 2.0, 4.0):
        m = vv & (np.abs(trend) >= tau)
        rep(f"M5 trend segue |trend|>={tau}", (np.sign(trend) == nc), m, hv)
    print("--- mesma ideia com o parcial do M5 atual (so pos>=1) e ultimo M5 fechado")
    rep("parcial M5 segue (pos>=1)", (np.sign(partial) == nc), vv & (pos >= 1) & (np.abs(partial) > 0), hv)
    rep("parcial M5 REVERTE (pos>=3)", (-np.sign(partial) == nc), vv & (pos >= 3) & (np.abs(partial) > 0), hv)
    rep("ultimo M5 fechado segue", (np.sign(lastm5) == nc), vv & (np.abs(lastm5) > 0), hv)

    print("\n--- A condicionado ao regime M5 (veredito = M5 bias vs direcao de A)")
    mA = vv & (sa != 0)
    winA = (sa == nc)
    for tau in (0.5, 1.0, 2.0):
        agree = mA & (np.sign(trend) == sa) & (np.abs(trend) >= tau)
        dis = mA & (np.sign(trend) == -sa) & (np.abs(trend) >= tau)
        neu = mA & (np.abs(trend) < tau)
        rep(f"A | M5 AGREE   (tau={tau})", winA, agree, hv)
        rep(f"A | M5 DISAGREE(tau={tau})", winA, dis, hv)
        rep(f"A | M5 neutro  (tau={tau})", winA, neu, hv)
        rep(f"A | SEM disagree (veto, tau={tau})", winA, mA & ~dis, hv)
    print("--- A por posicao dentro do M5 (0 = primeiro minuto)")
    for p in range(5):
        rep(f"A | pos_in_m5={p}", winA, mA & (pos == p), hv)
    print("--- A por regime de volatilidade")
    for vname in ("BAIXA", "NORMAL", "ALTA"):
        rep(f"A | vol {vname}", winA, mA & (vreg == vname), hv)

    return df, sigA, ot


# ----------------------------------------------------------------------------- 1s: 15s + B
def study_1s(df1m, sigA, ot1m):
    p = DATA / "fresh_spot_1s.parquet"
    if not p.exists():
        print("\n(sem fresh_spot_1s.parquet — pule o estudo de 15s/B)")
        return
    s = pd.read_parquet(p).sort_values("open_time").reset_index(drop=True)
    print(f"\n=== DADOS 1s: {len(s)} linhas  {pd.to_datetime(s.open_time.iloc[0], unit='ms')} -> {pd.to_datetime(s.open_time.iloc[-1], unit='ms')}")

    s["b15"] = (s.open_time // 15_000) * 15_000
    g = s.groupby("b15").agg(open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last"),
                            volume=("volume", "sum"), taker_buy_base=("taker_buy_base", "sum"), cnt=("open", "size")).reset_index()
    g = g[g.cnt == 15]
    d15 = {int(r["b15"]): {"open_time": int(r["b15"]), "open": r["open"], "high": r["high"], "low": r["low"],
                           "close": r["close"], "volume": r["volume"], "taker_buy_base": r["taker_buy_base"]}
           for r in g.to_dict("records")}

    s["m"] = (s.open_time // 60_000) * 60_000
    gm = s.groupby("m").agg(o=("open", "first"), cl=("close", "last"), cnt=("open", "size"))
    gm = gm[gm.cnt == 60]
    px5 = s[s.open_time % 60_000 == 4_000].set_index("m")["close"]
    open0 = s[s.open_time % 60_000 == 0].set_index("m")["open"]

    # sigma M1 causal (std dos 15 ultimos retornos M1 fechados, em fracao; piso 5e-5), por minuto previsto
    c1 = df1m.set_index("open_time")["close"]
    r1 = c1.pct_change()
    sig15 = r1.rolling(15).std().clip(lower=5e-5)
    sig_for_min = sig15.shift(1)  # sigma ate o minuto m-1 -> usado para prever o minuto m
    a_map = dict(zip(ot1m, sigA))
    recs = df1m[["open_time", "open", "high", "low", "close", "volume", "taker_buy_base"]].to_dict("records")
    pos_of = {int(r["open_time"]): i for i, r in enumerate(recs)}

    rows = []
    for m in gm.index:
        m = int(m)
        if m not in px5.index or m not in sig_for_min.index or np.isnan(sig_for_min.loc[m]):
            continue
        quad = [d15.get(m - 60_000 + k * 15_000) for k in range(4)]
        if any(q is None for q in quad):
            continue
        sig = float(sig_for_min.loc[m])
        mic = micro15(quad, sigma_bps=sig * 1e4)
        o0, clf, p5 = float(open0.loc[m]), float(gm.loc[m, "cl"]), float(px5.loc[m])
        ret5_bps = (p5 - o0) / o0 * 1e4
        snr = (ret5_bps / 1e4) / (sig * math.sqrt(55.0 / 60.0))
        i = pos_of.get(m)
        m5 = m5_context(recs[max(0, i - 320):i], m, trend_min_bps=0.0) if i and i > 330 else {"ready": False}
        rows.append({
            "m": m, "color": int(np.sign(clf - o0)), "entry": int(np.sign(clf - p5)),
            "mic_dir": mic["direction"], "mic_score": mic["score"], "mic_ready": mic["ready"],
            "snr": snr, "a": int(a_map.get(m - 60_000, 0)) if False else int(a_map.get(m - 60_000, 0)),
            "trend": m5.get("trend_bps", np.nan) if m5["ready"] else np.nan,
            "pos": m5["pos_in_m5"], "net_flow": mic.get("net_flow", 0.0), "net_ret": mic.get("net_ret_bps", 0.0),
        })
    R = pd.DataFrame(rows)
    if R.empty:
        print("sem linhas validas no estudo 1s")
        return
    half = (np.arange(len(R)) >= len(R) // 2).astype(int)
    # sinal A "do minuto m" foi decidido com z do fim do minuto m-1 -> indice m-60s em sigA
    R["a"] = [int(a_map.get(m - 60_000, 0)) for m in R.m]
    mdir = R.mic_dir.map({"CALL": 1, "PUT": -1, "NEUTRO": 0}).values
    color, entry = R.color.values, R.entry.values
    print(f"minutos validos no estudo 1s: {len(R)}")

    print("\n--- 15s sozinho: direcao do fluxo do MINUTO ANTERIOR (4x15s) vs cor do proximo minuto")
    mm = (mdir != 0) & (color != 0)
    rep("15s segue (continuacao)", (mdir == color), mm, half)
    rep("15s reverte", (-mdir == color), mm, half)
    for lo, hi in ((0.25, 0.45), (0.45, 0.65), (0.65, 9)):
        m = mm & (np.abs(R.mic_score.values) >= lo) & (np.abs(R.mic_score.values) < hi)
        rep(f"15s segue |score| {lo}-{hi}", (mdir == color), m, half)
    nf = np.sign(R.net_flow.values).astype(int)
    rep("so fluxo taker segue", (nf == color), (nf != 0) & (color != 0), half)
    nr = np.sign(R.net_ret.values).astype(int)
    rep("so retorno 4x15s segue", (nr == color), (nr != 0) & (color != 0), half)

    print("\n--- Produto B (|SNR5s|>=0.50): cor da vela x ENTRADA REAL")
    bdir = np.sign(R.snr.values).astype(int)
    bm = (np.abs(R.snr.values) >= 0.5)
    rep("B pela COR (open->close)", (bdir == color), bm & (color != 0), half)
    rep("B pela ENTRADA (+5s->close)", (bdir == entry), bm & (entry != 0), half)
    print("--- B (entrada real) condicionado ao 15s do minuto anterior")
    bm2 = bm & (entry != 0)
    rep("B | 15s AGREE", (bdir == entry), bm2 & (mdir == bdir), half)
    rep("B | 15s DISAGREE", (bdir == entry), bm2 & (mdir == -bdir), half)
    rep("B | 15s neutro", (bdir == entry), bm2 & (mdir == 0), half)
    rep("B | SEM disagree 15s (veto)", (bdir == entry), bm2 & (mdir != -bdir), half)
    print("--- B (entrada real) condicionado ao M5")
    tr = R.trend.values
    for tau in (0.5, 1.0, 2.0):
        ag = bm2 & ~np.isnan(tr) & (np.sign(tr) == bdir) & (np.abs(tr) >= tau)
        di = bm2 & ~np.isnan(tr) & (np.sign(tr) == -bdir) & (np.abs(tr) >= tau)
        rep(f"B | M5 AGREE (tau={tau})", (bdir == entry), ag, half)
        rep(f"B | M5 DISAGREE (tau={tau})", (bdir == entry), di, half)

    print("\n--- A (dos mesmos minutos 1s) condicionado ao 15s")
    ad = R.a.values
    am = (ad != 0) & (color != 0)
    rep("A todos", (ad == color), am, half)
    rep("A | 15s AGREE", (ad == color), am & (mdir == ad), half)
    rep("A | 15s DISAGREE", (ad == color), am & (mdir == -ad), half)
    rep("A | 15s neutro", (ad == color), am & (mdir == 0), half)

    print("\n--- Quando A e B concordam (mesmo minuto): so ha B se A estava WAIT no sistema real; aqui so referencia")
    both = am & bm & (bdir == ad)
    rep("A==B (cor)", (ad == color), both, half)


if __name__ == "__main__":
    df1, sigA, ot = study_1m()
    study_1s(df1, sigA, ot)
    print("\nFIM", flush=True)
