"""Per-User Isolated Asynchronous Trading Session.

Each active user runs an isolated `UserSession` with:
- Dedicated authenticated `DerivClient` using decrypted account token (never logged).
- Startup & reconnect reconciliation via Deriv `portfolio` (zero duplicate orders).
- Real-time `proposal_open_contract` net profit streaming -> Postgres + Redis.
- Redis distributed lock (`key_order_lock(user_id, symbol)`) guarding all `proposal`+`buy` calls.
- Collective `sell(price=0)` on TP, Trailing, Global SL, Channel Break, Timeout, or Kill Switch.
- Safe Subscription Expiration draining (blocks new entries/reentries, keeps managing open exits).
- Friendly Portuguese `BotEventLog` records published to DB and `ch_user_events(user_id)`.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import logging
import time
from typing import Any, Optional

from redis.asyncio import Redis

from engine.binance_lead import BinanceLeadFilter
from engine.channel_detector import ChannelSnapshot
from engine.confirmations import dca_reentry_signal, entry_signal
from engine.dca_basket_manager import (
    BasketState,
    ContractSnapshot,
    SmartDCABasketManager,
)
from engine.deriv_client import DerivAPIError, DerivClient, extract_stop_out_pct
from engine.gradient_linear_engine import (
    GradientCycleState,
    GradientLevel,
    GradientLinearEngine,
)
from engine.tick_mean_reversion_engine import (
    DcaLayerSignal,
    ExitSignal,
    ReversionSignal,
    TickMeanReversionEngine,
)
from engine.swing_grid_respiros_engine import (
    GridCycleState as RespirosCycleState,
    SwingGridRespirosEngine,
)
from engine.nexus_quantum_engine import (
    NexusQuantumEngine,
    NexusSignal,
    SUPPORTED_ASSETS,
)
from engine.syntra_tick_engine import (
    SyntraSignal,
    SyntraTickEngine,
)
from engine.linreg_atr_video import (
    ArmedBandOrder,
    arm_video_band_order,
    check_band_touch,
    compute_atr_exit_levels,
    evaluate_atr_price_exit,
)
from engine.market_hub import MarketHub
from engine.risk_guard import RiskConfig, RiskGuard, RiskVerdict, UserAccessState
from shared.config import get_settings
from shared.crypto_vault import decrypt_secret
from shared.database import session_scope
from shared.models import Basket, BotConfig, BotEventLog, Contract, DerivAccount, User, utcnow
from shared.redis_keys import ch_user_events, key_order_lock, key_user_state

logger = logging.getLogger("worker.user_session")


class UserSession:
    """Isolated async trading session for a single user."""

    def __init__(
        self,
        user_id: int,
        redis_client: Optional[Redis],
        market_hub: Optional[MarketHub] = None,
        binance_lead: Optional[BinanceLeadFilter] = None,
        deriv_client: Optional[DerivClient] = None,
    ) -> None:
        self.user_id = int(user_id)
        self.redis = redis_client
        self.market_hub = market_hub
        self.binance_lead = binance_lead
        self._injected_client = deriv_client
        self.client: Optional[DerivClient] = deriv_client

        # Cached user / config / account snapshots
        self.user_access: Optional[UserAccessState] = None
        self.max_stake_usd: float = 50.0
        self.config_snapshot: dict[str, Any] = {}
        self.account_snapshot: dict[str, Any] = {}
        self._active_account_id: Optional[int] = None
        self._token_encrypted: Optional[str] = None

        # Strategy & risk state
        self.basket_manager = SmartDCABasketManager()
        self.risk_guard = RiskGuard()
        self.open_basket_id: Optional[int] = None
        self.basket_state: Optional[BasketState] = None
        self._last_stop_out_pct: float = 0.0093
        self._balance: float = 0.0
        self._last_status_text: str = "OFF"
        self._last_risk_verdict: Optional[RiskVerdict] = None
        self._armed_video_order: Optional[ArmedBandOrder] = None
        self._armed_video_orders: dict[str, ArmedBandOrder] = {}
        self._video_atr_at_entry: float = 0.0
        self._gradient_engine: GradientLinearEngine = GradientLinearEngine()
        self._gradient_cycle: Optional[GradientCycleState] = None
        self._tick_engine: TickMeanReversionEngine = TickMeanReversionEngine()
        self._respiros_engine: SwingGridRespirosEngine = SwingGridRespirosEngine()
        self._respiros_cycle: Optional[RespirosCycleState] = None
        self._subscribed_direct_ticks: set[str] = set()
        self._nexus_engine: NexusQuantumEngine = NexusQuantumEngine(
            symbols=["cryETHUSD", "cryBTCUSD", "JD75", "R_100", "1HZ100V", "R_50", "JD50"],
            duration_ticks=1,
            deep_analysis_enabled=True,
            initial_stake_usd=1.00,
            martingale_factor=2.0,
            stop_win_usd=50.0,
            stop_loss_usd=50.0,
            cooldown_sec=3.0,
        )
        self._syntra_engine: SyntraTickEngine = SyntraTickEngine(
            symbols=["cryETHUSD", "cryBTCUSD", "JD75", "R_100", "1HZ100V", "R_50", "JD50"],
            duration_ticks=5,
            z_threshold=1.30,
            cooldown_sec=6.0,
        )
        self._opening_layer: bool = False

        self._trade_lock = asyncio.Lock()
        self._stop_event = asyncio.Event()
        self._maintenance_task: Optional[asyncio.Task[None]] = None
        self._last_state_flush_ts: float = 0.0
        self._started = False

    def get_candidate_symbols(self) -> list[str]:
        """Return candidate symbol list for scanning (or primary symbol only if multi-pair disabled)."""
        strategy_mode = str(self.config_snapshot.get("strategy_mode") or "")
        if strategy_mode in ("NEXUS_QUANTUM", "SYNTRA_TICKS"):
            raw_active = self.config_snapshot.get("active_indices_json")
            if raw_active:
                try:
                    parsed = json.loads(raw_active)
                    if isinstance(parsed, list) and parsed:
                        clean = [str(s).strip() for s in parsed if isinstance(s, str) and str(s).strip() and not str(s).startswith("RDB")]
                        if clean:
                            return clean
                except Exception:
                    pass
            return ["cryETHUSD", "cryBTCUSD", "JD75", "R_100", "1HZ100V", "R_50", "JD50"]
        primary = str(self.config_snapshot.get("symbol") or "cryBTCUSD")
        if not bool(self.config_snapshot.get("multi_pair_enabled", False)):
            return [primary]
        raw_json = self.config_snapshot.get("scanner_symbols_json")
        if raw_json:
            try:
                parsed = json.loads(raw_json)
                if isinstance(parsed, list):
                    res = [str(s).strip() for s in parsed if isinstance(s, str) and s.strip()]
                    if primary not in res:
                        res.insert(0, primary)
                    return res
            except Exception:
                pass
        return [primary, "cryETHUSD", "frxEURUSD", "frxGBPUSD", "frxUSDJPY", "1HZ100V", "R_75"]

    async def _hydrate_nexus_candles(self) -> None:
        """Fetch 300 M1 candles for all candidate symbols to instantly compute MA6, MA14, MA250, RSI5."""
        if self.client is None or not self.client.is_connected:
            return
        cands = self.get_candidate_symbols()
        for sym in cands:
            try:
                resp = await self.client.send_request({
                    "ticks_history": sym,
                    "style": "candles",
                    "granularity": 60,
                    "count": 500,
                    "end": "latest",
                }, timeout=8.0)
                candles = resp.get("candles") or []
                if candles:
                    self._nexus_engine.load_historical_candles(sym, candles)
            except Exception as exc:
                logger.debug("[NexusHydrate] Falha ao hidratar velas M1 de %s: %s", sym, exc)

    @property
    def has_open_basket(self) -> bool:
        if self.basket_state is None:
            return False
        return any(c.status == "OPEN" for c in self.basket_state.contracts)

    @property
    def is_running(self) -> bool:
        return self._started and not self._stop_event.is_set()

    async def start(self) -> None:
        """Load DB state, authenticate Deriv account, reconcile portfolio, and subscribe to feeds."""
        if self._started:
            return
        self._stop_event.clear()
        self._started = True

        loaded = await asyncio.to_thread(self._load_state_from_db)
        if not loaded:
            logger.warning("[user=%d] Não foi possível carregar usuário/configuração", self.user_id)
            self._started = False
            return

        # Evaluate subscription / risk before connecting
        verdict = self._evaluate_risk()
        if verdict.code == "EXPIRED_STOPPED":
            await self._disable_bot_in_db(verdict.status_text)
            await self._log_event("WARN", verdict.reason or "Assinatura expirada — robô desativado")
            await self.publish_state()
            self._started = False
            return

        if not self.account_snapshot or not self._token_encrypted:
            await self._update_status_text("SEM CONTA DERIV")
            await self._log_event(
                "WARN",
                "Nenhuma conta Deriv conectada. Conecte uma conta Demo ou Real para operar.",
            )
            await self.publish_state()
            self._started = False
            return

        # Initialize strategy engines with configured candidate symbols
        candidate_synth = self.get_candidate_symbols()
        self._nexus_engine.update_config(
            symbols=candidate_synth,
            duration_ticks=int(self.config_snapshot.get("tick_duration") or 1),
            deep_analysis_enabled=bool(self.config_snapshot.get("deep_analysis_enabled", True)),
            use_binance_cvd=bool(self.config_snapshot.get("use_binance_filter", True)),
            initial_stake_usd=float(self.config_snapshot.get("stake_usd") or 1.00),
            martingale_factor=float(self.config_snapshot.get("martingale_factor") or 2.0),
            stop_win_usd=float(self.config_snapshot.get("daily_profit_target_usd") or 50.0),
            stop_loss_usd=float(self.config_snapshot.get("max_daily_loss_usd") or 50.0),
            max_layers=int(self.config_snapshot.get("max_layers") or 3),
            basket_tp_usd=float(self.config_snapshot.get("basket_tp_usd") or 1.00),
        )
        self._syntra_engine = SyntraTickEngine(
            symbols=candidate_synth,
            duration_ticks=int(self.config_snapshot.get("tick_duration") or 5),
            z_threshold=1.30,
            cooldown_sec=6.0,
        )

        # Connect authenticated Deriv WS client
        try:
            await self._connect_deriv_client()
        except Exception as exc:
            await self._update_status_text("ERRO CONEXÃO")
            await self._log_event(
                "ERROR",
                f"Falha ao conectar na conta Deriv ({self.account_snapshot.get('loginid', '?')}): {exc}",
            )
            await self.publish_state()
            self._started = False
            return

        # Subscribe to balance & reconcile open contracts via portfolio
        await self.sync_account()
        await self.reconcile_portfolio()
        await self._hydrate_nexus_candles()

        # Register with shared MarketHub
        if self.market_hub is not None:
            cands = self.get_candidate_symbols()
            tf = int(self.config_snapshot.get("timeframe_sec") or 300)
            for sym in cands:
                await self.market_hub.ensure_symbol(sym, tf)
            self.market_hub.add_candle_close_listener(self._on_candle_close)
            self.market_hub.add_tick_listener(self._on_tick)

        verdict = self._evaluate_risk()
        await self._update_status_text(verdict.status_text)
        await self._log_event(
            "INFO",
            (
                f"Robô iniciado em {self.config_snapshot.get('symbol')} "
                f"({self.account_snapshot.get('account_type')} {self.account_snapshot.get('loginid')})"
            ),
            symbol=self.config_snapshot.get("symbol"),
        )
        await self.publish_state()

        self._maintenance_task = asyncio.create_task(
            self._maintenance_loop(), name=f"user-maint-{self.user_id}"
        )

    async def stop(self, reason_status: Optional[str] = None) -> None:
        """Stop session cleanly and detach listeners."""
        self._stop_event.set()
        self._started = False

        if self.market_hub is not None:
            self.market_hub.remove_candle_close_listener(self._on_candle_close)
            self.market_hub.remove_tick_listener(self._on_tick)

        if self._maintenance_task is not None:
            self._maintenance_task.cancel()
            try:
                await self._maintenance_task
            except asyncio.CancelledError:
                pass
            self._maintenance_task = None

        if self.client is not None and self._injected_client is None:
            await self.client.close()
            self.client = None

        if reason_status:
            await self._update_status_text(reason_status)
        await self.publish_state()

    async def reload_config(self) -> None:
        """Reload User, BotConfig, and active DerivAccount from Postgres."""
        prev_account_id = self._active_account_id
        prev_symbol = self.config_snapshot.get("symbol")
        prev_tf = self.config_snapshot.get("timeframe_sec")

        loaded = await asyncio.to_thread(self._load_state_from_db)
        if not loaded:
            return

        if self._active_account_id != prev_account_id and self._token_encrypted:
            logger.info("[user=%d] Conta Deriv ativa alterada — reconectando cliente...", self.user_id)
            if self.client is not None and self._injected_client is None:
                await self.client.close()
                self.client = None
            await self._connect_deriv_client()
            await self.sync_account()
            await self.reconcile_portfolio()

        new_symbol = str(self.config_snapshot.get("symbol") or "1HZ100V")
        new_tf = int(self.config_snapshot.get("timeframe_sec") or 60)
        if self.market_hub is not None:
            for s in self.get_candidate_symbols():
                await self.market_hub.ensure_symbol(s, new_tf)

        self._gradient_engine = GradientLinearEngine(
            stake_usd=float(self.config_snapshot.get("stake_usd") or 1.0),
            multiplier=int(self.config_snapshot.get("multiplier") or 100),
            max_layers=int(self.config_snapshot.get("max_layers") or 4),
            layer_multipliers=self.basket_manager.layer_multipliers,
            min_grid_step_pct=float(self.config_snapshot.get("grid_step_pct") or 0.25),
            cycle_tp_usd=float(self.config_snapshot.get("basket_tp_usd") or 0.20),
            max_hold_minutes=int(self.config_snapshot.get("max_hold_minutes") or 60),
        )

        self._tick_engine = TickMeanReversionEngine(
            base_stake_usd=float(self.config_snapshot.get("stake_usd") or 1.0),
            multiplier=int(self.config_snapshot.get("multiplier") or 100),
            max_layers=min(3, int(self.config_snapshot.get("max_layers") or 3)),
            layer_multipliers=self.basket_manager.layer_multipliers,
            z_threshold=2.0,
            dca_sigma_mult=1.8,
            commission_buffer_mult=2.5,
            base_tp_usd=float(self.config_snapshot.get("basket_tp_usd") or 0.20),
        )

        candidate_synth = self.get_candidate_symbols() if str(self.config_snapshot.get("strategy_mode") or "") in ("NEXUS_QUANTUM", "SYNTRA_TICKS") else ["cryETHUSD", "cryBTCUSD", "JD75", "R_100", "1HZ100V", "R_50", "JD50"]
        self._nexus_engine.update_config(
            symbols=candidate_synth,
            duration_ticks=int(self.config_snapshot.get("tick_duration") or 1),
            deep_analysis_enabled=bool(self.config_snapshot.get("deep_analysis_enabled", True)),
            use_binance_cvd=bool(self.config_snapshot.get("use_binance_filter", True)),
            initial_stake_usd=float(self.config_snapshot.get("stake_usd") or 1.00),
            martingale_factor=float(self.config_snapshot.get("martingale_factor") or 2.0),
            stop_win_usd=float(self.config_snapshot.get("daily_profit_target_usd") or 50.0),
            stop_loss_usd=float(self.config_snapshot.get("max_daily_loss_usd") or 50.0),
            max_layers=int(self.config_snapshot.get("max_layers") or 3),
            basket_tp_usd=float(self.config_snapshot.get("basket_tp_usd") or 1.00),
        )

        session_profit = float(self.config_snapshot.get("session_profit_usd") or 0.0)
        if session_profit == 0.0:
            self._nexus_engine.session_profit_usd = 0.0
            self._nexus_engine.is_locked_stop = False
            self._nexus_engine.lock_reason = ""
            self._nexus_engine.total_wins = 0
            self._nexus_engine.total_losses = 0
            self._nexus_engine.consecutive_losses = 0
            self._nexus_engine.accumulated_loss_streak_usd = 0.0

        await self._hydrate_nexus_candles()
        await self._ensure_tick_subscriptions()

        self._syntra_engine = SyntraTickEngine(
            symbols=candidate_synth,
            duration_ticks=int(self.config_snapshot.get("tick_duration") or 5),
            z_threshold=1.30,
            cooldown_sec=6.0,
        )

        verdict = self._evaluate_risk()
        await self._update_status_text(verdict.status_text)
        await self._log_event(
            "INFO",
            f"Configuração recarregada: par={new_symbol}, stake=${self.config_snapshot.get('stake_usd'):.2f}, "
            f"mult={self.config_snapshot.get('multiplier')}x, camadas={self.config_snapshot.get('max_layers')}",
            symbol=new_symbol,
        )
        await self.publish_state()

    async def _connect_deriv_client(self) -> None:
        if self._injected_client is not None:
            self.client = self._injected_client
            return

        if not self._token_encrypted:
            raise ValueError("Token da Deriv não encontrado")

        # Decrypt strictly in memory; NEVER log decrypted token
        token = decrypt_secret(self._token_encrypted)
        settings = get_settings()

        if token.startswith("pat_"):
            import httpx
            app_id = settings.DERIV_APP_ID.strip() or "34ZYYnkkxGGXQTva4v3lB"
            acc_loginid = self.account_snapshot.get("loginid", "DOT94651662")
            otp_endpoint = f"https://api.derivws.com/trading/v1/options/accounts/{acc_loginid}/otp"

            async def get_pat_otp_ws_url() -> str:
                async with httpx.AsyncClient(timeout=10.0) as http_client:
                    otp_resp = await http_client.post(
                        otp_endpoint,
                        json={},
                        headers={
                            "Authorization": f"Bearer {token}",
                            "Deriv-App-ID": app_id,
                            "User-Agent": "Mozilla/5.0",
                        },
                    )
                    if otp_resp.status_code == 200:
                        return otp_resp.json().get("data", {}).get("url")
                    raise DerivAPIError(f"Falha ao gerar sessão OTP na Deriv: {otp_resp.text}")

            self.client = DerivClient(
                ws_url_factory=get_pat_otp_ws_url,
                token=token,
                on_reconnect=self._handle_reconnect,
                label=f"user-{self.user_id}",
            )
        else:
            ws_url = f"wss://red.binaryws.com/websockets/v3?app_id={settings.DERIV_APP_ID}"
            fallback = f"{settings.DERIV_AUTH_WS_BASE}?app_id={settings.DERIV_APP_ID}"
            self.client = DerivClient(
                ws_url=ws_url,
                fallback_ws_url=fallback,
                token=token,
                on_reconnect=self._handle_reconnect,
                label=f"user-{self.user_id}",
            )
        await self.client.connect(timeout=20.0)

    async def _handle_reconnect(self, _client: DerivClient) -> None:
        logger.info("[user=%d] WebSocket Deriv reconectado — reconciliando portfólio...", self.user_id)
        await self.sync_account()
        await self.reconcile_portfolio()

    async def sync_account(self) -> None:
        """Sync live Deriv account balance into PostgreSQL and Redis."""
        token = decrypt_secret(self._token_encrypted) if self._token_encrypted else ""
        if token.startswith("pat_"):
            try:
                import httpx
                settings = get_settings()
                app_id = settings.DERIV_APP_ID.strip() or "34ZYYnkkxGGXQTva4v3lB"
                async with httpx.AsyncClient(timeout=10.0) as http_client:
                    res = await http_client.get(
                        "https://api.derivws.com/trading/v1/options/accounts",
                        headers={
                            "Authorization": f"Bearer {token}",
                            "Deriv-App-ID": app_id,
                            "User-Agent": "Mozilla/5.0",
                        },
                    )
                    if res.status_code == 200:
                        accounts_data = res.json().get("data") or []
                        if accounts_data:
                            acc = next((a for a in accounts_data if a.get("account_id") == self.account_snapshot.get("loginid")), accounts_data[0])
                            bal = acc.get("balance")
                            if bal is not None:
                                self._balance = float(bal)
                                await asyncio.to_thread(self._persist_balance_db, self._balance)
                                await self.publish_state()
                                return
            except Exception as exc:
                logger.warning("[user=%d] Falha ao sincronizar saldo PAT Deriv: %s", self.user_id, exc)

        if self.client is None:
            return
        try:
            async def _on_balance(msg: dict[str, Any]) -> None:
                bal = (msg.get("balance") or {}).get("balance")
                if bal is not None:
                    self._balance = float(bal)
                    await asyncio.to_thread(self._persist_balance_db, self._balance)
                    await self.publish_state()

            bal_resp = await self.client.get_balance(subscribe=True, callback=_on_balance)
            if bal_resp and bal_resp.get("balance") is not None:
                self._balance = float(bal_resp["balance"])
                await asyncio.to_thread(self._persist_balance_db, self._balance)

            await self._ensure_tick_subscriptions()
        except Exception as exc:
            logger.warning("[user=%d] Falha ao sincronizar saldo Deriv: %s", self.user_id, exc)

    async def _ensure_tick_subscriptions(self) -> None:
        """Subscribe Deriv direct tick feed for all candidate symbols."""
        if self.client is None or not self.client.is_connected:
            return

        async def _on_direct_tick(msg: dict[str, Any]) -> None:
            tick_obj = msg.get("tick") or {}
            sym = str(tick_obj.get("symbol") or "")
            quote = tick_obj.get("quote")
            epoch = tick_obj.get("epoch")
            if sym and quote is not None:
                await self._on_tick(sym, float(quote), int(epoch or 0))

        tick_syms = set(self.get_candidate_symbols())
        for sym in tick_syms:
            if sym in self._subscribed_direct_ticks:
                continue
            try:
                await self.client.subscribe_ticks(sym, _on_direct_tick)
                self._subscribed_direct_ticks.add(sym)
                logger.info("[user=%d] Assinatura direta de ticks ativada para %s", self.user_id, sym)
            except Exception as exc:
                err_msg = str(exc)
                if "MarketIsClosed" in err_msg or "closed" in err_msg.lower():
                    logger.info("[user=%d] Mercado %s fechado no momento (fim de semana). Reagendado.", self.user_id, sym)
                    self._subscribed_direct_ticks.add(sym)
                else:
                    logger.debug("[user=%d] Falha ao assinar ticks de %s: %s", self.user_id, sym, exc)

    async def reconcile_portfolio(self) -> None:
        """Reconcile open contracts on Deriv `portfolio` with local DB Basket/Contract rows."""
        if self.client is None:
            return

        try:
            remote_contracts = await self.client.portfolio()
        except Exception as exc:
            logger.warning("[user=%d] Falha ao consultar portfolio Deriv: %s", self.user_id, exc)
            return

        remote_by_id: dict[str, dict[str, Any]] = {}
        for rc in remote_contracts:
            cid = rc.get("contract_id")
            if cid is not None:
                remote_by_id[str(cid)] = rc

        # 1. Reconcile existing DB open basket contracts
        if self.basket_state is not None:
            for c_snap in list(self.basket_state.contracts):
                if c_snap.status != "OPEN" or not c_snap.deriv_contract_id:
                    continue
                cid = str(c_snap.deriv_contract_id)
                if cid in remote_by_id:
                    try:
                        await self.client.subscribe_open_contract(cid, self._on_contract_update)
                    except Exception as exc:
                        logger.warning("[user=%d] Erro ao assinar contrato %s: %s", self.user_id, cid, exc)
                else:
                    # Contract closed on Deriv while worker was offline
                    await self._reconcile_missing_contract(c_snap)

            if not self.has_open_basket and self.open_basket_id is not None:
                await self._finalize_basket_in_db("STOP_OUT", "Contratos encerrados durante desconexão")

        # 2. Detect any remote open Multiplier contract for the user's symbol not tracked locally
        tracked_ids = {
            str(c.deriv_contract_id)
            for c in (self.basket_state.contracts if self.basket_state else [])
            if c.deriv_contract_id
        }
        configured_symbol = str(self.config_snapshot.get("symbol") or "cryBTCUSD")
        for cid, rc in remote_by_id.items():
            ctype = str(rc.get("contract_type") or "").upper()
            rsym = str(rc.get("symbol") or "")
            if cid in tracked_ids:
                continue
            if ctype in ("MULTUP", "MULTDOWN") and (rsym == configured_symbol or not rsym):
                logger.info(
                    "[user=%d] Contrato Multiplier aberto detectado na Deriv (%s %s) — anexando ao cesto",
                    self.user_id,
                    cid,
                    ctype,
                )
                await self._attach_orphan_remote_contract(cid, rc)
                try:
                    await self.client.subscribe_open_contract(cid, self._on_contract_update)
                except Exception as exc:
                    logger.warning("[user=%d] Falha ao assinar contrato órfão %s: %s", self.user_id, cid, exc)

        await self.publish_state()

    async def _reconcile_missing_contract(self, c_snap: ContractSnapshot) -> None:
        if self.client is None or not c_snap.deriv_contract_id:
            return
        try:
            resp = await self.client.get_open_contract(c_snap.deriv_contract_id)
            poc = resp.get("proposal_open_contract") or {}
            profit = float(poc.get("profit", c_snap.current_profit_usd))
            exit_spot = poc.get("exit_tick") or poc.get("sell_spot") or poc.get("current_spot")
            c_snap.current_profit_usd = profit
            c_snap.status = "STOPPED_OUT" if profit <= -abs(c_snap.stake_usd) * 0.90 else "SOLD"
            await asyncio.to_thread(
                self._update_contract_closed_db,
                c_snap.deriv_contract_id,
                c_snap.status,
                profit,
                float(exit_spot) if exit_spot is not None else None,
            )
        except Exception as exc:
            logger.warning("[user=%d] Não foi possível consultar contrato %s: %s", self.user_id, c_snap.deriv_contract_id, exc)
            c_snap.status = "SOLD"

    async def _attach_orphan_remote_contract(self, cid: str, rc: dict[str, Any]) -> None:
        symbol = str(rc.get("symbol") or self.config_snapshot.get("symbol") or "cryBTCUSD")
        ctype = str(rc.get("contract_type") or "MULTUP").upper()
        stake = float(rc.get("buy_price") or self.config_snapshot.get("stake_usd") or 1.0)
        mult = int(self.config_snapshot.get("multiplier") or 100)
        entry_spot = float(rc.get("entry_spot") or 0.0)

        def _db_attach() -> tuple[int, int]:
            with session_scope() as db:
                basket = (
                    db.query(Basket)
                    .filter(Basket.user_id == self.user_id, Basket.status == "OPEN")
                    .first()
                )
                if basket is None:
                    basket = Basket(
                        user_id=self.user_id,
                        deriv_account_id=self._active_account_id,
                        account_type=str(self.account_snapshot.get("account_type") or "DEMO"),
                        symbol=symbol,
                        direction=ctype,
                        status="OPEN",
                        layers_count=0,
                        total_stake_usd=0.0,
                        opened_at=utcnow(),
                    )
                    db.add(basket)
                    db.flush()

                existing = db.query(Contract).filter(Contract.deriv_contract_id == cid).first()
                if existing is None:
                    layer_idx = (basket.layers_count or 0) + 1
                    contract = Contract(
                        basket_id=basket.id,
                        user_id=self.user_id,
                        layer_index=layer_idx,
                        deriv_contract_id=cid,
                        contract_type=ctype,
                        symbol=symbol,
                        stake_usd=stake,
                        multiplier=mult,
                        entry_spot=entry_spot,
                        status="OPEN",
                        opened_at=utcnow(),
                    )
                    db.add(contract)
                    basket.layers_count = layer_idx
                    basket.total_stake_usd = round((basket.total_stake_usd or 0.0) + stake, 2)
                else:
                    layer_idx = existing.layer_index
                return int(basket.id), int(layer_idx)

        basket_id, layer_idx = await asyncio.to_thread(_db_attach)
        self.open_basket_id = basket_id
        if self.basket_state is None:
            self.basket_state = BasketState(
                symbol=symbol,
                direction=ctype,
                opened_at=utcnow(),
                contracts=[],
            )
        self.basket_state.contracts.append(
            ContractSnapshot(
                layer_index=layer_idx,
                contract_type=ctype,
                stake_usd=stake,
                multiplier=mult,
                entry_spot=entry_spot,
                deriv_contract_id=cid,
                status="OPEN",
            )
        )

    async def _on_tick(self, symbol: str, price: float, epoch: int) -> None:
        if not self._started or price <= 0.0:
            return

        active_sym = str(self.config_snapshot.get("symbol") or "1HZ100V")
        if symbol == active_sym:
            self._tick_engine.update_tick(price, epoch)
            now = asyncio.get_running_loop().time()
            if (now - self._last_state_flush_ts) >= 1.0:
                self._last_state_flush_ts = now
                await self.publish_state()

        strategy_mode = str(self.config_snapshot.get("strategy_mode") or "NEXUS_QUANTUM")
        if strategy_mode in ("NEXUS_QUANTUM", "SYNTRA_TICKS"):
            if symbol not in self._nexus_engine.symbols:
                return
            target_profit = float(self.config_snapshot.get("daily_profit_target_usd") or 50.0)
            max_loss = abs(float(self.config_snapshot.get("max_daily_loss_usd") or 50.0))
            session_profit = float(self.config_snapshot.get("session_profit_usd") or 0.0)

            # Check if Meta or Stop Loss reached
            if session_profit >= target_profit or self._nexus_engine.session_profit_usd >= target_profit:
                if self.config_snapshot.get("enabled"):
                    await self._disable_bot_in_db("META ATINGIDA 🎯")
                    await self._log_event(
                        "TRADE",
                        f"🎯 META DE LUCRO ATINGIDA! Total acumulado na sessão: +${session_profit:.2f} USD (Meta: ${target_profit:.2f}). Robô Nexus Quantum finalizado com sucesso!",
                        symbol=symbol,
                    )
                    await self.publish_state()
                return

            if session_profit <= -max_loss or self._nexus_engine.session_profit_usd <= -max_loss:
                if self.config_snapshot.get("enabled"):
                    await self._disable_bot_in_db("STOP LOSS 🛑")
                    await self._log_event(
                        "WARN",
                        f"🛑 LIMITE DE PERDA ATINGIDO! Perda acumulada: -${abs(session_profit):.2f} USD (Stop: -${max_loss:.2f}). Robô Nexus Quantum desativado para proteção do capital.",
                        symbol=symbol,
                    )
                    await self.publish_state()
                return

            if self._nexus_engine.is_locked_stop:
                if self.config_snapshot.get("enabled"):
                    await self._disable_bot_in_db(self._nexus_engine.lock_reason)
                    await self._log_event(
                        "WARN",
                        f"🛑 {self._nexus_engine.lock_reason}. Robô Nexus Quantum pausado automaticamente.",
                        symbol=symbol,
                    )
                    await self.publish_state()
                return

            lead_state = self._get_binance_state_if_enabled(symbol)
            sig = self._nexus_engine.on_tick(symbol, price, epoch, binance_lead_state=lead_state)
            # Also keep syntra telemetry in sync for legacy dashboard consumers
            self._syntra_engine.on_tick(symbol, price, epoch)

            now = asyncio.get_running_loop().time()
            if (now - self._last_state_flush_ts) >= 1.0:
                self._last_state_flush_ts = now
                await self.publish_state()

            if bool(self.config_snapshot.get("enabled", False)) and sig is not None:
                asyncio.create_task(self._place_nexus_order(sig))
            return

        if strategy_mode in ("SWING_GRID_RESPIROS", "BOTH_ROBOTS", "GRADIENTE_LINEAR", "TICK_MEAN_REVERSION"):
            target_profit = float(self.config_snapshot.get("daily_profit_target_usd") or 50.0)
            max_loss = abs(float(self.config_snapshot.get("max_daily_loss_usd") or 100.0))
            session_profit = float(self.config_snapshot.get("session_profit_usd") or 0.0)

            # Check if Meta already reached
            if session_profit >= target_profit:
                if self.config_snapshot.get("enabled"):
                    await self._disable_bot_in_db("META ATINGIDA 🎯")
                    await self._log_event(
                        "TRADE",
                        f"🎯 META DE LUCRO ATINGIDA! Total acumulado na sessão: +${session_profit:.2f} (Meta: ${target_profit:.2f}). Robô Swing Grid finalizado com sucesso!",
                        symbol=symbol,
                    )
                return

            # Check if Max Loss reached
            if session_profit <= -max_loss:
                if self.config_snapshot.get("enabled"):
                    await self._disable_bot_in_db("STOP LOSS 🛑")
                    await self._log_event(
                        "WARN",
                        f"🛑 LIMITE DE PERDA ATINGIDO! Perda na sessão: -${abs(session_profit):.2f} (Limite: -${max_loss:.2f}). Robô desativado para proteção do capital.",
                        symbol=symbol,
                    )
                return

            active_sym = str(self.config_snapshot.get("symbol") or "1HZ100V")
            if symbol == active_sym:
                self._respiros_engine.update_tick(price, epoch)

                # 1) In position (Basket open) -> Check Respiro Exit or Accumulation
                if self.has_open_basket and self.basket_state is not None:
                    open_contracts = [c for c in self.basket_state.contracts if c.status == "OPEN"]
                    net_pnl = sum(c.current_profit_usd for c in open_contracts)

                    # Rehydrate cycle if missing
                    if self._respiros_cycle is None:
                        open_layers = [c for c in open_contracts if c.entry_spot and c.entry_spot > 0]
                        first_spot = float(open_layers[0].entry_spot) if open_layers else price
                        self._respiros_cycle = self._respiros_engine.initialize_cycle(
                            cycle_id=self.open_basket_id or 1,
                            symbol=symbol,
                            spot=first_spot,
                            direction=self.basket_state.direction,
                        )
                        for c in open_layers[1:]:
                            self._respiros_engine.register_layer(
                                self._respiros_cycle,
                                layer_index=c.layer_index,
                                spot=float(c.entry_spot),
                                contract_id=c.deriv_contract_id,
                            )

                    sig = self._respiros_engine.evaluate_cycle(
                        self._respiros_cycle,
                        current_spot=price,
                        current_net_pnl_usd=net_pnl,
                    )

                    if sig.action == "EXIT_RESPIRO":
                        self._respiros_cycle = None
                        await self.close_open_basket(
                            reason="RESPIRO_PROFIT",
                            details=f"🎯 Lucro no Respiro: +${net_pnl:.2f} USD @ {price:.4f}",
                        )
                        return

                    elif sig.action == "STOP_BASKET":
                        self._respiros_cycle = None
                        await self.close_open_basket(
                            reason="STOP_LOSS",
                            details=f"🛑 Stop do cesto atingido (-${abs(net_pnl):.2f} USD)",
                        )
                        return

                    elif sig.action == "ACCUMULATE_LAYER" and not self._opening_layer:
                        verdict = self._evaluate_risk()
                        if verdict.allow_reentry:
                            self._opening_layer = True
                            try:
                                await self._log_event(
                                    "TRADE",
                                    f"[Swing Grid] 📐 Acumulando Camada #{sig.layer_index} no recuo em {symbol} @ {price:.4f}: {sig.reason}",
                                    symbol=symbol,
                                )
                                await self._open_layer(
                                    direction=self.basket_state.direction,
                                    layer_index=sig.layer_index,
                                    reasons=[sig.reason],
                                    symbol=symbol,
                                    custom_stake_usd=sig.stake_usd,
                                )
                                self._respiros_engine.register_layer(
                                    self._respiros_cycle,
                                    layer_index=sig.layer_index,
                                    spot=price,
                                )
                            finally:
                                self._opening_layer = False
                            await self.publish_state()
                            return

                # 2) Flat (No basket open) and bot enabled -> Open Layer #1
                elif not self.has_open_basket and bool(self.config_snapshot.get("enabled", False)) and not self._opening_layer:
                    verdict = self._evaluate_risk()
                    if verdict.allow_new_entry:
                        dir_mode = str(self.config_snapshot.get("direction_mode") or "BOTH")
                        sig = self._respiros_engine.evaluate_cycle(
                            None,
                            current_spot=price,
                            current_net_pnl_usd=0.0,
                            direction_mode=dir_mode,
                        )
                        if sig.action == "OPEN_LAYER_1":
                            self._opening_layer = True
                            try:
                                self._respiros_cycle = self._respiros_engine.initialize_cycle(
                                    cycle_id=1,
                                    symbol=symbol,
                                    spot=price,
                                    direction=sig.direction,
                                )
                                await self._log_event(
                                    "TRADE",
                                    f"[Swing Grid] 🚀 Novo ciclo de acumulação em {symbol}: Entrada #1 ({sig.direction}) @ {price:.4f} | Alvo Respiro: +${self._respiros_cycle.target_net_profit_usd:.2f}",
                                    symbol=symbol,
                                )
                                await self._open_layer(
                                    direction=sig.direction,
                                    layer_index=1,
                                    reasons=[sig.reason],
                                    symbol=symbol,
                                    custom_stake_usd=sig.stake_usd,
                                )
                            finally:
                                self._opening_layer = False
                            await self.publish_state()
                            return

        elif strategy_mode == "LINREG_ATR_VIDEO":
            # 1) Dynamic ATR(10) price exit while basket is open
            if self.has_open_basket and self.basket_state is not None:
                active_sym = str(self.basket_state.symbol or self.config_snapshot.get("symbol") or "cryBTCUSD")
                if symbol == active_sym and self._video_atr_at_entry > 0.0:
                    verdict = self._evaluate_risk()
                    if verdict.allow_exit:
                        open_layers = [c for c in self.basket_state.contracts if c.status == "OPEN" and c.entry_spot > 0]
                        if open_layers:
                            avg_entry = self.basket_manager.compute_avg_entry_price(open_layers)
                            first_entry = float(open_layers[0].entry_spot)
                            tp_px, sl_px = compute_atr_exit_levels(
                                direction=self.basket_state.direction,
                                avg_entry_price=avg_entry,
                                first_entry_price=first_entry,
                                atr_value=self._video_atr_at_entry,
                                tp_atr_mult=float(self.config_snapshot.get("tp_atr_mult") or 0.75),
                                sl_atr_mult=float(self.config_snapshot.get("sl_atr_mult") or 1.90),
                            )
                            atr_exit = evaluate_atr_price_exit(self.basket_state.direction, price, tp_px, sl_px)
                            if atr_exit is not None:
                                await self.close_open_basket(
                                    reason=atr_exit,
                                    details=(
                                        f"Saída ATR({self.config_snapshot.get('atr_period', 10)}) "
                                        f"@ {price:.4f} (TP={tp_px:.4f}, SL={sl_px:.4f})"
                                    ),
                                )
                                return

            # 2) Intrabar limit band touch trigger when flat and order is armed (multi-pair scanner supported)
            elif not self.has_open_basket and bool(self.config_snapshot.get("enabled", False)):
                candidates = self.get_candidate_symbols()
                if symbol in candidates:
                    armed = self._armed_video_orders.get(symbol) or (
                        self._armed_video_order if symbol == self.config_snapshot.get("symbol") else None
                    )
                    if armed is not None and check_band_touch(armed, price):
                        verdict = self._evaluate_risk()
                        if verdict.allow_new_entry:
                            self._armed_video_orders.clear()
                            self._armed_video_order = None
                            self._video_atr_at_entry = armed.atr_value
                            side_lbl = "COMPRA (Banda Inf.)" if armed.direction == "MULTUP" else "VENDA (Banda Sup.)"
                            is_multi = bool(self.config_snapshot.get("multi_pair_enabled", False))
                            prefix = "[Scanner Multi-Pares]" if is_multi else "[Modo Vídeo]"
                            await self._log_event(
                                "SIGNAL",
                                (
                                    f"{prefix} 🎯 Toque intrabar {side_lbl} em {symbol} @ {price:.4f} "
                                    f"(gatilho={armed.trigger_price:.4f}, ATR={armed.atr_value:.4f}) — Executando ordem no primeiro par!"
                                ),
                                symbol=symbol,
                            )
                            await self._open_layer(
                                direction=armed.direction,
                                layer_index=1,
                                reasons=[f"{prefix} {side_lbl} @ {armed.trigger_price:.4f}"],
                                symbol=symbol,
                            )
                            await self.publish_state()
                            return

        # Throttled live state refresh for dashboard chart overlays
        now = asyncio.get_running_loop().time()
        if self.has_open_basket and (now - self._last_state_flush_ts) >= 2.0:
            self._last_state_flush_ts = now
            await self.publish_state()

    async def _on_contract_update(self, msg: dict[str, Any]) -> None:
        """Handle real-time `proposal_open_contract` stream updates from Deriv."""
        poc = msg.get("proposal_open_contract")
        if not poc or self.basket_state is None:
            return

        cid = str(poc.get("contract_id") or "")
        if not cid:
            return

        target: Optional[ContractSnapshot] = None
        for c in self.basket_state.contracts:
            if str(c.deriv_contract_id) == cid:
                target = c
                break
        if target is None:
            return

        profit = float(poc.get("profit", target.current_profit_usd))
        current_spot = poc.get("current_spot")
        entry_spot = poc.get("entry_spot")
        is_sold = bool(int(poc.get("is_sold", 0)) == 1 or str(poc.get("status", "")).lower() == "sold")
        target.current_profit_usd = profit
        if entry_spot is not None and target.entry_spot <= 0.0:
            target.entry_spot = float(entry_spot)

        if is_sold and target.status == "OPEN":
            exit_spot = poc.get("exit_tick") or poc.get("sell_spot") or current_spot
            target.status = "STOPPED_OUT" if profit <= -abs(target.stake_usd) * 0.90 else "SOLD"
            await asyncio.to_thread(
                self._update_contract_closed_db,
                cid,
                target.status,
                profit,
                float(exit_spot) if exit_spot is not None else None,
            )
            if target.status == "STOPPED_OUT":
                await self._log_event(
                    "WARN",
                    f"Camada #{target.layer_index} atingiu stop-out individual na Deriv (${profit:.2f})",
                    symbol=self.basket_state.symbol,
                )
        else:
            await asyncio.to_thread(
                self._update_contract_live_db,
                cid,
                profit,
                float(current_spot) if current_spot is not None else None,
            )

        net_pnl = self.basket_manager.compute_net_pnl(self.basket_state.contracts)
        self.risk_guard.set_floating_pnl(net_pnl)
        if hasattr(self, "_nexus_engine") and self.basket_state.symbol in self._nexus_engine.states:
            self._nexus_engine.states[self.basket_state.symbol].current_floating_pnl = net_pnl

        # If all contracts were closed externally / stopped out, finalize basket cleanly
        if not self.has_open_basket and self.open_basket_id is not None:
            await self._finalize_basket_in_db("STOP_OUT", "Todos os contratos do cesto foram encerrados")
            await self.publish_state()
            return

        # Evaluate real-time exit triggers (TP, Trailing, Global SL, Timeout)
        verdict = self._evaluate_risk()
        if verdict.allow_exit:
            decision = self.basket_manager.evaluate_exit(self.basket_state)
            await asyncio.to_thread(
                self._update_basket_live_db,
                net_pnl,
                self.basket_state.peak_pnl_usd,
            )
            if decision.should_close and decision.reason:
                await self.close_open_basket(reason=decision.reason, details=decision.details)
                return

        now = asyncio.get_running_loop().time()
        if (now - self._last_state_flush_ts) >= 1.0:
            self._last_state_flush_ts = now
            await self.publish_state()

    async def _on_candle_close(
        self,
        symbol: str,
        granularity: int,
        candles: list[dict[str, Any]],
        channel: Optional[ChannelSnapshot],
    ) -> None:
        """Evaluate causal channel strategy, DCA re-entries, and structural channel-break stops."""
        if not self._started:
            return
        if self.has_open_basket and self.basket_state is not None:
            active_sym = str(self.basket_state.symbol or self.config_snapshot.get("symbol") or "cryBTCUSD")
            if symbol != active_sym:
                return
        else:
            candidates = self.get_candidate_symbols()
            if symbol not in candidates:
                return
        if int(granularity) != int(self.config_snapshot.get("timeframe_sec", 300)):
            return

        # Refresh user subscription status & cooldown counter
        await asyncio.to_thread(self._refresh_user_access_from_db)
        self.risk_guard.on_candle_close()
        verdict = self._evaluate_risk()
        await self._update_status_text(verdict.status_text)

        closed_candles = [c for c in candles if c.get("is_closed", True)]
        if not closed_candles:
            return
        last_close = float(closed_candles[-1]["close"])

        # 1. Manage existing open basket
        bot_enabled = bool(self.config_snapshot.get("enabled", False))
        if self.has_open_basket and self.basket_state is not None:
            if verdict.allow_exit:
                exit_dec = self.basket_manager.evaluate_exit(
                    self.basket_state,
                    channel=channel,
                    latest_closed_close=last_close,
                )
                if exit_dec.should_close and exit_dec.reason:
                    await self.close_open_basket(reason=exit_dec.reason, details=exit_dec.details)
                    return

            # Evaluate Smart DCA re-entry if bot is enabled, risk allows, and below max_layers
            open_layers = [c for c in self.basket_state.contracts if c.status == "OPEN"]
            if bot_enabled and verdict.allow_reentry and len(open_layers) < self.basket_manager.max_layers:
                last_entry = float(open_layers[-1].entry_spot)
                atr_val = channel.atr if channel is not None else None
                min_step = self.basket_manager.compute_dynamic_step_pct(
                    stop_out_pct=self._last_stop_out_pct,
                    atr=atr_val,
                    current_price=last_close,
                )
                b_state = self._get_binance_state_if_enabled(symbol)
                dca_dec = dca_reentry_signal(
                    direction=self.basket_state.direction,
                    candles=closed_candles,
                    channel=channel,
                    last_entry_price=last_entry,
                    min_step_pct=min_step,
                    binance_state=b_state,
                )
                if dca_dec.should_enter:
                    next_layer = len(self.basket_state.contracts) + 1
                    await self._open_layer(
                        direction=self.basket_state.direction,
                        layer_index=next_layer,
                        reasons=dca_dec.reasons,
                    )
            await self.publish_state()
            return

        # 2. No open basket -> enforce auto-disable or evaluate Entry #1
        if not bot_enabled:
            await self.stop(reason_status="OFF")
            return

        if verdict.should_disable_bot:
            await self._disable_bot_in_db(verdict.status_text)
            await self._log_event("WARN", verdict.reason or "Robô desativado automaticamente", symbol=symbol)
            await self.publish_state()
            return

        if not verdict.allow_new_entry:
            self._armed_video_order = None
            await self.publish_state()
            return

        strategy_mode = str(self.config_snapshot.get("strategy_mode") or "SMART_CHANNEL_DCA")
        if strategy_mode == "LINREG_ATR_VIDEO":
            prev_armed = self._armed_video_orders.get(symbol)
            armed = arm_video_band_order(
                candles=closed_candles,
                direction_mode=str(self.config_snapshot.get("direction_mode") or "BOTH"),
                channel_window=int(self.config_snapshot.get("channel_window") or 50),
                channel_sigma=float(self.config_snapshot.get("channel_sigma") or 2.0),
                atr_period=int(self.config_snapshot.get("atr_period") or 10),
                use_crash_guard=bool(self.config_snapshot.get("use_crash_guard", True)),
                channel=channel,
            )
            if armed is not None:
                armed.symbol = symbol
                self._armed_video_orders[symbol] = armed
                if (
                    symbol == self.config_snapshot.get("symbol")
                    or prev_armed is None
                    or prev_armed.direction != armed.direction
                ):
                    side_txt = "COMPRA em LinB" if armed.direction == "MULTUP" else "VENDA em LinC"
                    is_multi = bool(self.config_snapshot.get("multi_pair_enabled", False))
                    prefix = "[Scanner Multi-Pares]" if is_multi else "[Modo Vídeo]"
                    await self._log_event(
                        "INFO",
                        (
                            f"{prefix} Ordem armada em {symbol}: {side_txt} @ {armed.trigger_price:.4f} "
                            f"(ATR{self.config_snapshot.get('atr_period', 10)}={armed.atr_value:.4f})"
                        ),
                        symbol=symbol,
                    )
            else:
                self._armed_video_orders.pop(symbol, None)

            primary_sym = str(self.config_snapshot.get("symbol") or "cryBTCUSD")
            self._armed_video_order = self._armed_video_orders.get(primary_sym) or next(iter(self._armed_video_orders.values()), None)
            await self.publish_state()
            return

        if channel is None:
            return

        b_state = self._get_binance_state_if_enabled(symbol)
        sig = entry_signal(
            direction_mode=str(self.config_snapshot.get("direction_mode") or "LONG_ONLY"),
            candles=closed_candles,
            channel=channel,
            binance_state=b_state,
        )
        if sig.should_enter and sig.direction:
            await self._log_event(
                "SIGNAL",
                (
                    f"Canal de {'alta' if sig.direction == 'MULTUP' else 'baixa'} confirmado em {symbol} "
                    f"(R²={channel.r2:.2f}) — {', '.join(sig.reasons)}"
                ),
                symbol=symbol,
                details=sig.to_dict(),
            )
            await self._open_layer(direction=sig.direction, layer_index=1, reasons=sig.reasons)
            await self.publish_state()

    def _get_binance_state_if_enabled(self, symbol: str) -> Optional[dict[str, Any]]:
        if (
            self.binance_lead is not None
            and bool(self.config_snapshot.get("use_binance_filter", True))
            and symbol == "cryBTCUSD"
        ):
            return self.binance_lead.get_state()
        return None

    async def _open_layer(
        self,
        direction: str,
        layer_index: int,
        reasons: list[str],
        symbol: Optional[str] = None,
        custom_stake_usd: Optional[float] = None,
    ) -> Optional[ContractSnapshot]:
        """Place a Multiplier order via `proposal` + `buy` guarded by Redis distributed lock."""
        if self.client is None:
            return None

        target_symbol = str(
            symbol
            or (self.basket_state.symbol if self.basket_state else None)
            or self.config_snapshot.get("symbol")
            or "cryBTCUSD"
        )
        currency = str(self.account_snapshot.get("currency") or "USD")
        asset_info = SUPPORTED_ASSETS.get(target_symbol, {})
        default_asset_mult = int(asset_info.get("default_mult") or 100)
        config_mult = int(self.config_snapshot.get("multiplier") or 0)
        multiplier = config_mult if config_mult >= 20 else default_asset_mult
        if target_symbol == "R_50" and multiplier not in (80, 200, 400, 600, 800):
            multiplier = 80
        base_stake = float(self.config_snapshot.get("stake_usd") or 1.0)
        if custom_stake_usd is not None and custom_stake_usd > 0:
            stake = round(float(custom_stake_usd), 2)
        else:
            stake = self.basket_manager.compute_layer_stake(
                base_stake_usd=base_stake,
                layer_index=layer_index,
                max_stake_usd=self.max_stake_usd,
            )

        lock_key = key_order_lock(self.user_id, target_symbol)
        acquired_redis_lock = False

        async with self._trade_lock:
            if self.redis is not None:
                acquired_redis_lock = bool(await self.redis.set(lock_key, "1", nx=True, ex=15))
                if not acquired_redis_lock:
                    logger.info("[user=%d] Ordem ignorada: lock distribuído ativo em %s", self.user_id, lock_key)
                    return None
            try:
                # 1. Request proposal
                prop_resp = await self.client.proposal(
                    symbol=target_symbol,
                    contract_type=direction,
                    amount=stake,
                    multiplier=multiplier,
                    currency=currency,
                )
                prop = prop_resp.get("proposal") or {}
                proposal_id = str(prop.get("id") or "")
                if not proposal_id:
                    raise DerivAPIError("ProposalMissingID", "Resposta de proposal sem ID")

                ask_price = float(prop.get("ask_price") or stake)
                commission = float(prop.get("commission") or 0.0)
                stop_out_pct, stop_out_spot = extract_stop_out_pct(
                    prop_resp,
                    contract_type=direction,
                    fallback_stake=stake,
                    fallback_multiplier=multiplier,
                )
                self._last_stop_out_pct = stop_out_pct

                # 2. Execute buy
                buy_resp = await self.client.buy(proposal_id=proposal_id, price=ask_price)
                buy_data = buy_resp.get("buy") or {}
                deriv_cid = str(buy_data.get("contract_id") or "")
                buy_price = float(buy_data.get("buy_price") or ask_price)
                entry_spot = float(buy_data.get("start_spot") or prop.get("spot") or 0.0)

                if not deriv_cid:
                    raise DerivAPIError("BuyMissingContractID", "Compra retornou sem contract_id")

                c_snap = ContractSnapshot(
                    layer_index=layer_index,
                    contract_type=direction,
                    stake_usd=buy_price,
                    multiplier=multiplier,
                    entry_spot=entry_spot,
                    current_profit_usd=-commission,
                    commission_usd=commission,
                    stop_out_spot=stop_out_spot,
                    deriv_contract_id=deriv_cid,
                    status="OPEN",
                )

                # 3. Persist Basket + Contract in PostgreSQL
                basket_id = await asyncio.to_thread(
                    self._persist_opened_layer_db,
                    target_symbol,
                    direction,
                    c_snap,
                )
                self.open_basket_id = basket_id

                if self.basket_state is None:
                    self.basket_state = BasketState(
                        symbol=target_symbol,
                        direction=direction,
                        opened_at=utcnow(),
                        contracts=[c_snap],
                    )
                else:
                    self.basket_state.contracts.append(c_snap)

                # 4. Subscribe to live proposal_open_contract updates
                await self.client.subscribe_open_contract(deriv_cid, self._on_contract_update)

                await self._update_status_text("EM OPERAÇÃO")
                await self._log_event(
                    "TRADE",
                    (
                        f"Entrada #{layer_index} executada em {target_symbol} ({direction} ${buy_price:.2f} "
                        f"x{multiplier} @ {entry_spot:.2f} | comissão ${commission:.2f})"
                    ),
                    symbol=target_symbol,
                    details={
                        "layer": layer_index,
                        "contract_id": deriv_cid,
                        "stake_usd": buy_price,
                        "multiplier": multiplier,
                        "entry_spot": entry_spot,
                        "stop_out_pct": round(stop_out_pct * 100, 3),
                        "reasons": reasons,
                    },
                )
                return c_snap
            except DerivAPIError as exc:
                await self._log_event(
                    "ERROR",
                    f"Deriv recusou ordem em {symbol} (Camada #{layer_index}): {exc}",
                    symbol=symbol,
                )
                return None
            except Exception as exc:
                await self._log_event(
                    "ERROR",
                    f"Erro inesperado ao abrir Camada #{layer_index} em {symbol}: {exc}",
                    symbol=symbol,
                )
                return None
            finally:
                if acquired_redis_lock and self.redis is not None:
                    try:
                        await self.redis.delete(lock_key)
                    except Exception:
                        pass

    async def _place_nexus_order(self, sig: NexusSignal) -> None:
        """Execute Multiplier entry (Layer 1) or Smart DCA re-entry on Deriv for Nexus Quantum AI."""
        if self.client is None or not self.client.is_connected:
            logger.warning("[NexusQuantum] Cliente Deriv desconectado, não pode abrir ordem em %s", sig.symbol)
            return

        stake = round(float(sig.stake_usd), 2)
        if stake < 0.50:
            stake = 0.50

        # Safety check: ensure balance is sufficient
        if self._balance > 0 and stake > self._balance:
            logger.warning("[NexusQuantum] Saldo insuficiente ($%.2f) para stake $%.2f", self._balance, stake)
            await self._log_event(
                "WARN",
                f"Saldo insuficiente ($ {self._balance:.2f} USD) para executar stake de $ {stake:.2f} USD.",
                symbol=sig.symbol,
            )
            return

        dir_norm = "MULTUP" if sig.direction in ("CALL", "MULTUP") else "MULTDOWN"

        if sig.is_reentry:
            if not self.has_open_basket or self.basket_state is None:
                return
            c_snap = await self._open_layer(
                direction=dir_norm,
                layer_index=sig.layer_index,
                reasons=[sig.reason],
                symbol=sig.symbol,
                custom_stake_usd=stake,
            )
            if c_snap is not None:
                self._nexus_engine.on_layer_added(sig.symbol, sig.layer_index, sig.current_spot, stake)
                await self.publish_state()
            else:
                logger.warning("[NexusQuantum] Falha ao adicionar camada na Deriv para %s", sig.symbol)
        else:
            if self.has_open_basket:
                return
            c_snap = await self._open_layer(
                direction=dir_norm,
                layer_index=1,
                reasons=[sig.reason],
                symbol=sig.symbol,
                custom_stake_usd=stake,
            )
            if c_snap is not None:
                self._nexus_engine.on_basket_opened(sig.symbol, dir_norm, sig.current_spot, stake)
                await self.publish_state()
            else:
                logger.warning("[NexusQuantum] Falha ao abrir ordem na Deriv para %s", sig.symbol)

    async def _place_nexus_tick_order(self, sig: NexusSignal) -> None:
        """Alias for _place_nexus_order."""
        await self._place_nexus_order(sig)

    async def _monitor_nexus_contract(
        self,
        deriv_cid: str,
        symbol: str,
        direction: str,
        stake: float,
        payout: float,
        entry_spot: float,
        opened_at: Any,
    ) -> None:
        """Monitor contract settlement via POC subscription and polling fallback."""
        settled = False

        async def _on_poc(msg: dict[str, Any]) -> None:
            nonlocal settled
            poc = msg.get("proposal_open_contract") or {}
            cid = str(poc.get("contract_id") or "")
            if cid != deriv_cid:
                return
            if poc.get("is_sold") == 1 and not settled:
                settled = True
                await self._handle_nexus_contract_settled(
                    poc=poc,
                    symbol=symbol,
                    direction=direction,
                    stake=stake,
                    payout=payout,
                    entry_spot=entry_spot,
                    opened_at=opened_at,
                )

        try:
            await self.client.subscribe_open_contract(deriv_cid, _on_poc)
        except Exception as exc:
            logger.debug("[NexusQuantum] Erro ao subscrever POC: %s", exc)

        for _ in range(35):
            if settled:
                break
            await asyncio.sleep(0.4)
            if self.client is None or not self.client.is_connected:
                continue
            try:
                resp = await self.client.get_open_contract(deriv_cid)
                poc = resp.get("proposal_open_contract") or {}
                if poc.get("is_sold") == 1 and not settled:
                    settled = True
                    await self._handle_nexus_contract_settled(
                        poc=poc,
                        symbol=symbol,
                        direction=direction,
                        stake=stake,
                        payout=payout,
                        entry_spot=entry_spot,
                        opened_at=opened_at,
                    )
                    break
            except Exception:
                pass

    async def _handle_nexus_contract_settled(
        self,
        poc: dict[str, Any],
        symbol: str,
        direction: str,
        stake: float,
        payout: float,
        entry_spot: float,
        opened_at: Any,
    ) -> None:
        contract_id = str(poc.get("contract_id") or "")
        profit = float(poc.get("profit") or 0.0)
        exit_spot = float(poc.get("exit_tick") or poc.get("barrier") or entry_spot)
        payout_ret = float(poc.get("payout") or payout or (stake + profit if profit > 0 else 0.0))
        epoch = int(poc.get("date_expiry") or poc.get("exit_tick_time") or time.time())
        closed_at = utcnow()

        trade_info = self._nexus_engine.on_contract_closed(
            symbol=symbol,
            contract_id=contract_id,
            profit_usd=profit,
            stake_usd=stake,
            payout_usd=payout_ret,
            exit_spot=exit_spot,
            epoch=epoch,
        )

        curr_session_profit = float(self.config_snapshot.get("session_profit_usd") or 0.0)
        new_session_profit = round(curr_session_profit + profit, 4)
        self.config_snapshot["session_profit_usd"] = new_session_profit
        cycles = int(self.config_snapshot.get("session_cycles_count") or 0) + 1
        self.config_snapshot["session_cycles_count"] = cycles

        def _persist_tick_trade():
            with session_scope() as db:
                basket = Basket(
                    user_id=self.user_id,
                    deriv_account_id=self._active_account_id,
                    account_type=str(self.account_snapshot.get("account_type") or "DEMO"),
                    symbol=symbol,
                    direction=direction,
                    status="CLOSED",
                    layers_count=1,
                    total_stake_usd=stake,
                    total_commission_usd=0.0,
                    avg_entry_price=entry_spot,
                    current_pnl_usd=profit,
                    realized_pnl_usd=profit,
                    opened_at=opened_at,
                    closed_at=closed_at,
                    close_reason="NEXUS_TICK_SETTLED",
                )
                db.add(basket)
                db.flush()

                contract = Contract(
                    basket_id=basket.id,
                    user_id=self.user_id,
                    layer_index=1,
                    deriv_contract_id=contract_id,
                    contract_type=direction,
                    symbol=symbol,
                    stake_usd=stake,
                    multiplier=1,
                    commission_usd=0.0,
                    entry_spot=entry_spot,
                    exit_spot=exit_spot,
                    current_profit_usd=profit,
                    final_profit_usd=profit,
                    status="SOLD",
                    opened_at=opened_at,
                    closed_at=closed_at,
                )
                db.add(contract)

                cfg = db.query(BotConfig).filter(BotConfig.user_id == self.user_id).first()
                if cfg is not None:
                    cfg.session_profit_usd = new_session_profit
                    cfg.session_cycles_count = cycles

        try:
            await asyncio.to_thread(_persist_tick_trade)
        except Exception as exc:
            logger.warning("[NexusQuantum] Erro ao persistir trade no Postgres: %s", exc)

        self.risk_guard.record_closed_basket(profit)

        if profit > 0:
            await self._log_event(
                "TRADE",
                f"🟢 [NEXUS QUANTUM] VITÓRIA: +${profit:.2f} USD ({symbol} {direction}) | Saída: {exit_spot} | Sessão: +${new_session_profit:.2f}",
                symbol=symbol,
            )
        else:
            next_stk = trade_info.get("next_stake_usd", stake)
            await self._log_event(
                "WARN",
                f"🔴 [NEXUS QUANTUM] DERROTA: -${abs(profit):.2f} USD ({symbol} {direction}) | Próximo Stake Recuperação: ${next_stk:.2f} | Sessão: ${new_session_profit:.2f}",
                symbol=symbol,
            )

        target_profit = float(self.config_snapshot.get("daily_profit_target_usd") or 50.0)
        max_loss = abs(float(self.config_snapshot.get("max_daily_loss_usd") or 50.0))

        if new_session_profit >= target_profit or self._nexus_engine.session_profit_usd >= target_profit:
            await self._disable_bot_in_db("META ATINGIDA 🎯")
            await self._log_event(
                "TRADE",
                f"🎯 META DE LUCRO ATINGIDA! Total acumulado na sessão: +${new_session_profit:.2f} USD (Meta: ${target_profit:.2f}). Robô Nexus Quantum finalizado com sucesso!",
                symbol=symbol,
            )
        elif new_session_profit <= -max_loss or self._nexus_engine.session_profit_usd <= -max_loss:
            await self._disable_bot_in_db("STOP LOSS 🛑")
            await self._log_event(
                "WARN",
                f"🛑 LIMITE DE PERDA ATINGIDO! Perda acumulada: -${abs(new_session_profit):.2f} USD (Stop: -${max_loss:.2f}). Robô Nexus Quantum desativado para proteção do capital!",
                symbol=symbol,
            )
        elif self._nexus_engine.is_locked_stop:
            await self._disable_bot_in_db(self._nexus_engine.lock_reason)
            await self._log_event(
                "WARN",
                f"🛑 {self._nexus_engine.lock_reason}. Robô Nexus Quantum desativado!",
                symbol=symbol,
            )

        await self.sync_account()
        await self.publish_state()

    async def _place_syntra_tick_order(self, sig: SyntraSignal) -> None:
        """Execute a 5-tick Rise/Fall contract on Deriv for Syntra Deep Analysis."""
        if self.client is None or not self.client.is_connected:
            logger.warning("[Syntra] Cliente Deriv desconectado, não pode abrir tick trade em %s", sig.symbol)
            return

        stake = float(self.config_snapshot.get("stake_usd") or 0.60)
        if stake < 0.60:
            stake = 0.60

        lock_key = f"syntra_lock:{self.user_id}:{sig.symbol}"
        if self.redis is not None:
            acquired = bool(await self.redis.set(lock_key, "1", nx=True, ex=8))
            if not acquired:
                logger.info("[Syntra] Ordem ignorada: lock ativo em %s", lock_key)
                return

        try:
            prop_resp = await self.client.proposal_tick(
                symbol=sig.symbol,
                contract_type=sig.direction,
                amount=stake,
                duration=sig.duration_ticks,
                duration_unit="t",
            )
            prop = prop_resp.get("proposal") or {}
            proposal_id = str(prop.get("id") or "")
            if not proposal_id:
                logger.error("[Syntra] Resposta de proposta sem ID: %s", prop_resp)
                return

            ask_price = float(prop.get("ask_price") or stake)
            payout = float(prop.get("payout") or 0.0)

            buy_resp = await self.client.buy(proposal_id=proposal_id, price=ask_price)
            buy_data = buy_resp.get("buy") or {}
            deriv_cid = str(buy_data.get("contract_id") or "")
            entry_spot = float(buy_data.get("start_spot") or prop.get("spot") or sig.current_spot)

            if not deriv_cid:
                logger.error("[Syntra] Resposta de compra sem contract_id: %s", buy_resp)
                return

            epoch = asyncio.get_running_loop().time()
            opened_at = utcnow()
            self._syntra_engine.on_contract_opened(
                symbol=sig.symbol,
                contract_id=deriv_cid,
                contract_type=sig.direction,
                entry_spot=entry_spot,
                epoch=epoch,
            )

            await self._log_event(
                "ORDER",
                f"✨ [Syntra AI] Ordem Aberta: {sig.symbol} {sig.direction} (5 ticks) | Stake: ${ask_price:.2f} | Payout: ${payout:.2f} | Contrato #{deriv_cid} | {sig.reason}",
                symbol=sig.symbol,
            )
            await self._update_status_text("OPERAÇÃO TICK ABERTA")
            await self.publish_state()

            asyncio.create_task(
                self._monitor_syntra_contract(
                    deriv_cid=deriv_cid,
                    symbol=sig.symbol,
                    direction=sig.direction,
                    stake=ask_price,
                    entry_spot=entry_spot,
                    opened_at=opened_at,
                )
            )

        except Exception as exc:
            logger.error("[Syntra] Erro ao abrir ordem de tick em %s: %s", sig.symbol, exc, exc_info=True)
            await self._log_event(
                "WARN",
                f"Falha ao executar ordem de tick em {sig.symbol}: {exc}",
                symbol=sig.symbol,
            )

    async def _monitor_syntra_contract(
        self,
        deriv_cid: str,
        symbol: str,
        direction: str,
        stake: float,
        entry_spot: float,
        opened_at: Any,
    ) -> None:
        """Monitor contract settlement via POC subscription and polling fallback."""
        settled = False

        async def _on_poc(msg: dict[str, Any]) -> None:
            nonlocal settled
            poc = msg.get("proposal_open_contract") or {}
            cid = str(poc.get("contract_id") or "")
            if cid != deriv_cid:
                return
            if poc.get("is_sold") == 1 and not settled:
                settled = True
                await self._handle_syntra_contract_settled(
                    poc=poc,
                    symbol=symbol,
                    direction=direction,
                    stake=stake,
                    entry_spot=entry_spot,
                    opened_at=opened_at,
                )

        try:
            await self.client.subscribe_open_contract(deriv_cid, _on_poc)
        except Exception as exc:
            logger.debug("[Syntra] Erro ao subscrever POC: %s", exc)

        for _ in range(30):
            if settled:
                break
            await asyncio.sleep(0.5)
            if self.client is None or not self.client.is_connected:
                continue
            try:
                resp = await self.client.get_open_contract(deriv_cid)
                poc = resp.get("proposal_open_contract") or {}
                if poc.get("is_sold") == 1 and not settled:
                    settled = True
                    await self._handle_syntra_contract_settled(
                        poc=poc,
                        symbol=symbol,
                        direction=direction,
                        stake=stake,
                        entry_spot=entry_spot,
                        opened_at=opened_at,
                    )
                    break
            except Exception:
                pass

    async def _handle_syntra_contract_settled(
        self,
        poc: dict[str, Any],
        symbol: str,
        direction: str,
        stake: float,
        entry_spot: float,
        opened_at: Any,
    ) -> None:
        contract_id = str(poc.get("contract_id") or "")
        profit = float(poc.get("profit") or 0.0)
        exit_spot = float(poc.get("exit_tick") or poc.get("barrier") or entry_spot)
        epoch = asyncio.get_running_loop().time()
        closed_at = utcnow()

        self._syntra_engine.on_contract_closed(
            symbol=symbol,
            contract_id=contract_id,
            profit_usd=profit,
            exit_spot=exit_spot,
            epoch=epoch,
        )

        curr_session_profit = float(self.config_snapshot.get("session_profit_usd") or 0.0)
        new_session_profit = round(curr_session_profit + profit, 4)
        self.config_snapshot["session_profit_usd"] = new_session_profit
        cycles = int(self.config_snapshot.get("session_cycles_count") or 0) + 1
        self.config_snapshot["session_cycles_count"] = cycles

        def _persist_tick_trade():
            with session_scope() as db:
                basket = Basket(
                    user_id=self.user_id,
                    deriv_account_id=self._active_account_id,
                    account_type=str(self.account_snapshot.get("account_type") or "DEMO"),
                    symbol=symbol,
                    direction=direction,
                    status="CLOSED",
                    layers_count=1,
                    total_stake_usd=stake,
                    total_commission_usd=0.0,
                    avg_entry_price=entry_spot,
                    current_pnl_usd=profit,
                    realized_pnl_usd=profit,
                    opened_at=opened_at,
                    closed_at=closed_at,
                    close_reason="SYNTRA_TICK_SETTLED",
                )
                db.add(basket)
                db.flush()

                contract = Contract(
                    basket_id=basket.id,
                    user_id=self.user_id,
                    layer_index=1,
                    deriv_contract_id=contract_id,
                    contract_type=direction,
                    symbol=symbol,
                    stake_usd=stake,
                    multiplier=1,
                    commission_usd=0.0,
                    entry_spot=entry_spot,
                    exit_spot=exit_spot,
                    current_profit_usd=profit,
                    final_profit_usd=profit,
                    status="SOLD",
                    opened_at=opened_at,
                    closed_at=closed_at,
                )
                db.add(contract)

                cfg = db.query(BotConfig).filter(BotConfig.user_id == self.user_id).first()
                if cfg is not None:
                    cfg.session_profit_usd = new_session_profit
                    cfg.session_cycles_count = cycles

        try:
            await asyncio.to_thread(_persist_tick_trade)
        except Exception as exc:
            logger.warning("[Syntra] Erro ao persistir trade no Postgres: %s", exc)

        self.risk_guard.record_closed_basket(profit)

        if profit > 0:
            await self._log_event(
                "TRADE",
                f"🟢 [Syntra AI] VITÓRIA: +${profit:.2f} USD ({symbol} {direction} 5T) | Saída: {exit_spot} | Sessão: +${new_session_profit:.2f}",
                symbol=symbol,
            )
        else:
            await self._log_event(
                "WARN",
                f"🔴 [Syntra AI] DERROTA: -${abs(profit):.2f} USD ({symbol} {direction} 5T) | Saída: {exit_spot} | Sessão: +${new_session_profit:.2f}",
                symbol=symbol,
            )

        target_profit = float(self.config_snapshot.get("daily_profit_target_usd") or 50.0)
        max_loss = abs(float(self.config_snapshot.get("max_daily_loss_usd") or 100.0))

        if new_session_profit >= target_profit:
            await self._disable_bot_in_db("META ATINGIDA 🎯")
            await self._log_event(
                "TRADE",
                f"🎯 META DE LUCRO ATINGIDA! Total acumulado na sessão: +${new_session_profit:.2f} USD (Meta: ${target_profit:.2f}). Robô Deep Analysis finalizado!",
                symbol=symbol,
            )
        elif new_session_profit <= -max_loss:
            await self._disable_bot_in_db("STOP LOSS 🛑")
            await self._log_event(
                "WARN",
                f"🛑 LIMITE DE PERDA ATINGIDO! Perda acumulada: -${abs(new_session_profit):.2f} USD (Stop: -${max_loss:.2f}). Robô desativado!",
                symbol=symbol,
            )
        else:
            await self._update_status_text("ANALISANDO MERCADO")

        await self.publish_state()

    async def close_open_basket(self, reason: str, details: str = "") -> float:
        """Close all open contracts in the active basket simultaneously via `sell(price=0)`."""
        async with self._trade_lock:
            if self.basket_state is None:
                return 0.0

            open_contracts = [c for c in self.basket_state.contracts if c.status == "OPEN" and c.deriv_contract_id]
            symbol = self.basket_state.symbol

            if open_contracts and self.client is not None:
                async def _sell_one(c_snap: ContractSnapshot) -> None:
                    try:
                        resp = await self.client.sell(str(c_snap.deriv_contract_id), price=0.0)
                        sell_obj = resp.get("sell") or {}
                        sold_for = sell_obj.get("sold_for")
                        if sold_for is not None:
                            c_snap.current_profit_usd = round(float(sold_for) - float(c_snap.stake_usd), 6)
                        c_snap.status = "SOLD"
                        await asyncio.to_thread(
                            self._update_contract_closed_db,
                            str(c_snap.deriv_contract_id),
                            "SOLD",
                            c_snap.current_profit_usd,
                            None,
                        )
                    except DerivAPIError as exc:
                        # Contract may have already closed/stopped out milliseconds earlier
                        logger.warning(
                            "[user=%d] Erro ao vender contrato %s: %s",
                            self.user_id,
                            c_snap.deriv_contract_id,
                            exc,
                        )
                        c_snap.status = "SOLD"

                await asyncio.gather(*(_sell_one(c) for c in open_contracts))

            realized_pnl = await self._finalize_basket_in_db(reason=reason, details=details)

            if hasattr(self, "_nexus_engine") and self._nexus_engine is not None:
                self._nexus_engine.on_basket_closed(
                    symbol=symbol,
                    profit_usd=realized_pnl,
                    is_win=bool(realized_pnl > 0),
                    epoch=time.time(),
                )

            # Check if subscription expired during draining or daily stop requires auto-disable
            await asyncio.to_thread(self._refresh_user_access_from_db)
            post_verdict = self._evaluate_risk()
            if post_verdict.should_disable_bot:
                await self._disable_bot_in_db(post_verdict.status_text)
                await self._log_event(
                    "WARN",
                    post_verdict.reason or "Robô desativado automaticamente após encerramento seguro do cesto.",
                    symbol=symbol,
                )
            else:
                await self._update_status_text(post_verdict.status_text)

            await self.publish_state()
            return realized_pnl

    async def kill_switch(self) -> dict[str, Any]:
        """Emergency Kill Switch: immediately close ALL open contracts on Deriv via `sell(price=0)` and disable bot."""
        closed_ids: list[str] = []
        errors: list[str] = []

        async with self._trade_lock:
            target_ids: set[str] = set()
            if self.basket_state is not None:
                for c in self.basket_state.contracts:
                    if c.status == "OPEN" and c.deriv_contract_id:
                        target_ids.add(str(c.deriv_contract_id))

            # Also query Deriv portfolio directly so NO open contract can slip through
            if self.client is not None:
                try:
                    remote = await self.client.portfolio()
                    for rc in remote:
                        cid = rc.get("contract_id")
                        if cid is not None:
                            target_ids.add(str(cid))
                except Exception as exc:
                    errors.append(f"portfolio: {exc}")

                async def _emergency_sell(cid: str) -> None:
                    try:
                        resp = await self.client.sell(cid, price=0.0)
                        sell_obj = resp.get("sell") or {}
                        sold_for = sell_obj.get("sold_for")
                        closed_ids.append(cid)
                        if self.basket_state is not None:
                            for c in self.basket_state.contracts:
                                if str(c.deriv_contract_id) == cid:
                                    if sold_for is not None:
                                        c.current_profit_usd = round(float(sold_for) - float(c.stake_usd), 6)
                                    c.status = "SOLD"
                                    await asyncio.to_thread(
                                        self._update_contract_closed_db,
                                        cid,
                                        "SOLD",
                                        c.current_profit_usd,
                                        None,
                                    )
                    except Exception as exc:
                        errors.append(f"{cid}: {exc}")

                if target_ids:
                    await asyncio.gather(*(_emergency_sell(cid) for cid in sorted(target_ids)))

            if self.basket_state is not None:
                await self._finalize_basket_in_db(
                    reason="KILL_SWITCH",
                    details=f"Kill Switch acionado ({len(closed_ids)} contratos encerrados)",
                )

            await self._disable_bot_in_db("KILL SWITCH")
            await self._log_event(
                "WARN",
                f"KILL SWITCH executado! {len(closed_ids)} contrato(s) encerrado(s) e robô desligado.",
                symbol=self.config_snapshot.get("symbol"),
                details={"closed_contract_ids": closed_ids, "errors": errors},
            )
            await self.publish_state()

        return {
            "user_id": self.user_id,
            "closed_contract_ids": closed_ids,
            "errors": errors,
        }

    async def _finalize_basket_in_db(self, reason: str, details: str = "") -> float:
        if self.basket_state is None:
            return 0.0
        symbol = self.basket_state.symbol
        realized_pnl = self.basket_manager.compute_net_pnl(self.basket_state.contracts)
        peak_pnl = self.basket_state.peak_pnl_usd
        basket_id = self.open_basket_id

        def _db_close() -> tuple[float, int]:
            if basket_id is None:
                return 0.0, 0
            with session_scope() as db:
                basket = db.query(Basket).filter(Basket.id == basket_id).first()
                if basket is not None:
                    basket.status = "CLOSED"
                    basket.current_pnl_usd = realized_pnl
                    basket.realized_pnl_usd = realized_pnl
                    basket.peak_pnl_usd = max(float(basket.peak_pnl_usd or 0.0), peak_pnl)
                    basket.close_reason = reason
                    basket.closed_at = utcnow()
                    for c in basket.contracts:
                        if c.status == "OPEN":
                            c.status = "SOLD"
                            c.final_profit_usd = c.current_profit_usd
                            c.closed_at = utcnow()

                # Accumulate session profit and cycles in BotConfig
                cfg = db.query(BotConfig).filter(BotConfig.user_id == self.user_id).first()
                if cfg is not None:
                    curr_profit = round(float(cfg.session_profit_usd or 0.0) + realized_pnl, 4)
                    curr_cycles = int(cfg.session_cycles_count or 0) + 1
                    cfg.session_profit_usd = curr_profit
                    cfg.session_cycles_count = curr_cycles
                    return curr_profit, curr_cycles
                return 0.0, 0

        new_session_profit, new_cycles = await asyncio.to_thread(_db_close)
        self.config_snapshot["session_profit_usd"] = new_session_profit
        self.config_snapshot["session_cycles_count"] = new_cycles
        self.risk_guard.record_closed_basket(realized_pnl)

        sign_str = "+" if realized_pnl >= 0.0 else "-"
        await self._log_event(
            "TRADE",
            (
                f"Cesto encerrado ({reason}) em {symbol} | "
                f"Lucro líquido: {sign_str}${abs(realized_pnl):.2f}"
                + (f" — {details}" if details else "")
                + f" | Sessão: {'+' if new_session_profit >= 0 else '-'}${abs(new_session_profit):.2f} (Ciclo #{new_cycles})"
            ),
            symbol=symbol,
            details={"basket_id": basket_id, "reason": reason, "realized_pnl_usd": realized_pnl, "session_profit_usd": new_session_profit},
            event_type="basket_closed",
        )

        # Check if Target Profit or Max Loss reached
        target = float(self.config_snapshot.get("daily_profit_target_usd") or 15.0)
        max_loss = abs(float(self.config_snapshot.get("max_daily_loss_usd") or 20.0))

        if new_session_profit >= target:
            await self._disable_bot_in_db("META ATINGIDA 🎯")
            await self._log_event(
                "TRADE",
                f"🎯 META DE LUCRO ATINGIDA! Saldo acumulado na sessão: +${new_session_profit:.2f} (Meta: ${target:.2f}). Robô finalizado com sucesso!",
                symbol=symbol,
            )
        elif new_session_profit <= -max_loss:
            await self._disable_bot_in_db("STOP LOSS 🛑")
            await self._log_event(
                "WARN",
                f"🛑 LIMITE DE PERDA ATINGIDO! Saldo na sessão: -${abs(new_session_profit):.2f} (Limite: -${max_loss:.2f}). Robô desativado para proteção do capital.",
                symbol=symbol,
            )

        if hasattr(self, "_nexus_engine") and self._nexus_engine is not None and symbol in self._nexus_engine.states:
            if self._nexus_engine.states[symbol].has_open_basket:
                self._nexus_engine.on_basket_closed(
                    symbol=symbol,
                    profit_usd=realized_pnl,
                    is_win=bool(realized_pnl > 0),
                    epoch=time.time(),
                )

        self.open_basket_id = None
        self.basket_state = None
        self._armed_video_order = None
        self._video_atr_at_entry = 0.0
        return realized_pnl

    async def _maintenance_loop(self) -> None:
        """Periodic safety check every 5s for timeout exits, subscription expiry, and state push."""
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=5.0)
                break
            except asyncio.TimeoutError:
                pass

            try:
                await asyncio.to_thread(self._refresh_user_access_from_db)
                verdict = self._evaluate_risk()

                if self.has_open_basket and self.basket_state is not None and verdict.allow_exit:
                    decision = self.basket_manager.evaluate_exit(self.basket_state)
                    if decision.should_close and decision.reason:
                        await self.close_open_basket(reason=decision.reason, details=decision.details)
                        continue
                elif not self.has_open_basket and verdict.should_disable_bot:
                    await self._disable_bot_in_db(verdict.status_text)
                    await self._log_event("WARN", verdict.reason or "Robô desativado automaticamente")
                    await self.publish_state()
                    await self.stop(reason_status=verdict.status_text)
                    return

                await self.publish_state()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.debug("[user=%d] Erro no loop de manutenção: %s", self.user_id, exc)

    def _evaluate_risk(self) -> RiskVerdict:
        access = self.user_access or UserAccessState(
            status="SUSPENDED",
            expires_at=datetime(2000, 1, 1, tzinfo=timezone.utc),
        )
        verdict = self.risk_guard.evaluate(access, has_open_basket=self.has_open_basket)
        self._last_risk_verdict = verdict
        return verdict

    def _load_state_from_db(self) -> bool:
        with session_scope() as db:
            user = db.query(User).filter(User.id == self.user_id).first()
            if user is None:
                return False

            self.user_access = UserAccessState(status=user.status, expires_at=user.expires_at)
            self.max_stake_usd = float(user.max_stake_usd or 50.0)

            cfg = db.query(BotConfig).filter(BotConfig.user_id == self.user_id).first()
            if cfg is None:
                return False

            self.config_snapshot = {
                "enabled": bool(cfg.enabled),
                "symbol": str(cfg.symbol),
                "timeframe_sec": int(cfg.timeframe_sec),
                "direction_mode": str(cfg.direction_mode),
                "stake_usd": float(cfg.stake_usd),
                "multiplier": int(cfg.multiplier),
                "max_layers": int(cfg.max_layers),
                "layer_multipliers_json": str(cfg.layer_multipliers_json),
                "basket_tp_usd": float(cfg.basket_tp_usd),
                "trailing_enabled": bool(cfg.trailing_enabled),
                "max_basket_loss_usd": float(cfg.max_basket_loss_usd),
                "max_daily_loss_usd": float(cfg.max_daily_loss_usd),
                "daily_profit_target_usd": float(cfg.daily_profit_target_usd),
                "max_consecutive_losses": int(cfg.max_consecutive_losses),
                "max_hold_minutes": int(cfg.max_hold_minutes),
                "use_binance_filter": bool(cfg.use_binance_filter),
                "strategy_mode": str(getattr(cfg, "strategy_mode", "SMART_CHANNEL_DCA") or "SMART_CHANNEL_DCA"),
                "channel_window": int(getattr(cfg, "channel_window", 60) or 60),
                "channel_sigma": float(getattr(cfg, "channel_sigma", 2.0) or 2.0),
                "atr_period": int(getattr(cfg, "atr_period", 10) or 10),
                "tp_atr_mult": float(getattr(cfg, "tp_atr_mult", 0.75) or 0.75),
                "sl_atr_mult": float(getattr(cfg, "sl_atr_mult", 1.90) or 1.90),
                "use_crash_guard": bool(getattr(cfg, "use_crash_guard", True)),
                "status_text": str(cfg.status_text),
            }
            self._last_status_text = str(cfg.status_text)

            # Build SmartDCABasketManager safely (falling back to conservative default if DB has bad JSON)
            try:
                mults = SmartDCABasketManager.parse_multipliers_json(
                    cfg.layer_multipliers_json, max_layers=int(cfg.max_layers)
                )
            except ValueError:
                mults = [1.0, 1.0, 1.5][: max(1, int(cfg.max_layers))]

            self.basket_manager = SmartDCABasketManager(
                max_layers=int(cfg.max_layers),
                layer_multipliers=mults,
                basket_tp_usd=float(cfg.basket_tp_usd),
                trailing_enabled=bool(cfg.trailing_enabled),
                max_basket_loss_usd=float(cfg.max_basket_loss_usd),
                max_hold_minutes=int(cfg.max_hold_minutes),
            )

            strat = str(getattr(cfg, "strategy_mode", "") or "")
            max_consec = 0 if strat in ("SWING_GRID_RESPIROS", "BOTH_ROBOTS", "TICK_MEAN_REVERSION", "GRADIENTE_LINEAR", "SYNTRA_TICKS") else int(cfg.max_consecutive_losses)

            self.risk_guard.update_config(
                RiskConfig(
                    max_daily_loss_usd=float(cfg.max_daily_loss_usd),
                    daily_profit_target_usd=float(cfg.daily_profit_target_usd),
                    max_consecutive_losses=max_consec,
                )
            )
            if strat in ("SWING_GRID_RESPIROS", "BOTH_ROBOTS", "TICK_MEAN_REVERSION", "GRADIENTE_LINEAR"):
                self.risk_guard.consecutive_losses = 0

            # Active DerivAccount
            acct = (
                db.query(DerivAccount)
                .filter(DerivAccount.user_id == self.user_id, DerivAccount.is_active_selected.is_(True))
                .first()
            )
            if acct is None:
                acct = db.query(DerivAccount).filter(DerivAccount.user_id == self.user_id).first()

            if acct is not None:
                self._active_account_id = int(acct.id)
                self._token_encrypted = str(acct.token_encrypted)
                self._balance = float(acct.balance or 0.0)
                self.account_snapshot = {
                    "id": int(acct.id),
                    "loginid": str(acct.loginid),
                    "account_type": str(acct.account_type),
                    "currency": str(acct.currency),
                    "balance": float(acct.balance or 0.0),
                }
            else:
                self._active_account_id = None
                self._token_encrypted = None
                self.account_snapshot = {}

            # Hydrate today's realized PnL & consecutive losses for the active account_type
            today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
            active_type = self.account_snapshot.get("account_type", "DEMO")
            closed_today = (
                db.query(Basket)
                .filter(
                    Basket.user_id == self.user_id,
                    Basket.status == "CLOSED",
                    Basket.account_type == active_type,
                    Basket.closed_at >= today_start,
                )
                .order_by(Basket.closed_at.asc())
                .all()
            )
            realized_today = sum(float(b.realized_pnl_usd or 0.0) for b in closed_today)
            consec_losses = 0
            for b in closed_today:
                pnl = float(b.realized_pnl_usd or 0.0)
                if pnl < 0.0:
                    consec_losses += 1
                elif pnl > 0.0:
                    consec_losses = 0
            strat = str(getattr(cfg, "strategy_mode", "") or "")
            if strat in ("NEXUS_QUANTUM", "SYNTRA_TICKS"):
                sess_prof = float(getattr(cfg, "session_profit_usd", 0.0) or 0.0)
                self.risk_guard.load_daily_stats(sess_prof, 0)
            else:
                self.risk_guard.load_daily_stats(realized_today, consec_losses)

            # Hydrate any OPEN basket
            open_basket = (
                db.query(Basket)
                .filter(Basket.user_id == self.user_id, Basket.status == "OPEN")
                .order_by(Basket.opened_at.desc())
                .first()
            )
            if open_basket is not None:
                self.open_basket_id = int(open_basket.id)
                c_snaps = [
                    ContractSnapshot(
                        layer_index=int(c.layer_index),
                        contract_type=str(c.contract_type),
                        stake_usd=float(c.stake_usd),
                        multiplier=int(c.multiplier),
                        entry_spot=float(c.entry_spot),
                        current_profit_usd=float(c.current_profit_usd or 0.0),
                        commission_usd=float(c.commission_usd or 0.0),
                        stop_out_spot=float(c.stop_out_spot) if c.stop_out_spot is not None else None,
                        deriv_contract_id=str(c.deriv_contract_id) if c.deriv_contract_id else None,
                        status=str(c.status),
                    )
                    for c in sorted(open_basket.contracts, key=lambda x: x.layer_index)
                ]
                self.basket_state = BasketState(
                    symbol=str(open_basket.symbol),
                    direction=str(open_basket.direction),
                    opened_at=open_basket.opened_at,
                    contracts=c_snaps,
                    peak_pnl_usd=float(open_basket.peak_pnl_usd or 0.0),
                )
                self.risk_guard.set_floating_pnl(self.basket_manager.compute_net_pnl(c_snaps))
            else:
                self.open_basket_id = None
                self.basket_state = None
                self.risk_guard.set_floating_pnl(0.0)

            return True

    def _refresh_user_access_from_db(self) -> None:
        with session_scope() as db:
            user = db.query(User).filter(User.id == self.user_id).first()
            if user is not None:
                self.user_access = UserAccessState(status=user.status, expires_at=user.expires_at)
                self.max_stake_usd = float(user.max_stake_usd or 50.0)

    def _persist_balance_db(self, balance: float) -> None:
        if self._active_account_id is None:
            return
        with session_scope() as db:
            acct = db.query(DerivAccount).filter(DerivAccount.id == self._active_account_id).first()
            if acct is not None:
                acct.balance = float(balance)
                acct.last_synced_at = utcnow()
        self.account_snapshot["balance"] = float(balance)

    def _persist_opened_layer_db(self, symbol: str, direction: str, c_snap: ContractSnapshot) -> int:
        with session_scope() as db:
            basket = None
            if self.open_basket_id is not None:
                basket = db.query(Basket).filter(Basket.id == self.open_basket_id, Basket.status == "OPEN").first()
            if basket is None:
                basket = Basket(
                    user_id=self.user_id,
                    deriv_account_id=self._active_account_id,
                    account_type=str(self.account_snapshot.get("account_type") or "DEMO"),
                    symbol=symbol,
                    direction=direction,
                    status="OPEN",
                    layers_count=0,
                    total_stake_usd=0.0,
                    total_commission_usd=0.0,
                    avg_entry_price=c_snap.entry_spot,
                    current_pnl_usd=c_snap.current_profit_usd,
                    opened_at=utcnow(),
                )
                db.add(basket)
                db.flush()

            contract = Contract(
                basket_id=basket.id,
                user_id=self.user_id,
                layer_index=c_snap.layer_index,
                deriv_contract_id=c_snap.deriv_contract_id,
                contract_type=direction,
                symbol=symbol,
                stake_usd=c_snap.stake_usd,
                multiplier=c_snap.multiplier,
                commission_usd=c_snap.commission_usd,
                entry_spot=c_snap.entry_spot,
                stop_out_spot=c_snap.stop_out_spot,
                current_spot=c_snap.entry_spot,
                current_profit_usd=c_snap.current_profit_usd,
                status="OPEN",
                opened_at=utcnow(),
            )
            db.add(contract)
            db.flush()

            all_snaps = list(self.basket_state.contracts) + [c_snap] if self.basket_state else [c_snap]
            basket.layers_count = len(all_snaps)
            basket.total_stake_usd = self.basket_manager.compute_total_stake(all_snaps)
            basket.total_commission_usd = self.basket_manager.compute_total_commission(all_snaps)
            basket.avg_entry_price = self.basket_manager.compute_avg_entry_price(all_snaps)
            basket.current_pnl_usd = self.basket_manager.compute_net_pnl(all_snaps)
            return int(basket.id)

    def _update_contract_live_db(self, deriv_cid: str, profit: float, current_spot: Optional[float]) -> None:
        with session_scope() as db:
            c = db.query(Contract).filter(Contract.deriv_contract_id == deriv_cid).first()
            if c is not None:
                c.current_profit_usd = float(profit)
                if current_spot is not None:
                    c.current_spot = float(current_spot)

    def _update_contract_closed_db(
        self,
        deriv_cid: str,
        status: str,
        final_profit: float,
        exit_spot: Optional[float],
    ) -> None:
        with session_scope() as db:
            c = db.query(Contract).filter(Contract.deriv_contract_id == deriv_cid).first()
            if c is not None:
                c.status = status
                c.current_profit_usd = float(final_profit)
                c.final_profit_usd = float(final_profit)
                if exit_spot is not None:
                    c.exit_spot = float(exit_spot)
                c.closed_at = utcnow()

    def _update_basket_live_db(self, current_pnl: float, peak_pnl: float) -> None:
        if self.open_basket_id is None:
            return
        with session_scope() as db:
            b = db.query(Basket).filter(Basket.id == self.open_basket_id).first()
            if b is not None and b.status == "OPEN":
                b.current_pnl_usd = float(current_pnl)
                b.peak_pnl_usd = max(float(b.peak_pnl_usd or 0.0), float(peak_pnl))

    async def _update_status_text(self, status_text: str) -> None:
        if status_text == self._last_status_text:
            return
        self._last_status_text = status_text
        self.config_snapshot["status_text"] = status_text

        def _db_upd() -> None:
            with session_scope() as db:
                cfg = db.query(BotConfig).filter(BotConfig.user_id == self.user_id).first()
                if cfg is not None:
                    cfg.status_text = status_text

        await asyncio.to_thread(_db_upd)

    async def _disable_bot_in_db(self, status_text: str) -> None:
        self._last_status_text = status_text
        self.config_snapshot["enabled"] = False
        self.config_snapshot["status_text"] = status_text

        def _db_disable() -> None:
            with session_scope() as db:
                cfg = db.query(BotConfig).filter(BotConfig.user_id == self.user_id).first()
                if cfg is not None:
                    cfg.enabled = False
                    cfg.status_text = status_text

        await asyncio.to_thread(_db_disable)

    async def _log_event(
        self,
        level: str,
        message: str,
        symbol: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
        event_type: str = "event",
    ) -> None:
        now_iso = utcnow().isoformat()
        details_str = json.dumps(details) if details is not None else None

        def _db_log() -> None:
            with session_scope() as db:
                db.add(
                    BotEventLog(
                        user_id=self.user_id,
                        level=level,
                        symbol=symbol,
                        message=message,
                        details_json=details_str,
                        created_at=utcnow(),
                    )
                )

        try:
            await asyncio.to_thread(_db_log)
        except Exception as exc:
            logger.debug("[user=%d] Falha ao gravar BotEventLog: %s", self.user_id, exc)

        if self.redis is not None:
            payload = {
                "type": event_type,
                "user_id": self.user_id,
                "level": level,
                "symbol": symbol,
                "message": message,
                "details": details or {},
                "created_at": now_iso,
            }
            try:
                await self.redis.publish(ch_user_events(self.user_id), json.dumps(payload))
            except Exception:
                pass

    def build_state_payload(self) -> dict[str, Any]:
        """Build JSON-serializable state snapshot for Redis `key_user_state(user_id)`."""
        symbol = str(self.config_snapshot.get("symbol") or "cryBTCUSD")
        open_basket_dict: Optional[dict[str, Any]] = None
        if self.basket_state is not None and self.has_open_basket:
            contracts = self.basket_state.contracts
            net_pnl = self.basket_manager.compute_net_pnl(contracts)
            avg_entry = self.basket_manager.compute_avg_entry_price(contracts)
            curr_spot = (
                self.market_hub.get_last_price(self.basket_state.symbol) if self.market_hub is not None else avg_entry
            ) or avg_entry
            be_price, tp_price = self.basket_manager.estimate_price_levels(
                contracts,
                self.basket_state.direction,
                curr_spot,
                self.basket_manager.basket_tp_usd,
            )
            open_basket_dict = {
                "id": self.open_basket_id,
                "symbol": self.basket_state.symbol,
                "direction": self.basket_state.direction,
                "layers_count": len(contracts),
                "open_layers_count": sum(1 for c in contracts if c.status == "OPEN"),
                "max_layers": self.basket_manager.max_layers,
                "total_stake_usd": self.basket_manager.compute_total_stake(contracts),
                "total_commission_usd": self.basket_manager.compute_total_commission(contracts),
                "avg_entry_price": avg_entry,
                "breakeven_price": be_price,
                "target_price": tp_price,
                "current_pnl_usd": net_pnl,
                "peak_pnl_usd": round(self.basket_state.peak_pnl_usd, 6),
                "trailing_active": bool(self.basket_state.trailing_armed),
                "trailing_floor_usd": self.basket_state.trailing_floor_usd,
                "opened_at": self.basket_state.opened_at.isoformat(),
                "contracts": [
                    {
                        "layer_index": c.layer_index,
                        "deriv_contract_id": c.deriv_contract_id,
                        "contract_type": c.contract_type,
                        "stake_usd": c.stake_usd,
                        "multiplier": c.multiplier,
                        "entry_spot": c.entry_spot,
                        "stop_out_spot": c.stop_out_spot,
                        "current_profit_usd": round(c.current_profit_usd, 6),
                        "commission_usd": c.commission_usd,
                        "status": c.status,
                    }
                    for c in contracts
                ],
            }

        verdict = self._last_risk_verdict
        armed_order_dict = None
        armed = self._armed_video_order or (next(iter(self._armed_video_orders.values()), None) if self._armed_video_orders else None)
        if armed is not None and not self.has_open_basket:
            tp_px, sl_px = compute_atr_exit_levels(
                direction=armed.direction,
                avg_entry_price=armed.trigger_price,
                first_entry_price=armed.trigger_price,
                atr_value=armed.atr_value,
                tp_atr_mult=float(self.config_snapshot.get("tp_atr_mult") or 0.75),
                sl_atr_mult=float(self.config_snapshot.get("sl_atr_mult") or 1.90),
            )
            armed_order_dict = {
                "symbol": armed.symbol or symbol,
                "direction": armed.direction,
                "trigger_price": armed.trigger_price,
                "atr_value": armed.atr_value,
                "tp_price": tp_px,
                "sl_price": sl_px,
                "lin_upper": armed.lin_upper,
                "lin_mid": armed.lin_mid,
                "lin_lower": armed.lin_lower,
            }

        tick_ema, tick_std, tick_z = self._tick_engine.compute_stats()

        return {
            "user_id": self.user_id,
            "strategy_mode": str(self.config_snapshot.get("strategy_mode") or "GRADIENTE_LINEAR"),
            "tick_ema_20": round(tick_ema, 4) if tick_ema > 0 else None,
            "tick_std_60": round(tick_std, 4) if tick_std > 0 else None,
            "tick_z_score": round(tick_z, 2) if tick_std > 0 else None,
            "enabled": bool(self.config_snapshot.get("enabled", False)),
            "connected": bool(self.client is not None and self.client.is_connected),
            "status_text": self._last_status_text,
            "symbol": symbol,
            "timeframe_sec": int(self.config_snapshot.get("timeframe_sec", 300)),
            "account_type": self.account_snapshot.get("account_type", "DEMO"),
            "loginid": self.account_snapshot.get("loginid"),
            "currency": self.account_snapshot.get("currency", "USD"),
            "balance": round(float(self._balance), 2),
            "daily_realized_pnl": round(self.risk_guard.realized_pnl_today, 4),
            "daily_floating_pnl": round(self.risk_guard.floating_pnl, 4),
            "daily_total_pnl": round(self.risk_guard.total_daily_pnl, 4),
            "consecutive_losses": int(self.risk_guard.consecutive_losses),
            "cooldown_bars_left": int(self.risk_guard.cooldown_bars_left),
            "risk_code": verdict.code if verdict else "OFF",
            "risk_reason": verdict.reason if verdict else None,
            "open_basket": open_basket_dict,
            "armed_order": armed_order_dict,
            "multi_pair_enabled": bool(self.config_snapshot.get("multi_pair_enabled", False)),
            "session_profit_usd": round(float(self.config_snapshot.get("session_profit_usd") or 0.0), 4),
            "session_cycles_count": int(self.config_snapshot.get("session_cycles_count") or 0),
            "daily_profit_target_usd": float(self.config_snapshot.get("daily_profit_target_usd") or 15.0),
            "max_daily_loss_usd": float(self.config_snapshot.get("max_daily_loss_usd") or 20.0),
            "gradient_levels": [
                {
                    "level_index": l.level_index,
                    "target_price": l.target_price,
                    "stake_usd": l.stake_usd,
                    "executed": l.executed,
                }
                for l in self._gradient_cycle.levels
            ] if self._gradient_cycle else None,
            "gradient_target_price": self._gradient_cycle.cycle_target_price if self._gradient_cycle else None,
            "gradient_avg_entry": self._gradient_cycle.avg_entry_price if self._gradient_cycle else None,
            "respiros_target_net_profit": self._respiros_cycle.target_net_profit_usd if self._respiros_cycle else 0.50,
            "respiros_avg_entry": self._respiros_cycle.avg_entry_price if self._respiros_cycle else None,
            "respiros_layers_count": len(self._respiros_cycle.layers) if self._respiros_cycle else 0,
            "nexus_telemetry": self._nexus_engine.get_telemetry() if hasattr(self, "_nexus_engine") else None,
            "syntra_telemetry": (self._nexus_engine.get_telemetry() if hasattr(self, "_nexus_engine") else (self._syntra_engine.get_telemetry() if hasattr(self, "_syntra_engine") else None)),
            "updated_at": utcnow().isoformat(),
        }

    async def publish_state(self) -> None:
        """Persist state snapshot in Redis `key_user_state` and notify `ch_user_events`."""
        if self.redis is None:
            return
        payload = self.build_state_payload()
        try:
            await self.redis.set(key_user_state(self.user_id), json.dumps(payload), ex=300)
            await self.redis.publish(
                ch_user_events(self.user_id),
                json.dumps({"type": "state_update", "user_id": self.user_id, "state": payload, "data": payload, **payload}),
            )
        except Exception as exc:
            logger.debug("[user=%d] Falha ao publicar estado no Redis: %s", self.user_id, exc)
