//+------------------------------------------------------------------+
//|                                          RoboVolatilityStyle.mq5 |
//|                             Copyright 2026, Quant Scalp Systems  |
//|                                             https://www.mql5.com |
//+------------------------------------------------------------------+
#property copyright   "Copyright 2026, Quant Scalp Systems"
#property link        "https://www.mql5.com"
#property version     "1.30"
#property description "Robô Quantitativo Volatility Style para MT5"
#property description "Opera nos ativos BTCUSD e XAUUSD com Micro-Scalping rápido estilo vídeo:"
#property description "1. Agressivo (Alvo Curto $15-$25 | Trailing Rápido | Micro-Scalping M1)"
#property description "2. Moderado (M5 Trend | Break-Even | Recuperacao Suave)"
#property description "3. Conservador (M15 Protecao | Payoff 1:2 | Zero Martingale)"
#property strict

#include <Trade\Trade.mqh>
#include <Trade\PositionInfo.mqh>
#include <Trade\AccountInfo.mqh>
#include <Trade\SymbolInfo.mqh>
#include <Trade\HistoryOrderInfo.mqh>

//+------------------------------------------------------------------+
//| ENUMERAÇÕES DO SISTEMA                                           |
//+------------------------------------------------------------------+
enum ENUM_RISK_MODE
{
   MODE_AGGRESSIVE   = 0, // 🔥 AGRESSIVO (M1 Micro-Scalping Rápido $15-$25 | Trailing Stop)
   MODE_MODERATE     = 1, // ⚖️ MODERADO (M5 Trend | Break-Even Automático)
   MODE_CONSERVATIVE = 2  // 🛡️ CONSERVADOR (M15 Proteção | Payoff 1:2 Fixo | Sem Martingale)
};

enum ENUM_TARGET_ASSET
{
   ASSET_AUTO_CHART  = 0, // Automático (Usar o ativo do gráfico atual)
   ASSET_BTCUSD      = 1, // BTCUSD (Bitcoin)
   ASSET_XAUUSD      = 2, // XAUUSD (Gold / Ouro)
   ASSET_CUSTOM      = 3  // Customizado (Definir no campo abaixo)
};

enum ENUM_LOT_CALC_MODE
{
   LOT_FIXED_SIZE        = 0, // Lote Fixo Manual (Recomendado Micro-Scalping como no vídeo)
   LOT_AUTO_RISK_PERCENT = 1  // Automático (% de Risco da Banca baseado no SL)
};

//+------------------------------------------------------------------+
//| INPUTS DO USUÁRIO                                                |
//+------------------------------------------------------------------+

//--- 1. CONFIGURAÇÃO GERAL E ATIVO
input group "=== 1. CONFIGURAÇÃO GERAL E ATIVO ==="
input ENUM_RISK_MODE      InpRiskMode          = MODE_AGGRESSIVE;     // Perfil de Risco Operacional
input ENUM_TARGET_ASSET   InpTargetAsset       = ASSET_AUTO_CHART;    // Ativo Alvo
input string              InpCustomSymbol      = "BTCUSD";            // Símbolo Customizado (se Customizado)
input ulong               InpMagicNumber       = 778899;              // Magic Number do Robô
input string              InpTradeComment      = "RoboVolStyle";      // Comentário das Ordens

//--- 2. GESTÃO DE BANCA E LOTE
input group "=== 2. GESTÃO DE BANCA E LOTE ==="
input ENUM_LOT_CALC_MODE  InpLotCalcMode       = LOT_FIXED_SIZE;      // Dimensionamento do Lote
input double              InpFixedLot          = 0.01;                // Lote Fixo (0.01 idêntico ao vídeo)
input double              InpRiskPercent       = 1.0;                 // Risco por Operação (% se Automático)
input double              InpMinLot            = 0.01;                // Lote Mínimo Permitido
input double              InpMaxLot            = 5.0;                 // Lote Máximo de Segurança

//--- 3. GESTÃO DE RECUPERAÇÃO (APENAS AGRESSIVO E MODERADO)
input group "=== 3. RECUPERAÇÃO DE PERDAS (AGRESSIVO / MODERADO) ==="
input bool                InpEnableRecovery    = false;               // Ativar Recuperação de Lote (Falso = Mão Fixa Plana como no Vídeo)
input double              InpAggMultLevel1     = 1.5;                 // Agressivo: Multiplicador Perda 1 (ex: 1.5x)
input double              InpAggMultLevel2     = 2.2;                 // Agressivo: Multiplicador Perda 2 (ex: 2.2x - Máx Nível 2)
input double              InpModMultLevel1     = 1.6;                 // Moderado: Multiplicador Perda 1 (ex: 1.6x)
input double              InpModMultLevel2     = 2.5;                 // Moderado: Multiplicador Perda 2 (ex: 2.5x - Máx Nível 2)

//--- 4. PARÂMETROS MODO AGRESSIVO (M1 MICRO-SCALPING RÁPIDO ESTILO VÍDEO)
input group "=== 4. PARÂMETROS MODO AGRESSIVO (ALVO CURTO M1) ==="
input ENUM_TIMEFRAMES     InpAggTimeframe      = PERIOD_M1;           // Agressivo: Timeframe
input double              InpAggTpDollars      = 15.0;                // Agressivo: Take Profit Curto ($ no BTC, ex: $15.00 a $20.00)
input double              InpAggSlDollars      = 30.0;                // Agressivo: Stop Loss Curto ($ no BTC, ex: $30.00)
input bool                InpAggUseDynamicAtr  = false;               // Agressivo: Usar ATR em vez de Dólares (Falso = Dólares Fixos)
input double              InpAggSlAtrMult      = 0.8;                 // Agressivo: Multiplicador SL em ATR (se ATR)
input double              InpAggTpAtrMult      = 0.4;                 // Agressivo: Multiplicador TP em ATR (se ATR)
input bool                InpAggUseTrailing    = true;                // Agressivo: Ativar Trailing Stop Rápido
input double              InpAggTrailingStartDl= 10.0;                // Agressivo: Trailing Ativa ao Ganhar ($ no BTC, ex: $10.00)
input double              InpAggTrailingStepDl = 5.0;                 // Agressivo: Distância de Proteção ($ no BTC, ex: $5.00)
input bool                InpAggIntraBar       = true;                // Agressivo: Operar Rompimento Instantâneo no Tick
input bool                InpAggAllowTrendCont = true;                // Agressivo: Operar Continuação de Tendência
input bool                InpAggAllowBreakout  = true;                // Agressivo: Rompimento da Máxima/Mínima Anterior
input bool                InpAggAllowPullback  = true;                // Agressivo: Pullback e Retomada na EMA Rápida
input int                 InpAggEmaFast        = 8;                   // Agressivo: Período EMA Rápida
input int                 InpAggEmaSlow        = 21;                  // Agressivo: Período EMA Lenta
input int                 InpAggRsiPeriod      = 14;                  // Agressivo: Período RSI
input double              InpAggRsiBuyMin      = 50.0;                // Agressivo: RSI Mínimo Compra (Momentum >= 50)
input double              InpAggRsiSellMax     = 50.0;                // Agressivo: RSI Máximo Venda (Momentum <= 50)
input int                 InpAggAtrPeriod      = 14;                  // Agressivo: Período ATR
input int                 InpAggAtrSmaPeriod   = 20;                  // Agressivo: Período Média do ATR
input bool                InpAggRequireAtrFilter = false;             // Agressivo: Exigir ATR acima da média
input double              InpAggAtrThresholdMult = 0.8;               // Agressivo: Limiar ATR (% da Média, ex: 0.8)

//--- 5. PARÂMETROS MODO MODERADO (M5 BALANCEADO)
input group "=== 5. PARÂMETROS MODO MODERADO (M5) ==="
input ENUM_TIMEFRAMES     InpModTimeframe      = PERIOD_M5;           // Moderado: Timeframe
input int                 InpModEmaFast        = 9;                   // Moderado: Período EMA Rápida
input int                 InpModEmaSlow        = 21;                  // Moderado: Período EMA Lenta
input int                 InpModEmaTrend       = 50;                  // Moderado: Período EMA Tendência Maior
input int                 InpModRsiPeriod      = 14;                  // Moderado: Período RSI
input double              InpModRsiBuyMin      = 50.0;                // Moderado: RSI Mínimo Compra
input double              InpModRsiBuyMax      = 70.0;                // Moderado: RSI Máximo Compra (Evita Sobrecompra)
input double              InpModRsiSellMin     = 30.0;                // Moderado: RSI Mínimo Venda (Evita Sobrevenda)
input double              InpModRsiSellMax     = 50.0;                // Moderado: RSI Máximo Venda
input int                 InpModAtrPeriod      = 14;                  // Moderado: Período ATR
input double              InpModSlAtrMult      = 1.8;                 // Moderado: Multiplicador SL em ATR (1.5 a 2.0)
input double              InpModTpRrRatio      = 2.0;                 // Moderado: Relação Risco-Retorno TP (1:1.8 a 1:2.2)
input bool                InpModUseBreakEven   = true;                // Moderado: Ativar Break-Even Automático
input double              InpModBreakEvenTriggerRr = 1.0;             // Moderado: Disparar Break-Even ao Atingir (1.0x Risco)
input double              InpModBreakEvenBuffer= 10.0;                // Moderado: Margem Acima do Preço de Entrada (Pontos)

//--- 6. PARÂMETROS MODO CONSERVADOR (M15 PROTEÇÃO)
input group "=== 6. PARÂMETROS MODO CONSERVADOR (M15) ==="
input ENUM_TIMEFRAMES     InpConsTimeframe     = PERIOD_M15;          // Conservador: Timeframe
input int                 InpConsEmaFast       = 9;                   // Conservador: EMA Gatilho
input int                 InpConsEmaMid        = 21;                  // Conservador: EMA Tendência Média
input int                 InpConsEmaSlow       = 50;                  // Conservador: EMA Tendência Longa
input int                 InpConsRsiPeriod     = 14;                  // Conservador: Período RSI
input double              InpConsRsiBuyMin     = 50.0;                // Conservador: RSI Compra Min
input double              InpConsRsiBuyMax     = 65.0;                // Conservador: RSI Compra Max
input double              InpConsRsiSellMin    = 35.0;                // Conservador: RSI Venda Min
input double              InpConsRsiSellMax    = 50.0;                // Conservador: RSI Venda Max
input int                 InpConsAtrPeriod     = 14;                  // Conservador: Período ATR
input double              InpConsSlAtrMult     = 2.2;                 // Conservador: Multiplicador SL em ATR (2.0 a 2.5)
input double              InpConsTpRrRatio     = 2.0;                 // Conservador: Payoff Obrigatório 1:2 (Fixo)
input bool                InpConsUseSwing      = true;                // Conservador: Confirmar Estrutura de Swing
input int                 InpConsSwingBars     = 5;                   // Conservador: Barras para Cálculo de Topo/Fundo

