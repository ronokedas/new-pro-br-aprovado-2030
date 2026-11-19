# PROMPT MESTRE — Plataforma Web Multi-Usuário (Docker) de Robô Quantitativo de Canal + Preço Médio Controlado na Deriv

> **Como usar este arquivo:** Copie e cole este prompt completo em um agente de engenharia de software/IA para projetar, validar em backtest realista e construir toda a plataforma web multi-usuário rodando em **Docker**, conectada 100% à **API WebSocket Oficial da Deriv** (sem MT5).

---

## 1. Papel e Visão Geral do Sistema

Atue como um **Arquiteto Full-Stack Sênior e Engenheiro Quantitativo de Sistemas de Trading em Python (`asyncio`) + React/TypeScript + Docker**.

Seu objetivo é projetar, validar cientificamente e construir uma **plataforma web moderna, segura e multi-usuário instalada via Docker (`docker-compose`)**, conectada diretamente à **API WebSocket oficial da Deriv** (sem MetaTrader / sem MT5), com os seguintes pilares:

1. **Multi-Usuário com Painel Admin e Controle de Validade:**
   - Existe um perfil **Administrador (`ADMIN`)** que cadastra, edita, renova a **data de validade (`expires_at`)**, ativa ou bloqueia outros usuários (`USER`).
2. **Conexão Individual com a Deriv (Cada Usuário na Sua Própria Conta):**
   - Cada usuário faz login na própria conta pessoal da Deriv dentro do painel via **OAuth Oficial da Deriv** (ou fallback de API Token pessoal), visualizando suas próprias contas **Demo (`VRTC...`)** e **Real (`CR...`)**, alternando entre elas com segurança.
3. **Configuração Individual & Listagem Dinâmica de Ativos/Pares:**
   - O sistema lista dinamicamente os **pares e ativos disponíveis na Deriv** (`active_symbols` + `contracts_for` com suporte a `MULTUP`/`MULTDOWN`: Cripto como `cryBTCUSD`/`cryETHUSD`, Forex como `frxEURUSD`/`frxGBPUSD`, Índices Sintéticos como `R_75`/`1HZ100V`, etc.), exibindo status (aberto/fechado), stake mínimo real e multiplicadores permitidos.
   - Cada usuário configura seu próprio **valor de entrada (stake)**, **conta (Demo ou Real)**, **par(es)**, **limites de risco** e liga/desliga seu robô com um botão **`ON / OFF`** (além de um **Kill Switch** de emergência).
4. **Estratégia Quantitativa de Canal + Confirmação + Preço Médio Controlado (Smart DCA):**
   - Entra na **linha inferior de um canal de alta** (e opcionalmente na linha superior de um canal de baixa) **somente após confirmação causal de sustentação/rejeição** por indicadores (nunca comprar "faca caindo").
   - Se o preço continuar recuando contra a entrada, realiza **reentradas estratégicas controladas** (preço médio com teto rígido de camadas e proteção de capital) e, assim que o **PnL líquido somado do cesto** (já descontadas comissões e spread) atingir a meta de lucro, **encerra todas as posições do cesto simultaneamente** e reinicia o ciclo.
5. **Interface Moderna + Relatórios Profissionais:**
   - Dashboard dark-mode elegante em tempo real com gráfico do canal + marcação das entradas/preço médio, assertividade por ciclo, lucros, Profit Factor, curva de patrimônio (*Equity Curve*), histórico detalhado e exportação CSV.

---

## 2. Integração Oficial da Deriv (OAuth + WebSocket API — ZERO INVENÇÃO)

Respeite estritamente a documentação e os limites reais da **Deriv API** (`developers.deriv.com`):

### 2.1. Login Individual via OAuth Oficial da Deriv
- **Registro do App:** o administrador registra um `DERIV_APP_ID` no Deriv API Dashboard (`Dashboard → Applications → Application manager`) apontando a `redirect_uri` para o domínio da plataforma (ex.: `https://seu-dominio.com/auth/deriv/callback`).
- **Fluxo OAuth2:**
  1. Usuário clica em **"Conectar Conta Deriv"** → redirecionado para:
     `https://oauth.deriv.com/oauth2/authorize?app_id=<DERIV_APP_ID>`
  2. Após autorizar na Deriv, o usuário volta para `/auth/deriv/callback` com os parâmetros de query:
     `?acct1=CR123456&token1=<TOKEN_1>&cur1=USD&acct2=VRTC654321&token2=<TOKEN_2>&cur2=USD`
  3. O frontend captura os pares `(acctN, tokenN, curN)`, envia via `POST /api/deriv/connect` (HTTPS) para o backend e **limpa imediatamente a URL do navegador** (`window.history.replaceState`) para que nenhum token fique no histórico.
  4. **Fallback opcional:** permitir também colar um **API Token pessoal** gerado na Deriv (exigindo apenas escopos `Read` + `Trade` — **recusar e alertar** caso o usuário cole um token com permissão `Payments` ou `Admin`).
