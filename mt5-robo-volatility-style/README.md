# Robo Volatility Style (MT5 Expert Advisor)

Robô Quantitativo de Alta Performance para **MetaTrader 5 (MQL5)** projetado para operar nos ativos **BTCUSD** (Bitcoin) e **XAUUSD** (Ouro / Gold) com **3 perfis de risco distintos** e rigorosa gestão de risco profissional.

---

## 📁 Arquivos Incluídos

- `RoboVolatilityStyle.mq5`: Código-fonte completo em MQL5.
- `RoboVolatilityStyle.ex5`: Binário compilado pronto para execução no MetaTrader 5 (**0 erros, 0 avisos**).
- `README.md`: Manual de instalação, configuração e parâmetros.

---

## 🎯 Ativos Suportados

1. **BTCUSD** (Principal – Alta volatilidade e expansão de momentum)
2. **XAUUSD** (Secundário – Excelente liquidez e spreads estáveis)

> **Dica:** O EA pode ser anexado diretamente no gráfico do ativo desejado (modo automático `ASSET_AUTO_CHART`) ou configurado explicitamente no input `InpTargetAsset`.

---

## 🛡️ Os 3 Perfis de Risco

### 1. 🔥 MODO AGRESSIVO (Alta Frequência | M1 Scalping)
Focado em frequência de vitórias rápidas aproveitando a volatilidade do M1.
- **Timeframe Recomendado:** M1
- **Gatilho de Entrada:**
  - Cruzamento de **EMA 8** e **EMA 21** a favor do momentum.
  - **RSI (14)** em zona de força: Compra se RSI $\ge$ 55; Venda se RSI $\le$ 45.
  - **Filtro de Volatilidade ATR (14):** Só entra se o ATR atual estiver acima da média móvel do ATR (evita consolidação rasa).
- **Gestão de Posição:**
  - Stop Loss curto: $1.0\times$ a $1.5\times$ ATR.
  - Take Profit: Relação Risco:Retorno de $1:1.2$ a $1:1.5$.
  - **Trailing Stop:** Ativado automaticamente após o preço andar $1.0\times$ o risco inicial a favor da operação.
- **Recuperação de Perdas:**
  - **Split / Soft Recovery:** Máximo de 2 níveis suaves (ex: 1.5x na 1ª perda $\to$ 2.2x na 2ª perda). Se houver a 3ª perda, reseta compulsoriamente para o lote base. Zero Martingale infinito.
- **Meta Sugerida:** 2% a 4% da banca por sessão.

---

### 2. ⚖️ MODO MODERADO (Equilíbrio Trend & Break-Even | M5)
Equilíbrio matemático entre taxa de acerto e relação risco-retorno.
- **Timeframe Recomendado:** M5
- **Gatilho de Entrada:**
  - Cruzamento de **EMA 9** e **EMA 21**.
  - **Filtro de Tendência Maior:** Preço acima da **EMA 50** para Compra; abaixo da **EMA 50** para Venda.
  - **Filtro RSI (14):** Compra entre 50 e 70; Venda entre 30 e 50 (evita sobrecompra e sobrevenda exaustiva).
- **Gestão de Posição:**
  - Stop Loss: $1.5\times$ a $2.0\times$ ATR.
  - Take Profit: Relação Risco:Retorno de $1:1.8$ a $1:2.2$.
  - **Break-Even Automático:** Quando o lucro flutuante atinge $1.0\times$ o risco inicial, o Stop Loss é movido automaticamente para a entrada mais um pequeno buffer de segurança.
- **Recuperação de Perdas:**
  - Máximo de 2 níveis suaves (1.6x $\to$ 2.5x). Ao atingir o limite, reseta imediatamente para o lote base.
- **Meta Sugerida:** 3% a 6% da banca por sessão.

---