//--- 7. REGRAS GLOBAIS DE SEGURANÇA (CIRCUIT BREAKERS)
input group "=== 7. REGRAS GLOBAIS DE SEGURANÇA ==="
input double              InpDailyStopLossPct  = 5.0;                 // Stop Loss Diário Global (% da Banca)
input double              InpDailyStopWinPct   = 5.0;                 // Stop Win Diário Global (% da Banca)
input int                 InpMaxConsecutiveLoss= 10;                  // Máx Perdas Consecutivas para Pausa (0 = Desativar)
input int                 InpMaxSpreadBtcPts   = 3000;                // Spread Máximo BTCUSD (Pontos, ex: 3000 = $30.00)
input int                 InpMaxSpreadXauPts   = 50;                  // Spread Máximo XAUUSD (Pontos, ex: 50 = $0.50)
input int                 InpMaxSpreadDefPts   = 50;                  // Spread Máximo Padrão Outros Ativos
input bool                InpAllowOnlyOnePos   = false;               // Permitir Apenas 1 Posição por Vez (Falso = permite 2 como no vídeo)
input int                 InpMaxTotalPositions = 2;                   // Limite de Posições Simultâneas (ex: 2 como no vídeo)

//--- 8. FILTRO DE HORÁRIO
input group "=== 8. FILTRO DE HORÁRIO DE OPERAÇÃO ==="
input bool                InpUseTimeFilter     = false;               // Ativar Filtro de Horário
input int                 InpStartHour         = 1;                   // Hora de Início (0-23)
input int                 InpStartMinute       = 0;                   // Minuto de Início (0-59)
input int                 InpEndHour           = 23;                  // Hora de Término (0-23)
input int                 InpEndMinute         = 0;                   // Minuto de Término (0-59)
input bool                InpCloseFriday       = true;                // Fechar Operações na Sexta-feira
input int                 InpFridayCloseHour   = 21;                  // Hora de Encerramento Sexta-feira

//--- 9. PAINEL VISUAL E LOGS
input group "=== 9. PAINEL VISUAL E LOGS ==="
input bool                InpShowDashboard     = true;                // Exibir Dashboard Visual no Gráfico
input bool                InpLogEveryCandle    = true;                // Exibir Diagnóstico da Vela no Log dos Experts

//+------------------------------------------------------------------+
//| VARIÁVEIS GLOBAIS                                                |
//+------------------------------------------------------------------+
CTrade            m_trade;
CPositionInfo     m_position;
CAccountInfo      m_account;
CSymbolInfo       m_symbol_info;

string            m_symbol = "";
ENUM_TIMEFRAMES   m_timeframe = PERIOD_M1;
datetime          m_last_bar_time = 0;
datetime          m_last_trade_bar = 0;
datetime          m_current_day_start = 0;
datetime          m_session_start_time = 0;
double            m_day_start_balance = 0.0;

int               m_consecutive_losses = 0;
int               m_recovery_level = 0;
bool              m_circuit_breaker_active = false;
string            m_circuit_breaker_reason = "";

// Estado Operacional Interativo (Controlado via Botões do Gráfico)
bool              m_bot_active = true;
ENUM_RISK_MODE    m_current_risk_mode = MODE_AGGRESSIVE;

// Telemetria ao vivo dos indicadores para o HUD e Logs
double            m_live_ema_fast = 0.0;
double            m_live_ema_slow = 0.0;
double            m_live_ema_trend = 0.0;
double            m_live_rsi = 0.0;
double            m_live_atr = 0.0;
double            m_live_atr_sma = 0.0;
string            m_last_eval_status = "Iniciando monitoramento...";
string            m_last_order_status = "Pronto para operar";

// Handles dos indicadores
int               h_ema_fast = INVALID_HANDLE;
int               h_ema_slow = INVALID_HANDLE;
int               h_ema_trend = INVALID_HANDLE;
int               h_rsi = INVALID_HANDLE;
int               h_atr = INVALID_HANDLE;

// Declarações antecipadas de funções
bool InitIndicators();
void ReleaseIndicators();
bool IsNewBar();
void UpdateLiveTelemetry();
int EvaluateAggressiveMode(double &out_sl_dist, double &out_tp_dist);
int EvaluateModerateMode(double &out_sl_dist, double &out_tp_dist);
int EvaluateConservativeMode(double &out_sl_dist, double &out_tp_dist);
double NormalizeVolume(double lot);
double CalculateLotSize(double sl_distance_price, int signal_type = 1);
double ApplyRecoveryMultiplier(double base_lot);
void ExecuteOrder(int signal, double sl_distance_price, double tp_distance_price);
void ManageOpenPositions();
bool ValidateGlobalRiskLimits();
double GetDailyRealizedPnL();
void SyncConsecutiveLosses();
void CloseAllPositions();
bool ValidateSpread();
bool IsWithinTradingHours();
int CountOpenPositions();
void CheckNewDayReset();
datetime GetDayStartTime();
void RenderDashboard();

// Funções da Interface Gráfica Interativa (GUI)
void InitGUI();
void UpdateGUI();
void DestroyGUI();
void SwitchRiskMode(ENUM_RISK_MODE new_mode);
void GuiCreateRect(string name, int x, int y, int w, int h, color bg, color border);
void GuiCreateButton(string name, string text, int x, int y, int w, int h, color bg, color txt_color, int fsize = 9);
void GuiCreateLabel(string name, string text, int x, int y, color txt_color, int fsize = 8, bool bold = false);
void GuiSetText(string name, string text, color txt_color = clrNONE);

//+------------------------------------------------------------------+
//| Expert initialization function                                   |
//+------------------------------------------------------------------+
int OnInit()
{
   // 1. Determinar Símbolo de Trabalho
   if (InpTargetAsset == ASSET_BTCUSD)
      m_symbol = "BTCUSD";
   else if (InpTargetAsset == ASSET_XAUUSD)
      m_symbol = "XAUUSD";
   else if (InpTargetAsset == ASSET_CUSTOM)
      m_symbol = InpCustomSymbol;
   else
      m_symbol = _Symbol;

   if (!m_symbol_info.Name(m_symbol))
   {
      PrintFormat("[RoboVolStyle] ERRO CRÍTICO: Ativo %s não encontrado no Market Watch!", m_symbol);
      return INIT_FAILED;
   }
   m_symbol_info.Refresh();

   // 1. Inicializar Modo e Estado Interativo
   m_current_risk_mode = InpRiskMode;
   m_bot_active = true;

   // 2. Determinar Timeframe Operacional pelo Modo
   if (m_current_risk_mode == MODE_AGGRESSIVE)
      m_timeframe = InpAggTimeframe;
   else if (m_current_risk_mode == MODE_MODERATE)
      m_timeframe = InpModTimeframe;
   else
      m_timeframe = InpConsTimeframe;

   // 3. Configurar CTrade
   m_trade.SetExpertMagicNumber(InpMagicNumber);
   m_trade.SetDeviationInPoints(30);

   // Auto-detectar tipo de preenchimento (Filling) suportado pela corretora
   uint filling = (uint)SymbolInfoInteger(m_symbol, SYMBOL_FILLING_MODE);
   if ((filling & SYMBOL_FILLING_IOC) != 0)
      m_trade.SetTypeFilling(ORDER_FILLING_IOC);
   else if ((filling & SYMBOL_FILLING_FOK) != 0)
      m_trade.SetTypeFilling(ORDER_FILLING_FOK);
   else
      m_trade.SetTypeFilling(ORDER_FILLING_RETURN);

   // 4. Inicializar Handles de Indicadores conforme o Modo
   if (!InitIndicators())
   {
      Print("[RoboVolStyle] ERRO: Falha ao inicializar indicadores técnicos.");
      return INIT_FAILED;
   }

   // 5. Inicializar Estado Diário da Banca
   m_day_start_balance = AccountInfoDouble(ACCOUNT_BALANCE);
   m_current_day_start = GetDayStartTime();
   m_session_start_time = TimeCurrent();
   m_last_bar_time = 0;
   m_last_trade_bar = 0;
   SyncConsecutiveLosses();
   UpdateLiveTelemetry();

   // 6. Inicializar Interface Gráfica Moderna
   if (InpShowDashboard)
   {
      InitGUI();
      UpdateGUI();
   }

   bool ea_trade_allowed = (bool)MQLInfoInteger(MQL_TRADE_ALLOWED);
   bool term_trade_allowed = (bool)TerminalInfoInteger(TERMINAL_TRADE_ALLOWED);

   PrintFormat("[RoboVolStyle v1.31] Inicializado com sucesso! Ativo: %s | Timeframe: %s | Modo: %s | Saldo: $%.2f",
               m_symbol, EnumToString(m_timeframe), EnumToString(m_current_risk_mode), m_day_start_balance);
   PrintFormat("[RoboVolStyle] Volume Corretora: Passo=%.4f | Mín=%.4f | Máx=%.4f | Lote Operacional=%.2f",
               SymbolInfoDouble(m_symbol, SYMBOL_VOLUME_STEP),
               SymbolInfoDouble(m_symbol, SYMBOL_VOLUME_MIN),
               SymbolInfoDouble(m_symbol, SYMBOL_VOLUME_MAX),
               (InpLotCalcMode == LOT_FIXED_SIZE ? InpFixedLot : 0.01));
   PrintFormat("[RoboVolStyle] Permissões: EA_Trade_Allowed=%s | Terminal_AlgoTrading=%s | TP: $%.2f | SL: $%.2f",
               ea_trade_allowed ? "SIM" : "NÃO (Ative F7 -> Comum -> Permitir Negociação)",
               term_trade_allowed ? "SIM" : "NÃO (Ative AlgoTrading na barra)",
               InpAggTpDollars, InpAggSlDollars);

   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
//| Expert deinitialization function                                 |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   DestroyGUI();
   ReleaseIndicators();
   Comment(""); // Limpar tela do gráfico
   PrintFormat("[RoboVolStyle] Encerrado com código: %d", reason);
}