- **Segurança Obrigatória de Tokens:**
  - **TODOS** os tokens da Deriv devem ser criptografados em repouso no PostgreSQL usando **AES-256-GCM / `Fernet`** com chave `ENCRYPTION_KEY` injetada via `.env`.
  - Nunca logar tokens em `stdout`, arquivos de log, Sentry ou respostas da API.
  - Alternar de **DEMO (`VRTC...`)** para **REAL (`CR...`)** exige **modal de confirmação explícita** com aviso claro de risco financeiro.

### 2.2. Contrato Correto na API WebSocket & Limites Reais
- **WebSocket Autenticado (por conta conectada):** `wss://ws.derivws.com/websockets/v3?app_id=<DERIV_APP_ID>` → `{"authorize": "<USER_TOKEN>"}`
- **WebSocket Público (feed compartilhado de cotações/velas):** `wss://api.derivws.com/trading/v1/options/ws/public`
- **Tipo de Contrato Obrigatório para Preço Médio:** **`Multipliers` (`MULTUP` / `MULTDOWN`)**:
  - **Não têm expiração por tempo** (`dur = 0..0`): permitem manter a cesta aberta até o preço retornar ao lucro e encerrar manualmente via `sell`.
  - **Proteção contra saldo negativo:** cada contrato limita a perda máxima ao seu próprio stake.
  - *(NÃO usar Opções Digitais `CALL`/`PUT` de expiração fixa para preço médio, pois expiram por relógio e não permitem segurar a recuperação.)*
- **Validação Dinâmica de Stake Mínimo e Multiplicadores (NÃO HARDCODAR):**
  - Na inicialização (e em cache renovado no Redis), consultar `active_symbols` (`{"active_symbols": "brief"}`) e `contracts_for` (`{"contracts_for": "<symbol>"}`) para filtrar automaticamente os ativos que possuem `contract_category == "multiplier"` (`MULTUP`/`MULTDOWN`).
  - Validar sempre via `proposal` antes de salvar a configuração do usuário — respeitando o stake mínimo real da Deriv para aquele ativo/moeda (ex.: em **`cryBTCUSD` USD**, a API exige **mínimo de `$1.00`**, multiplicadores `[100, 200, 300, 500, 800]`, comissão de abertura **`~$0.07` por `$1.00`** em `100x` e **stop-out individual automático em `~-0.93%`**; já em Forex/Sintéticos os multiplicadores e distâncias de stop-out variam e devem ser lidos dinamicamente do `proposal`!).
- **Fluxo de Execução na Deriv:**
  1. `proposal` (`contract_type: "MULTUP"` ou `"MULTDOWN"`, `basis: "stake"`, `amount`, `multiplier`, `underlying_symbol`, `currency`, `limit_order: {stop_loss}`)
  2. `buy` (`{"buy": "<proposal_id>", "price": <amount>}`) → salvar `contract_id`, `entry_spot`, `buy_price` e `commission` no PostgreSQL.
  3. `proposal_open_contract` (`subscribe: 1`) → acompanhar em tempo real o campo **`profit` líquido real** (já descontando comissão/spread) e o `is_sold` / `status`.
  4. `sell` (`{"sell": <contract_id>, "price": 0}`) → encerramento imediato de todos os contratos do cesto ao bater a meta de lucro ou stop global.
  5. **Reconciliação de Estado:** ao reiniciar o container `bot-worker`, consultar `portfolio` de cada usuário ativo para sincronizar qualquer contrato aberto sem jamais duplicar ordens.

---

## 3. Controle de Usuários, Admin e Validade de Assinatura (RBAC)

### 3.1. Autenticação do Sistema Próprio
- Login por e-mail + senha com hash **`Argon2id`** (ou `bcrypt`), **JWT** (`access_token` curto + `refresh_token` `HttpOnly` cookie), proteção contra força bruta (*rate limit* no login via Redis) e troca obrigatória de senha no primeiro acesso do Admin padrão criado via seed.
- Dois perfis (`role`): `ADMIN` e `USER`.

