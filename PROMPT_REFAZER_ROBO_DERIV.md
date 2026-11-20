# PROMPT MESTRE — Refatoração e Criação do Novo Robô Deriv

Copie e cole todo o conteúdo abaixo no início do novo chat:

---

```markdown
Olá! Estou iniciando este chat para **refazer completamente o meu Robô de Operações da Deriv** no projeto localizado em:
📂 `d:\backup importantes\ficar-rico\btc-proxima-vela\robo-deriv`

---

### 1. CONTEXTO DA ARQUITETURA ATUAL DO SISTEMA

O projeto roda inteiramente em **Docker Compose** unificado na porta **8095**:
- **Frontend**: React + Vite + Tailwind CSS + Lightweight-Charts (porta 8095 via Nginx).
- **Backend API**: FastAPI (Python 3.12, porta interna 8000) gerenciando autenticação JWT, configurações do bot (`/api/bot`), WebSocket e endpoints.
- **Worker**: Processo em background (`worker/main.py`) com loop assíncrono conectado via WebSocket na API da Deriv (`wss://ws.derivws.com/websockets/v3?app_id=...`).
- **Banco de Dados**: PostgreSQL (armazenando usuários, contas Deriv cadastradas, tokens e histórico de trades).
- **Cache / Mensageria**: Redis (troca de mensagens de controle, snapshots de estado e pub/sub entre Backend e Worker).
- **Módulo Paralelo**: Na rota `/btc-radar`, existe o Radar BTC M1 (análise de fluxo e microestrutura Spot/Perp) que roda de forma independente e deve ser preservado.

---

### 2. POR QUE O ROBÔ ANTERIOR FALHOU (DIAGNÓSTICO TÉCNICO)

Estávamos rodando um motor chamado `syntra_tick_engine.py` (estratégia de 5 ticks em Synthetic Indices) e os resultados reais foram:
1. **Resultado Negativo & Matemática Desfavorável**:
   - Total de 661 trades realizados: **323 Vitórias (W) e 338 Derrotas (L)**.
   - Assertividade: **48.9% Winrate** (Profit Factor: 0.88 | Prejuízo na sessão: -$31.75 USD).
   - Com payout de ~92% (retorno de $0.55 numa stake de $0.66), o ponto de equilíbrio matemático (breakeven) mínimo é **52.1%**. Operar a 48.9% causa perda gradual contínua e irreversível por expectativa matemática negativa.
2. **Erro de Duração em Pares Inválidos**:
   - O robô tentava disparar ordens de 5 ticks em pares Forex (`frxUSDJPY`) e Cripto (`cryBTCUSD`), gerando rejeições contínuas da API: `[TradingDurationNotAllowed] Trading is not offered for this duration`. Contratos de ticks só são suportados em Índices Sintéticos (Volatility/Jump/Crash/Boom).
3. **Bug ao Trocar de Par**:
   - Ao clicar em outro ativo no seletor (por exemplo, "Bull Market Index"), os dados de lucro e estatísticas na tela zeravam, porque o backend associa o snapshot por símbolo individual e não unifica o extrato da sessão global da conta.

---

### 3. ARQUIVOS PRINCIPAIS DO ROBÔ ATUAL

- `worker/main.py` → Ponto de entrada do worker que roda os motores.
- `worker/engine/user_session.py` → Gerencia a sessão do usuário, controle de saldo, meta de lucro e stop loss.
- `worker/engine/deriv_client.py` → Conexão WebSocket com a Deriv, autorização de token, subscrição de ticks e envio de ordens (`buy` proposal).
- `worker/engine/syntra_tick_engine.py` → Motor de ticks antigo (será substituído ou reescrito).
- `worker/engine/risk_guard.py` → Travas de proteção de risco e limites de perda.
- `backend/app/api/bot.py` → Endpoints FastAPI (`/api/bot/config`, `/api/bot/toggle`, `/api/bot/reset-session`).
- `frontend/src/pages/Dashboard.tsx` → Painel principal com saldo, KPIs, gráfico e feed de eventos.

---

### 4. O QUE VAMOS FAZER AGORA

Quero **reformular e refazer a estratégia operacional desse robô**.
Por favor, confirme que você leu e entendeu toda a arquitetura acima, o estado dos arquivos e os problemas identificados. 
Assim que você confirmar, vou explicar em detalhes a nova estratégia e as novas regras operacionais que quero que você implemente!
```