//+------------------------------------------------------------------+
//| ChartEvent function (Interação com Botões da Interface Gráfica)  |
//+------------------------------------------------------------------+
void OnChartEvent(const int id, const long &lparam, const double &dparam, const string &sparam)
{
   if (id == CHARTEVENT_OBJECT_CLICK)
   {
      if (sparam == "RVS_BTN_PLAY")
      {
         m_bot_active = !m_bot_active;
         ObjectSetInteger(0, "RVS_BTN_PLAY", OBJPROP_STATE, false);
         if (m_bot_active)
         {
            m_circuit_breaker_active = false;
            m_circuit_breaker_reason = "";
            m_consecutive_losses = 0;
            m_session_start_time = TimeCurrent();
            m_last_order_status = "Robô REINICIADO (Travas resetadas)";
            PrintFormat("[RoboVolStyle] Robô REINICIADO pelo usuário (Sessão limpa às %s).", TimeToString(m_session_start_time, TIME_SECONDS));
         }
         else
         {
            m_last_order_status = "Robô PAUSADO pelo botão!";
         }
         UpdateGUI();
         ChartRedraw(0);
      }
      else if (sparam == "RVS_BTN_CLOSE_ALL")
      {
         ObjectSetInteger(0, "RVS_BTN_CLOSE_ALL", OBJPROP_STATE, false);
         CloseAllPositions();
         m_last_order_status = "Posições encerradas pelo botão Pânico!";
         Print("[RoboVolStyle] Fechamento de emergência executado via botão no gráfico.");
         UpdateGUI();
         ChartRedraw(0);
      }
      else if (sparam == "RVS_BTN_MODE_AGG")
      {
         ObjectSetInteger(0, "RVS_BTN_MODE_AGG", OBJPROP_STATE, false);
         SwitchRiskMode(MODE_AGGRESSIVE);
         ChartRedraw(0);
      }
      else if (sparam == "RVS_BTN_MODE_MOD")
      {
         ObjectSetInteger(0, "RVS_BTN_MODE_MOD", OBJPROP_STATE, false);
         SwitchRiskMode(MODE_MODERATE);
         ChartRedraw(0);
      }
      else if (sparam == "RVS_BTN_MODE_CONS")
      {
         ObjectSetInteger(0, "RVS_BTN_MODE_CONS", OBJPROP_STATE, false);
         SwitchRiskMode(MODE_CONSERVATIVE);
         ChartRedraw(0);
      }
   }
}

//+------------------------------------------------------------------+
//| Expert tick function                                             |
//+------------------------------------------------------------------+
void OnTick()
{
   // 1. Atualizar informações do símbolo e saldo
   m_symbol_info.Refresh();
   CheckNewDayReset();

   // 2. Gerenciamento ativo de posições abertas (Trailing Stop & Break-Even)
   ManageOpenPositions();

   // 3. Atualizar telemetria em tempo real
   UpdateLiveTelemetry();

   // 4. Atualizar HUD Visual
   if (InpShowDashboard)
      UpdateGUI();

   // Se o robô foi pausado pelo usuário, interrompe aqui (não abre novas ordens)
   if (!m_bot_active)
      return;

   // Trava de segurança: se a negociação algorítmica estiver desligada no F7 ou no terminal, não tenta enviar ordens
   if (!MQLInfoInteger(MQL_TRADE_ALLOWED) || !TerminalInfoInteger(TERMINAL_TRADE_ALLOWED))
      return;

   // 5. Heartbeat periódico no Log dos Experts a cada 10 segundos
   static datetime last_heartbeat = 0;
   datetime now = TimeCurrent();
   if (now - last_heartbeat >= 10)
   {
      last_heartbeat = now;
      PrintFormat("[RoboVolStyle %s] Monitorando | Spread: %d pts | EMA8: %.2f | EMA21: %.2f | RSI: %.1f | %s",
                  EnumToString(m_timeframe),
                  (int)SymbolInfoInteger(m_symbol, SYMBOL_SPREAD),
                  m_live_ema_fast,
                  m_live_ema_slow,
                  m_live_rsi,
                  m_last_eval_status);
   }

   // 6. Verificar Circuit Breakers Globais (Stop Win, Stop Loss, Perdas)
   if (!ValidateGlobalRiskLimits())
      return;

   // 7. Filtro de Horário
   if (!IsWithinTradingHours())
      return;

   // 8. Filtro de Spread
   if (!ValidateSpread())
      return;

   // 9. Controle de Posições Simultâneas
   int open_count = CountOpenPositions();
   if (InpAllowOnlyOnePos && open_count >= 1)
      return;
   if (open_count >= InpMaxTotalPositions)
      return;

   // 10. Controle de Execução: Intra-bar (Scalping Instantâneo) ou Fechamento de Vela
   datetime current_bar_time = iTime(m_symbol, m_timeframe, 0);
   bool is_new_bar = IsNewBar();

   if (m_current_risk_mode == MODE_AGGRESSIVE)
   {
      // No modo agressivo, opera no tick se InpAggIntraBar for true, ou na nova vela
      if (!InpAggIntraBar && !is_new_bar)
         return;

      // Trava para evitar abrir mais de 1 operação dentro da mesma vela
      if (current_bar_time != 0 && current_bar_time == m_last_trade_bar && open_count >= 1)
         return;
   }
   else
   {
      // Nos modos Moderado e Conservador, aguarda o fechamento da vela
      if (!is_new_bar)
         return;
   }

   // 11. Processar Gatilho conforme o Modo de Risco Escolhido
   int signal = 0; // 1 = Compra, -1 = Venda, 0 = Neutro
   double sl_distance = 0.0;
   double tp_distance = 0.0;

   if (m_current_risk_mode == MODE_AGGRESSIVE)
      signal = EvaluateAggressiveMode(sl_distance, tp_distance);
   else if (m_current_risk_mode == MODE_MODERATE)
      signal = EvaluateModerateMode(sl_distance, tp_distance);
   else if (m_current_risk_mode == MODE_CONSERVATIVE)
      signal = EvaluateConservativeMode(sl_distance, tp_distance);

   // 12. Executar Ordem caso haja sinal
   if (signal != 0 && sl_distance > 0 && tp_distance > 0)
   {
      m_last_trade_bar = current_bar_time;
      ExecuteOrder(signal, sl_distance, tp_distance);
   }
   else if (is_new_bar && InpLogEveryCandle)
   {
      PrintFormat("[RoboVolStyle %s] Vela fechada | %s", EnumToString(m_timeframe), m_last_eval_status);
   }
}

//+------------------------------------------------------------------+
//| INICIALIZAÇÃO DE INDICADORES                                     |
//+------------------------------------------------------------------+
bool InitIndicators()
{
   ReleaseIndicators();

   if (m_current_risk_mode == MODE_AGGRESSIVE)
   {
      h_ema_fast = iMA(m_symbol, m_timeframe, InpAggEmaFast, 0, MODE_EMA, PRICE_CLOSE);
      h_ema_slow = iMA(m_symbol, m_timeframe, InpAggEmaSlow, 0, MODE_EMA, PRICE_CLOSE);
      h_rsi      = iRSI(m_symbol, m_timeframe, InpAggRsiPeriod, PRICE_CLOSE);
      h_atr      = iATR(m_symbol, m_timeframe, InpAggAtrPeriod);

      return (h_ema_fast != INVALID_HANDLE && h_ema_slow != INVALID_HANDLE &&
              h_rsi != INVALID_HANDLE && h_atr != INVALID_HANDLE);
   }
   else if (m_current_risk_mode == MODE_MODERATE)
   {
      h_ema_fast  = iMA(m_symbol, m_timeframe, InpModEmaFast, 0, MODE_EMA, PRICE_CLOSE);
      h_ema_slow  = iMA(m_symbol, m_timeframe, InpModEmaSlow, 0, MODE_EMA, PRICE_CLOSE);
      h_ema_trend = iMA(m_symbol, m_timeframe, InpModEmaTrend, 0, MODE_EMA, PRICE_CLOSE);
      h_rsi       = iRSI(m_symbol, m_timeframe, InpModRsiPeriod, PRICE_CLOSE);
      h_atr       = iATR(m_symbol, m_timeframe, InpModAtrPeriod);

      return (h_ema_fast != INVALID_HANDLE && h_ema_slow != INVALID_HANDLE &&
              h_ema_trend != INVALID_HANDLE && h_rsi != INVALID_HANDLE && h_atr != INVALID_HANDLE);
   }
   else // MODE_CONSERVATIVE
   {
      h_ema_fast  = iMA(m_symbol, m_timeframe, InpConsEmaFast, 0, MODE_EMA, PRICE_CLOSE);
      h_ema_slow  = iMA(m_symbol, m_timeframe, InpConsEmaMid, 0, MODE_EMA, PRICE_CLOSE);
      h_ema_trend = iMA(m_symbol, m_timeframe, InpConsEmaSlow, 0, MODE_EMA, PRICE_CLOSE);
      h_rsi       = iRSI(m_symbol, m_timeframe, InpConsRsiPeriod, PRICE_CLOSE);
      h_atr       = iATR(m_symbol, m_timeframe, InpConsAtrPeriod);

      return (h_ema_fast != INVALID_HANDLE && h_ema_slow != INVALID_HANDLE &&
              h_ema_trend != INVALID_HANDLE && h_rsi != INVALID_HANDLE && h_atr != INVALID_HANDLE);
   }
}

void ReleaseIndicators()
{
   if (h_ema_fast  != INVALID_HANDLE) { IndicatorRelease(h_ema_fast);  h_ema_fast  = INVALID_HANDLE; }
   if (h_ema_slow  != INVALID_HANDLE) { IndicatorRelease(h_ema_slow);  h_ema_slow  = INVALID_HANDLE; }
   if (h_ema_trend != INVALID_HANDLE) { IndicatorRelease(h_ema_trend); h_ema_trend = INVALID_HANDLE; }
   if (h_rsi       != INVALID_HANDLE) { IndicatorRelease(h_rsi);       h_rsi       = INVALID_HANDLE; }
   if (h_atr       != INVALID_HANDLE) { IndicatorRelease(h_atr);       h_atr       = INVALID_HANDLE; }
}

//+------------------------------------------------------------------+
//| DETECÇÃO DE NOVA VELA (ANTI-REPAINT)                            |
//+------------------------------------------------------------------+
bool IsNewBar()
{
   datetime current_bar_time = iTime(m_symbol, m_timeframe, 0);
   if (current_bar_time == 0)
      return false;

   if (current_bar_time != m_last_bar_time)
   {
      m_last_bar_time = current_bar_time;
      return true;
   }
   return false;
}

//+------------------------------------------------------------------+
//| ATUALIZAR TELEMETRIA DOS INDICADORES AO VIVO                     |
//+------------------------------------------------------------------+
void UpdateLiveTelemetry()
{
   if (h_ema_fast != INVALID_HANDLE)
   {
      double b[1];
      if (CopyBuffer(h_ema_fast, 0, 1, 1, b) > 0) m_live_ema_fast = b[0];
   }
   if (h_ema_slow != INVALID_HANDLE)
   {
      double b[1];
      if (CopyBuffer(h_ema_slow, 0, 1, 1, b) > 0) m_live_ema_slow = b[0];
   }
   if (h_ema_trend != INVALID_HANDLE)
   {
      double b[1];
      if (CopyBuffer(h_ema_trend, 0, 1, 1, b) > 0) m_live_ema_trend = b[0];
   }
   if (h_rsi != INVALID_HANDLE)
   {
      double b[1];
      if (CopyBuffer(h_rsi, 0, 1, 1, b) > 0) m_live_rsi = b[0];
   }
   if (h_atr != INVALID_HANDLE)
   {
      double b[1];
      if (CopyBuffer(h_atr, 0, 1, 1, b) > 0) m_live_atr = b[0];
   }
}