### 3. 🛡️ MODO CONSERVADOR (Proteção de Capital & Payoff 1:2 | M15)
Focado em sobrevivência no longo prazo, trades de alta convicção e proteção de capital.
- **Timeframe Recomendado:** M15 (ou M5 com filtros estritos)
- **Gatilho de Entrada:**
  - Alinhamento de 3 Médias: **EMA 9**, **EMA 21** e **EMA 50**.
  - Compra: Preço acima das duas médias lentas + cruzamento da EMA 9 para cima + RSI entre 50 e 65.
  - Venda: Preço abaixo das duas médias lentas + cruzamento da EMA 9 para baixo + RSI entre 35 e 50.
  - **Filtro de Estrutura de Swing:** Validação de Higher High (para compra) ou Lower Low (para venda) nos últimos $N$ candles.
- **Gestão de Posição:**
  - Risco fixo de 0.8% a 1.2% da banca por trade.
  - Stop Loss baseado em ATR ($2.0\times$ a $2.5\times$ ATR) ou no suporte/resistência do swing recente.
  - **Take Profit Fixo de 1:2 (Payoff Positivo Obrigatório):** Ganho exatamente o dobro da perda potencial.
  - **Zero Martingale:** Lote fixo e disciplinado sempre.
- **Meta Sugerida:** 2% a 5% da banca por dia.

---

## 🔒 Regras Globais de Segurança (Circuit Breakers)

1. **Stop Loss Diário Global:** Fecha todas as posições e bloqueia novas operações se o prejuízo diário atingir o limite estipulado (padrão: 5%).
2. **Stop Win Diário Global:** Encerra as operações do dia ao atingir a meta financeira da sessão (padrão: 5%).
3. **Trava de Sequência de Derrotas:** Pausa o robô se atingir $N$ perdas consecutivas (padrão: 3 no Agressivo, 4 no Moderado, 3 no Conservador).
4. **Filtro Dinâmico de Spread:**
   - Para **BTCUSD:** Bloqueia ordens se o spread for maior que 3000 pontos ($30.00).
   - Para **XAUUSD:** Bloqueia ordens se o spread for maior que 50 pontos ($0.50).
5. **Filtro de Horário e Fim de Semana:**
   - Horário de início e término configurável.
   - Opção de fechamento compulsório na Sexta-feira para evitar riscos de gap de abertura.
6. **Controle de Posições Simultâneas:** Permite limitar a no máximo 1 posição ativa por vez para evitar sobrealavancagem.
7. **Cálculo de Lote Automático por Risco:** Calcula o tamanho do lote com base no saldo da conta, no valor do tick da corretora e na distância exata em pontos até o Stop Loss.

---

## 🚀 Como Instalar no MetaTrader 5

1. Abra o MetaTrader 5 no seu computador.
2. Vá no menu **Arquivo (File)** $\to$ **Abrir Pasta de Dados (Open Data Folder)**.
3. Navegue até a pasta: `MQL5` $\to$ `Experts`.
4. Copie os arquivos `RoboVolatilityStyle.mq5` e `RoboVolatilityStyle.ex5` para dentro da pasta `Experts`.
5. No painel **Navegador (Navigator)** do MT5, clique com o botão direito em **Consultores Expert (Expert Advisors)** e clique em **Atualizar (Refresh)**.
6. Abra o gráfico do ativo desejado (**BTCUSD** ou **XAUUSD**) no timeframe correspondente (**M1**, **M5** ou **M15**).
7. Arraste o **Robo Volatility Style** para o gráfico.
8. Na aba **Comum (Common)**, certifique-se de marcar a caixa:
   - ✅ **Permitir Trading Automatizado (Allow Algo Trading)**.
9. Na barra superior do MT5, certifique-se de que o botão **Algo Trading** está verde (Ativado).

---

## 📊 Painel Visual no Gráfico (HUD)

O robô projeta automaticamente na tela um painel em tempo real exibindo:
- Ativo operado, timeframe e spread atual
- Modo de risco selecionado
- Status do motor (`OPERANDO NORMALMENTE`, `STOP WIN ATINGIDO`, `STOP LOSS DIÁRIO`, etc.)
- Saldo da conta, Equidade e Lucro diário ($ e %)
- Metas de Stop Win e Limites de Stop Loss
- Sequência de perdas consecutivas e nível de recuperação ativo
- Quantidade de posições abertas
