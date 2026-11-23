"""Causal BTCUSDT M15 trend + M1 pullback/realignment study. Research only."""
from __future__ import annotations
import csv, json, math, datetime
from pathlib import Path
from statistics import mean, median
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "audit_data"
OUT = Path(__file__).resolve().parent / "results"
OUT.mkdir(exist_ok=True)
PERP_FILES = ["BTCUSDT_perp_1m_virgin45d.parquet", "BTCUSDT_perp_1m_65d.parquet"]
TARGET_R = 1.2
HORIZONS = (5, 10, 15)
SPLIT_FRACS = (0.60, 0.80)

def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()

def wilson(wins: int, n: int):
    if n <= 0: return None
    z=1.959963984540054; p=wins/n; den=1+z*z/n
    center=(p+z*z/(2*n))/den
    margin=z*math.sqrt((p*(1-p)/n)+(z*z/(4*n*n)))/den
    return [round(max(0,center-margin)*100,2), round(min(1,center+margin)*100,2)]

def load_data():
    frames=[]
    for name in PERP_FILES:
        p=DATA/name
        if not p.exists(): raise FileNotFoundError(p)
        d=pd.read_parquet(p)
        frames.append(d)
    d=pd.concat(frames,ignore_index=True).sort_values("open_time")
    d=d.drop_duplicates("open_time",keep="last").reset_index(drop=True)
    required=["open_time","open","high","low","close","volume","quote_asset_volume","taker_buy_quote"]
    d=d[required].copy()
    for col in required[1:]:
        d[col]=pd.to_numeric(d[col],errors="coerce")
    d=d.dropna().reset_index(drop=True)
    gaps=int((d.open_time.diff().dt.total_seconds().dropna()!=60).sum())
    return d,gaps