//+------------------------------------------------------------------+
//| 1. LÓGICA DO MODO AGRESSIVO (M1 MICRO-SCALPING RÁPIDO)           |
//+------------------------------------------------------------------+
int EvaluateAggressiveMode(double &out_sl_dist, double &out_tp_dist)
{
   double ema_f[3], ema_s[3], rsi[2], atr[25];
   if (CopyBuffer(h_ema_fast, 0, 1, 3, ema_f) < 3)
   {
      m_last_eval_status = "Carregando buffer da EMA rápida...";
      return 0;
   }
   if (CopyBuffer(h_ema_slow, 0, 1, 3, ema_s) < 3)
   {
      m_last_eval_status = "Carregando buffer da EMA lenta...";
      return 0;
   }
   if (CopyBuffer(h_rsi, 0, 1, 2, rsi) < 2)
   {
      m_last_eval_status = "Carregando buffer do RSI...";
      return 0;
   }
   if (CopyBuffer(h_atr, 0, 1, 25, atr) < 25)
   {
      m_last_eval_status = "Carregando buffer do ATR...";
      return 0;
   }

   // Indexação: 0 = vela 3 atras, 1 = vela 2 atras, 2 = vela 1 (fechada)
   double ef_now = ema_f[2]; double ef_prev = ema_f[1];
   double es_now = ema_s[2]; double es_prev = ema_s[1];
   double cur_rsi = rsi[1];
   double cur_atr = atr[24];

   // Média do ATR para filtro de volatilidade
   double atr_sum = 0.0;
   int atr_period = MathMin(InpAggAtrSmaPeriod, 25);
   for (int i = 25 - atr_period; i < 25; i++)
      atr_sum += atr[i];
   double atr_sma = (atr_period > 0) ? (atr_sum / atr_period) : cur_atr;

   // Atualizar variáveis de telemetria ao vivo
   m_live_ema_fast = ef_now;
   m_live_ema_slow = es_now;
   m_live_rsi      = cur_rsi;
   m_live_atr      = cur_atr;
   m_live_atr_sma  = atr_sma;

   // Filtro de volatilidade opcional
   if (InpAggRequireAtrFilter && cur_atr < (atr_sma * InpAggAtrThresholdMult))
   {
      m_last_eval_status = StringFormat("Pausa por baixa volatilidade (ATR %.2f < Limiar %.2f)", cur_atr, atr_sma * InpAggAtrThresholdMult);
      return 0;
   }

   // Preços das últimas velas fechadas e preços atuais
   double close1 = iClose(m_symbol, m_timeframe, 1);
   double open1  = iOpen(m_symbol, m_timeframe, 1);
   double high1  = iHigh(m_symbol, m_timeframe, 1);
   double low1   = iLow(m_symbol, m_timeframe, 1);

   double close2 = iClose(m_symbol, m_timeframe, 2);
   double high2  = iHigh(m_symbol, m_timeframe, 2);
   double low2   = iLow(m_symbol, m_timeframe, 2);

   double ask    = SymbolInfoDouble(m_symbol, SYMBOL_ASK);
   double bid    = SymbolInfoDouble(m_symbol, SYMBOL_BID);

   // Relação de tendência de curto prazo
   bool trend_bull = (ef_now > es_now);
   bool trend_bear = (ef_now < es_now);

   // Gatilhos de Compra
   bool cross_up          = (ef_prev <= es_prev && ef_now > es_now);
   bool breakout_tick_bull= InpAggIntraBar && trend_bull && (ask > high1) && (cur_rsi >= InpAggRsiBuyMin);
   bool breakout_bar_bull = InpAggAllowBreakout && trend_bull && (close1 > high2) && (cur_rsi >= InpAggRsiBuyMin);
   bool trend_cont_bull   = InpAggAllowTrendCont && trend_bull && (close1 > open1) && (close1 > ef_now) && (cur_rsi >= InpAggRsiBuyMin);
   bool pullback_bull     = InpAggAllowPullback && trend_bull && (low1 <= ef_now) && (close1 > ef_now) && (cur_rsi >= InpAggRsiBuyMin);

   // Gatilhos de Venda
   bool cross_down        = (ef_prev >= es_prev && ef_now < es_now);
   bool breakout_tick_bear= InpAggIntraBar && trend_bear && (bid < low1) && (cur_rsi <= InpAggRsiSellMax);
   bool breakout_bar_bear = InpAggAllowBreakout && trend_bear && (close1 < low2) && (cur_rsi <= InpAggRsiSellMax);
   bool trend_cont_bear   = InpAggAllowTrendCont && trend_bear && (close1 < open1) && (close1 < ef_now) && (cur_rsi <= InpAggRsiSellMax);
   bool pullback_bear     = InpAggAllowPullback && trend_bear && (high1 >= ef_now) && (close1 < ef_now) && (cur_rsi <= InpAggRsiSellMax);

   // Definição de Alvo e Stop Curto (Estilo Vídeo: $15 a $25 no BTC)
   if (!InpAggUseDynamicAtr)
   {
      out_sl_dist = InpAggSlDollars;
      out_tp_dist = InpAggTpDollars;
   }
   else
   {
      out_sl_dist = cur_atr * InpAggSlAtrMult;
      out_tp_dist = cur_atr * InpAggTpAtrMult;
   }

   // 1. Sinal de COMPRA
   if (cross_up && cur_rsi >= InpAggRsiBuyMin)
   {
      m_last_eval_status = StringFormat("COMPRA (Cruzamento EMA 8 > 21) | RSI: %.1f", cur_rsi);
      PrintFormat("[Agressivo M1] %s | SL: $%.2f | TP: $%.2f", m_last_eval_status, out_sl_dist, out_tp_dist);
      return 1;
   }
   if (breakout_tick_bull)
   {
      m_last_eval_status = StringFormat("COMPRA (Rompimento Tick > Máx Anterior) | Ask: %.2f > High1: %.2f | RSI: %.1f", ask, high1, cur_rsi);
      PrintFormat("[Agressivo M1] %s | SL: $%.2f | TP: $%.2f", m_last_eval_status, out_sl_dist, out_tp_dist);
      return 1;
   }
   if (breakout_bar_bull)
   {
      m_last_eval_status = StringFormat("COMPRA (Rompimento Vela) | Close1: %.2f > High2: %.2f | RSI: %.1f", close1, high2, cur_rsi);
      PrintFormat("[Agressivo M1] %s | SL: $%.2f | TP: $%.2f", m_last_eval_status, out_sl_dist, out_tp_dist);
      return 1;
   }
   if (trend_cont_bull)
   {
      m_last_eval_status = StringFormat("COMPRA (Continuação de Alta) | Close1: %.2f > EMA8: %.2f | RSI: %.1f", close1, ef_now, cur_rsi);
      PrintFormat("[Agressivo M1] %s | SL: $%.2f | TP: $%.2f", m_last_eval_status, out_sl_dist, out_tp_dist);
      return 1;
   }
   if (pullback_bull)
   {
      m_last_eval_status = StringFormat("COMPRA (Pullback EMA 8) | RSI: %.1f", cur_rsi);
      PrintFormat("[Agressivo M1] %s | SL: $%.2f | TP: $%.2f", m_last_eval_status, out_sl_dist, out_tp_dist);
      return 1;
   }

   // 2. Sinal de VENDA
   if (cross_down && cur_rsi <= InpAggRsiSellMax)
   {
      m_last_eval_status = StringFormat("VENDA (Cruzamento EMA 8 < 21) | RSI: %.1f", cur_rsi);
      PrintFormat("[Agressivo M1] %s | SL: $%.2f | TP: $%.2f", m_last_eval_status, out_sl_dist, out_tp_dist);
      return -1;
   }
   if (breakout_tick_bear)
   {
      m_last_eval_status = StringFormat("VENDA (Rompimento Tick < Mín Anterior) | Bid: %.2f < Low1: %.2f | RSI: %.1f", bid, low1, cur_rsi);
      PrintFormat("[Agressivo M1] %s | SL: $%.2f | TP: $%.2f", m_last_eval_status, out_sl_dist, out_tp_dist);
      return -1;
   }
   if (breakout_bar_bear)
   {
      m_last_eval_status = StringFormat("VENDA (Rompimento Vela) | Close1: %.2f < Low2: %.2f | RSI: %.1f", close1, low2, cur_rsi);
      PrintFormat("[Agressivo M1] %s | SL: $%.2f | TP: $%.2f", m_last_eval_status, out_sl_dist, out_tp_dist);
      return -1;
   }
   if (trend_cont_bear)
   {
      m_last_eval_status = StringFormat("VENDA (Continuação de Baixa) | Close1: %.2f < EMA8: %.2f | RSI: %.1f", close1, ef_now, cur_rsi);
      PrintFormat("[Agressivo M1] %s | SL: $%.2f | TP: $%.2f", m_last_eval_status, out_sl_dist, out_tp_dist);
      return -1;
   }
   if (pullback_bear)
   {
      m_last_eval_status = StringFormat("VENDA (Pullback EMA 8) | RSI: %.1f", cur_rsi);
      PrintFormat("[Agressivo M1] %s | SL: $%.2f | TP: $%.2f", m_last_eval_status, out_sl_dist, out_tp_dist);
      return -1;
   }

   // Diagnóstico quando neutro
   if (trend_bull)
      m_last_eval_status = StringFormat("Tendência Alta (EMA8 > 21) | RSI: %.1f | Aguardando Rompimento (Ask > %.2f)", cur_rsi, high1);
   else if (trend_bear)
      m_last_eval_status = StringFormat("Tendência Baixa (EMA8 < 21) | RSI: %.1f | Aguardando Rompimento (Bid < %.2f)", cur_rsi, low1);
   else
      m_last_eval_status = "Médias alinhadas / Mercado lateral";

   return 0;
}

//+------------------------------------------------------------------+
//| 2. LÓGICA DO MODO MODERADO (M5 TREND & BREAK-EVEN)               |
//+------------------------------------------------------------------+
int EvaluateModerateMode(double &out_sl_dist, double &out_tp_dist)
{
   double ema_f[3], ema_s[3], ema_t[2], rsi[2], atr[2];
   if (CopyBuffer(h_ema_fast, 0, 1, 3, ema_f) < 3) return 0;
   if (CopyBuffer(h_ema_slow, 0, 1, 3, ema_s) < 3) return 0;
   if (CopyBuffer(h_ema_trend, 0, 1, 2, ema_t) < 2) return 0;
   if (CopyBuffer(h_rsi, 0, 1, 2, rsi) < 2) return 0;
   if (CopyBuffer(h_atr, 0, 1, 2, atr) < 2) return 0;

   double ef_now = ema_f[2]; double ef_prev = ema_f[1];
   double es_now = ema_s[2]; double es_prev = ema_s[1];
   double trend_ema = ema_t[1];
   double cur_rsi = rsi[1];
   double cur_atr = atr[1];

   m_live_ema_fast  = ef_now;
   m_live_ema_slow  = es_now;
   m_live_ema_trend = trend_ema;
   m_live_rsi       = cur_rsi;
   m_live_atr       = cur_atr;

   double close_1 = iClose(m_symbol, m_timeframe, 1);

   bool cross_up   = (ef_prev <= es_prev && ef_now > es_now);
   bool cross_down = (ef_prev >= es_prev && ef_now < es_now);

   out_sl_dist = cur_atr * InpModSlAtrMult;
   out_tp_dist = out_sl_dist * InpModTpRrRatio;

   // Compra: EMA 9 cruza EMA 21 para cima + RSI entre 50 e 70 + Preço acima da EMA 50
   if (cross_up && cur_rsi >= InpModRsiBuyMin && cur_rsi <= InpModRsiBuyMax && close_1 > trend_ema)
   {
      m_last_eval_status = StringFormat("COMPRA MODERADA! EMA9 > EMA21 | Close > EMA50 | RSI: %.1f", cur_rsi);
      PrintFormat("[Moderado M5] %s | SL: %.2f pts", m_last_eval_status, out_sl_dist / _Point);
      return 1;
   }

   // Venda: EMA 9 cruza EMA 21 para baixo + RSI entre 30 e 50 + Preço abaixo da EMA 50
   if (cross_down && cur_rsi >= InpModRsiSellMin && cur_rsi <= InpModRsiSellMax && close_1 < trend_ema)
   {
      m_last_eval_status = StringFormat("VENDA MODERADA! EMA9 < EMA21 | Close < EMA50 | RSI: %.1f", cur_rsi);
      PrintFormat("[Moderado M5] %s | SL: %.2f pts", m_last_eval_status, out_sl_dist / _Point);
      return -1;
   }

   m_last_eval_status = StringFormat("Moderado M5: Close=%.2f | EMA50=%.2f | RSI=%.1f | Sem Crossover", close_1, trend_ema, cur_rsi);
   return 0;
}