### 3.2. Painel Administrativo (`/admin`)
O Administrador pode:
- **Criar usuário:** nome, e-mail, senha inicial, **data de validade (`expires_at`)**, status (`ACTIVE`, `SUSPENDED`), plano/limite opcional de pares simultâneos ou stake máximo.
- **Gerenciar validade:** botões rápidos `+7 dias`, `+30 dias`, `+90 dias`, data customizada, ou bloquear/desbloquear acesso instantaneamente.
- **Monitoramento Global (somente metadados — privacidade preservada):** ver lista de usuários, quantos dias restam de validade, se estão com conta Deriv conectada (`DEMO` ou `REAL`), se o robô está `ON` ou `OFF`, status de saúde do `bot-worker` e logs de erros de API. *(Nunca exibir tokens da Deriv no painel admin.)*

### 3.3. Regra de Expiração Segura (CRÍTICO!)
Quando `now() > user.expires_at` (ou usuário for bloqueado pelo Admin):
- **Novas entradas são bloqueadas imediatamente** e o painel exibe aviso de assinatura expirada.
- **Gestão segura de cestos já abertos:** se o usuário ainda tiver um cesto com contratos abertos na Deriv no momento em que a licença expirar, o `bot-worker` **CONTINUA gerenciando apenas a SAÍDA (Take Profit / Stop Loss) daquele cesto até encerrá-lo com segurança** (sem abrir novas camadas/novos ciclos), desativando o robô automaticamente logo após o fechamento! Nunca abandonar uma posição aberta sem gerenciamento!

---

## 4. Motor Quantitativo — Canal + Confirmação + Preço Médio Controlado

**Regra anti-repaint absoluta:** todos os cálculos de canal e indicadores são **100% causais** (apenas velas fechadas e ticks até o instante `t` — zero lookahead).

### 4.1. Detecção de Canal de Alta / Baixa (Timeframe Guia: `M15` ou configurável `M5`/`M15`/`H1`)
- **Canal de Regressão Linear Dinâmico (`window = 60–100` velas):**
  - Reta central $\hat{y}_t = a + b \cdot t$, desvio padrão dos resíduos $\sigma$, bandas $\hat{y}_t \pm 2.0\sigma$, e coeficiente $R^2$.
  - **Modo Compra (`LONG`):** exige inclinação positiva ($b > b_{\min}$), qualidade estrutural ($R^2 \ge 0.45$) e filtro macro `EMA(50) > EMA(200)`.
  - **Modo Venda (`SHORT`, opcional por usuário):** espelho simétrico para canal de baixa.
- **Crash Guard (Filtro de Regime):** bloqueia novas entradas se `ADX(14) > 35` contra a direção da operação (`-DI > +DI` em compras) ou se `ATR(14)` atual exceder `2.5x` sua mediana recente.

### 4.2. Confirmação de Sustentação na Linha Inferior (Entrada #1)
Para não entrar em rompimento de baixa ("faca caindo"), a Entrada #1 só ocorre quando o preço toca a zona inferior do canal (`low <= lower_band + 0.15 * ATR`) **E** confirma **pelo menos 2 de 3 critérios**:
1. **Rejeição de Price Action:** pavio inferior `>= 40%` do tamanho da vela ou fechamento acima da metade do candle após testar a banda inferior.
2. **Virada de Indicadores:** `RSI(14) < 35` virando para cima (`RSI[t] > RSI[t-1]`) **ou** histograma `MACD(12,26,9)` perdendo força negativa por 2 velas consecutivas.
3. **Filtro Adicional de Fluxo (Opcional para `cryBTCUSD`):** se o par for BTC, permitir ativar o feed público da **Binance (`btcusdt@aggTrade` + `@depth20@100ms`)** — que lidera a Deriv em `~600ms` e possui volume/livro reais — exigindo **Delta comprador virando positivo** ou absorção na zona de suporte.

### 4.3. Motor de Preço Médio Inteligente (`Smart DCA`)
Se o preço continuar contra após a Entrada #1:
- **Espaçamento Adaptativo ao Stop-Out Real do Ativo:**
  - O robô lê a distância real até o `stop_out` do contrato (retornada pela Deriv no `proposal`!).
  - Exemplo para `cryBTCUSD` em `100x` (`stop_out ≈ -0.93%`): **`max_layers = 3`**, espaçadas por **`~0.25%`** (Camada 1 em `0%`, Camada 2 após `-0.25%`, Camada 3 após `-0.50%`), garantindo que todas as camadas caibam **antes** do stop-out da Camada 1!
  - Em ativos com stop-out mais distante (ex.: Forex com multiplicador menor), o espaçamento escala automaticamente com `ATR` e respeita `step_total < 0.75 * stop_out_distance`.
