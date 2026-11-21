"""NexusQuantumEngine - Institutional High-Frequency & Deep Conservative Trading Engine.

Brand Identity: NEXUS QUANTUM AI
Specialization: Multi-Asset Scalper (Forex, Crypto, Synthetic Indices) via Deriv Multipliers (MULTUP / MULTDOWN).

Estratégia Katie Tutorials (M1 Candlesticks + 3 MAs + RSI 5):
1. Processamento de velas M1 com hidratação instantânea de 300 velas via Deriv WS.
2. Fast Moving Average: MA 6 (SMA)
3. Medium Moving Average: MA 14 (SMA)
4. Macro Trend Moving Average: MA 250 (SMA)
5. Momentum Oscillator: RSI 5 (níveis 50 com alinhamento direcional)
6. Gatilho Causal no Fechamento da Vela M1:
   - COMPRA (MULTUP): MA 6 > MA 14 AND Fechamento > MA 250 AND RSI 5 >= 50 AND Vela Verde.
   - VENDA (MULTDOWN): MA 6 < MA 14 AND Fechamento < MA 250 AND RSI 5 <= 50 AND Vela Vermelha.
7. Smart DCA / Preço Médio na Movimentação (Multipliers):
   - Sem expiração por relógio (opera a movimentação real).
   - Se a vela fechar contra a operação (ex: fecha verde numa venda), abre reentrada (Camada 2)
     com o dobro da taxa inicial (ou multiplicador configurado).
   - Teto rígido de camadas (máx 3 a 4 camadas) respeitando a margem estrita de $50 USD Stop Loss.
   - Fechamento em bloco com lucro líquido no primeiro respiro (Take Profit).
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
import logging
import math
from typing import Any, Dict, List, Optional

logger = logging.getLogger("worker.nexus_quantum_engine")

SUPPORTED_ASSETS: dict[str, dict[str, Any]] = {
    # Cripto (24/7)
    "cryBTCUSD": {"name": "Bitcoin / USD", "category": "Cripto", "default_mult": 100},
    "cryETHUSD": {"name": "Ethereum / USD", "category": "Cripto", "default_mult": 100},
    # Sintéticos Deriv (24/7)
    "JD75": {"name": "Jump 75 Index", "category": "Sintéticos", "default_mult": 100},
    "JD50": {"name": "Jump 50 Index", "category": "Sintéticos", "default_mult": 100},
    "JD25": {"name": "Jump 25 Index", "category": "Sintéticos", "default_mult": 100},
    "1HZ100V": {"name": "Volatility 100 (1s) Index", "category": "Sintéticos", "default_mult": 100},
    "R_100": {"name": "Volatility 100 Index", "category": "Sintéticos", "default_mult": 100},
    "R_50": {"name": "Volatility 50 Index", "category": "Sintéticos", "default_mult": 80},
    "R_25": {"name": "Volatility 25 Index", "category": "Sintéticos", "default_mult": 100},
    "R_10": {"name": "Volatility 10 Index", "category": "Sintéticos", "default_mult": 100},
    # Forex Majors & Crosses
    "frxEURUSD": {"name": "EUR/USD", "category": "Forex", "default_mult": 100},
    "frxUSDJPY": {"name": "USD/JPY", "category": "Forex", "default_mult": 100},
    "frxGBPUSD": {"name": "GBP/USD", "category": "Forex", "default_mult": 100},
    "frxAUDUSD": {"name": "AUD/USD", "category": "Forex", "default_mult": 100},
    "frxUSDCAD": {"name": "USD/CAD", "category": "Forex", "default_mult": 100},
    "frxUSDCHF": {"name": "USD/CHF", "category": "Forex", "default_mult": 100},
    "frxEURJPY": {"name": "EUR/JPY", "category": "Forex", "default_mult": 100},
    "frxGBPJPY": {"name": "GBP/JPY", "category": "Forex", "default_mult": 100},
    "frxEURGBP": {"name": "EUR/GBP", "category": "Forex", "default_mult": 100},
    "frxAUDJPY": {"name": "AUD/JPY", "category": "Forex", "default_mult": 100},
}

SUPPORTED_SYNTHETIC_INDICES: dict[str, str] = {
    sym: data["name"] for sym, data in SUPPORTED_ASSETS.items()
}


def compute_sma(series: List[float], period: int) -> Optional[float]:
    if not series or len(series) < period:
        return None
    return sum(series[-period:]) / float(period)


def compute_ema(series: List[float], period: int) -> Optional[float]:
    if not series or len(series) < period:
        return None
    alpha = 2.0 / (period + 1.0)
    ema = sum(series[:period]) / float(period)
    for val in series[period:]:
        ema = alpha * val + (1.0 - alpha) * ema
    return ema


def compute_rsi(closes: List[float], period: int = 5) -> Optional[float]:
    if not closes or len(closes) < period + 1:
        return None
    diffs = [closes[i] - closes[i - 1] for i in range(-period, 0)]
    gains = [d for d in diffs if d > 0]
    losses = [abs(d) for d in diffs if d < 0]
    avg_gain = sum(gains) / float(period)
    avg_loss = sum(losses) / float(period)
    if avg_loss <= 1e-8:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


@dataclass
class MarketStreamState:
    symbol: str
    ticks: deque = field(default_factory=lambda: deque(maxlen=240))
    # M1 Candles buffer: up to 600 candles for MA250 calculation
    candles_m1: deque = field(default_factory=lambda: deque(maxlen=600))
    current_forming_candle: Optional[Dict[str, Any]] = None
    is_hydrated: bool = False

    # Classical Tick Indicators (Deep Analysis)
    ema10: Optional[float] = None
    ema20: Optional[float] = None
    std30: Optional[float] = None
    z_score: Optional[float] = None

    # Modo Katie Tutorials / Deep Conservador Indicators (3 MAs + RSI 5)
    ma6: Optional[float] = None
    ma14: Optional[float] = None
    ma250: Optional[float] = None
    rsi5: Optional[float] = None

    last_signal_epoch: float = 0.0
    last_traded_candle_epoch: int = 0  # Trava anti-duplicação na mesma vela M1
    active_contract_id: Optional[str] = None
    active_contract_type: Optional[str] = None
    active_entry_spot: Optional[float] = None
    active_start_time: float = 0.0
    status_text: str = "ANALISANDO"

    # Multiplier Basket & Smart DCA state for this symbol
    has_open_basket: bool = False
    basket_direction: Optional[str] = None  # "MULTUP" or "MULTDOWN"
    basket_layers: int = 0
    basket_initial_spot: Optional[float] = None
    last_layer_spot: Optional[float] = None
    last_layer_epoch: float = 0.0
    current_floating_pnl: float = 0.0
    current_stake_usd: float = 1.0


@dataclass
class NexusSignal:
    symbol: str
    direction: str  # "MULTUP" (CALL) or "MULTDOWN" (PUT)
    stake_usd: float
    layer_index: int = 1
    is_reentry: bool = False
    duration_ticks: int = 1
    duration_unit: str = "m"  # "m" for M1 candle timing
    reason: str = ""
    current_spot: float = 0.0
    z_score: float = 0.0
    deep_analysis: bool = True
    ma6: Optional[float] = None
    ma14: Optional[float] = None
    ma250: Optional[float] = None
    rsi5: Optional[float] = None


class NexusQuantumEngine:
    """Institutional Scalping & Smart DCA Engine for Deriv Multipliers."""

    def __init__(
        self,
        symbols: Optional[list[str]] = None,
        duration_ticks: int = 1,
        duration_unit: str = "m",
        deep_analysis_enabled: bool = True,
        conservative_mode_enabled: bool = True,
        use_binance_cvd: bool = True,
        initial_stake_usd: float = 1.00,
        martingale_factor: float = 2.0,
        stop_win_usd: float = 50.0,
        stop_loss_usd: float = 50.0,
        cooldown_sec: float = 3.0,
        max_layers: int = 3,
        basket_tp_usd: float = 1.00,
    ) -> None:
        self.symbols = symbols if symbols else ["cryBTCUSD"]
        self.duration_ticks = max(1, min(60, int(duration_ticks)))
        self.duration_unit = "m" if duration_unit in ("m", "1m") else "m"
        self.deep_analysis_enabled = bool(deep_analysis_enabled)
        self.conservative_mode_enabled = bool(conservative_mode_enabled)
        self.use_binance_cvd = bool(use_binance_cvd)
        self.initial_stake_usd = max(0.50, float(initial_stake_usd))
        self.martingale_factor = max(1.5, float(martingale_factor))
        self.stop_win_usd = abs(float(stop_win_usd))
        self.stop_loss_usd = abs(float(stop_loss_usd))
        self.cooldown_sec = float(cooldown_sec)
        self.max_layers = max(1, min(5, int(max_layers)))
        self.basket_tp_usd = max(0.20, float(basket_tp_usd))

        self.states: dict[str, MarketStreamState] = {
            sym: MarketStreamState(symbol=sym) for sym in self.symbols
        }

        # Session metrics
        self.total_wins: int = 0
        self.total_losses: int = 0
        self.session_profit_usd: float = 0.0
        self.accumulated_loss_streak_usd: float = 0.0
        self.consecutive_losses: int = 0
        self.current_stake_usd: float = self.initial_stake_usd
        self.last_payout_ratio: float = 0.92
        self.recent_trades: deque = deque(maxlen=100)
        self.is_locked_stop: bool = False
        self.lock_reason: str = ""

        # Performance breakdown per symbol for the session
        self.stats_by_symbol: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"trades": 0, "wins": 0, "losses": 0, "pnl_usd": 0.0}
        )

    def update_config(
        self,
        symbols: Optional[list[str]] = None,
        duration_ticks: Optional[int] = None,
        duration_unit: Optional[str] = None,
        deep_analysis_enabled: Optional[bool] = None,
        conservative_mode_enabled: Optional[bool] = None,
        use_binance_cvd: Optional[bool] = None,
        initial_stake_usd: Optional[float] = None,
        martingale_factor: Optional[float] = None,
        stop_win_usd: Optional[float] = None,
        stop_loss_usd: Optional[float] = None,
        max_layers: Optional[int] = None,
        basket_tp_usd: Optional[float] = None,
    ) -> None:
        """Dynamically update runtime strategy parameters."""
        if symbols is not None and symbols:
            # Filter out unsupported symbols like RDBULL or RDBEAR
            valid_symbols = [s for s in symbols if s in SUPPORTED_ASSETS or not s.startswith("RDB")]
            if valid_symbols:
                self.symbols = valid_symbols
                for s in self.symbols:
                    if s not in self.states:
                        self.states[s] = MarketStreamState(symbol=s)

        if duration_ticks is not None:
            self.duration_ticks = max(1, min(60, int(duration_ticks)))
        if duration_unit is not None:
            self.duration_unit = "m"
        if deep_analysis_enabled is not None:
            self.deep_analysis_enabled = bool(deep_analysis_enabled)
        if conservative_mode_enabled is not None:
            self.conservative_mode_enabled = bool(conservative_mode_enabled)
        if use_binance_cvd is not None:
            self.use_binance_cvd = bool(use_binance_cvd)
        if initial_stake_usd is not None:
            self.initial_stake_usd = max(0.50, float(initial_stake_usd))
            if self.consecutive_losses == 0:
                self.current_stake_usd = self.initial_stake_usd
        if martingale_factor is not None:
            self.martingale_factor = max(1.5, float(martingale_factor))
        if stop_win_usd is not None:
            self.stop_win_usd = abs(float(stop_win_usd))
        if stop_loss_usd is not None:
            self.stop_loss_usd = abs(float(stop_loss_usd))
        if max_layers is not None:
            self.max_layers = max(1, min(5, int(max_layers)))
        if basket_tp_usd is not None:
            self.basket_tp_usd = max(0.20, float(basket_tp_usd))

    def load_historical_candles(self, symbol: str, candles: list[dict[str, Any]]) -> None:
        """Instant hydration of up to 300 M1 candles from Deriv WS ticks_history."""
        if symbol not in self.states:
            self.states[symbol] = MarketStreamState(symbol=symbol)
        st = self.states[symbol]
        st.candles_m1.clear()

        for c in candles:
            t = int(c.get("epoch") or c.get("time") or 0)
            o = float(c.get("open") or 0.0)
            h = float(c.get("high") or 0.0)
            l = float(c.get("low") or 0.0)
            cl = float(c.get("close") or 0.0)
            if cl > 0:
                st.candles_m1.append({"time": t, "open": o, "high": h, "low": l, "close": cl})

        st.is_hydrated = True
        self._recalculate_indicators(st)
        logger.info(
            "[%s] Carga inicial: %d velas M1 carregadas. MA6=%.4f, MA14=%.4f, MA250=%.4f, RSI5=%.1f",
            symbol,
            len(st.candles_m1),
            st.ma6 or 0.0,
            st.ma14 or 0.0,
            st.ma250 or 0.0,
            st.rsi5 or 0.0,
        )

    def _recalculate_indicators(self, state: MarketStreamState) -> None:
        """Compute MA 6, MA 14, MA 250 and RSI 5 over M1 candles."""
        all_candles = list(state.candles_m1)
        if state.current_forming_candle:
            all_candles.append(state.current_forming_candle)

        closes = [c["close"] for c in all_candles if c.get("close", 0) > 0]
        if not closes:
            return

        # 1. Moving Averages (MA 6, MA 14, MA 250)
        state.ma6 = compute_sma(closes, 6) or compute_ema(closes, 6)
        state.ma14 = compute_sma(closes, 14) or compute_ema(closes, 14)
        if len(closes) >= 250:
            state.ma250 = compute_sma(closes, 250) or compute_ema(closes, 250)
        elif len(closes) >= 30:
            state.ma250 = compute_sma(closes, len(closes)) or compute_ema(closes, len(closes))

        # 2. RSI 5 (Fast Relative Strength Index)
        state.rsi5 = compute_rsi(closes, 5)

    def calculate_layer_stake(self, layer_index: int) -> float:
        """Calculate stake for Layer 1, 2, 3, 4... Aggressive Smart DCA (Dobra de Stake).
        
        Layer 1: initial_stake_usd (e.g. $1.00)
        Layer 2: initial_stake_usd * 2 ($2.00)
        Layer 3: initial_stake_usd * 4 ($4.00)
        Layer 4: initial_stake_usd * 8 ($8.00)
        Layer n: initial_stake_usd * (2 ** (layer_index - 1))
        """
        if layer_index <= 1:
            return round(self.initial_stake_usd, 2)
        factor = 2 ** (layer_index - 1)
        return round(self.initial_stake_usd * factor, 2)

    def on_tick(
        self,
        symbol: str,
        price: float,
        epoch: float,
        binance_lead_state: Optional[dict[str, Any]] = None,
    ) -> Optional[NexusSignal]:
        """Process incoming tick, manage candle boundaries, and trigger initial entry or DCA re-entry."""
        if price <= 0:
            return None

        # Ignore symbols not monitored or blacklisted
        if symbol not in self.symbols:
            return None

        if symbol not in self.states:
            self.states[symbol] = MarketStreamState(symbol=symbol)

        state = self.states[symbol]
        state.ticks.append((price, epoch))

        candle_epoch = int(epoch // 60) * 60
        just_closed_candle: Optional[Dict[str, Any]] = None

        if state.current_forming_candle is None or state.current_forming_candle["time"] != candle_epoch:
            # Previous candle is now closed!
            if state.current_forming_candle is not None:
                just_closed_candle = dict(state.current_forming_candle)
                state.candles_m1.append(just_closed_candle)

            # Initialize new forming M1 candle
            state.current_forming_candle = {
                "time": candle_epoch,
                "open": price,
                "high": price,
                "low": price,
                "close": price,
            }
        else:
            fc = state.current_forming_candle
            fc["high"] = max(fc["high"], price)
            fc["low"] = min(fc["low"], price)
            fc["close"] = price

        # Recalculate indicators
        self._recalculate_indicators(state)

        # Classical Z-score for telemetry
        recent_prices = [p for p, _ in state.ticks]
        if len(recent_prices) >= 12:
            w = recent_prices[-30:]
            mean = sum(w) / len(w)
            var = sum((x - mean) ** 2 for x in w) / len(w)
            state.std30 = math.sqrt(var)
            if state.std30 > 1e-6 and state.ema20 is not None:
                state.z_score = (price - state.ema20) / state.std30
            else:
                state.z_score = 0.0

        # Safety locks (Stop Win & Stop Loss)
        if self.session_profit_usd >= self.stop_win_usd:
            self.is_locked_stop = True
            self.lock_reason = f"META ATINGIDA 🎯 (+${self.session_profit_usd:.2f})"
            return None

        if self.session_profit_usd <= -self.stop_loss_usd:
            self.is_locked_stop = True
            self.lock_reason = f"STOP LOSS 🛑 (-${abs(self.session_profit_usd):.2f})"
            return None

        # We evaluate triggers strictly at candle close (or when just_closed_candle was detected)
        # to respect the exact video timing and avoid tick-level noise!
        if just_closed_candle is None:
            return None

        c_open = float(just_closed_candle["open"])
        c_close = float(just_closed_candle["close"])
        is_green_candle = c_close >= c_open
        is_red_candle = c_close < c_open

        # =========================================================================
        # CENÁRIO A: POSIÇÃO ABERTA -> AVALIAR REENTRADA SMART DCA (PREÇO MÉDIO)
        # =========================================================================
        if state.has_open_basket and state.basket_direction:
            # Trava anti-duplicação: máximo 1 reentrada por vela M1
            if state.last_traded_candle_epoch == candle_epoch:
                return None

            if state.basket_layers >= self.max_layers:
                return None

            # Reentrada em VENDA (MULTDOWN): Se a vela M1 fechou VERDE (subiu contra a posição)
            if state.basket_direction in ("MULTDOWN", "PUT"):
                if is_green_candle or (state.last_layer_spot and price > state.last_layer_spot):
                    next_layer = state.basket_layers + 1
                    reentry_stake = self.calculate_layer_stake(next_layer)
                    state.last_traded_candle_epoch = candle_epoch
                    state.last_signal_epoch = epoch
                    return NexusSignal(
                        symbol=symbol,
                        direction="MULTDOWN",
                        stake_usd=reentry_stake,
                        layer_index=next_layer,
                        is_reentry=True,
                        reason=f"Smart DCA #{next_layer}: Vela M1 fechou verde contra Venda. Preço médio em ${price:.4f} (Stake ${reentry_stake:.2f})",
                        current_spot=price,
                        z_score=state.z_score or 0.0,
                        ma6=state.ma6,
                        ma14=state.ma14,
                        ma250=state.ma250,
                        rsi5=state.rsi5,
                    )

            # Reentrada em COMPRA (MULTUP): Se a vela M1 fechou VERMELHA (caiu contra a posição)
            if state.basket_direction in ("MULTUP", "CALL"):
                if is_red_candle or (state.last_layer_spot and price < state.last_layer_spot):
                    next_layer = state.basket_layers + 1
                    reentry_stake = self.calculate_layer_stake(next_layer)
                    state.last_traded_candle_epoch = candle_epoch
                    state.last_signal_epoch = epoch
                    return NexusSignal(
                        symbol=symbol,
                        direction="MULTUP",
                        stake_usd=reentry_stake,
                        layer_index=next_layer,
                        is_reentry=True,
                        reason=f"Smart DCA #{next_layer}: Vela M1 fechou vermelha contra Compra. Preço médio em ${price:.4f} (Stake ${reentry_stake:.2f})",
                        current_spot=price,
                        z_score=state.z_score or 0.0,
                        ma6=state.ma6,
                        ma14=state.ma14,
                        ma250=state.ma250,
                        rsi5=state.rsi5,
                    )

            return None

        # =========================================================================
        # CENÁRIO B: NENHUMA POSIÇÃO ABERTA -> GATILHO MOMENTUM (3 MAs + RSI 5 + CVD)
        # =========================================================================
        # Trava de capital: operar no máximo 1 par simultâneo por vez para proteger os $50
        if any(st.has_open_basket or st.active_contract_id for st in self.states.values()):
            return None

        if state.last_traded_candle_epoch == candle_epoch:
            return None

        if state.ma6 is None or state.ma14 is None or state.rsi5 is None:
            return None

        ma250_ref = state.ma250 if state.ma250 is not None else state.ma14

        # CVD Binance validation for cryBTCUSD
        binance_allows_call = True
        binance_allows_put = True

        if symbol == "cryBTCUSD" and self.use_binance_cvd and binance_lead_state:
            if binance_lead_state.get("available"):
                d15 = float(binance_lead_state.get("delta_15s", 0.0) or 0.0)
                d60 = float(binance_lead_state.get("delta_60s", 0.0) or 0.0)
                abs_bull = bool(binance_lead_state.get("absorption_bullish", False))
                abs_bear = bool(binance_lead_state.get("absorption_bearish", False))

                # For CALL: veto if sellers are dominating and no bullish absorption
                if d15 < 0 and d60 < 0 and not abs_bull:
                    binance_allows_call = False
                    logger.debug(
                        "[NexusQuantum] Veto CVD Binance em COMPRA BTC: Venda agressiva dominante (d15=%.0f, d60=%.0f)",
                        d15,
                        d60,
                    )

                # For PUT: veto if buyers are dominating and no bearish absorption
                if d15 > 0 and d60 > 0 and not abs_bear:
                    binance_allows_put = False
                    logger.debug(
                        "[NexusQuantum] Veto CVD Binance em VENDA BTC: Compra agressiva dominante (d15=%.0f, d60=%.0f)",
                        d15,
                        d60,
                    )

        # GATILHO COMPRA (MULTUP):
        # 1. MA 6 > MA 14 (alinhamento comprador)
        # 2. Fechamento >= MA 250 (tendência institucional de alta)
        # 3. RSI 5 >= 70.0 (explosão de força compradora no rompimento)
        # 4. Vela fechada M1 é Verde (confirmação da pressão compradora)
        # 5. Filtro CVD Binance autoriza
        is_call = (
            (state.ma6 > state.ma14)
            and (c_close >= ma250_ref)
            and (state.rsi5 >= 70.0)
            and is_green_candle
            and binance_allows_call
        )

        if is_call:
            state.last_traded_candle_epoch = candle_epoch
            state.last_signal_epoch = epoch
            stake = self.calculate_layer_stake(1)
            cvd_tag = " + CVD Binance Confirmado" if (symbol == "cryBTCUSD" and self.use_binance_cvd) else ""
            return NexusSignal(
                symbol=symbol,
                direction="MULTUP",
                stake_usd=stake,
                layer_index=1,
                is_reentry=False,
                reason=f"Gatilho Momentum COMPRA: MA6 ({state.ma6:.2f}) > MA14 ({state.ma14:.2f}) + Preço >= MA250 + RSI5 ({state.rsi5:.1f}) >= 70 + Vela Verde{cvd_tag}",
                current_spot=price,
                z_score=state.z_score or 0.0,
                ma6=state.ma6,
                ma14=state.ma14,
                ma250=state.ma250,
                rsi5=state.rsi5,
            )

        # GATILHO VENDA (MULTDOWN):
        # 1. MA 6 < MA 14 (alinhamento vendedor)
        # 2. Fechamento <= MA 250 (tendência institucional de baixa)
        # 3. RSI 5 <= 30.0 (explosão de força vendedora no rompimento)
        # 4. Vela fechada M1 é Vermelha (confirmação da pressão vendedora)
        # 5. Filtro CVD Binance autoriza
        is_put = (
            (state.ma6 < state.ma14)
            and (c_close <= ma250_ref)
            and (state.rsi5 <= 30.0)
            and is_red_candle
            and binance_allows_put
        )

        if is_put:
            state.last_traded_candle_epoch = candle_epoch
            state.last_signal_epoch = epoch
            stake = self.calculate_layer_stake(1)
            cvd_tag = " + CVD Binance Confirmado" if (symbol == "cryBTCUSD" and self.use_binance_cvd) else ""
            return NexusSignal(
                symbol=symbol,
                direction="MULTDOWN",
                stake_usd=stake,
                layer_index=1,
                is_reentry=False,
                reason=f"Gatilho Momentum VENDA: MA6 ({state.ma6:.2f}) < MA14 ({state.ma14:.2f}) + Preço <= MA250 + RSI5 ({state.rsi5:.1f}) <= 30 + Vela Vermelha{cvd_tag}",
                current_spot=price,
                z_score=state.z_score or 0.0,
                ma6=state.ma6,
                ma14=state.ma14,
                ma250=state.ma250,
                rsi5=state.rsi5,
            )

        return None

    def on_basket_opened(self, symbol: str, direction: str, spot: float, stake: float) -> None:
        """Mark basket as opened on symbol."""
        if symbol in self.states:
            st = self.states[symbol]
            st.has_open_basket = True
            st.basket_direction = direction
            st.basket_layers = 1
            st.basket_initial_spot = spot
            st.last_layer_spot = spot
            st.last_layer_epoch = 0.0
            st.current_floating_pnl = 0.0
            st.current_stake_usd = stake
            st.status_text = f"CESTO_{direction}_1L"

    def on_layer_added(self, symbol: str, layer_index: int, spot: float, stake: float) -> None:
        """Register DCA re-entry layer."""
        if symbol in self.states:
            st = self.states[symbol]
            st.basket_layers = max(st.basket_layers, layer_index)
            st.last_layer_spot = spot
            st.current_stake_usd += stake
            st.status_text = f"CESTO_{st.basket_direction}_{st.basket_layers}L"

    def on_basket_closed(
        self,
        symbol: str,
        profit_usd: float,
        is_win: bool,
        exit_spot: float = 0.0,
        epoch: float = 0.0,
        layers_count: int = 1,
    ) -> dict[str, Any]:
        """Record basket settlement, update session statistics per symbol, and release market locks."""
        if is_win:
            self.total_wins += 1
            self.session_profit_usd += profit_usd
            self.consecutive_losses = 0
            self.accumulated_loss_streak_usd = 0.0
            self.current_stake_usd = self.initial_stake_usd
        else:
            self.total_losses += 1
            self.session_profit_usd += profit_usd  # negative
            self.consecutive_losses += 1
            self.accumulated_loss_streak_usd += abs(profit_usd)

        # Update per-symbol performance metrics
        stat = self.stats_by_symbol[symbol]
        stat["trades"] += 1
        if is_win:
            stat["wins"] += 1
        else:
            stat["losses"] += 1
        stat["pnl_usd"] = round(stat["pnl_usd"] + profit_usd, 4)

        trade_info = {
            "symbol": symbol,
            "contract_id": f"BSK-{symbol}-{int(epoch or 0)}",
            "stake_usd": round(self.initial_stake_usd * layers_count, 2),
            "profit_usd": round(profit_usd, 2),
            "is_win": is_win,
            "exit_spot": exit_spot,
            "layers_count": layers_count,
            "epoch": epoch,
        }
        self.recent_trades.appendleft(trade_info)

        # Release lock on symbol
        if symbol in self.states:
            st = self.states[symbol]
            st.has_open_basket = False
            st.basket_direction = None
            st.basket_layers = 0
            st.basket_initial_spot = None
            st.last_layer_spot = None
            st.current_floating_pnl = 0.0
            st.current_stake_usd = 0.0
            st.status_text = "ANALISANDO"

        # Check stopping criteria
        if self.session_profit_usd >= self.stop_win_usd:
            self.is_locked_stop = True
            self.lock_reason = f"META DE LUCRO ATINGIDA 🎯 (+${self.session_profit_usd:.2f})"
        elif self.session_profit_usd <= -self.stop_loss_usd:
            self.is_locked_stop = True
            self.lock_reason = f"STOP LOSS ATINGIDO 🛑 (-${abs(self.session_profit_usd):.2f})"

        return trade_info

    def reset_session(self) -> None:
        """Clear session statistics."""
        self.session_profit_usd = 0.0
        self.total_wins = 0
        self.total_losses = 0
        self.consecutive_losses = 0
        self.accumulated_loss_streak_usd = 0.0
        self.current_stake_usd = self.initial_stake_usd
        self.is_locked_stop = False
        self.lock_reason = ""
        self.stats_by_symbol.clear()
        for st in self.states.values():
            st.has_open_basket = False
            st.basket_direction = None
            st.basket_layers = 0
            st.status_text = "ANALISANDO"

    def get_session_report(self) -> dict[str, Any]:
        """Detailed breakdown report per symbol for the session."""
        pairs_report = []
        for sym, stat in self.stats_by_symbol.items():
            tot = stat["trades"]
            wr = round((stat["wins"] / tot) * 100.0, 1) if tot > 0 else 0.0
            asset_info = SUPPORTED_ASSETS.get(sym, {"name": sym, "category": "Outros"})
            pairs_report.append({
                "symbol": sym,
                "name": asset_info["name"],
                "category": asset_info["category"],
                "trades": tot,
                "wins": stat["wins"],
                "losses": stat["losses"],
                "winrate_pct": wr,
                "pnl_usd": stat["pnl_usd"],
            })

        # Sort by pnl_usd descending (best first)
        pairs_report.sort(key=lambda x: x["pnl_usd"], reverse=True)
        best_pair = pairs_report[0]["symbol"] if pairs_report else None
        worst_pair = pairs_report[-1]["symbol"] if pairs_report else None

        return {
            "session_profit_usd": round(self.session_profit_usd, 2),
            "total_wins": self.total_wins,
            "total_losses": self.total_losses,
            "win_rate_pct": round((self.total_wins / (self.total_wins + self.total_losses)) * 100.0, 1) if (self.total_wins + self.total_losses) > 0 else 0.0,
            "is_stopped": self.is_locked_stop,
            "lock_reason": self.lock_reason,
            "best_pair": best_pair,
            "worst_pair": worst_pair,
            "pairs": pairs_report,
        }

    def get_telemetry(self) -> dict[str, Any]:
        """Telemetry snapshot for WebSocket clients and live Dashboard UI."""
        tot = self.total_wins + self.total_losses
        wr = round((self.total_wins / tot) * 100.0, 1) if tot > 0 else 0.0

        markets_data: dict[str, dict[str, Any]] = {}
        for sym, st in self.states.items():
            last_p = st.ticks[-1][0] if st.ticks else (st.candles_m1[-1]["close"] if st.candles_m1 else 0.0)
            asset_info = SUPPORTED_ASSETS.get(sym, {"name": sym, "category": "Sintéticos"})
            markets_data[sym] = {
                "symbol": sym,
                "name": asset_info["name"],
                "category": asset_info["category"],
                "status": st.status_text,
                "spot": last_p,
                "z_score": round(st.z_score, 2) if st.z_score is not None else None,
                "ma6": round(st.ma6, 4) if st.ma6 is not None else None,
                "ma14": round(st.ma14, 4) if st.ma14 is not None else None,
                "ma250": round(st.ma250, 4) if st.ma250 is not None else None,
                "rsi5": round(st.rsi5, 2) if st.rsi5 is not None else None,
                "has_open_trade": bool(st.has_open_basket or st.active_contract_id),
                "basket_layers": st.basket_layers,
                "is_hydrated": st.is_hydrated,
                "entry_spot": st.basket_initial_spot,
                "direction": st.basket_direction,
                "floating_pnl": round(st.current_floating_pnl, 2),
                "stake_usd": round(st.current_stake_usd, 2),
            }

        return {
            "engine_brand": "NEXUS QUANTUM AI",
            "strategy_title": "KATIE M1 MULTIPLIER + SMART DCA",
            "conservative_mode_enabled": self.conservative_mode_enabled,
            "deep_analysis_enabled": self.deep_analysis_enabled,
            "tick_duration": self.duration_ticks,
            "duration_unit": self.duration_unit,
            "markets": markets_data,
            "total_wins": self.total_wins,
            "total_losses": self.total_losses,
            "win_rate": wr,
            "session_profit_usd": round(self.session_profit_usd, 2),
            "current_stake_usd": round(self.current_stake_usd, 2),
            "initial_stake_usd": round(self.initial_stake_usd, 2),
            "martingale_factor": round(self.martingale_factor, 2),
            "consecutive_losses": self.consecutive_losses,
            "accumulated_loss_streak_usd": round(self.accumulated_loss_streak_usd, 2),
            "is_locked_stop": self.is_locked_stop,
            "lock_reason": self.lock_reason,
            "stop_win_usd": round(self.stop_win_usd, 2),
            "stop_loss_usd": round(self.stop_loss_usd, 2),
            "recent_trades": list(self.recent_trades)[:25],
            "session_report": self.get_session_report(),
        }