//+------------------------------------------------------------------+
//| 3. LÓGICA DO MODO CONSERVADOR (M15 PROTEÇÃO & PAYOFF 1:2)        |
//+------------------------------------------------------------------+
int EvaluateConservativeMode(double &out_sl_dist, double &out_tp_dist)
{
   double ema_f[3], ema_m[3], ema_s[2], rsi[2], atr[2];
   if (CopyBuffer(h_ema_fast, 0, 1, 3, ema_f) < 3) return 0;
   if (CopyBuffer(h_ema_slow, 0, 1, 3, ema_m) < 3) return 0;
   if (CopyBuffer(h_ema_trend, 0, 1, 2, ema_s) < 2) return 0;
   if (CopyBuffer(h_rsi, 0, 1, 2, rsi) < 2) return 0;
   if (CopyBuffer(h_atr, 0, 1, 2, atr) < 2) return 0;

   double ef_now = ema_f[2]; double ef_prev = ema_f[1];
   double em_now = ema_m[2]; double em_prev = ema_m[1];
   double trend_ema = ema_s[1];
   double cur_rsi = rsi[1];
   double cur_atr = atr[1];

   m_live_ema_fast  = ef_now;
   m_live_ema_slow  = em_now;
   m_live_ema_trend = trend_ema;
   m_live_rsi       = cur_rsi;
   m_live_atr       = cur_atr;

   double close_1 = iClose(m_symbol, m_timeframe, 1);

   bool cross_up   = (ef_prev <= em_prev && ef_now > em_now);
   bool cross_down = (ef_prev >= em_prev && ef_now < em_now);

   // Confirmação de Swing Structure (Higher High para Compra, Lower Low para Venda)
   if (InpConsUseSwing)
   {
      double lowest_low = iLow(m_symbol, m_timeframe, 1);
      double highest_high = iHigh(m_symbol, m_timeframe, 1);
      for (int b = 2; b <= InpConsSwingBars; b++)
      {
         lowest_low = MathMin(lowest_low, iLow(m_symbol, m_timeframe, b));
         highest_high = MathMax(highest_high, iHigh(m_symbol, m_timeframe, b));
      }

      double swing_dist = (cross_up) ? (close_1 - lowest_low) : (highest_high - close_1);
      out_sl_dist = MathMax(swing_dist, cur_atr * InpConsSlAtrMult);
   }
   else
   {
      out_sl_dist = cur_atr * InpConsSlAtrMult;
   }

   // Payoff estritamente 1:2
   out_tp_dist = out_sl_dist * InpConsTpRrRatio;

   // Compra: Preço acima de EMA21 e EMA50 + Cruzamento EMA9 para cima + RSI entre 50 e 65
   if (close_1 > em_now && close_1 > trend_ema && cross_up)
   {
      if (cur_rsi >= InpConsRsiBuyMin && cur_rsi <= InpConsRsiBuyMax)
      {
         m_last_eval_status = StringFormat("COMPRA CONSERVADORA! Preço > EMAs | RSI: %.1f | Payoff 1:2", cur_rsi);
         PrintFormat("[Conservador M15] %s", m_last_eval_status);
         return 1;
      }
   }

   // Venda: Preço abaixo de EMA21 e EMA50 + Cruzamento EMA9 para baixo + RSI entre 35 e 50
   if (close_1 < em_now && close_1 < trend_ema && cross_down)
   {
      if (cur_rsi >= InpConsRsiSellMin && cur_rsi <= InpConsRsiSellMax)
      {
         m_last_eval_status = StringFormat("VENDA CONSERVADORA! Preço < EMAs | RSI: %.1f | Payoff 1:2", cur_rsi);
         PrintFormat("[Conservador M15] %s", m_last_eval_status);
         return -1;
      }
   }

   m_last_eval_status = StringFormat("Conservador M15: Preço=%.2f | EMAs=%.2f/%.2f | RSI=%.1f | Sem Sinal", close_1, em_now, trend_ema, cur_rsi);
   return 0;
}

//+------------------------------------------------------------------+
//| NORMALIZADOR UNIVERSAL DE LOTE CONFORME REGRAS DA CORRETORA       |
//+------------------------------------------------------------------+
double NormalizeVolume(double lot)
{
   double lot_step = SymbolInfoDouble(m_symbol, SYMBOL_VOLUME_STEP);
   double min_lot  = SymbolInfoDouble(m_symbol, SYMBOL_VOLUME_MIN);
   double max_lot  = SymbolInfoDouble(m_symbol, SYMBOL_VOLUME_MAX);

   if (lot_step <= 0.0) lot_step = 0.01;
   if (min_lot <= 0.0)  min_lot = 0.01;
   if (max_lot <= 0.0)  max_lot = 100.0;

   // Respeitar limites do usuário se configurados
   if (InpMinLot > min_lot) min_lot = InpMinLot;
   if (InpMaxLot > 0.0 && InpMaxLot < max_lot) max_lot = InpMaxLot;

   // 1. Arredondar estritamente para o múltiplo de volume step da corretora
   lot = MathRound(lot / lot_step) * lot_step;

   // 2. Limitar entre o mínimo e máximo permitido pela corretora
   if (lot < min_lot) lot = min_lot;
   if (lot > max_lot) lot = max_lot;

   // 3. Determinar o número exato de casas decimais a partir do passo
   int vol_digits = 0;
   if (lot_step < 1.0)
   {
      double temp_step = lot_step;
      while (temp_step < 1.0 && vol_digits < 6)
      {
         temp_step *= 10.0;
         vol_digits++;
      }
   }
   if (vol_digits < 2) vol_digits = 2; // Garantir no mínimo 2 casas decimais para CFDs

   return NormalizeDouble(lot, vol_digits);
}

//+------------------------------------------------------------------+
//| CÁLCULO DE LOTE E DIMENSIONAMENTO DE RISCO                       |
//+------------------------------------------------------------------+
double CalculateLotSize(double sl_distance_price, int signal_type = 1)
{
   double raw_lot = InpFixedLot;

   if (InpLotCalcMode == LOT_FIXED_SIZE)
   {
      raw_lot = InpFixedLot;
      if (InpEnableRecovery && m_current_risk_mode != MODE_CONSERVATIVE)
      {
         raw_lot = ApplyRecoveryMultiplier(InpFixedLot);
      }
   }
   else
   {
      double balance = AccountInfoDouble(ACCOUNT_BALANCE);
      double risk_usd = balance * (InpRiskPercent / 100.0);

      double tick_value = SymbolInfoDouble(m_symbol, SYMBOL_TRADE_TICK_VALUE);
      double tick_size  = SymbolInfoDouble(m_symbol, SYMBOL_TRADE_TICK_SIZE);
      double point      = SymbolInfoDouble(m_symbol, SYMBOL_POINT);

      if (point <= 0 || tick_size <= 0 || tick_value <= 0 || sl_distance_price <= 0)
      {
         raw_lot = InpMinLot;
      }
      else
      {
         double points_risk = sl_distance_price / point;
         double value_per_point = tick_value * (point / tick_size);
         double risk_per_lot = points_risk * value_per_point;

         if (risk_per_lot <= 0)
            raw_lot = InpMinLot;
         else
         {
            raw_lot = risk_usd / risk_per_lot;
            if (m_current_risk_mode != MODE_CONSERVATIVE)
            {
               raw_lot = ApplyRecoveryMultiplier(raw_lot);
            }
         }
      }
   }

   // Normalização rigorosa de lote pelos limites e passo da corretora
   double calculated_lot = NormalizeVolume(raw_lot);

   // Proteção de margem: verificar se há margem livre suficiente para o lote calculado
   ENUM_ORDER_TYPE otype = (signal_type == -1) ? ORDER_TYPE_SELL : ORDER_TYPE_BUY;
   double chk_price = (signal_type == -1) ? SymbolInfoDouble(m_symbol, SYMBOL_BID) : SymbolInfoDouble(m_symbol, SYMBOL_ASK);
   double margin_req = 0.0;
   if (OrderCalcMargin(otype, m_symbol, calculated_lot, chk_price, margin_req))
   {
      double free_margin = AccountInfoDouble(ACCOUNT_MARGIN_FREE);
      if (margin_req > free_margin * 0.7 && margin_req > 0)
      {
         double safe_factor = (free_margin * 0.7) / margin_req;
         calculated_lot = NormalizeVolume(calculated_lot * safe_factor);
      }
   }

   return NormalizeVolume(calculated_lot);
}

//+------------------------------------------------------------------+
//| APLICAR MULTIPLICADOR DE RECUPERAÇÃO SUAVE (MÁX 2 NÍVEIS)        |
//+------------------------------------------------------------------+
double ApplyRecoveryMultiplier(double base_lot)
{
   if (!InpEnableRecovery || m_current_risk_mode == MODE_CONSERVATIVE)
   {
      m_recovery_level = 0;
      return base_lot;
   }

   double mult = 1.0;
   if (m_current_risk_mode == MODE_AGGRESSIVE)
   {
      if (m_consecutive_losses == 1) mult = InpAggMultLevel1;
      else if (m_consecutive_losses == 2) mult = InpAggMultLevel2;
   }
   else if (m_current_risk_mode == MODE_MODERATE)
   {
      if (m_consecutive_losses == 1) mult = InpModMultLevel1;
      else if (m_consecutive_losses == 2) mult = InpModMultLevel2;
   }

   m_recovery_level = (m_consecutive_losses > 0 && m_consecutive_losses <= 2) ? m_consecutive_losses : 0;
   return base_lot * mult;
}