- **Reentrada Somente com Nova Confirmação:** jamais abrir camada 2/3 no meio de vela de pânico — exigir nova confirmação de suporte/rejeição/virada de `RSI`.
- **Progressão Conservadora (PROIBIDO Martingale `2x/4x/8x`):**
  - Ex.: `1.0x → 1.0x → 1.5x` (ou no máximo `1.0x → 1.3x → 1.7x`, configurável dentro de teto seguro definido no sistema).
- **Cancelamento em Rompimento Estrutural:** se o preço fechar abaixo de `lower_band - 1.0 * ATR`, suspender reentradas imediatamente.

### 4.4. Saída Coletiva no Lucro (`Basket Take Profit + Trailing`)
- O `BasketManager` soma em tempo real o lucro líquido de todas as camadas abertas do cesto:
  $$\text{NetBasketPnL} = \sum_{i \in \text{cesto}} \text{contract}_i.\text{profit}$$
- Quando `NetBasketPnL >= basket_tp_usd` (calculado de forma a cobrir integralmente as comissões de todas as camadas e entregar lucro líquido configurável pelo usuário):
  - **Modo Direto:** dispara `sell` (`price: 0`) em todas as camadas do cesto simultaneamente.
  - **Modo Trailing Profit do Cesto (opcional):** ativa piso de proteção ao bater o alvo e acompanha a subida fechando se recuar `30%` do lucro máximo do cesto.
- Após o fechamento completo, grava o ciclo no PostgreSQL, atualiza estatísticas em tempo real via WebSocket e aguarda `cooldown` (ex.: `2` velas) antes de buscar nova entrada.

---

## 5. Gestão de Risco Individual por Usuário (Circuit Breakers)

Cada usuário configura (dentro de limites mínimos de segurança) e o `bot-worker` aplica de forma rígida:
1. **Stop Loss Global do Cesto (`max_basket_loss_usd`):** encerra todo o cesto caso o prejuízo líquido do cesto atinja o limite ou haja rompimento estrutural confirmado do canal.
2. **Stop Loss Diário (`max_daily_loss_usd` / `%`):** desliga o robô automaticamente até o próximo dia UTC ao bater o limite de perda diária.
3. **Meta de Lucro Diária (`daily_profit_target_usd` / `%`):** pausa automática ao bater a meta do dia.
4. **Stop por Sequência (`max_consecutive_losses = 3`):** pausa automática se 3 ciclos consecutivos fecharem negativos.
5. **Timeout Máximo do Cesto (`max_hold_minutes`):** opção de reduzir alvo para breakeven ou encerrar cesto preso por tempo excessivo.
6. **Kill Switch Instantâneo:** botão vermelho no topo do dashboard que envia comando imediato ao `bot-worker` para encerrar todos os contratos abertos do usuário na Deriv e desligar o robô.

---

## 6. Interface Web Moderna, Completa e Profissional (UI/UX)

Construir um frontend **elegante, responsivo e rápido** (**React + Vite + TypeScript + Tailwind CSS + shadcn/ui + TradingView Lightweight Charts**, tema **Dark Pro** estilo terminal quantitativo moderno):

### 6.1. Telas Obrigatórias
1. **Login / Status da Assinatura:** tela limpa de acesso com alerta claro caso a conta esteja expirada ou suspensa.
2. **Conexão Deriv (`/deriv-connect`):**
   - Botão oficial **"Conectar com a Deriv (OAuth)"** + aba opcional "Conectar via API Token".
   - Seletor visual de conta (**DEMO `VRTC`** com badge azul vs **REAL `CR`** com badge verde/alerta de confirmação explícita) exibindo saldo ao vivo e moeda.