def make_signals(d):
    # M15 bars are built from complete groups of 15 finalized M1 candles only.
    tmp=d[["open_time","open","high","low","close","volume"]].copy()
    tmp["bucket"]=tmp.open_time.dt.floor("15min")
    g=tmp.groupby("bucket",sort=True).agg(
        o=("open","first"), h=("high","max"), l=("low","min"), c=("close","last"),
        count=("open","size"), first=("open_time","min"), last=("open_time","max")
    ).reset_index()
    g=g[(g["count"]==15) & ((g["last"]-g["first"]).dt.total_seconds()==14*60)].copy()
    g=g.sort_values("bucket").reset_index(drop=True)
    g["ema20"]=ema(g.c,20); g["ema50"]=ema(g.c,50)
    g["long_bias"]=(g.c>g.ema20)&(g.ema20>g.ema50)&(g.ema20>g.ema20.shift(3))
    g["short_bias"]=(g.c<g.ema20)&(g.ema20<g.ema50)&(g.ema20<g.ema20.shift(3))
    g["end_ms"]=g.bucket.map(lambda x:int(x.timestamp()*1000)+15*60_000).to_numpy(dtype=np.int64)

    # At M1 close, use only the latest M15 bar whose close time has already passed.
    close_ms=d.open_time.map(lambda x:int(x.timestamp()*1000)).to_numpy(dtype=np.int64)+60_000
    end_ms=g.end_ms.to_numpy(dtype=np.int64)
    htf_idx=np.searchsorted(end_ms,close_ms,side="right")-1
    valid_htf=htf_idx>=0
    htf_long=np.zeros(len(d),dtype=bool); htf_short=np.zeros(len(d),dtype=bool)
    htf_long[valid_htf]=g.long_bias.to_numpy()[htf_idx[valid_htf]]
    htf_short[valid_htf]=g.short_bias.to_numpy()[htf_idx[valid_htf]]

    # M1 timing / pullback state.
    c=d.close; o=d.open; h=d.high; l=d.low
    e9=ema(c,9); e21=ema(c,21)
    tr=pd.concat([(h-l),(h-c.shift(1)).abs(),(l-c.shift(1)).abs()],axis=1).max(axis=1)
    atr=tr.ewm(alpha=1/14,adjust=False,min_periods=14).mean()
    span=(h-l).replace(0,np.nan)
    close_pos=((c-l)/span).fillna(0.5)

    # M1 is in a bearish pullback when price is below EMA9, EMA9 below EMA21,
    # and EMA9 is falling. Require at least two of the previous three closed candles.
    m1_bear=(c<e9)&(e9<e21)&(e9<e9.shift(1))
    prior_bear=(m1_bear.shift(1).rolling(3,min_periods=3).sum()>=2).fillna(False).to_numpy()
    green=(c>o).to_numpy()
    reclaim9=(prior_bear & green & (c.to_numpy()>e9.to_numpy())
              & (e9.to_numpy()>e9.shift(1).to_numpy())
              & (close_pos.to_numpy()>=0.60))
    cross921=(prior_bear & green & (e9.shift(1).to_numpy()<=e21.shift(1).to_numpy())
              & (e9.to_numpy()>e21.to_numpy())
              & (c.to_numpy()>e9.to_numpy()) & (close_pos.to_numpy()>=0.55))

    # Flow/volume are calculated only from data available at the signal candle close.
    quote=d.quote_asset_volume
    buyq=d.taker_buy_quote
    base=quote.shift(1).rolling(20,min_periods=20).median()
    vol_ratio=(quote/base.replace(0,np.nan)).to_numpy()
    total3=quote.rolling(3,min_periods=3).sum().to_numpy()
    delta3=(2*buyq-quote).rolling(3,min_periods=3).sum().to_numpy()
    flow3=np.divide(delta3,total3,out=np.zeros(len(d),dtype=float),where=total3>0)
    flow_filter=(vol_ratio>=1.0)&(flow3>=0.05)

    m15long=htf_long
    signals={
      "M15_LONG_M1_RECLAIM9": m15long & reclaim9,
      "M15_LONG_M1_CROSS921": m15long & cross921,
      "M15_LONG_RECLAIM9_FLOW": m15long & reclaim9 & flow_filter,
      "M15_LONG_M1_BULL_NO_PULLBACK": m15long & green & (c.to_numpy()>e9.to_numpy()) & (e9.to_numpy()>e9.shift(1).to_numpy()) & (close_pos.to_numpy()>=0.60),
      "NO_M15_M1_RECLAIM9_CONTROL": reclaim9,
    }
    ctx={
      "t":d.open_time.map(lambda x:int(x.timestamp()*1000)).to_numpy(dtype=np.int64),
      "o":o.to_numpy(dtype=float),"h":h.to_numpy(dtype=float),"l":l.to_numpy(dtype=float),
      "c":c.to_numpy(dtype=float),"atr":atr.to_numpy(dtype=float),
      "e9":e9.to_numpy(dtype=float),"m15long":m15long,
      "vol_ratio":vol_ratio,"flow3":flow3,
    }
    return ctx,signals,g

def run_split(ctx, signal, target_r, hold, start, end):
    n=len(ctx["t"]); rows=[]; i=max(60,start); last_exit=start-1
    while i < min(end-1,n-2):
        if i<=last_exit or not bool(signal[i]):
            i+=1; continue
        at=ctx["atr"][i]
        if not np.isfinite(at) or at<=0:
            i+=1; continue
        entry=ctx["o"][i+1]
        stop=min(ctx["l"][i-4:i+1])-0.10*at
        risk=entry-stop
        if risk<=0 or risk<0.15*at or risk>2.0*at or entry<=stop:
            i+=1; continue
        target=entry+target_r*risk
        final=min(i+hold,end-1,n-1)
        if final<=i:
            i+=1; continue
        outcome="TIME"; r_value=(ctx["c"][final]-entry)/risk; exit_i=final
        mfe=0.0; mae=0.0
        for j in range(i+1,final+1):
            favorable=(ctx["h"][j]-entry)/risk
            adverse=(entry-ctx["l"][j])/risk
            mfe=max(mfe,favorable); mae=max(mae,adverse)
            hit_stop=ctx["l"][j]<=stop
            hit_target=ctx["h"][j]>=target
            # Conservative intrabar convention: if both touched, stop is counted first.
            if hit_stop:
                outcome="STOP"; r_value=-1.0; exit_i=j; break
            if hit_target:
                outcome="TARGET"; r_value=target_r; exit_i=j; break
        rows.append({
          "signal_i":i,"exit_i":exit_i,"signal_ts":int(ctx["t"][i]),
          "entry_ts":int(ctx["t"][i+1]),"exit_ts":int(ctx["t"][exit_i]),
          "entry":float(entry),"stop":float(stop),"target":float(target),
          "risk_pct":float(risk/entry*100),"r":float(r_value),"outcome":outcome,
          "bars_held":int(exit_i-i),"mfe_r":float(mfe),"mae_r":float(mae),
          "flow3":float(ctx["flow3"][i]) if np.isfinite(ctx["flow3"][i]) else 0.0,
          "volume_ratio":float(ctx["vol_ratio"][i]) if np.isfinite(ctx["vol_ratio"][i]) else 0.0,
        })
        last_exit=exit_i
        i=exit_i+1
    return rows

