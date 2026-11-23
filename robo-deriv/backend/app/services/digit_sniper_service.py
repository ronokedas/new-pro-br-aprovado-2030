"""Digit Sniper Service (Virtual Loss + Split Recovery) - Backend Autonomous Runner.

Operates invisible in Shadow Mode (Virtual Loss) per tick and executes real 1-tick
orders on Deriv WebSocket API v3 / Options OTP when statistical anomalies occur.
Runs 24/7 in background even when browser is closed.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict, deque
import json
import logging
from typing import Any, Dict, List, Optional, Tuple
import httpx
import websockets

from shared.config import get_settings
from shared.crypto_vault import decrypt_secret
from shared.database import get_session_factory
from shared.models import Basket, BotConfig, BotEventLog, Contract, DerivAccount, utcnow

logger = logging.getLogger("backend.digit_sniper")


SNIPER_ALL_ASSETS = ["1HZ100V", "R_100", "1HZ75V", "1HZ50V", "1HZ25V"]


def extract_last_digit(quote: float, pip_size: int = 2) -> int:
    """Extract exact last decimal digit respecting pip_size to never drop trailing zeros."""
    try:
        if quote is None:
            return 0
        safe_pip = max(0, int(pip_size)) if pip_size is not None else 2
        formatted = f"{float(quote):.{safe_pip}f}"
        return int(formatted[-1])
    except Exception:
        return 0


def check_markov_safety(digits_seq: deque, current_d: int, losing_d: int) -> Tuple[bool, float]:
    """Calculate empirical conditional transition P(losing_d | current_d) in rolling window.

    In synthetic indices like 1HZ100V, consecutive repeats of the same digit occur in ~11-14% of
    cases due to micro-tick clustering. This function detects cluster inertia and vetos entry.
    """
    seq = list(digits_seq)
    if len(seq) < 25:
        return True, 0.10
    transitions = [seq[k + 1] for k in range(len(seq) - 1) if seq[k] == current_d]
    if len(transitions) < 4:
        return True, 0.10
    prob_losing = sum(1 for d in transitions if d == losing_d) / len(transitions)
    # If the probability of the losing digit appearing immediately is > 8.0%, veto entry!
    return (prob_losing <= 0.08), prob_losing


class DigitSniperService:
    _instance: Optional["DigitSniperService"] = None

    def __init__(self) -> None:
        self.active_tasks: Dict[int, asyncio.Task] = {}
        self.user_states: Dict[int, Dict[str, Any]] = {}
        self._lock = asyncio.Lock()
        self._shutdown_event = asyncio.Event()

    @classmethod
    def get_instance(cls) -> "DigitSniperService":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def start(self) -> None:
        """Start background supervisor on app startup; resume runners for users with sniper_enabled=True."""
        logger.info("Iniciando DigitSniperService de background...")
        self._shutdown_event.clear()
        db = get_session_factory()()
        try:
            configs = db.query(BotConfig).filter(BotConfig.sniper_enabled.is_(True)).all()
            for cfg in configs:
                asyncio.create_task(self.start_for_user(cfg.user_id))
        except Exception as exc:
            logger.warning("Erro ao restaurar robôs sniper ativos no startup: %s", exc)
        finally:
            db.close()

    async def stop(self) -> None:
        """Stop all running sniper tasks gracefully."""
        logger.info("Encerrando DigitSniperService...")
        self._shutdown_event.set()
        tasks = []
        async with self._lock:
            for uid, task in list(self.active_tasks.items()):
                task.cancel()
                tasks.append(task)
            self.active_tasks.clear()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def get_user_state(self, user_id: int) -> Dict[str, Any]:
        """Return live in-memory state or load persistent state from DB."""
        if user_id in self.user_states:
            return self.user_states[user_id]

        db = get_session_factory()()
        try:
            cfg = db.query(BotConfig).filter(BotConfig.user_id == user_id).first()
            raw_cfg = json.loads(cfg.sniper_config_json) if cfg and cfg.sniper_config_json else {}
            raw_state = json.loads(cfg.sniper_state_json) if cfg and cfg.sniper_state_json else {}
            is_active = bool(cfg.sniper_enabled) if cfg else False

            state = {
                "is_running": is_active and (user_id in self.active_tasks and not self.active_tasks[user_id].done()),
                "status_text": "RODANDO (BACKGROUND)" if is_active else "PARADO",
                "symbol": raw_cfg.get("symbol", "MULTI_RADAR"),
                "sub_mode": raw_cfg.get("sub_mode", "MULTI_CONFLUENCE_AI"),
                "virtual_losses_required": int(raw_cfg.get("virtual_losses_required", 1)),
                "initial_stake": float(raw_cfg.get("initial_stake", 0.35)),
                "split_installments": int(raw_cfg.get("split_installments", 3)),
                "max_recovery_stake": float(raw_cfg.get("max_recovery_stake", 3.50)),
                "use_markov_filter": bool(raw_cfg.get("use_markov_filter", True)),
                "stop_win": float(raw_cfg.get("stop_win", 10.0)),
                "stop_loss": float(raw_cfg.get("stop_loss", 15.0)),
                "session_wins": int(raw_state.get("session_wins", 0)),
                "session_losses": int(raw_state.get("session_losses", 0)),
                "session_profit": float(raw_state.get("session_profit", 0.0)),
                "loss_pool": float(raw_state.get("loss_pool", 0.0)),
                "remaining_installments": int(raw_state.get("remaining_installments", 3)),
                "current_stake": float(raw_state.get("current_stake", 0.35)),
                "virtual_loss_count": 0,
                "cooldown": 0,
                "lock_reason": None,
                "recent_trades": raw_state.get("recent_trades", []),
            }
            self.user_states[user_id] = state
            return state
        except Exception as exc:
            logger.warning("Erro ao carregar estado do sniper para user %d: %s", user_id, exc)
            return {
                "is_running": False,
                "status_text": "PARADO",
                "session_wins": 0,
                "session_losses": 0,
                "session_profit": 0.0,
                "loss_pool": 0.0,
                "recent_trades": [],
            }
        finally:
            db.close()

    async def update_user_config(self, user_id: int, config_updates: Dict[str, Any]) -> Dict[str, Any]:
        """Update user sniper config in memory and database."""
        db = get_session_factory()()
        try:
            cfg = db.query(BotConfig).filter(BotConfig.user_id == user_id).first()
            if cfg is None:
                cfg = BotConfig(user_id=user_id)
                db.add(cfg)

            current_cfg = json.loads(cfg.sniper_config_json or "{}")
            current_cfg.update(config_updates)
            cfg.sniper_config_json = json.dumps(current_cfg)
            db.commit()

            st = self.get_user_state(user_id)
            for k, v in config_updates.items():
                st[k] = v
            return st
        finally:
            db.close()

    async def reset_user_session(self, user_id: int) -> Dict[str, Any]:
        """Reset scoreboard and loss pool for current user only."""
        st = self.get_user_state(user_id)
        st["session_wins"] = 0
        st["session_losses"] = 0
        st["session_profit"] = 0.0
        st["loss_pool"] = 0.0
        st["remaining_installments"] = st.get("split_installments", 3)
        st["current_stake"] = st.get("initial_stake", 0.35)
        st["virtual_loss_count"] = 0
        st["cooldown"] = 0
        st["lock_reason"] = None
        st["recent_trades"] = []
        st["status_text"] = "PLACAR ZERADO"

        db = get_session_factory()()
        try:
            cfg = db.query(BotConfig).filter(BotConfig.user_id == user_id).first()
            if cfg:
                persistent_state = {
                    "session_wins": 0,
                    "session_losses": 0,
                    "session_profit": 0.0,
                    "loss_pool": 0.0,
                    "remaining_installments": st["remaining_installments"],
                    "current_stake": st["current_stake"],
                    "recent_trades": [],
                }
                cfg.sniper_state_json = json.dumps(persistent_state)
                db.commit()
        finally:
            db.close()

        return st

    async def start_for_user(self, user_id: int, config_override: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Activate the sniper bot for user in background."""
        async with self._lock:
            if config_override:
                await self.update_user_config(user_id, config_override)

            # Cancel existing task if running
            existing = self.active_tasks.get(user_id)
            if existing and not existing.done():
                existing.cancel()

            db = get_session_factory()()
            try:
                cfg = db.query(BotConfig).filter(BotConfig.user_id == user_id).first()
                if cfg is None:
                    cfg = BotConfig(user_id=user_id)
                    db.add(cfg)
                cfg.sniper_enabled = True
                db.commit()
            finally:
                db.close()

            st = self.get_user_state(user_id)
            st["is_running"] = True
            st["status_text"] = "INICIANDO MODO SOMBRA (BACKGROUND)"
            st["lock_reason"] = None

            task = asyncio.create_task(
                self._run_user_sniper_loop(user_id),
                name=f"sniper-user-{user_id}",
            )
            self.active_tasks[user_id] = task
            return st

    async def stop_for_user(self, user_id: int, reason: Optional[str] = None) -> Dict[str, Any]:
        """Deactivate the sniper bot for user."""
        async with self._lock:
            existing = self.active_tasks.pop(user_id, None)
            if existing and not existing.done():
                existing.cancel()

            db = get_session_factory()()
            try:
                cfg = db.query(BotConfig).filter(BotConfig.user_id == user_id).first()
                if cfg:
                    cfg.sniper_enabled = False
                    db.commit()
            finally:
                db.close()

            st = self.get_user_state(user_id)
            st["is_running"] = False
            st["status_text"] = reason or "⏸️ ROBÔ PAUSADO PELO USUÁRIO"
            if reason:
                st["lock_reason"] = reason
            self._persist_state(user_id)
            return st

    def _persist_state(self, user_id: int) -> None:
        """Helper to save snapshot of session and trades to DB."""
        st = self.user_states.get(user_id)
        if not st:
            return
        db = get_session_factory()()
        try:
            cfg = db.query(BotConfig).filter(BotConfig.user_id == user_id).first()
            if cfg:
                persistent = {
                    "session_wins": st.get("session_wins", 0),
                    "session_losses": st.get("session_losses", 0),
                    "session_profit": st.get("session_profit", 0.0),
                    "loss_pool": st.get("loss_pool", 0.0),
                    "remaining_installments": st.get("remaining_installments", 3),
                    "current_stake": st.get("current_stake", 0.35),
                    "recent_trades": st.get("recent_trades", [])[:30],
                }
                cfg.sniper_state_json = json.dumps(persistent)
                db.commit()
        except Exception as exc:
            logger.warning("Falha ao persistir sniper_state_json para user %d: %s", user_id, exc)
        finally:
            db.close()

    async def _resolve_user_connection(self, user_id: int) -> Tuple[str, Optional[str], bool, str]:
        """Obtain decrypted token and appropriate WebSocket URL for user's active account."""
        db = get_session_factory()()
        try:
            account = (
                db.query(DerivAccount)
                .filter(DerivAccount.user_id == user_id, DerivAccount.is_active_selected.is_(True))
                .first()
            )
            if not account:
                account = (
                    db.query(DerivAccount)
                    .filter(DerivAccount.user_id == user_id)
                    .order_by(DerivAccount.account_type.asc(), DerivAccount.id.asc())
                    .first()
                )
            if not account:
                raise ValueError("Nenhuma conta Deriv conectada.")

            raw_token = decrypt_secret(account.token_encrypted)
            settings = get_settings()
            app_id = settings.DERIV_APP_ID.strip() if settings.DERIV_APP_ID else "1089"
            is_pat = raw_token.startswith("pat_")
            currency = account.currency or "USD"

            if is_pat:
                otp_app_id = "34ZYYnkkxGGXQTva4v3lB"
                otp_endpoint = f"https://api.derivws.com/trading/v1/options/accounts/{account.loginid}/otp"
                async with httpx.AsyncClient(timeout=10.0) as client:
                    r = await client.post(
                        otp_endpoint,
                        json={},
                        headers={
                            "Authorization": f"Bearer {raw_token}",
                            "Deriv-App-ID": otp_app_id,
                            "User-Agent": "Mozilla/5.0",
                        },
                    )
                    if r.status_code == 200:
                        ws_url = r.json().get("data", {}).get("url")
                        if ws_url:
                            return ws_url, None, True, currency
                # Fallback public URL if OTP request fails
                return "wss://api.derivws.com/trading/v1/options/ws/public", raw_token, True, currency
            else:
                num_id = app_id if app_id.isdigit() else "1089"
                ws_url = f"wss://red.derivws.com/websockets/v3?app_id={num_id}"
                return ws_url, raw_token, False, currency
        finally:
            db.close()

    async def _run_user_sniper_loop(self, user_id: int) -> None:
        """Main asynchronous background loop executing the exact quantitative strategy."""
        logger.info("[User %d] Iniciando loop autônomo do Digit Sniper...", user_id)
        st = self.get_user_state(user_id)
        recent_ticks_map: Dict[str, deque] = {s: deque(maxlen=100) for s in SNIPER_ALL_ASSETS}
        recent_digits_map: Dict[str, deque] = {s: deque(maxlen=100) for s in SNIPER_ALL_ASSETS}
        virtual_counts_map: Dict[str, int] = defaultdict(int)

        backoff = 2.0
        while not self._shutdown_event.is_set():
            try:
                ws_url, auth_token, is_pat, currency = await self._resolve_user_connection(user_id)
                st["status_text"] = "CONECTANDO À DERIV API (BACKGROUND)..."

                async with websockets.connect(ws_url, open_timeout=8.0, close_timeout=3.0) as ws:
                    backoff = 2.0
                    st["status_text"] = "CONECTADO: MONITORANDO MODO SOMBRA"

                    if not is_pat and auth_token:
                        await ws.send(json.dumps({"authorize": auth_token}))
                        auth_msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=5.0))
                        if auth_msg.get("error"):
                            raise ValueError(f"Auth error: {auth_msg['error'].get('message')}")

                    sym = st.get("symbol", "1HZ100V")
                    
                    async def subscribe_current_mode(target_sym: str):
                        targets = SNIPER_ALL_ASSETS if target_sym == "MULTI_RADAR" else [target_sym]
                        for s in targets:
                            await ws.send(json.dumps({"ticks_history": s, "end": "latest", "count": 100, "style": "ticks"}))
                            await ws.send(json.dumps({"ticks": s, "subscribe": 1}))

                    await subscribe_current_mode(sym)
                    await ws.send(json.dumps({"balance": 1, "subscribe": 1}))

                    ping_deadline = asyncio.get_running_loop().time() + 20.0
                    order_in_flight = False

                    while not self._shutdown_event.is_set():
                        # Verificação de troca a quente do símbolo
                        current_conf_sym = st.get("symbol", "1HZ100V")
                        if current_conf_sym != sym:
                            try:
                                await ws.send(json.dumps({"forget_all": "ticks"}))
                                sym = current_conf_sym
                                for s in SNIPER_ALL_ASSETS:
                                    recent_ticks_map[s].clear()
                                    recent_digits_map[s].clear()
                                    virtual_counts_map[s] = 0
                                await subscribe_current_mode(sym)
                                logger.info("[User %d] Ativo do Sniper alterado a quente para %s", user_id, sym)
                            except Exception as ex_sym:
                                logger.warning("[User %d] Falha ao trocar ativo a quente: %s", user_id, ex_sym)

                        now = asyncio.get_running_loop().time()
                        if now >= ping_deadline:
                            await ws.send(json.dumps({"ping": 1}))
                            ping_deadline = now + 20.0

                        try:
                            raw_msg = await asyncio.wait_for(ws.recv(), timeout=25.0)
                        except asyncio.TimeoutError:
                            await ws.send(json.dumps({"ping": 1}))
                            continue

                        data = json.loads(raw_msg)
                        msg_type = data.get("msg_type")

                        # 0. Atualização de Saldo Oficial
                        if msg_type == "balance" and data.get("balance"):
                            b_val = float(data["balance"].get("balance", 0.0))
                            st["balance"] = b_val
                            db = get_session_factory()()
                            try:
                                acc = db.query(DerivAccount).filter(
                                    DerivAccount.user_id == user_id, DerivAccount.is_active_selected.is_(True)
                                ).first()
                                if acc:
                                    acc.balance = b_val
                                    db.commit()
                            finally:
                                db.close()

                        # 1. Carga Histórica dos 100 Ticks
                        elif (msg_type in ("ticks_history", "history") or "history" in data) and data.get("history"):
                            hist = data["history"]
                            hist_sym = data.get("echo_req", {}).get("ticks_history", sym)
                            if hist_sym not in recent_digits_map:
                                hist_sym = "1HZ100V"
                            prices = hist.get("prices") or []
                            for p in prices[-100:]:
                                d = extract_last_digit(p, 2)
                                recent_ticks_map[hist_sym].append(float(p))
                                recent_digits_map[hist_sym].append(d)
                            if hist_sym == sym or sym == "MULTI_RADAR":
                                st["last_100_digits"] = list(recent_digits_map[hist_sym])
                            logger.info("[User %d] Carga historica de %s: %d ticks.", user_id, hist_sym, len(recent_digits_map[hist_sym]))

                        # 2. Tick em Tempo Real
                        elif msg_type == "tick" and data.get("tick"):
                            t = data["tick"]
                            t_sym = t.get("symbol")
                            if sym != "MULTI_RADAR" and t_sym != sym:
                                continue
                            if t_sym not in SNIPER_ALL_ASSETS:
                                continue

                            quote = float(t.get("quote", 0.0))
                            raw_pip = t.get("pip_size")
                            pip = int(float(raw_pip)) if raw_pip is not None else 2
                            current_digit = extract_last_digit(quote, pip)
                            
                            recent_ticks = recent_ticks_map[t_sym]
                            recent_digits = recent_digits_map[t_sym]
                            recent_ticks.append(quote)
                            recent_digits.append(current_digit)

                            st["last_tick"] = {
                                "quote": quote,
                                "digit": current_digit,
                                "symbol": t_sym,
                                "epoch": t.get("epoch"),
                            }
                            if sym == t_sym or sym == "MULTI_RADAR":
                                st["last_100_digits"] = list(recent_digits)

                            if order_in_flight:
                                continue

                            # Cooldown temporal real pós-loss/erro/trade
                            now = asyncio.get_running_loop().time()
                            cd_until = float(st.get("cooldown_until", 0.0))
                            if now < cd_until:
                                rem_s = cd_until - now
                                st["status_text"] = f"❄️ COOLDOWN PÓS-OPERAÇÃO: Aguardando {rem_s:.1f}s..."
                                continue

                            # Stop Win e Stop Loss
                            pnl = st.get("session_profit", 0.0)
                            stop_win = st.get("stop_win", 10.0)
                            stop_loss = st.get("stop_loss", 15.0)

                            if pnl >= stop_win:
                                await self.stop_for_user(user_id, reason=f"🎯 STOP WIN ATINGIDO (+${pnl:.2f})!")
                                return
                            if pnl <= -stop_loss:
                                await self.stop_for_user(user_id, reason=f"🛑 STOP LOSS ATINGIDO (-${abs(pnl):.2f})!")
                                return

                            # Sub-Modo com Desativação Definitiva de Even/Odd (EV Negativo)
                            sub_mode = st.get("sub_mode", "MULTI_CONFLUENCE_AI")
                            if sub_mode == "EVEN_ODD_SNIPER":
                                # Intercepta e converte automaticamente para o motor de IA Confluência
                                sub_mode = "MULTI_CONFLUENCE_AI"
                                st["sub_mode"] = "MULTI_CONFLUENCE_AI"

                            loss_pool = st.get("loss_pool", 0.0)
                            rem_inst = max(1, st.get("remaining_installments", 3))
                            initial_stake = st.get("initial_stake", 0.35)
                            max_rec_stake = float(st.get("max_recovery_stake", 3.50))
                            
                            if sub_mode in ("OVER1_UNDER8_HFT", "MULTI_CONFLUENCE_AI"):
                                effective_payout = 0.235
                            elif sub_mode == "DIGITDIFF_COLD":
                                effective_payout = 0.098
                            else:
                                effective_payout = 0.088

                            if loss_pool > 0:
                                target_gain = (loss_pool / rem_inst) + (0.05 if effective_payout < 0.50 else 0.02)
                                calculated = max(0.35, round((target_gain / effective_payout), 2))
                                # Teto Máximo Anti-Quebra: A stake NUNCA ultrapassa max_rec_stake
                                if calculated > max_rec_stake:
                                    next_stake = max_rec_stake
                                    gain_per_win = max(0.01, round(next_stake * effective_payout, 2))
                                    st["remaining_installments"] = max(1, int(round(loss_pool / gain_per_win)))
                                else:
                                    next_stake = round(calculated, 2)
                            else:
                                next_stake = initial_stake

                            st["current_stake"] = next_stake

                            # Trava Preventiva: Se a próxima stake exceder o Stop Loss restante
                            if (pnl - next_stake) < -stop_loss:
                                await self.stop_for_user(
                                    user_id,
                                    reason=f"🛑 RISCO EXCEDIDO: Próxima stake (${next_stake:.2f}) violaria o Stop Loss (-${stop_loss:.2f})."
                                )
                                return

                            # Se estiver em recuperação (loss_pool > 0), a exigência de perda virtual é rigorosa (>= 2)
                            # para garantir que stakes de recuperação só disparem em alta confluência (WR >= 93%)
                            base_virt_req = int(st.get("virtual_losses_required", 1))
                            virt_req = max(2 if loss_pool > 0 else 1, base_virt_req)
                            virt_count = virtual_counts_map[t_sym]
                            use_markov = bool(st.get("use_markov_filter", True))

                            # Validação de Saldo Disponível
                            cur_bal = st.get("balance")
                            if cur_bal is None:
                                db = get_session_factory()()
                                try:
                                    acc = db.query(DerivAccount).filter(
                                        DerivAccount.user_id == user_id, DerivAccount.is_active_selected.is_(True)
                                    ).first()
                                    cur_bal = float(acc.balance or 0.0) if acc else 0.0
                                    st["balance"] = cur_bal
                                finally:
                                    db.close()

                            if cur_bal < next_stake:
                                await self.stop_for_user(
                                    user_id,
                                    reason=f"⚠️ Saldo insuficiente (${cur_bal:.2f}) para stake (${next_stake:.2f})."
                                )
                                return

                            # Estatísticas da Janela de 100 Ticks
                            total_ticks = len(recent_digits) or 1
                            counts = [0] * 10
                            for d in recent_digits:
                                counts[d] += 1
                            pcts = [(c / total_ticks) * 100.0 for c in counts]

                            coldest_digit = min(range(10), key=lambda d: pcts[d])
                            cold_pct = pcts[coldest_digit]
                            zero_pct = pcts[0]
                            nine_pct = pcts[9]

                            # Análise de vício nos últimos 50 ticks (>14%)
                            last50 = list(recent_digits)[-50:]
                            len50 = len(last50) or 1
                            c50 = [0] * 10
                            for d in last50:
                                c50[d] += 1
                            addicted_digits = [d for d in range(10) if (c50[d] / len50) * 100.0 > 14.0]

                            target_contract: Optional[str] = None
                            target_barrier: str = "0"
                            trigger_reason: str = ""

                            # -------------------------------------------------------------
                            # MODO 1: OVER 0 / UNDER 9 ADAPTATIVO
                            # -------------------------------------------------------------
                            if sub_mode == "OVER_UNDER_AUTO":
                                can_over0 = (zero_pct <= 11.0 and 0 not in addicted_digits)
                                can_under9 = (nine_pct <= 11.0 and 9 not in addicted_digits)

                                if current_digit == 0 and can_over0:
                                    virt_count += 1
                                    virtual_counts_map[t_sym] = virt_count
                                    st["virtual_loss_count"] = virt_count
                                    if virt_count >= virt_req:
                                        if use_markov:
                                            safe, p_trans = check_markov_safety(recent_digits, current_digit, 0)
                                            if not safe and p_trans > 0.12:
                                                st["status_text"] = f"🧠 [{t_sym}] IA VETO: P(0|0)={p_trans*100:.1f}% > 12%"
                                                continue
                                        target_contract = "DIGITOVER"
                                        target_barrier = "0"
                                        trigger_reason = f"Perda Virtual 0 ({virt_count}/{virt_req})"
                                elif current_digit == 9 and can_under9:
                                    virt_count += 1
                                    virtual_counts_map[t_sym] = virt_count
                                    st["virtual_loss_count"] = virt_count
                                    if virt_count >= virt_req:
                                        if use_markov:
                                            safe, p_trans = check_markov_safety(recent_digits, current_digit, 9)
                                            if not safe and p_trans > 0.12:
                                                st["status_text"] = f"🧠 [{t_sym}] IA VETO: P(9|9)={p_trans*100:.1f}% > 12%"
                                                continue
                                        target_contract = "DIGITUNDER"
                                        target_barrier = "9"
                                        trigger_reason = f"Perda Virtual 9 ({virt_count}/{virt_req})"
                                else:
                                    if virt_count > 0:
                                        virtual_counts_map[t_sym] = 0
                                        st["virtual_loss_count"] = 0
                                    prefix = f"🛰️ [RADAR 5 ATIVOS] {t_sym}" if sym == "MULTI_RADAR" else f"[{t_sym}]"
                                    st["status_text"] = f"{prefix} 👁️ SOMBRA OVER/UNDER: 0 ({zero_pct:.1f}%), 9 ({nine_pct:.1f}%)"

                            # -------------------------------------------------------------
                            # MODO 2: DIGIT DIFFERS (ADORMECIDOS & MAIS FRIOS)
                            # -------------------------------------------------------------
                            elif sub_mode == "DIGITDIFF_COLD":
                                indices = [idx for idx, val in enumerate(list(recent_digits)[:-1]) if val == current_digit]
                                penult_gap = (len(recent_digits) - 1 - indices[-1]) if indices else 999

                                is_sleeping_wakeup = (penult_gap >= 14 and current_digit not in addicted_digits)
                                is_cold_hit = (cold_pct <= 9.0 and current_digit == coldest_digit and coldest_digit not in addicted_digits)

                                if is_sleeping_wakeup or is_cold_hit:
                                    virt_count += 1
                                    virtual_counts_map[t_sym] = virt_count
                                    st["virtual_loss_count"] = virt_count
                                    if virt_count >= virt_req:
                                        if use_markov:
                                            safe, p_trans = check_markov_safety(recent_digits, current_digit, current_digit)
                                            if not safe and p_trans > 0.12:
                                                st["status_text"] = f"🧠 [{t_sym}] IA VETO: P({current_digit}|{current_digit})={p_trans*100:.1f}% > 12%"
                                                continue
                                        target_contract = "DIGITDIFF"
                                        target_barrier = str(current_digit)
                                        desc = f"Adormecido {penult_gap}t" if is_sleeping_wakeup else f"Mais Frio {cold_pct:.1f}%"
                                        trigger_reason = f"DIFF {current_digit} ({desc})"
                                else:
                                    if virt_count > 0:
                                        virtual_counts_map[t_sym] = 0
                                        st["virtual_loss_count"] = 0
                                    prefix = f"🛰️ [RADAR 5 ATIVOS] {t_sym}" if sym == "MULTI_RADAR" else f"[{t_sym}]"
                                    st["status_text"] = f"{prefix} 👁️ SOMBRA DIFFERS: Menor={coldest_digit} ({cold_pct:.1f}%)"

                            # -------------------------------------------------------------
                            # MODO 3: OVER 1 / UNDER 8 HFT (+EV POSITIVO, PAYOUT ~23.5%)
                            # -------------------------------------------------------------
                            elif sub_mode == "OVER1_UNDER8_HFT":
                                last3 = list(recent_digits)[-3:]
                                low_hits = sum(1 for d in last3 if d in (0, 1))
                                high_hits = sum(1 for d in last3 if d in (8, 9))

                                if (virt_req == 1 and current_digit in (0, 1)) or (virt_req > 1 and low_hits >= virt_req):
                                    target_contract = "DIGITOVER"
                                    target_barrier = "1"
                                    trigger_reason = f"Perda Virtual Baixa [{current_digit}] -> OVER 1"
                                elif (virt_req == 1 and current_digit in (8, 9)) or (virt_req > 1 and high_hits >= virt_req):
                                    target_contract = "DIGITUNDER"
                                    target_barrier = "8"
                                    trigger_reason = f"Perda Virtual Alta [{current_digit}] -> UNDER 8"
                                else:
                                    prefix = f"🛰️ [RADAR 5 ATIVOS] {t_sym}" if sym == "MULTI_RADAR" else f"[{t_sym}]"
                                    st["status_text"] = f"{prefix} 👁️ SOMBRA OVER1/UNDER8: Dígito [{current_digit}] (Payout 23.5%)"

                            # -------------------------------------------------------------
                            # MODO 4: IA MULTI-SETUP CONFLUENCE (SCANNER OPORTUNISTA GLOBAL)
                            # -------------------------------------------------------------
                            elif sub_mode == "MULTI_CONFLUENCE_AI":
                                last2 = list(recent_digits)[-2:] if len(recent_digits) >= 2 else []
                                low_consecutive = (len(last2) == 2 and all(d in (0, 1) for d in last2))
                                high_consecutive = (len(last2) == 2 and all(d in (8, 9) for d in last2))

                                indices = [idx for idx, val in enumerate(list(recent_digits)[:-1]) if val == current_digit]
                                penult_gap = (len(recent_digits) - 1 - indices[-1]) if indices else 999

                                # 1. Prioridade A: Exaustão Baixa (2 dígitos 0/1 seguidos) -> Disparo OVER 1 (+23.5% payout)
                                if low_consecutive:
                                    target_contract = "DIGITOVER"
                                    target_barrier = "1"
                                    trigger_reason = f"⚡ IA CONFLUÊNCIA: Exaustão Baixa {last2} -> OVER 1"

                                # 2. Prioridade B: Exaustão Alta (2 dígitos 8/9 seguidos) -> Disparo UNDER 8 (+23.5% payout)
                                elif high_consecutive:
                                    target_contract = "DIGITUNDER"
                                    target_barrier = "8"
                                    trigger_reason = f"⚡ IA CONFLUÊNCIA: Exaustão Alta {last2} -> UNDER 8"

                                # 3. Prioridade C: Dígito Super Adormecido acordando após 18+ ticks (apenas em modo normal, loss_pool == 0)
                                elif loss_pool == 0 and penult_gap >= 18 and current_digit not in addicted_digits:
                                    target_contract = "DIGITDIFF"
                                    target_barrier = str(current_digit)
                                    trigger_reason = f"⚡ IA CONFLUÊNCIA: Despertar Dígito {current_digit} ({penult_gap}t)"

                                # 4. Prioridade D: Dígito Raro/Frio (apenas loss_pool == 0 e cold_pct <= 6.0%)
                                elif loss_pool == 0 and cold_pct <= 6.0 and current_digit == coldest_digit and coldest_digit not in addicted_digits:
                                    target_contract = "DIGITDIFF"
                                    target_barrier = str(coldest_digit)
                                    trigger_reason = f"⚡ IA CONFLUÊNCIA: Dígito Raro {coldest_digit} ({cold_pct:.1f}%)"
                                else:
                                    prefix = f"🛰️ [RADAR 5 ATIVOS] {t_sym}" if sym == "MULTI_RADAR" else f"[{t_sym}]"
                                    st["status_text"] = f"{prefix} 🧠 IA RADAR: Monitorando confluência... Atual: {t_sym} (D={current_digit})"

                            # -------------------------------------------------------------
                            # MODO 5: EVEN_ODD_SNIPER (DESATIVADO - EV NEGATIVO)
                            # -------------------------------------------------------------
                            elif sub_mode == "EVEN_ODD_SNIPER":
                                prefix = f"🛰️ [RADAR 5 ATIVOS] {t_sym}" if sym == "MULTI_RADAR" else f"[{t_sym}]"
                                st["status_text"] = f"{prefix} ⚠️ EVEN/ODD DESATIVADO (EV NEGATIVO). Migrando para IA Confluência..."
                                st["sub_mode"] = "MULTI_CONFLUENCE_AI"
                                sub_mode = "MULTI_CONFLUENCE_AI"

                            # -------------------------------------------------------------
                            # DISPARO DA ORDEM REAL (SE SINAL IDENTIFICADO)
                            # -------------------------------------------------------------
                            if target_contract:
                                st["status_text"] = f"⚡ DISPARO [{t_sym}]! {trigger_reason} • Stake: ${next_stake:.2f}"
                                logger.info("[User %d] GATILHO %s (%s) no ativo %s! %s (Stake=$%.2f)...", user_id, target_contract, target_barrier, t_sym, trigger_reason, next_stake)
                                virtual_counts_map[t_sym] = 0
                                st["virtual_loss_count"] = 0
                                order_in_flight = True
                                await self._execute_real_trade(
                                    ws, user_id, t_sym, target_contract, target_barrier, next_stake, currency,
                                    recent_ticks, recent_digits
                                )
                                order_in_flight = False

            except asyncio.CancelledError:
                logger.info("[User %d] Runner sniper cancelado.", user_id)
                break
            except Exception as exc:
                logger.warning("[User %d] Erro no loop sniper: %s. Reconectando em %.1fs...", user_id, exc, backoff)
                st["status_text"] = f"RECONECTANDO EM {backoff:.0f}s: {exc}"
                await asyncio.sleep(backoff)
                backoff = min(backoff * 1.5, 30.0)

    async def _execute_real_trade(
        self,
        ws: Any,
        user_id: int,
        symbol: str,
        contract_type: str,
        barrier: str,
        stake: float,
        currency: str,
        recent_ticks: deque,
        recent_digits: deque,
    ) -> None:
        """Send 1-tick real contract to Deriv WebSocket and await settlement (is_sold==1)."""
        st = self.get_user_state(user_id)
        st["status_text"] = f"🚀 ENVIANDO ORDEM REAL: {contract_type} {barrier} (${stake:.2f})"

        params = {
            "amount": stake,
            "basis": "stake",
            "contract_type": contract_type,
            "currency": currency,
            "duration": 1,
            "duration_unit": "t",
            "underlying_symbol": symbol,
        }
        if contract_type not in ("DIGITEVEN", "DIGITODD"):
            params["barrier"] = barrier

        buy_req = {
            "buy": 1,
            "price": stake,
            "parameters": params,
        }

        try:
            await ws.send(json.dumps(buy_req))

            contract_id = None
            buy_deadline = asyncio.get_running_loop().time() + 8.0
            while asyncio.get_running_loop().time() < buy_deadline:
                raw_msg = await asyncio.wait_for(ws.recv(), timeout=6.0)
                parsed = json.loads(raw_msg)
                if parsed.get("error") and parsed.get("echo_req", {}).get("buy"):
                    err = parsed["error"]
                    logger.warning("[User %d] Erro ao enviar ordem sniper: %s", user_id, err)
                    st["status_text"] = f"❌ ERRO NA ORDEM: {err.get('message') or err.get('code')}"
                    st["cooldown_until"] = asyncio.get_running_loop().time() + 6.0
                    return
                if parsed.get("msg_type") == "buy" and parsed.get("buy"):
                    contract_id = parsed["buy"].get("contract_id")
                    break
                elif parsed.get("msg_type") == "tick" and parsed.get("tick"):
                    t = parsed["tick"]
                    if t.get("symbol") == symbol:
                        q = float(t.get("quote", 0.0))
                        pip = int(t.get("pip_size", 2))
                        recent_ticks.append(q)
                        recent_digits.append(extract_last_digit(q, pip))

            if not contract_id:
                logger.warning("[User %d] Timeout aguardando confirmacao de compra", user_id)
                st["status_text"] = "⚠️ TIMEOUT AGUARDANDO ORDEM"
                return

            # Inscreve para acompanhar liquidação oficial
            await ws.send(json.dumps({"proposal_open_contract": 1, "contract_id": contract_id, "subscribe": 1}))

            # Aguarda liquidação oficial
            deadline = asyncio.get_running_loop().time() + 15.0
            is_win = False
            is_settled = False
            profit = 0.0
            exit_digit: Optional[int] = None
            exit_spot: Optional[float] = None

            while asyncio.get_running_loop().time() < deadline:
                raw_poc = await asyncio.wait_for(ws.recv(), timeout=6.0)
                poc_data = json.loads(raw_poc)
                if poc_data.get("msg_type") == "proposal_open_contract":
                    poc = poc_data.get("proposal_open_contract", {})
                    if poc.get("is_sold") == 1:
                        is_settled = True
                        is_win = (poc.get("status") == "won")
                        profit = float(poc.get("profit", 0.0))
                        exit_spot = float(poc.get("exit_tick", 0.0)) if poc.get("exit_tick") else None
                        if exit_spot:
                            exit_digit = extract_last_digit(exit_spot, int(poc.get("pip_size", 2)))
                        # Desinscreve
                        if poc.get("id"):
                            try:
                                await ws.send(json.dumps({"forget": poc["id"]}))
                            except Exception:
                                pass
                        break
                elif poc_data.get("msg_type") == "tick" and poc_data.get("tick"):
                    t = poc_data["tick"]
                    if t.get("symbol") == symbol:
                        q = float(t.get("quote", 0.0))
                        raw_pip = t.get("pip_size")
                        pip = int(raw_pip) if raw_pip is not None else 2
                        recent_ticks.append(q)
                        recent_digits.append(extract_last_digit(q, pip))

            # Se não recebeu confirmação de liquidação no tempo limite, consulta status individual
            if not is_settled:
                logger.warning("[User %d] Contrato %s aguardando liquidação. Consultando status...", user_id, contract_id)
                try:
                    await ws.send(json.dumps({"proposal_open_contract": 1, "contract_id": contract_id}))
                    retry_dl = asyncio.get_running_loop().time() + 5.0
                    while asyncio.get_running_loop().time() < retry_dl:
                        raw_p = await asyncio.wait_for(ws.recv(), timeout=4.0)
                        pd = json.loads(raw_p)
                        if pd.get("msg_type") == "proposal_open_contract":
                            p = pd.get("proposal_open_contract", {})
                            if p.get("is_sold") == 1:
                                is_settled = True
                                is_win = (p.get("status") == "won")
                                profit = float(p.get("profit", 0.0))
                                exit_spot = float(p.get("exit_tick", 0.0)) if p.get("exit_tick") else None
                                if exit_spot:
                                    exit_digit = extract_last_digit(exit_spot, int(p.get("pip_size", 2)))
                                break
                except Exception as ex_chk:
                    logger.warning("[User %d] Falha ao verificar contrato pendente: %s", user_id, ex_chk)

            if not is_settled:
                # Contrato ainda não resolvido: suspende para segurança evitando ordens órfãs
                st["status_text"] = f"⚠️ Contrato {contract_id} pendente de liquidação na Deriv. Aguardando sincronização."
                return

            # Processa resultado financeiro
            split_inst = st.get("split_installments", 3)
            initial_stake = st.get("initial_stake", 0.35)

            if is_win:
                st["session_wins"] = st.get("session_wins", 0) + 1
                st["session_profit"] = round(st.get("session_profit", 0.0) + profit, 2)

                # Split Recovery: Abate do LossPool
                cur_pool = st.get("loss_pool", 0.0)
                if cur_pool > 0:
                    new_pool = max(0.0, round(cur_pool - profit, 2))
                    st["loss_pool"] = new_pool
                    st["remaining_installments"] = max(1, st.get("remaining_installments", split_inst) - 1)
                    if new_pool <= 0:
                        st["loss_pool"] = 0.0
                        st["remaining_installments"] = split_inst
                        st["current_stake"] = initial_stake

                st["cooldown_until"] = asyncio.get_running_loop().time() + 1.5
                st["status_text"] = f"🎉 VITÓRIA! +${profit:.2f} (Dígito: {exit_digit})"
            else:
                st["session_losses"] = st.get("session_losses", 0) + 1
                st["session_profit"] = round(st.get("session_profit", 0.0) - stake, 2)

                # Adiciona ao LossPool com amortização dinâmica sem empilhar multiplicadores
                st["loss_pool"] = round(st.get("loss_pool", 0.0) + stake, 2)
                max_rec = float(st.get("max_recovery_stake", 3.50))
                sub_mode = st.get("sub_mode", "MULTI_CONFLUENCE_AI")
                if sub_mode == "EVEN_ODD_SNIPER":
                    eff_payout = 0.955
                elif sub_mode in ("OVER1_UNDER8_HFT", "MULTI_CONFLUENCE_AI"):
                    eff_payout = 0.235
                elif sub_mode == "DIGITDIFF_COLD":
                    eff_payout = 0.098
                else:
                    eff_payout = 0.088
                gain_per_win = max(0.01, round(max_rec * eff_payout, 2))
                st["remaining_installments"] = max(split_inst, int(round(st["loss_pool"] / gain_per_win)))
                st["cooldown_until"] = asyncio.get_running_loop().time() + 5.0
                st["cooldown"] = 5
                st["virtual_loss_count"] = 0
                st["status_text"] = f"💔 DERROTA! -${stake:.2f} • Amortização segura ativada ({st['remaining_installments']} parcelas)."

            # Registra Trade na Lista Recente
            trade_record = {
                "id": str(contract_id),
                "time": utcnow().strftime("%H:%M:%S"),
                "symbol": symbol,
                "contract_type": contract_type,
                "barrier": barrier,
                "stake": stake,
                "profit": profit,
                "is_win": is_win,
                "exit_digit": exit_digit,
                "is_recovery": st.get("loss_pool", 0.0) > 0,
            }
            recent = st.get("recent_trades", [])
            st["recent_trades"] = [trade_record, *recent[:40]]

            # Persiste no Banco de Dados (Basket, Contract, BotEventLog, BotConfig.sniper_state_json) em transação atômica
            self._persist_trade_and_state(user_id, symbol, contract_type, barrier, stake, profit, is_win, str(contract_id), exit_spot, exit_digit)

        except Exception as exc:
            logger.warning("[User %d] Falha ao executar trade no Deriv WS: %s", user_id, exc)
            st["status_text"] = f"⚠️ FALHA NO TRADE: {exc}"

    def _persist_trade_and_state(
        self,
        user_id: int,
        symbol: str,
        contract_type: str,
        barrier: str,
        stake: float,
        profit: float,
        is_win: bool,
        contract_id: str,
        exit_spot: Optional[float],
        exit_digit: Optional[int],
    ) -> None:
        """Atomic persistence for Trade records and User Session State in PostgreSQL."""
        st = self.user_states.get(user_id, {})
        db = get_session_factory()()
        try:
            active_acc = (
                db.query(DerivAccount)
                .filter(DerivAccount.user_id == user_id, DerivAccount.is_active_selected.is_(True))
                .first()
            )
            acc_id = active_acc.id if active_acc else None
            acc_type = active_acc.account_type if active_acc else "DEMO"

            basket = Basket(
                user_id=user_id,
                deriv_account_id=acc_id,
                account_type=acc_type,
                symbol=symbol,
                direction=contract_type,
                status="CLOSED",
                layers_count=1,
                total_stake_usd=stake,
                total_commission_usd=0.0,
                avg_entry_price=exit_spot or 0.0,
                current_pnl_usd=profit,
                realized_pnl_usd=profit,
                peak_pnl_usd=max(0.0, profit),
                close_reason="TP" if is_win else "STOP_LOSS",
                opened_at=utcnow(),
                closed_at=utcnow(),
            )
            db.add(basket)
            db.flush()

            contract = Contract(
                basket_id=basket.id,
                user_id=user_id,
                layer_index=1,
                deriv_contract_id=contract_id or f"SNP-{basket.id}",
                contract_type=contract_type,
                symbol=symbol,
                stake_usd=stake,
                multiplier=1,
                commission_usd=0.0,
                entry_spot=exit_spot or 0.0,
                current_spot=exit_spot or 0.0,
                exit_spot=exit_spot or 0.0,
                current_profit_usd=profit,
                final_profit_usd=profit,
                status="SOLD",
                opened_at=utcnow(),
                closed_at=utcnow(),
            )
            db.add(contract)

            result_tag = "VITÓRIA" if is_win else "DERROTA"
            msg = (
                f"🎯 [SNIPER DÍGITOS] {result_tag} ({'+' if is_win else '-'}${abs(profit):.2f} USD) "
                f"| {symbol} {contract_type} barreira {barrier} | Stake: ${stake:.2f} | Dígito: {exit_digit}"
            )
            db.add(
                BotEventLog(
                    user_id=user_id,
                    level="TRADE",
                    symbol=symbol,
                    message=msg,
                    details_json=json.dumps({
                        "module": "GHOST_LOSS_SNIPER",
                        "barrier": barrier,
                        "exit_digit": exit_digit,
                        "is_win": is_win,
                        "profit_usd": profit,
                        "stake_usd": stake,
                    }),
                )
            )

            # Atualização atômica do saldo da conta
            if active_acc and active_acc.balance is not None:
                active_acc.balance = round(float(active_acc.balance) + profit, 2)

            # Atualização atômica do estado persistente do Sniper
            cfg = db.query(BotConfig).filter(BotConfig.user_id == user_id).first()
            if cfg:
                persistent = {
                    "session_wins": st.get("session_wins", 0),
                    "session_losses": st.get("session_losses", 0),
                    "session_profit": st.get("session_profit", 0.0),
                    "loss_pool": st.get("loss_pool", 0.0),
                    "remaining_installments": st.get("remaining_installments", 3),
                    "current_stake": st.get("current_stake", 0.35),
                    "recent_trades": st.get("recent_trades", [])[:30],
                }
                cfg.sniper_state_json = json.dumps(persistent)
                cfg.session_profit_usd = round(float(cfg.session_profit_usd or 0.0) + profit, 4)
                cfg.session_cycles_count = int(cfg.session_cycles_count or 0) + 1

            db.commit()
        except Exception as exc:
            db.rollback()
            logger.warning("[User %d] Erro atômico ao gravar trade e estado: %s", user_id, exc)
        finally:
            db.close()