3. **Dashboard Principal (`/dashboard`):**
   - **Header de Controle:** Status do robô (`LIGADO` / `DESLIGADO` / `EM OPERAÇÃO` / `COOLDOWN` / `STOP DIÁRIO`), **Switch grande Ligar/Desligar Robô**, botão **Kill Switch**, conta ativa (`DEMO`/`REAL`), saldo ao vivo e contador de **dias restantes da assinatura**.
   - **Cards KPI em Tempo Real:** Saldo Deriv, Lucro Hoje (`$` e `%`), Lucro Acumulado, **Assertividade por Ciclo (%)**, Profit Factor, Max Drawdown e Cesto Atual (Camadas `X/3`, Preço Médio, PnL Flutuante Líquido).
   - **Gráfico Ao Vivo (`TradingView Lightweight Charts`):** candles em tempo real do ativo selecionado com **linhas do Canal de Alta/Baixa plotadas**, marcadores das entradas do Preço Médio, linha tracejada do **Breakeven Líquido do Cesto** e linha do **Alvo de Lucro**.
   - **Feed de Eventos Ao Vivo:** log amigável em tempo real (*"Canal de alta detectado em cryBTCUSD (R²=0.72)"*, *"Toque na linha inferior + RSI virando → Entrada #1 ($1.00)"*, *"Cesto encerrado com lucro líquido +$0.18"*).
4. **Configurações do Robô & Seletor de Pares (`/bot-config`):**
   - **Tabela/Lista pesquisável de pares da Deriv** (agrupados por categoria: Cripto, Forex, Sintéticos, Commodities) com busca, status Aberto/Fechado, stake mínimo real, multiplicadores suportados e botão para selecionar.
   - Formulário intuitivo com validação em tempo real contra a API da Deriv: valor de entrada (`stake`), multiplicador (padrão = menor disponível), conta (`DEMO`/`REAL`), direção (`Somente Alta`, `Somente Baixa`, `Ambos`), nº máximo de camadas DCA, meta de lucro, stop global, stop diário e meta diária.
   - **Simulador rápido de risco na própria tela:** ao digitar stake e camadas, mostrar automaticamente o **capital máximo em risco no cesto**, a **comissão estimada da Deriv** e a **distância até o stop-out**!
5. **Relatórios & Performance (`/reports`):**
   - Filtros por período (`Hoje`, `7 dias`, `30 dias`, `Personalizado`), por conta (`DEMO` vs `REAL` — **nunca misturar estatísticas Demo com Real!**) e por par.
   - Gráficos: **Curva de Patrimônio (*Equity Curve*)**, **PnL Diário (barras verde/vermelho)**, **Distribuição de Camadas usadas (1, 2 ou 3 camadas)** e **Assertividade por Par**.
   - Tabela paginada de todos os ciclos concluídos (abertura, fechamento, par, camadas, stake total, comissão paga, lucro líquido, motivo de saída: `TP`, `TRAILING`, `STOP_LOSS`, `KILL_SWITCH`) + **botão Exportar CSV**.
6. **Painel Admin (`/admin` — visível apenas para `ADMIN`):**
   - CRUD completo de usuários, controle rápido de **data de validade**, status (`ATIVO` / `EXPIRADO` / `BLOQUEADO`), filtros de vencimento próximo e painel de saúde do `bot-worker` / conexões WebSocket.

---

## 7. Arquitetura Docker Multi-Tenant (Produção Limpa)

Entregar toda a stack pronta para subir com um único `docker compose up -d --build`:

```text
deriv-dca-saas/
├── docker-compose.yml              # Orquestracao completa com healthchecks e volumes persistentes
├── .env.example                    # DERIV_APP_ID, ENCRYPTION_KEY, JWT_SECRET, POSTGRES_*, ADMIN_EMAIL/PASS
├── nginx/
│   └── default.conf                # Reverse proxy HTTP/HTTPS + suporte a WebSocket (/ws)
├── backend/                        # FastAPI (Python 3.12)
│   ├── Dockerfile
│   ├── app/
│   │   ├── main.py                 # API REST + WebSocket para dashboard em tempo real
│   │   ├── core/                   # config, security (JWT, Argon2id, AES-256-GCM/Fernet)
│   │   ├── models/                 # SQLAlchemy: User, DerivAccount, BotConfig, Basket, Contract, TradeLog
│   │   ├── migrations/             # Alembic migrations
│   │   ├── api/                    # auth, admin, deriv_oauth, bot_config, reports, kill_switch
│   │   └── services/               # crypto_vault, subscription_guard, report_aggregator
├── worker/                         # Motor Quantitativo Multi-Usuario (Python 3.12 asyncio)
│   ├── Dockerfile
│   ├── engine/
│   │   ├── market_hub.py           # Feed COMPARTILHADO Deriv (+ opcional Binance) publicado no Redis
│   │   ├── channel_detector.py     # Canal de Regressao Linear causal + R^2 + EMA + ADX Crash Guard
│   │   ├── confirmations.py        # Rejeicao de pavio, RSI, MACD, filtro de fluxo
│   │   ├── user_session.py         # Conexao Deriv autenticada isolada por usuario ativo
│   │   ├── dca_basket_manager.py   # Gestao de camadas, preco medio, trailing e reconciliacao portfolio
│   │   ├── risk_guard.py           # Circuit breakers diarios, stop global e expiracao segura
│   │   └── coordinator.py          # Supervisiona sessoes de todos os usuarios com robos ligados
├── backtest/                       # Validacao Cientifica Obrigatoria
│   ├── simulator.py                # Simulador Walk-Forward com comissao real, spread e stop-out exato
│   └── report.py                   # Relatorio Out-of-Sample + Stress Test de Crash
└── frontend/                       # React + Vite + TypeScript + Tailwind + shadcn/ui + Lightweight Charts
    ├── Dockerfile
    └── src/
        ├── pages/                  # Login, Dashboard, BotConfig, DerivConnect, Reports, Admin
        ├── components/             # ChannelChart, KpiCards, PairSelector, RiskCalculator, KillSwitch
        └── hooks/                  # useAuth, useLiveSocket
```