//+------------------------------------------------------------------+
//| EXECUÇÃO DE ORDENS                                               |
//+------------------------------------------------------------------+
void ExecuteOrder(int signal, double sl_distance_price, double tp_distance_price)
{
   double ask = SymbolInfoDouble(m_symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(m_symbol, SYMBOL_BID);
   int digits = (int)SymbolInfoInteger(m_symbol, SYMBOL_DIGITS);

   double lot = CalculateLotSize(sl_distance_price, signal);
   lot = NormalizeVolume(lot); // Garantia absoluta de volume válido

   if (lot <= 0)
   {
      m_last_order_status = "Lote inválido calculado (<= 0)";
      Print("[RoboVolStyle] Lote inválido calculado: ", lot);
      return;
   }

   // Respeitar Stop Level da corretora
   long stop_level_pts = SymbolInfoInteger(m_symbol, SYMBOL_TRADE_STOPS_LEVEL);
   double stop_level = stop_level_pts * _Point;

   if (sl_distance_price < stop_level) sl_distance_price = stop_level + (10 * _Point);
   if (tp_distance_price < stop_level) tp_distance_price = stop_level + (10 * _Point);

   if (signal == 1) // COMPRA
   {
      double sl_price = NormalizeDouble(ask - sl_distance_price, digits);
      double tp_price = NormalizeDouble(ask + tp_distance_price, digits);

      // Usando price = 0.0 para execução a mercado instantânea (evita requotes)
      bool res = m_trade.Buy(lot, m_symbol, 0.0, sl_price, tp_price, InpTradeComment);
      if (res)
      {
         m_last_order_status = StringFormat("COMPRA %.2f EXECUTADA @ %.*f | TP: +$%.2f", lot, digits, ask, tp_distance_price);
         PrintFormat("[RoboVolStyle] COMPRA EXECUTADA! Lote: %.2f | Ask: %.*f | SL: %.*f (Dist $%.2f) | TP: %.*f (Dist $%.2f)",
                     lot, digits, ask, digits, sl_price, sl_distance_price, digits, tp_price, tp_distance_price);
      }
      else
      {
         m_last_order_status = StringFormat("ERRO COMPRA: retcode=%u (%s) | Lote: %.2f",
                                            m_trade.ResultRetcode(), m_trade.ResultRetcodeDescription(), lot);
         PrintFormat("[RoboVolStyle] ERRO ao comprar: retcode=%u (%s) | FreeMargin: $%.2f",
                     m_trade.ResultRetcode(), m_trade.ResultRetcodeDescription(), AccountInfoDouble(ACCOUNT_MARGIN_FREE));
      }
   }
   else if (signal == -1) // VENDA
   {
      double sl_price = NormalizeDouble(bid + sl_distance_price, digits);
      double tp_price = NormalizeDouble(bid - tp_distance_price, digits);

      // Usando price = 0.0 para execução a mercado instantânea (evita requotes)
      bool res = m_trade.Sell(lot, m_symbol, 0.0, sl_price, tp_price, InpTradeComment);
      if (res)
      {
         m_last_order_status = StringFormat("VENDA %.2f EXECUTADA @ %.*f | TP: +$%.2f", lot, digits, bid, tp_distance_price);
         PrintFormat("[RoboVolStyle] VENDA EXECUTADA! Lote: %.2f | Bid: %.*f | SL: %.*f (Dist $%.2f) | TP: %.*f (Dist $%.2f)",
                     lot, digits, bid, digits, sl_price, sl_distance_price, digits, tp_price, tp_distance_price);
      }
      else
      {
         m_last_order_status = StringFormat("ERRO VENDA: retcode=%u (%s) | Lote: %.2f",
                                            m_trade.ResultRetcode(), m_trade.ResultRetcodeDescription(), lot);
         PrintFormat("[RoboVolStyle] ERRO ao vender: retcode=%u (%s) | FreeMargin: $%.2f",
                     m_trade.ResultRetcode(), m_trade.ResultRetcodeDescription(), AccountInfoDouble(ACCOUNT_MARGIN_FREE));
      }
   }
}

//+------------------------------------------------------------------+
//| GERENCIAMENTO ATIVO DE POSIÇÕES (TRAILING STOP E BREAK-EVEN)     |
//+------------------------------------------------------------------+
void ManageOpenPositions()
{
   int digits = (int)SymbolInfoInteger(m_symbol, SYMBOL_DIGITS);
   double point = SymbolInfoDouble(m_symbol, SYMBOL_POINT);
   double ask = SymbolInfoDouble(m_symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(m_symbol, SYMBOL_BID);

   for (int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if (ticket == 0) continue;

      if (!m_position.SelectByTicket(ticket)) continue;
      if (m_position.Symbol() != m_symbol || m_position.Magic() != InpMagicNumber) continue;

      ENUM_POSITION_TYPE type = m_position.PositionType();
      double open_price = m_position.PriceOpen();
      double current_sl = m_position.StopLoss();
      double current_tp = m_position.TakeProfit();

      double risk_price_dist = MathAbs(open_price - current_sl);
      if (risk_price_dist <= 0) continue;

      // 1. BREAK-EVEN (MODO MODERADO)
      if (m_current_risk_mode == MODE_MODERATE && InpModUseBreakEven)
      {
         double be_buffer = InpModBreakEvenBuffer * point;
         if (type == POSITION_TYPE_BUY)
         {
            double gain = bid - open_price;
            if (gain >= risk_price_dist * InpModBreakEvenTriggerRr)
            {
               double new_sl = NormalizeDouble(open_price + be_buffer, digits);
               if (current_sl < open_price)
               {
                  m_trade.PositionModify(ticket, new_sl, current_tp);
                  PrintFormat("[Moderado] BREAK-EVEN ATIVADO para compra #%I64u! Novo SL: %.*f", ticket, digits, new_sl);
               }
            }
         }
         else if (type == POSITION_TYPE_SELL)
         {
            double gain = open_price - ask;
            if (gain >= risk_price_dist * InpModBreakEvenTriggerRr)
            {
               double new_sl = NormalizeDouble(open_price - be_buffer, digits);
               if (current_sl > open_price || current_sl == 0)
               {
                  m_trade.PositionModify(ticket, new_sl, current_tp);
                  PrintFormat("[Moderado] BREAK-EVEN ATIVADO para venda #%I64u! Novo SL: %.*f", ticket, digits, new_sl);
               }
            }
         }
      }

      // 2. TRAILING STOP RÁPIDO DO MODO AGRESSIVO (PROTEÇÃO CURTA ESTILO VÍDEO)
      if (m_current_risk_mode == MODE_AGGRESSIVE && InpAggUseTrailing)
      {
         double trigger_dist = InpAggTrailingStartDl;
         double trail_buffer = InpAggTrailingStepDl;

         if (type == POSITION_TYPE_BUY)
         {
            double gain = bid - open_price;
            if (gain >= trigger_dist)
            {
               double proposed_sl = NormalizeDouble(bid - trail_buffer, digits);
               if (proposed_sl > current_sl + (1.0 * _Point) && proposed_sl > open_price)
               {
                  m_trade.PositionModify(ticket, proposed_sl, current_tp);
                  PrintFormat("[Agressivo] TRAILING STOP RÁPIDO protegeu lucro da compra #%I64u! Novo SL: %.*f (Ganho: +$%.2f)",
                              ticket, digits, proposed_sl, gain);
               }
            }
         }
         else if (type == POSITION_TYPE_SELL)
         {
            double gain = open_price - ask;
            if (gain >= trigger_dist)
            {
               double proposed_sl = NormalizeDouble(ask + trail_buffer, digits);
               if (current_sl == 0 || (proposed_sl < current_sl - (1.0 * _Point) && proposed_sl < open_price))
               {
                  m_trade.PositionModify(ticket, proposed_sl, current_tp);
                  PrintFormat("[Agressivo] TRAILING STOP RÁPIDO protegeu lucro da venda #%I64u! Novo SL: %.*f (Ganho: +$%.2f)",
                              ticket, digits, proposed_sl, gain);
               }
            }
         }
      }
   }
}

//+------------------------------------------------------------------+
//| GERENCIADOR DE RISCO GLOBAL E CIRCUIT BREAKERS                   |
//+------------------------------------------------------------------+
bool ValidateGlobalRiskLimits()
{
   if (m_circuit_breaker_active)
      return false;

   double daily_pnl = GetDailyRealizedPnL();
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   double ref_balance = (m_day_start_balance > 0) ? m_day_start_balance : balance;

   // 1. Stop Loss Diário Global
   double max_daily_loss = ref_balance * (InpDailyStopLossPct / 100.0);
   if (daily_pnl <= -max_daily_loss)
   {
      m_circuit_breaker_active = true;
      m_circuit_breaker_reason = StringFormat("STOP LOSS DIÁRIO ATINGIDO: -$%.2f (Máx -$%.2f)", MathAbs(daily_pnl), max_daily_loss);
      PrintFormat("[CIRCUIT BREAKER] %s. Fechando todas as posições!", m_circuit_breaker_reason);
      CloseAllPositions();
      return false;
   }

   // 2. Stop Win Diário Global
   double daily_win_target = ref_balance * (InpDailyStopWinPct / 100.0);
   if (daily_pnl >= daily_win_target)
   {
      m_circuit_breaker_active = true;
      m_circuit_breaker_reason = StringFormat("STOP WIN DIÁRIO ATINGIDO: +$%.2f (Meta +$%.2f)", daily_pnl, daily_win_target);
      PrintFormat("[CIRCUIT BREAKER] %s. Parando operações pelo dia!", m_circuit_breaker_reason);
      return false;
   }

   // 3. Trava de Sequência de Derrotas Consecutivas
   if (InpMaxConsecutiveLoss > 0)
   {
      SyncConsecutiveLosses();
      if (m_consecutive_losses >= InpMaxConsecutiveLoss)
      {
         m_circuit_breaker_reason = StringFormat("PAUSA: %d DERROTAS (Clique Iniciar p/ resetar)", m_consecutive_losses);
         return false;
      }
   }

   return true;
}

//+------------------------------------------------------------------+
//| CÁLCULO DO PNL DIÁRIO REALIZADO                                  |
//+------------------------------------------------------------------+
double GetDailyRealizedPnL()
{
   datetime start_time = GetDayStartTime();
   if (!HistorySelect(start_time, TimeCurrent()))
      return 0.0;

   double total_pnl = 0.0;
   int deals = HistoryDealsTotal();
   for (int i = 0; i < deals; i++)
   {
      ulong ticket = HistoryDealGetTicket(i);
      if (ticket == 0) continue;

      if (HistoryDealGetInteger(ticket, DEAL_MAGIC) != InpMagicNumber)
         continue;

      long entry = HistoryDealGetInteger(ticket, DEAL_ENTRY);
      if (entry == DEAL_ENTRY_OUT || entry == DEAL_ENTRY_INOUT)
      {
         total_pnl += HistoryDealGetDouble(ticket, DEAL_PROFIT)
                    + HistoryDealGetDouble(ticket, DEAL_SWAP)
                    + HistoryDealGetDouble(ticket, DEAL_COMMISSION);
      }
   }
   return total_pnl;
}

//+------------------------------------------------------------------+
//| SINCRONIZAR SEQUÊNCIA DE DERROTAS CONSECUTIVAS                   |
//+------------------------------------------------------------------+
void SyncConsecutiveLosses()
{
   if (InpMaxConsecutiveLoss <= 0)
   {
      m_consecutive_losses = 0;
      return;
   }

   datetime start_time = MathMax(GetDayStartTime(), m_session_start_time);
   if (!HistorySelect(start_time, TimeCurrent()))
      return;

   int count = 0;
   int deals = HistoryDealsTotal();
   for (int i = deals - 1; i >= 0; i--)
   {
      ulong ticket = HistoryDealGetTicket(i);
      if (ticket == 0) continue;

      if (HistoryDealGetInteger(ticket, DEAL_MAGIC) != InpMagicNumber)
         continue;

      long entry = HistoryDealGetInteger(ticket, DEAL_ENTRY);
      if (entry == DEAL_ENTRY_OUT || entry == DEAL_ENTRY_INOUT)
      {
         double profit = HistoryDealGetDouble(ticket, DEAL_PROFIT);
         if (profit < 0)
            count++;
         else if (profit > 0)
            break; // Vitória reseta a contagem
      }
   }
   m_consecutive_losses = count;
}

//+------------------------------------------------------------------+
//| FECHAR TODAS AS POSIÇÕES DESTE ROBÔ                              |
//+------------------------------------------------------------------+
void CloseAllPositions()
{
   for (int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if (ticket > 0 && m_position.SelectByTicket(ticket))
      {
         if (m_position.Symbol() == m_symbol && m_position.Magic() == InpMagicNumber)
         {
            m_trade.PositionClose(ticket);
         }
      }
   }
}

//+------------------------------------------------------------------+
//| VALIDAÇÃO DE SPREAD DINÂMICO                                     |
//+------------------------------------------------------------------+
bool ValidateSpread()
{
   long spread = SymbolInfoInteger(m_symbol, SYMBOL_SPREAD);
   long max_spread = InpMaxSpreadDefPts;

   string upper = m_symbol;
   StringToUpper(upper);

   if (StringFind(upper, "BTC") >= 0)
      max_spread = InpMaxSpreadBtcPts;
   else if (StringFind(upper, "XAU") >= 0 || StringFind(upper, "GOLD") >= 0)
      max_spread = InpMaxSpreadXauPts;

   if (spread > max_spread)
   {
      return false;
   }
   return true;
}

//+------------------------------------------------------------------+
//| FILTRO DE HORÁRIO DE OPERAÇÃO                                    |
//+------------------------------------------------------------------+
bool IsWithinTradingHours()
{
   if (!InpUseTimeFilter)
      return true;

   MqlDateTime dt;
   TimeCurrent(dt);

   // Proteção para fechar e pausar na sexta-feira à noite
   if (InpCloseFriday && dt.day_of_week == 5)
   {
      if (dt.hour >= InpFridayCloseHour)
      {
         CloseAllPositions();
         return false;
      }
   }

   int current_minutes = dt.hour * 60 + dt.min;
   int start_minutes   = InpStartHour * 60 + InpStartMinute;
   int end_minutes     = InpEndHour * 60 + InpEndMinute;

   if (start_minutes <= end_minutes)
   {
      return (current_minutes >= start_minutes && current_minutes <= end_minutes);
   }
   else
   {
      return (current_minutes >= start_minutes || current_minutes <= end_minutes);
   }
}

//+------------------------------------------------------------------+
//| CONTAGEM DE POSIÇÕES ABERTAS DESTE ROBÔ                          |
//+------------------------------------------------------------------+
int CountOpenPositions()
{
   int count = 0;
   for (int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if (ticket > 0 && m_position.SelectByTicket(ticket))
      {
         if (m_position.Symbol() == m_symbol && m_position.Magic() == InpMagicNumber)
            count++;
      }
   }
   return count;
}

//+------------------------------------------------------------------+
//| REINÍCIO DO SALDO BASE NO INÍCIO DE NOVO DIA                     |
//+------------------------------------------------------------------+
void CheckNewDayReset()
{
   datetime day_start = GetDayStartTime();
   if (day_start != m_current_day_start)
   {
      m_current_day_start = day_start;
      m_day_start_balance = AccountInfoDouble(ACCOUNT_BALANCE);
      m_circuit_breaker_active = false;
      m_circuit_breaker_reason = "";
      m_consecutive_losses = 0;
      m_recovery_level = 0;
      PrintFormat("[RoboVolStyle] Novo dia iniciado (%s). Saldo base resetado para: $%.2f",
                  TimeToString(day_start, TIME_DATE), m_day_start_balance);
   }
}

//+------------------------------------------------------------------+
//| RETORNA TIMESTAMP DE INÍCIO DO DIA ATUAL                         |
//+------------------------------------------------------------------+
datetime GetDayStartTime()
{
   MqlDateTime dt;
   TimeCurrent(dt);
   dt.hour = 0;
   dt.min = 0;
   dt.sec = 0;
   return StructToTime(dt);
}

//+------------------------------------------------------------------+
//| GERENCIADOR DE MODOS DINÂMICOS                                    |
//+------------------------------------------------------------------+
void SwitchRiskMode(ENUM_RISK_MODE new_mode)
{
   if (m_current_risk_mode == new_mode) return;

   m_current_risk_mode = new_mode;

   if (m_current_risk_mode == MODE_AGGRESSIVE)
      m_timeframe = InpAggTimeframe;
   else if (m_current_risk_mode == MODE_MODERATE)
      m_timeframe = InpModTimeframe;
   else
      m_timeframe = InpConsTimeframe;

   InitIndicators();
   UpdateLiveTelemetry();
   m_last_trade_bar = 0;
   m_last_order_status = StringFormat("Modo alterado para %s", EnumToString(m_current_risk_mode));
   PrintFormat("[RoboVolStyle] Modo alterado pelo usuário para: %s | Timeframe: %s",
               EnumToString(m_current_risk_mode), EnumToString(m_timeframe));
   UpdateGUI();
}

//+------------------------------------------------------------------+
//| CRIAÇÃO E CONTROLE DA INTERFACE GRÁFICA MODERNA                  |
//+------------------------------------------------------------------+
void GuiCreateRect(string name, int x, int y, int w, int h, color bg, color border)
{
   ObjectDelete(0, name);
   ObjectCreate(0, name, OBJ_RECTANGLE_LABEL, 0, 0, 0);
   ObjectSetInteger(0, name, OBJPROP_CORNER, CORNER_LEFT_UPPER);
   ObjectSetInteger(0, name, OBJPROP_XDISTANCE, x);
   ObjectSetInteger(0, name, OBJPROP_YDISTANCE, y);
   ObjectSetInteger(0, name, OBJPROP_XSIZE, w);
   ObjectSetInteger(0, name, OBJPROP_YSIZE, h);
   ObjectSetInteger(0, name, OBJPROP_BGCOLOR, bg);
   ObjectSetInteger(0, name, OBJPROP_BORDER_COLOR, border);
   ObjectSetInteger(0, name, OBJPROP_BORDER_TYPE, BORDER_FLAT);
   ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, name, OBJPROP_ZORDER, 1);
}

void GuiCreateButton(string name, string text, int x, int y, int w, int h, color bg, color txt_color, int fsize = 9)
{
   ObjectDelete(0, name);
   ObjectCreate(0, name, OBJ_BUTTON, 0, 0, 0);
   ObjectSetInteger(0, name, OBJPROP_CORNER, CORNER_LEFT_UPPER);
   ObjectSetInteger(0, name, OBJPROP_XDISTANCE, x);
   ObjectSetInteger(0, name, OBJPROP_YDISTANCE, y);
   ObjectSetInteger(0, name, OBJPROP_XSIZE, w);
   ObjectSetInteger(0, name, OBJPROP_YSIZE, h);
   ObjectSetString(0, name, OBJPROP_TEXT, text);
   ObjectSetString(0, name, OBJPROP_FONT, "Segoe UI");
   ObjectSetInteger(0, name, OBJPROP_FONTSIZE, fsize);
   ObjectSetInteger(0, name, OBJPROP_COLOR, txt_color);
   ObjectSetInteger(0, name, OBJPROP_BGCOLOR, bg);
   ObjectSetInteger(0, name, OBJPROP_BORDER_COLOR, bg);
   ObjectSetInteger(0, name, OBJPROP_STATE, false);
   ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, name, OBJPROP_ZORDER, 15);
}

