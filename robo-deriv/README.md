# Robo Deriv — Plataforma Web Multi-Usuário de Canal + Smart DCA (Docker)

Plataforma web multi-usuário rodando em **Docker**, conectada diretamente à **API WebSocket oficial da Deriv** (`MULTUP` / `MULTDOWN`, sem MT5), com painel Admin, controle de data de validade por usuário, login individual na Deriv (OAuth ou API Token criptografado), seletor dinâmico de pares, gráfico TradingView Lightweight Charts em tempo real, relatórios separados (`DEMO` vs `REAL`) e motor quantitativo assíncrono compartilhado via Redis.

---

## Acesso Rápido (Docker Local)

- **URL do Painel:** `http://localhost:8095`
- **Admin inicial (seed automático do `.env`):**
  - **E-mail:** `admin@roboderiv.local`
  - **Senha inicial:** `Admin@123456` *(troque após o primeiro acesso)*

---

## Arquitetura de Containers (`docker-compose.yml`)

| Container | Serviço | Porta | Função |
|---|---|---|---|
| `deriv_dca_frontend` | React + Vite + Nginx | `8095 -> 80` | Interface Dark Pro + Reverse Proxy `/api/` e `/ws/` |
| `deriv_dca_backend` | FastAPI (Python 3.12) | Interna `8000` | Auth JWT/Argon2, Admin RBAC, OAuth Deriv, Relatórios, WS |
| `deriv_dca_worker` | Motor Quantitativo (`asyncio`) | Interna | Feed compartilhado Deriv (+ Binance opcional), Canal Causal, Smart DCA, Circuit Breakers |
| `deriv_dca_postgres` | PostgreSQL 16 | Interna `5432` | Usuários, contas Deriv criptografadas, cestos, contratos, logs |
| `deriv_dca_redis` | Redis 7 | Interna `6379` | Cache de mercado, pub/sub de ticks/comandos e locks anti-duplicação |

---

## Comandos Docker

```powershell
cd C:\robo-deriv

# Subir / reconstruir todos os containers
docker compose up -d --build

# Ver status e saúde
docker compose ps

# Ver logs em tempo real
docker compose logs -f backend worker

# Parar tudo (mantendo volumes do PostgreSQL e Redis)
docker compose down
```

---

## Como Conectar sua Conta Deriv (Demo ou Real)

1. **Via API Token Pessoal (mais rápido para testar localmente):**
   - Acesse sua conta Deriv → *Settings → API Token*.
   - Crie um token marcando **APENAS** os escopos **`Read`** e **`Trade`** (nunca marque `Payments` ou `Admin` — o sistema recusa por segurança).
   - No painel (`Conectar Deriv` → aba **API Token**), cole o token e clique em **Validar e Conectar**.
   - Selecione sua conta **DEMO (`VRTC...`)** primeiro para validar o funcionamento antes de considerar conta Real.
2. **Via OAuth Oficial da Deriv (ideal para múltiplos usuários):**
   - Registre seu App gratuito em `https://developers.deriv.com` (*Dashboard → Applications → Application manager*).
   - Configure a *Redirect URL* para `http://localhost:8095/auth/deriv/callback` (ou `https://seu-dominio.com/auth/deriv/callback` em produção).
   - Coloque o `app_id` gerado no arquivo `.env` em `DERIV_APP_ID=...` e reinicie `docker compose up -d backend`.

---

## Validação Científica (Fase 1 — Backtest Walk-Forward)

Os scripts de backtest realista (com comissão exata da Deriv, spread e stop-out intrabar) ficam em `C:\robo-deriv\backtest\`:

```powershell
cd C:\robo-deriv
python backtest/run_backtest.py
```

Consulte `C:\robo-deriv\backtest\BACKTEST_REPORT.md` antes de habilitar qualquer capital real. **Sempre teste primeiro em Conta Demo (`VRTC`).**
