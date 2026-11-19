# ⚡ Radar de Antecipação M1 | IA Scalp (Clone & Evolução)

Sistema de alta frequência para previsão da próxima vela M1 do BTCUSDT.

---

## 🎯 Funcionalidades Identificadas no Vídeo e Implementadas

- **Painel Dark Mode Ultra-Compacto**: Projetado para operar lado a lado com a corretora (Pocket Option, Quotex, Binance, etc.).
- **Cronômetro Regressivo M1 (`⏱ 58`, `⏱ 14`, etc.)**: Sincronizado tick-a-tick com o fechamento exato da vela.
- **Card de Antecipação Lead-Lag**:
  - `🚨 ANTECIPAÇÃO COMPRA / VENDA`
  - Identificação de liderança de pares (ex: `XRPUSDT → BTCUSDT 677MS`) baseada em disparidade de momentum e latência.
- **Seta de Projeção Neon (`⬆` Verde / `⬇` Vermelha)**: Exibe a cor prevista para a vela atual / próxima.
- **Gráfico de Candlestick das 5 Velas**: Visualização limpa com pavios de alta definição e tamanho dinâmico.
- **Toggles de Confluência em Tempo Real**:
  - `[EMA ON/OFF]` (9 vs 21 e 50 vs 200)
  - `[CCI ON/OFF]` (Sobrecompra / Sobrevenda no período 20)
  - `[SR ON/OFF]` (Suporte e Resistência dinâmicos de Swings)
  - `[Próx. ON/OFF]` (Ligar/desligar projeção da próxima vela)
- **Modo IA**: Pipeline treinado com `HistGradientBoostingClassifier` em 93.000+ velas de 1m reais da Binance.
- **Alertas Sonoros Sintetizados**: Sons de compra e venda via Web Audio API do navegador (sem delay de arquivos externos).

---

## 🚀 Como Rodar

### Opção 1: Via Docker (Recomendado se quiser isolado)
Certifique-se de que o Docker Desktop está aberto e rode:
```bash
cd c:\trading-agents\btc_realtime
docker compose up --build
```
Acesse no navegador: **`http://localhost:8080`**

### Opção 2: Direto pelo Windows (Instantâneo)
```bash
cd c:\trading-agents\btc_realtime
pip install -r requirements.txt
python -m uvicorn api.server:app --host 0.0.0.0 --port 8080
```
Ou dê dois cliques no arquivo **`start.bat`**.

---

## 🏗️ Estrutura do Projeto

```
btc_realtime/
├── api/
│   └── server.py              # FastAPI + WebSockets Hub + Servidor Estático
├── data_feed/
│   ├── binance_ws.py          # WebSocket multi-ativo Binance (BTC, XRP, ETH, SOL)
│   └── lead_lag_detector.py   # Detector de discrepâncias temporais e impulsos
├── engine/
│   ├── indicators.py          # EMA, CCI, Suporte/Resistência, RSI, CVD
│   ├── pattern_matcher.py     # Padrões de sequência de cores (3 a 5 velas)
│   └── predictor.py           # Confluência IA + Heurísticas
├── dashboard/
│   ├── index.html             # Interface idêntica à do vídeo
│   ├── style.css              # Tema escuro neon
│   └── app.js                 # WebSocket client, render Canvas e Web Audio
├── models/
│   └── gboost_latest.joblib   # Modelo treinado
├── trainer/
│   └── train.py               # Script para retreinar o modelo
├── Dockerfile
├── docker-compose.yml
└── start.bat
```
