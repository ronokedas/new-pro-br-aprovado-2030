# Estudo causal — M15 comprador + M1 vendedor e retomada compradora

Dados: Binance USD-M BTCUSDT M1; 158403 candles; 2026-07-30 20:20:00+00:00 a 2026-11-17 20:22:00+00:00; gaps=0.
Split temporal: 60% desenvolvimento, 20% validação, 20% teste OOS.
M15 calculado apenas com barras completas e fechadas; não usa o candle M15 em formação.
M1 pullback: >=2 dos 3 candles fechados anteriores abaixo da EMA9, EMA9 abaixo da EMA21 e inclinada para baixo.
Reclaim: candle verde fecha acima da EMA9, EMA9 vira para cima e fechamento fica no terço superior (close position >=0,60). Entrada na abertura do M1 seguinte.
Stop: mínima dos cinco candles M1 até o sinal menos 0,10 ATR14; rejeita risco menor que 0,15 ATR ou maior que 2 ATR. Alvo fixo 1,2R.
Horizontes testados: 5, 10 e 15 candles; stop vence se stop e alvo forem tocados na mesma barra.
Ignora taxas, spread, slippage e funding. OHLC M1 não permite saber a ordem intrabar dos toques; por isso stop-first é conservador.
Controle principal: M15 comprador + M1 comprador sem exigir que M1 tenha estado vendedor. Segundo controle: M1 reclaim sem filtro M15.
O teste avalia preço e risco/retorno, não assertividade da cor da vela isolada.

## Resultados OOS (ver results/summary.csv)
- M15_LONG_M1_RECLAIM9 | hold=5m | n=141 | win=55.32% | IC95=[47.08, 63.28] | target=31.21% | exp=0.1439R | PF=1.4286 | DD=4.438R
- M15_LONG_M1_RECLAIM9 | hold=10m | n=131 | win=54.96% | IC95=[46.42, 63.22] | target=44.27% | exp=0.18R | PF=1.4495 | DD=5.142R
- M15_LONG_M1_RECLAIM9 | hold=15m | n=128 | win=54.69% | IC95=[46.05, 63.05] | target=47.66% | exp=0.1761R | PF=1.4078 | DD=5.762R
- M15_LONG_M1_CROSS921 | hold=5m | n=11 | win=36.36% | IC95=[15.17, 64.62] | target=18.18% | exp=-0.3556R | PF=0.412 | DD=4.347R
- M15_LONG_M1_CROSS921 | hold=10m | n=11 | win=36.36% | IC95=[15.17, 64.62] | target=18.18% | exp=-0.3581R | PF=0.4372 | DD=4.602R
- M15_LONG_M1_CROSS921 | hold=15m | n=11 | win=36.36% | IC95=[15.17, 64.62] | target=18.18% | exp=-0.3797R | PF=0.4034 | DD=4.388R
- M15_LONG_RECLAIM9_FLOW | hold=5m | n=32 | win=56.25% | IC95=[39.33, 71.83] | target=34.38% | exp=0.1931R | PF=1.5441 | DD=5.962R
- M15_LONG_RECLAIM9_FLOW | hold=10m | n=32 | win=62.5% | IC95=[45.25, 77.07] | target=43.75% | exp=0.263R | PF=1.7646 | DD=4.593R
- M15_LONG_RECLAIM9_FLOW | hold=15m | n=32 | win=59.38% | IC95=[42.26, 74.48] | target=50.0% | exp=0.2687R | PF=1.6896 | DD=6.27R
- M15_LONG_M1_BULL_NO_PULLBACK | hold=5m | n=566 | win=48.06% | IC95=[43.97, 52.17] | target=30.21% | exp=0.0255R | PF=1.0629 | DD=18.595R
- M15_LONG_M1_BULL_NO_PULLBACK | hold=10m | n=487 | win=45.59% | IC95=[41.21, 50.03] | target=37.78% | exp=-0.0079R | PF=0.9841 | DD=20.718R
- M15_LONG_M1_BULL_NO_PULLBACK | hold=15m | n=457 | win=45.73% | IC95=[41.22, 50.32] | target=41.58% | exp=-0.0018R | PF=0.9965 | DD=19.209R
- NO_M15_M1_RECLAIM9_CONTROL | hold=5m | n=780 | win=49.87% | IC95=[46.37, 53.37] | target=27.05% | exp=0.0421R | PF=1.1091 | DD=16.259R
- NO_M15_M1_RECLAIM9_CONTROL | hold=10m | n=716 | win=48.88% | IC95=[45.24, 52.54] | target=39.66% | exp=0.0542R | PF=1.117 | DD=17.739R
- NO_M15_M1_RECLAIM9_CONTROL | hold=15m | n=692 | win=49.57% | IC95=[45.85, 53.28] | target=44.22% | exp=0.0723R | PF=1.1501 | DD=13.863R