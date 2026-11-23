# M15 compra + M1 venda -> M1 retomando compra
Data da pesquisa: 21/11/2026
Status: candidato para paper trading; nenhuma alteração em produção.

## Resposta à hipótese
Sim, a sequência mostrou uma vantagem histórica preliminar em BTCUSDT: M15 em tendência compradora, recuo vendedor no M1 e candle M1 retomando acima da EMA9. O resultado foi melhor do que comprar simplesmente porque M15 e M1 já estavam verdes.

## Dados e protocolo
- Binance USD-M BTCUSDT, candles M1.
- 158.403 candles de 2026-07-30 20:20 UTC a 2026-11-17 20:22 UTC.
- Nenhum gap M1 após concatenação e deduplicação dos dois arquivos.
- Split cronológico: 60% desenvolvimento, 20% validação, 20% teste OOS.
- M15 foi montado somente com 15 candles M1 completos. O candle M15 em formação nunca foi usado.
- Sinal no fechamento M1; entrada na abertura do M1 seguinte.
- Sem taxas, spread, slippage ou funding. Se stop e alvo foram tocados no mesmo candle, stop contou primeiro.

## Regra candidata — M15_LONG_M1_RECLAIM9
1. M15 comprador: EMA20 acima da EMA50, fechamento M15 acima da EMA20 e EMA20 crescente nas últimas 3 barras M15.
2. Pullback vendedor M1: pelo menos 2 dos 3 candles M1 anteriores fecharam abaixo da EMA9, com EMA9 abaixo da EMA21 e inclinada para baixo.
3. Retomada M1: candle M1 atual fecha verde, acima da EMA9; EMA9 vira para cima; fechamento está nos 40% superiores da amplitude do candle (close position >=0,60).
4. Entrada: open do candle M1 seguinte.
5. Stop: menor mínima dos 5 candles M1 até o sinal menos 0,10 ATR14. Rejeitar risco menor que 0,15 ATR ou maior que 2 ATR.
6. Alvo: 1,2R. Horizonte inicial testado: 5 candles M1. Se não atingir stop nem alvo, fecha ao fim do quinto candle.
7. Uma posição por vez; sem sobreposição.

## Resultado principal — horizonte máximo de 5 minutos
| Período | N | Positivas | Expectativa | PF | Alvo 1,2R | Stop |
|---|---:|---:|---:|---:|---:|---:|
| Desenvolvimento | 665 | 48,87% | +0,0584R | 1,1535 | 28,42% | 28,72% |
| Validação | 176 | 50,00% | +0,0630R | 1,1589 | 31,25% | 32,39% |
| Teste OOS | 141 | 55,32% | +0,1439R | 1,4286 | 31,21% | 27,66% |

Teste OOS: intervalo Wilson 95% da taxa de positivas = 47,08% a 63,28%; drawdown máximo = 4,438R; máximo de perdas consecutivas = 4; duração média = 3,77 minutos. A taxa positiva considera também saídas por tempo com lucro menor que 1,2R; não significa que 55,32% atingiram o alvo. O alvo cheio foi atingido em 31,21%.

## Quanto o preço precisou andar
- Stop médio observado: aproximadamente 0,0842% da entrada (mediana 0,0789%).
- Distância média até o alvo 1,2R: aproximadamente 0,1010% (10,1 bps).
- Em até 5 minutos, 60,28% das operações tiveram excursão favorável de pelo menos 0,5R e 38,30% chegaram a 1R antes do encerramento segundo a excursão OHLC calculada.
- A ordenação intrabar não é conhecida com OHLC de 1 minuto; resultados de PnL usam stop primeiro quando ambos os níveis aparecem na mesma barra.

## Por que a sequência importa — controles
- M15 comprador + M1 simplesmente comprador, sem exigir pullback vendedor antes: OOS N=566, 48,06% positivas, +0,0255R, PF 1,0629, DD 18,595R.
- M1 reclaim9 sem filtro M15: OOS N=780, 49,87% positivas, +0,0421R, PF 1,1091.
- M15 comprador + M1 vendedor antes + reclaim9: OOS N=141, 55,32% positivas, +0,1439R, PF 1,4286.
- Esperar EMA9 cruzar EMA21 no M1 foi tardio e fraco: OOS N=11, 36,36% positivas, PF 0,412. Não usar esse cruzamento como gatilho obrigatório.

## Cautelas
- O OOS abrange aproximadamente 2026-10-27 a 2026-11-17. Na janela parcial iniciada em 2026-11-16 houve 23 sinais, 43,48% positivos, -0,1462R e PF 0,705. São poucos sinais e uma semana incompleta, mas é um alerta de possível mudança de regime.
- O intervalo de confiança de 95% ainda inclui 50%; o teste não prova uma taxa de acerto superior a 50%.
- O resultado é histórico bruto e não considera custos, spread, funding, latência, fila ou slippage.
- O teste usa candles M1 e agressão não participa da regra principal. Não reconstitui book, CVD Spot, absorção ou liquidações históricos.
- A tela `aba_zonas_interesse.html` não calcula esse viés M15/M1. Ela desenha zonas aproximadas de perfil de volume e paredes do livro Spot Binance no timeframe escolhido. O perfil divide volume de candles entre faixas de preço; não é um volume-at-price transacional exato.
- O projeto principal `engine/context.py` implementa contexto M5 e microestrutura de 4 candles de 15 segundos (`micro15`), não M15. `predictor.py` usa essas camadas M5/15s.
- Nada neste estudo enviou ordens ou alterou os módulos de produção.

## Recomendação
Criar uma camada independente de sinal M15/M1 em modo paper. Começar com a regra RECLAIM9, stop estrutural e alvo 1,2R; exibir M15 bias, estado de pullback M1, gatilho, entrada, stop, alvo, distância em %, prazo e motivo de invalidação. Coletar no mínimo 100 operações prospectivas e depois repetir validação por regime antes de considerar uso real.