### Requisitos Críticos de Engenharia Multi-Usuário
- **Feed de Mercado Compartilhado (`market_hub.py` + Redis):** se 50 usuários estiverem operando `cryBTCUSD`, o sistema mantém **apenas 1 conexão pública** para coletar ticks/candles de `cryBTCUSD` e distribuir o sinal via Redis — economizando banda e evitando rate-limit na Deriv.
- **Execução Isolada por Usuário (`user_session.py`):** cada usuário com robô ligado possui sua própria sessão WebSocket autenticada com o token da sua conta (Demo ou Real) e **lock distribuído no Redis** por `(user_id, symbol)` para impedir ordens duplicadas em caso de reconexão.
- **Persistência e Recuperação:** PostgreSQL com volumes Docker nomeados + Alembic migrations automáticas no startup.

---

## 8. Validação Científica Obrigatória e Fases de Entrega

**Regra de transparência absoluta:** jamais prometer lucro garantido ou taxas irreais. Antes de recomendar uso em conta Real, toda a estratégia deve passar por validação empírica honesta:

### Fase 1 — Backtest Realista Walk-Forward (Obrigatório Antes do Deploy)
- Implementar `backtest/simulator.py` simulando **comissão real da Deriv** (ex.: `$0.07` por `$1.00` em `cryBTCUSD 100x`), **spread real**, **stop-out individual exato** e **latência de `+1s`**.
- Rodar teste **Out-of-Sample (70% Dev / 30% Holdout virgem)** em múltiplos ativos (ex.: `cryBTCUSD`, `frxEURUSD`, `R_75`) e gerar relatório honesto com: **Assertividade por ciclo**, **Expectancy líquido**, **Profit Factor**, **Max Drawdown (%)** e **Stress Test de queda rápida (`-5%`)**.
- Caso algum ativo/multiplicador reprove no backtest (ex.: comissão de `7%` + stop-out `-0.93%` tornando o expectância negativa), documentar claramente quais pares/timeframes/multiplicadores têm melhor viabilidade matemática.

### Fase 2 — Backend, Banco PostgreSQL, Auth/Admin e OAuth Deriv
- Implementar API FastAPI, banco PostgreSQL + Alembic, CRUD Admin com controle de `expires_at`, criptografia de tokens (`Fernet`/AES-256-GCM), fluxo OAuth Deriv (`acctN`/`tokenN`/`curN`) e listagem dinâmica de pares (`active_symbols` + `contracts_for`).

### Fase 3 — Worker Multi-Usuário (Estratégia + DCA + Circuit Breakers)
- Implementar `market_hub.py`, `channel_detector.py`, `confirmations.py`, `dca_basket_manager.py`, `risk_guard.py`, reconciliação via `portfolio` e testes unitários automatizados (`pytest`) cobrindo: stop-out, preço médio, expiração segura de licença, reconexão e Kill Switch.

### Fase 4 — Frontend Moderno + Docker Compose Completo
- Construir todas as telas (`Dashboard`, `BotConfig`, `DerivConnect`, `Reports`, `Admin`) com `Lightweight Charts` plotando o canal + entradas/preço médio em tempo real, empacotar tudo no `docker-compose.yml` com Nginx e entregar `README.md` com guia passo a passo de criação do `DERIV_APP_ID`, configuração do `.env` e deploy em VPS/local.




D:\backup importantes\ficar-rico\btc-proxima-vela\aba_zonas_interesse.html