void GuiCreateLabel(string name, string text, int x, int y, color txt_color, int fsize = 8, bool bold = false)
{
   ObjectDelete(0, name);
   ObjectCreate(0, name, OBJ_LABEL, 0, 0, 0);
   ObjectSetInteger(0, name, OBJPROP_CORNER, CORNER_LEFT_UPPER);
   ObjectSetInteger(0, name, OBJPROP_XDISTANCE, x);
   ObjectSetInteger(0, name, OBJPROP_YDISTANCE, y);
   ObjectSetString(0, name, OBJPROP_TEXT, text);
   ObjectSetString(0, name, OBJPROP_FONT, bold ? "Segoe UI Bold" : "Segoe UI");
   ObjectSetInteger(0, name, OBJPROP_FONTSIZE, fsize);
   ObjectSetInteger(0, name, OBJPROP_COLOR, txt_color);
   ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, name, OBJPROP_ZORDER, 10);
}

void GuiSetText(string name, string text, color txt_color = clrNONE)
{
   ObjectSetString(0, name, OBJPROP_TEXT, text);
   if (txt_color != clrNONE)
      ObjectSetInteger(0, name, OBJPROP_COLOR, txt_color);
}

void DestroyGUI()
{
   ObjectsDeleteAll(0, "RVS_");
   Comment("");
   ChartRedraw(0);
}