def metrics(rows):
    if not rows:return {"trades":0}
    r=[x["r"] for x in rows]; win=[x for x in r if x>0]; loss=[x for x in r if x<=0]
    gp=sum(win); gl=abs(sum(loss)); eq=peak=dd=0.0; streak=maxstreak=0
    for v in r:
        eq+=v; peak=max(peak,eq); dd=max(dd,peak-eq)
        streak=streak+1 if v<=0 else 0; maxstreak=max(maxstreak,streak)
    return {
      "trades":len(rows),"wins":len(win),"win_rate_pct":round(100*len(win)/len(rows),2),
      "win_rate_wilson95":wilson(len(win),len(rows)),
      "target_hit_pct":round(100*sum(x["outcome"]=="TARGET" for x in rows)/len(rows),2),
      "stop_pct":round(100*sum(x["outcome"]=="STOP" for x in rows)/len(rows),2),
      "expectancy_r":round(mean(r),4),
      "profit_factor":round(gp/gl,4) if gl>0 else None,
      "avg_win_r":round(mean(win),4) if win else None,"avg_loss_r":round(mean(loss),4) if loss else None,
      "max_drawdown_r":round(dd,3),"max_consecutive_losses":maxstreak,
      "avg_bars_held":round(mean(x["bars_held"] for x in rows),2),
      "avg_mfe_r":round(mean(x["mfe_r"] for x in rows),3),
      "avg_mae_r":round(mean(x["mae_r"] for x in rows),3),
      "pct_mfe_0_5R":round(100*sum(x["mfe_r"]>=0.5 for x in rows)/len(rows),2),
      "pct_mfe_1R":round(100*sum(x["mfe_r"]>=1.0 for x in rows)/len(rows),2),
    }

def dt(ms):
    return datetime.datetime.fromtimestamp(ms/1000,datetime.timezone.utc)