void InitGUI()
{
   DestroyGUI();
   if (!InpShowDashboard) return;

   int px = 20;
   int py = 25;
   int pw = 360;

   // 1. Fundo Principal (Card Tecnológico Slate Escuro)
   GuiCreateRect("RVS_BG_MAIN", px, py, pw, 345, C'15, 23, 42', C'30, 41, 59');

   // 2. Faixa do Cabeçalho
   GuiCreateRect("RVS_BG_HEAD", px, py, pw, 44, C'22, 30, 49', C'30, 41, 59');
   GuiCreateLabel("RVS_TXT_TITLE", "⚡ QUANT SCALPER v1.31", px + 14, py + 7, C'56, 189, 248', 11, true);
   GuiCreateLabel("RVS_SUBTITLE", "BTCUSD · Alta Frequência", px + 14, py + 26, C'148, 163, 184', 8, false);

   // 3. Botões de Ação (Iniciar/Pausar e Fechar Tudo)
   GuiCreateButton("RVS_BTN_PLAY", "⏸️ PAUSAR ROBÔ", px + 10, py + 52, 165, 32, C'185, 28, 28', clrWhite, 9);
   GuiCreateButton("RVS_BTN_CLOSE_ALL", "❌ FECHAR TUDO", px + 185, py + 52, 165, 32, C'30, 41, 59', C'248, 113, 113', 9);

   // 4. Seletores de Modo (3 Abas Clicáveis no Gráfico)
   GuiCreateButton("RVS_BTN_MODE_AGG", "🔥 AGRESSIVO", px + 10, py + 92, 110, 26, C'14, 116, 144', clrWhite, 8);
   GuiCreateButton("RVS_BTN_MODE_MOD", "⚖️ MODERADO", px + 125, py + 92, 110, 26, C'30, 41, 59', C'148, 163, 184', 8);
   GuiCreateButton("RVS_BTN_MODE_CONS", "🛡️ CONSERV.", px + 240, py + 92, 110, 26, C'30, 41, 59', C'148, 163, 184', 8);

   // 5. Container de Métricas
   GuiCreateRect("RVS_BG_BOX", px + 10, py + 126, 340, 208, C'18, 26, 44', C'30, 41, 59');

   // Linhas de Métricas
   GuiCreateLabel("RVS_LBL_STATUS_T", "ESTADO:", px + 18, py + 134, C'148, 163, 184', 8, false);
   GuiCreateLabel("RVS_VAL_STATUS", "🟢 OPERANDO NORMAL", px + 115, py + 134, C'52, 211, 153', 8, true);

   GuiCreateLabel("RVS_LBL_PNL_T", "LUCRO HOJE:", px + 18, py + 158, C'148, 163, 184', 8, false);
   GuiCreateLabel("RVS_VAL_PNL", "+$0.00 (+0.00%)", px + 115, py + 158, C'52, 211, 153', 9, true);

   GuiCreateLabel("RVS_LBL_BAL_T", "SALDO BANCA:", px + 18, py + 182, C'148, 163, 184', 8, false);
   GuiCreateLabel("RVS_VAL_BAL", "$0.00 (Eq: $0.00)", px + 115, py + 182, clrWhite, 8, false);

   GuiCreateLabel("RVS_LBL_TARGETS_T", "METAS DIÁRIAS:", px + 18, py + 206, C'148, 163, 184', 8, false);
   GuiCreateLabel("RVS_VAL_TARGETS", "Win: +$0 | Loss: -$0", px + 115, py + 206, C'226, 232, 240', 8, false);

   GuiCreateLabel("RVS_LBL_POS_T", "OPERAÇÕES:", px + 18, py + 230, C'148, 163, 184', 8, false);
   GuiCreateLabel("RVS_VAL_POS", "0 / 2 Abertas · Lote: 0.01", px + 115, py + 230, C'226, 232, 240', 8, false);

   GuiCreateLabel("RVS_LBL_SIGNAL_T", "TELEMETRIA:", px + 18, py + 254, C'148, 163, 184', 8, false);
   GuiCreateLabel("RVS_VAL_SIGNAL", "Spread: 0 | RSI: 0.0", px + 115, py + 254, C'56, 189, 248', 8, false);

   GuiCreateLabel("RVS_LBL_RADAR_T", "DIAGNÓSTICO:", px + 18, py + 278, C'148, 163, 184', 8, false);
   GuiCreateLabel("RVS_VAL_RADAR", "Aguardando rompimento...", px + 115, py + 278, C'203, 213, 225', 8, false);

   GuiCreateLabel("RVS_LBL_LAST_T", "ÚLTIMO ENVIO:", px + 18, py + 302, C'148, 163, 184', 8, false);
   GuiCreateLabel("RVS_VAL_LAST", "Pronto para operar", px + 115, py + 302, C'251, 191, 36', 8, false);

   Comment(""); // Limpar qualquer resíduo de Comment no gráfico
   ChartRedraw(0);
}

void UpdateGUI()
{
   if (!InpShowDashboard) return;

   // 1. Atualizar botão Play/Pause
   if (m_bot_active)
   {
      ObjectSetString(0, "RVS_BTN_PLAY", OBJPROP_TEXT, "⏸️ PAUSAR ROBÔ");
      ObjectSetInteger(0, "RVS_BTN_PLAY", OBJPROP_BGCOLOR, C'185, 28, 28');
      ObjectSetInteger(0, "RVS_BTN_PLAY", OBJPROP_BORDER_COLOR, C'239, 68, 68');
   }
   else
   {
      ObjectSetString(0, "RVS_BTN_PLAY", OBJPROP_TEXT, "▶️ INICIAR ROBÔ");
      ObjectSetInteger(0, "RVS_BTN_PLAY", OBJPROP_BGCOLOR, C'5, 150, 105');
      ObjectSetInteger(0, "RVS_BTN_PLAY", OBJPROP_BORDER_COLOR, C'16, 185, 129');
   }

   // 2. Destacar aba do modo ativo
   color agg_bg = (m_current_risk_mode == MODE_AGGRESSIVE) ? C'14, 116, 144' : C'30, 41, 59';
   color agg_txt = (m_current_risk_mode == MODE_AGGRESSIVE) ? clrWhite : C'148, 163, 184';
   ObjectSetInteger(0, "RVS_BTN_MODE_AGG", OBJPROP_BGCOLOR, agg_bg);
   ObjectSetInteger(0, "RVS_BTN_MODE_AGG", OBJPROP_COLOR, agg_txt);
   ObjectSetInteger(0, "RVS_BTN_MODE_AGG", OBJPROP_BORDER_COLOR, agg_bg);

   color mod_bg = (m_current_risk_mode == MODE_MODERATE) ? C'180, 83, 9' : C'30, 41, 59';
   color mod_txt = (m_current_risk_mode == MODE_MODERATE) ? clrWhite : C'148, 163, 184';
   ObjectSetInteger(0, "RVS_BTN_MODE_MOD", OBJPROP_BGCOLOR, mod_bg);
   ObjectSetInteger(0, "RVS_BTN_MODE_MOD", OBJPROP_COLOR, mod_txt);
   ObjectSetInteger(0, "RVS_BTN_MODE_MOD", OBJPROP_BORDER_COLOR, mod_bg);

   color cons_bg = (m_current_risk_mode == MODE_CONSERVATIVE) ? C'29, 78, 216' : C'30, 41, 59';
   color cons_txt = (m_current_risk_mode == MODE_CONSERVATIVE) ? clrWhite : C'148, 163, 184';
   ObjectSetInteger(0, "RVS_BTN_MODE_CONS", OBJPROP_BGCOLOR, cons_bg);
   ObjectSetInteger(0, "RVS_BTN_MODE_CONS", OBJPROP_COLOR, cons_txt);
   ObjectSetInteger(0, "RVS_BTN_MODE_CONS", OBJPROP_BORDER_COLOR, cons_bg);

   // 3. Subtítulo
   string sub = StringFormat("%s · %s · Spread: %d pts", m_symbol, EnumToString(m_timeframe), (int)SymbolInfoInteger(m_symbol, SYMBOL_SPREAD));
   GuiSetText("RVS_SUBTITLE", sub);

   // 4. Status de Operação
   bool ea_trade_allowed = (bool)MQLInfoInteger(MQL_TRADE_ALLOWED);
   bool term_trade_allowed = (bool)TerminalInfoInteger(TERMINAL_TRADE_ALLOWED);
   string st_str = "🟢 OPERANDO NORMAL";
   color st_clr = C'52, 211, 153';

   if (!ea_trade_allowed)
   {
      st_str = "⚠️ ATIVE F7 -> PERMITIR NEGOCIAÇÃO";
      st_clr = C'251, 191, 36';
   }
   else if (!term_trade_allowed)
   {
      st_str = "⚠️ CLIQUE NO BOTÃO 'AlgoTrading'";
      st_clr = C'251, 191, 36';
   }
   else if (!m_bot_active)
   {
      st_str = "⏸️ ROBÔ EM PAUSA";
      st_clr = C'251, 146, 60';
   }
   else if (m_circuit_breaker_active)
   {
      st_str = "🔴 " + m_circuit_breaker_reason;
      st_clr = C'248, 113, 113';
   }
   else if (m_consecutive_losses >= InpMaxConsecutiveLoss)
   {
      st_str = "🟡 " + m_circuit_breaker_reason;
      st_clr = C'251, 191, 36';
   }
   GuiSetText("RVS_VAL_STATUS", st_str, st_clr);

   // 5. Lucro Hoje
   double daily_pnl = GetDailyRealizedPnL();
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   double ref_balance = (m_day_start_balance > 0) ? m_day_start_balance : balance;
   double pnl_pct = (ref_balance > 0) ? (daily_pnl / ref_balance) * 100.0 : 0.0;
   string pnl_str = StringFormat("%s$%.2f (%s%.2f%%)",
                                 daily_pnl >= 0 ? "+" : "-",
                                 MathAbs(daily_pnl),
                                 pnl_pct >= 0 ? "+" : "",
                                 pnl_pct);
   color pnl_clr = (daily_pnl >= 0) ? C'52, 211, 153' : C'248, 113, 113';
   GuiSetText("RVS_VAL_PNL", pnl_str, pnl_clr);

   // 6. Saldo e Equidade
   GuiSetText("RVS_VAL_BAL", StringFormat("$%.2f (Eq: $%.2f)", balance, equity));

   // 7. Metas Diárias
   double target_win = ref_balance * (InpDailyStopWinPct / 100.0);
   double max_loss = ref_balance * (InpDailyStopLossPct / 100.0);
   GuiSetText("RVS_VAL_TARGETS", StringFormat("Win: +$%.0f | Loss: -$%.0f", target_win, max_loss));

   // 8. Posições e Lote
   int open_pos = CountOpenPositions();
   double current_lot = (InpLotCalcMode == LOT_FIXED_SIZE ? InpFixedLot : 0.01);
   GuiSetText("RVS_VAL_POS", StringFormat("%d / %d Abertas · Lote: %.2f", open_pos, InpMaxTotalPositions, current_lot));

   // 9. Telemetria
   string tele_str = "";
   if (m_current_risk_mode == MODE_AGGRESSIVE)
      tele_str = StringFormat("RSI: %.1f | TP: +$%.0f | SL: -$%.0f", m_live_rsi, InpAggTpDollars, InpAggSlDollars);
   else if (m_current_risk_mode == MODE_MODERATE)
      tele_str = StringFormat("RSI: %.1f | ATR: %.2f | Payoff M5", m_live_rsi, m_live_atr);
   else
      tele_str = StringFormat("RSI: %.1f | Payoff 1:2 Fixo M15", m_live_rsi);
   GuiSetText("RVS_VAL_SIGNAL", tele_str);

   // 10. Diagnóstico
   GuiSetText("RVS_VAL_RADAR", m_last_eval_status);

   // 11. Último Envio
   GuiSetText("RVS_VAL_LAST", m_last_order_status);

   Comment(""); // Garante que a tela fique limpa sem texto ASCII antigo
   ChartRedraw(0);
}

void RenderDashboard()
{
   UpdateGUI();
}
//+------------------------------------------------------------------+