def main():
    d,gaps=load_data()
    ctx,signals,m15=make_signals(d)
    n=len(d); dev=int(n*0.60); val=int(n*0.80)
    splits={"DEV":(0,dev),"VAL":(dev,val),"OOS":(val,n)}
    print(f"DATA rows={n} from={d.open_time.iloc[0]} to={d.open_time.iloc[-1]} M15_complete={len(m15)} gaps={gaps}",flush=True)
    print("Regra M15: apenas candle M15 fechado; EMA20>EMA50, close M15>EMA20 e EMA20 crescente em 3 barras.",flush=True)
    print("Regra M1 pullback: ao menos 2 dos 3 M1 anteriores com close<EMA9, EMA9<EMA21 e EMA9 caindo.",flush=True)
    print("Gatilho: candle M1 atual fecha acima da EMA9, candle verde, EMA9 vira para cima e close-pos>=60%; entrada no open seguinte.",flush=True)
    print("Stop: menor mínima dos 5 M1 candles até o sinal menos 0,10 ATR14; alvo 1,2R; stop prioritário no mesmo OHLC.",flush=True)
    summaries=[]; all_trades=[]
    for setup,signal in signals.items():
      for hold in HORIZONS:
        for split,(lo,hi) in splits.items():
          rows=run_split(ctx,signal,TARGET_R,hold,lo,hi)
          m=metrics(rows)
          row={"setup":setup,"target_r":TARGET_R,"hold_minutes":hold,"split":split,**m}
          summaries.append(row)
          print(f"{setup:34s} hold={hold:2d} {split:3s} n={m.get('trades',0):5d} win={m.get('win_rate_pct')} target={m.get('target_hit_pct')} exp={m.get('expectancy_r')} PF={m.get('profit_factor')} DD={m.get('max_drawdown_r')}R",flush=True)
          for x in rows: all_trades.append({"setup":setup,"hold_minutes":hold,"split":split,**x})
          if split=="OOS" and rows:
            for week in sorted({(dt(x["signal_ts"])-datetime.timedelta(days=dt(x["signal_ts"]).weekday())).strftime("%Y-%m-%d") for x in rows}):
              subset=[x for x in rows if (dt(x["signal_ts"])-datetime.timedelta(days=dt(x["signal_ts"]).weekday())).strftime("%Y-%m-%d")==week]
              z=metrics(subset)
              print(f"  week {week}: n={z['trades']} win={z['win_rate_pct']} exp={z['expectancy_r']} PF={z['profit_factor']}",flush=True)
    with (OUT/"summary.csv").open("w",newline="",encoding="utf-8-sig") as f:
      fields=sorted({k for row in summaries for k in row}); w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(summaries)
    if all_trades:
      with (OUT/"trades.csv").open("w",newline="",encoding="utf-8-sig") as f:
        w=csv.DictWriter(f,fieldnames=list(all_trades[0].keys())); w.writeheader(); w.writerows(all_trades)
    report=[
      "# Estudo causal — M15 comprador + M1 vendedor e retomada compradora",
      "",
      f"Dados: Binance USD-M BTCUSDT M1; {n} candles; {d.open_time.iloc[0]} a {d.open_time.iloc[-1]}; gaps={gaps}.",
      "Split temporal: 60% desenvolvimento, 20% validação, 20% teste OOS.",
      "M15 calculado apenas com barras completas e fechadas; não usa o candle M15 em formação.",
      "M1 pullback: >=2 dos 3 candles fechados anteriores abaixo da EMA9, EMA9 abaixo da EMA21 e inclinada para baixo.",
      "Reclaim: candle verde fecha acima da EMA9, EMA9 vira para cima e fechamento fica no terço superior (close position >=0,60). Entrada na abertura do M1 seguinte.",
      "Stop: mínima dos cinco candles M1 até o sinal menos 0,10 ATR14; rejeita risco menor que 0,15 ATR ou maior que 2 ATR. Alvo fixo 1,2R.",
      "Horizontes testados: 5, 10 e 15 candles; stop vence se stop e alvo forem tocados na mesma barra.",
      "Ignora taxas, spread, slippage e funding. OHLC M1 não permite saber a ordem intrabar dos toques; por isso stop-first é conservador.",
      "Controle principal: M15 comprador + M1 comprador sem exigir que M1 tenha estado vendedor. Segundo controle: M1 reclaim sem filtro M15.",
      "O teste avalia preço e risco/retorno, não assertividade da cor da vela isolada.",
      "",
      "## Resultados OOS (ver results/summary.csv)",
    ]
    for x in summaries:
      if x["split"]=="OOS":
        report.append(f"- {x['setup']} | hold={x['hold_minutes']}m | n={x.get('trades',0)} | win={x.get('win_rate_pct')}% | IC95={x.get('win_rate_wilson95')} | target={x.get('target_hit_pct')}% | exp={x.get('expectancy_r')}R | PF={x.get('profit_factor')} | DD={x.get('max_drawdown_r')}R")
    (OUT/"REPORT.md").write_text("\n".join(report),encoding="utf-8")
    print("DONE "+str(OUT),flush=True)

if __name__=="__main__":
    main()
