"""Robust Async Deriv WebSocket API v3 Client.

Features:
- Supports public (shared market feed) and authenticated (per-user) sessions.
- Automatic req_id -> asyncio.Future correlation with timeouts.
- Periodic ping heartbeat every 25 seconds.
- Automatic reconnect with exponential backoff, re-authorization, and reconnect hooks.
- Strict token redaction: NEVER logs API tokens in stdout, logs, or exceptions.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Awaitable, Callable, Optional, Union

import websockets

from shared.config import get_settings

logger = logging.getLogger("worker.deriv_client")

CallbackType = Callable[[dict[str, Any]], Union[Awaitable[None], None]]
ReconnectHook = Callable[["DerivClient"], Awaitable[None]]


class DerivAPIError(Exception):
    """Raised when Deriv WebSocket API returns an error object."""

    def __init__(self, code: str, message: str, raw: Optional[dict[str, Any]] = None) -> None:
        self.code = code or "UnknownError"
        self.message = message or "Erro desconhecido na API Deriv"
        self.raw = raw or {}
        super().__init__(f"[{self.code}] {self.message}")


def _sanitize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of payload with sensitive credentials redacted."""
    clean = dict(payload)
    if "authorize" in clean:
        clean["authorize"] = "***REDACTED***"
    if "token" in clean:
        clean["token"] = "***REDACTED***"
    return clean


def extract_stop_out_pct(
    proposal_resp: dict[str, Any],
    contract_type: str = "MULTUP",
    fallback_stake: float = 1.0,
    fallback_multiplier: int = 100,
) -> tuple[float, Optional[float]]:
    """Extract real stop-out distance fraction and stop_out_spot from a Deriv `proposal` response.

    Returns:
        (stop_out_pct_positive, stop_out_spot_or_None)
    """
    prop = proposal_resp.get("proposal", proposal_resp) or {}
    spot = float(prop.get("spot") or 0.0)
    limit_order = prop.get("limit_order") or {}
    stop_out_obj = limit_order.get("stop_out") or {}
    stop_out_val = stop_out_obj.get("value")

    if stop_out_val is not None and spot > 0.0:
        try:
            so_spot = float(stop_out_val)
            pct = abs(spot - so_spot) / spot
            if 1e-5 < pct < 0.99:
                return round(pct, 6), so_spot
        except (TypeError, ValueError):
            pass

    # Exact mathematical fallback for Multipliers: (stake - commission) / (stake * multiplier)
    stake = float(prop.get("ask_price") or fallback_stake or 1.0)
    commission = float(prop.get("commission") or (0.07 * stake))
    mult = int(prop.get("multiplier") or fallback_multiplier or 100)
    if stake > 0.0 and mult > 0:
        pct = max(0.001, (stake - commission) / (stake * mult))
    else:
        pct = 0.0093

    so_spot = None
    if spot > 0.0:
        if contract_type.upper() == "MULTUP":
            so_spot = round(spot * (1.0 - pct), 6)
        else:
            so_spot = round(spot * (1.0 + pct), 6)

    return round(pct, 6), so_spot


class DerivClient:
    """Async Deriv WebSocket client with req_id correlation, ping, and reconnect."""

    def __init__(
        self,
        ws_url: Optional[str] = None,
        fallback_ws_url: Optional[str] = None,
        ws_url_factory: Optional[Callable[[], Awaitable[str]]] = None,
        token: Optional[str] = None,
        ping_interval: float = 25.0,
        reconnect_min_sec: float = 1.0,
        reconnect_max_sec: float = 30.0,
        on_reconnect: Optional[ReconnectHook] = None,
        label: str = "deriv",
    ) -> None:
        settings = get_settings()
        default_v3 = f"{settings.DERIV_AUTH_WS_BASE}?app_id={settings.DERIV_APP_ID}"
        self.ws_url = ws_url or default_v3
        self.fallback_ws_url = fallback_ws_url
        self.ws_url_factory = ws_url_factory
        self._token: Optional[str] = token
        self.ping_interval = float(ping_interval)
        self.reconnect_min_sec = float(reconnect_min_sec)
        self.reconnect_max_sec = float(reconnect_max_sec)
        self.on_reconnect = on_reconnect
        self.label = label

        self._ws: Any = None
        self._connected_event = asyncio.Event()
        self._stop_event = asyncio.Event()
        self._send_lock = asyncio.Lock()

        self._req_id_counter = 0
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._sub_callbacks: dict[str, CallbackType] = {}
        self._pending_sub_callbacks: dict[int, CallbackType] = {}
        self._contract_callbacks: dict[str, CallbackType] = {}

        self._runner_task: Optional[asyncio.Task[None]] = None
        self._ping_task: Optional[asyncio.Task[None]] = None
        self._authorized_info: Optional[dict[str, Any]] = None
        self._has_connected_once = False

    @property
    def is_connected(self) -> bool:
        return self._connected_event.is_set() and self._ws is not None

    @property
    def authorized_info(self) -> Optional[dict[str, Any]]:
        return self._authorized_info

    async def connect(self, timeout: float = 15.0) -> None:
        """Start connection supervisor and wait until the first connection is ready."""
        if self._runner_task is None or self._runner_task.done():
            self._stop_event.clear()
            self._runner_task = asyncio.create_task(
                self._connection_supervisor(), name=f"deriv-ws-{self.label}"
            )
        await asyncio.wait_for(self._connected_event.wait(), timeout=timeout)

    async def close(self) -> None:
        """Gracefully close connection and stop background tasks."""
        self._stop_event.set()
        self._connected_event.clear()

        if self._ping_task is not None:
            self._ping_task.cancel()
            try:
                await self._ping_task
            except asyncio.CancelledError:
                pass
            self._ping_task = None

        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

        if self._runner_task is not None:
            self._runner_task.cancel()
            try:
                await self._runner_task
            except asyncio.CancelledError:
                pass
            self._runner_task = None

        self._fail_all_pending(ConnectionError("DerivClient fechado"))
        self._sub_callbacks.clear()
        self._pending_sub_callbacks.clear()

    def _fail_all_pending(self, exc: Exception) -> None:
        for req_id, fut in list(self._pending.items()):
            if not fut.done():
                fut.set_exception(exc)
            self._pending.pop(req_id, None)

    async def _connection_supervisor(self) -> None:
        backoff = self.reconnect_min_sec
        urls = [self.ws_url]
        if self.fallback_ws_url and self.fallback_ws_url != self.ws_url:
            urls.append(self.fallback_ws_url)
        url_idx = 0

        while not self._stop_event.is_set():
            if self.ws_url_factory is not None:
                try:
                    current_url = await self.ws_url_factory()
                except Exception as fac_exc:
                    logger.warning("[%s] Erro ao obter URL via ws_url_factory: %s", self.label, fac_exc)
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2.0, self.reconnect_max_sec)
                    continue
            else:
                current_url = urls[url_idx % len(urls)]

            try:
                async with websockets.connect(
                    current_url,
                    ping_interval=None,  # We manage Deriv JSON ping explicitly every 25s
                    max_size=4 * 1024 * 1024,
                    close_timeout=5,
                ) as ws:
                    self._ws = ws
                    self._sub_callbacks.clear()
                    self._pending_sub_callbacks.clear()
                    backoff = self.reconnect_min_sec

                    # Start reader & ping concurrently
                    read_task = asyncio.create_task(self._read_loop(ws), name=f"deriv-read-{self.label}")
                    self._ping_task = asyncio.create_task(self._ping_loop(), name=f"deriv-ping-{self.label}")

                    try:
                        # If token is configured, authorize immediately before marking ready (PAT tokens are pre-authorized via OTP URL)
                        if self._token:
                            if not self._token.startswith("pat_"):
                                self._authorized_info = await self._authorize_internal(self._token)
                            else:
                                self._authorized_info = {"loginid": self.label, "is_virtual": True}

                        is_reconnect = self._has_connected_once
                        self._has_connected_once = True
                        self._connected_event.set()
                        logger.info("[%s] Conectado ao WebSocket Deriv (%s)", self.label, current_url.split("?")[0])

                        if is_reconnect and self.on_reconnect is not None:
                            try:
                                await self.on_reconnect(self)
                            except Exception as hook_exc:
                                logger.warning("[%s] Erro em on_reconnect: %s", self.label, hook_exc)

                        await read_task
                    finally:
                        self._connected_event.clear()
                        if self._ping_task is not None:
                            self._ping_task.cancel()
                            try:
                                await self._ping_task
                            except asyncio.CancelledError:
                                pass
                            self._ping_task = None
                        if not read_task.done():
                            read_task.cancel()
                            try:
                                await read_task
                            except asyncio.CancelledError:
                                pass
                        self._ws = None
                        self._fail_all_pending(ConnectionError("WebSocket Deriv desconectado"))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._connected_event.clear()
                self._ws = None
                self._fail_all_pending(ConnectionError(f"Falha de conexão Deriv: {exc.__class__.__name__}"))
                logger.warning(
                    "[%s] Conexão WS Deriv perdida (%s). Reconectando em %.1fs...",
                    self.label,
                    exc.__class__.__name__,
                    backoff,
                )
                url_idx += 1
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=backoff)
                except asyncio.TimeoutError:
                    pass
                backoff = min(self.reconnect_max_sec, backoff * 2.0)

    async def _read_loop(self, ws: Any) -> None:
        async for raw in ws:
            if self._stop_event.is_set():
                break
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            await self._dispatch_message(msg)

    async def _dispatch_message(self, msg: dict[str, Any]) -> None:
        req_id = msg.get("req_id")
        sub_obj = msg.get("subscription") or {}
        sub_id = sub_obj.get("id")

        # Promote pending subscription callback using req_id -> sub_id on first reply
        if req_id is not None and req_id in self._pending_sub_callbacks and sub_id:
            cb = self._pending_sub_callbacks.pop(req_id)
            self._sub_callbacks[sub_id] = cb

        # Resolve request-response Future
        if req_id is not None and req_id in self._pending:
            fut = self._pending.pop(req_id)
            if not fut.done():
                if "error" in msg:
                    err = msg["error"]
                    fut.set_exception(
                        DerivAPIError(
                            code=str(err.get("code", "APIError")),
                            message=str(err.get("message", "Erro na API Deriv")),
                            raw=err,
                        )
                    )
                else:
                    fut.set_result(msg)

        # Dispatch streaming update callbacks
        cb = self._sub_callbacks.get(sub_id) if sub_id else None
        if cb is None and msg.get("msg_type") == "proposal_open_contract":
            poc = msg.get("proposal_open_contract") or {}
            cid = poc.get("contract_id")
            if cid is not None:
                cb = self._contract_callbacks.get(str(cid))

        if cb is not None and "error" not in msg:
            try:
                res = cb(msg)
                if asyncio.iscoroutine(res):
                    asyncio.create_task(self._safe_callback(res))
            except Exception as exc:
                logger.warning("[%s] Erro no callback de stream: %s", self.label, exc)

    async def _safe_callback(self, coro: Awaitable[None]) -> None:
        try:
            await coro
        except Exception as exc:
            logger.warning("[%s] Exceção assíncrona em callback de stream: %s", self.label, exc)

    async def _ping_loop(self) -> None:
        while not self._stop_event.is_set():
            await asyncio.sleep(self.ping_interval)
            if self._ws is None:
                break
            try:
                await self.send_request({"ping": 1}, timeout=10.0, allow_unready=True)
            except Exception as exc:
                logger.debug("[%s] Ping Deriv falhou: %s", self.label, exc)
                if self._ws is not None:
                    try:
                        await self._ws.close()
                    except Exception:
                        pass
                break

    async def send_request(
        self,
        payload: dict[str, Any],
        timeout: float = 15.0,
        stream_callback: Optional[CallbackType] = None,
        allow_unready: bool = False,
    ) -> dict[str, Any]:
        """Send JSON request correlated by `req_id` and await Deriv response."""
        if not allow_unready:
            await asyncio.wait_for(self._connected_event.wait(), timeout=timeout)

        if self._ws is None:
            raise ConnectionError("WebSocket Deriv não conectado")

        self._req_id_counter += 1
        req_id = self._req_id_counter
        req_payload = dict(payload)
        req_payload["req_id"] = req_id

        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[req_id] = fut
        if stream_callback is not None:
            self._pending_sub_callbacks[req_id] = stream_callback

        try:
            async with self._send_lock:
                if self._ws is None:
                    raise ConnectionError("WebSocket Deriv desconectado durante envio")
                await self._ws.send(json.dumps(req_payload))
            return await asyncio.wait_for(fut, timeout=timeout)
        except Exception:
            self._pending.pop(req_id, None)
            self._pending_sub_callbacks.pop(req_id, None)
            raise

    async def _authorize_internal(self, token: str) -> dict[str, Any]:
        resp = await self.send_request({"authorize": token}, timeout=15.0, allow_unready=True)
        return resp.get("authorize") or {}

    async def authorize(self, token: str) -> dict[str, Any]:
        """Authenticate session with Deriv API token (never logged)."""
        self._token = token
        resp = await self.send_request({"authorize": token}, timeout=15.0)
        self._authorized_info = resp.get("authorize") or {}
        return self._authorized_info

    async def active_symbols(self, mode: str = "brief", product_type: Optional[str] = None) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {"active_symbols": mode}
        if product_type:
            payload["product_type"] = product_type
        resp = await self.send_request(payload, timeout=20.0)
        return list(resp.get("active_symbols") or [])

    async def contracts_for(self, symbol: str, currency: Optional[str] = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"contracts_for": symbol}
        if currency:
            payload["currency"] = currency
        resp = await self.send_request(payload, timeout=15.0)
        return resp.get("contracts_for") or {}

    async def ticks_history(
        self,
        symbol: str,
        granularity: int = 300,
        count: int = 250,
        style: str = "candles",
        end: str = "latest",
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "ticks_history": symbol,
            "adjust_start_time": 1,
            "count": int(count),
            "end": end,
            "style": style,
        }
        if style == "candles":
            payload["granularity"] = int(granularity)
        return await self.send_request(payload, timeout=20.0)

    async def subscribe_ticks(self, symbol: str, callback: CallbackType) -> str:
        resp = await self.send_request(
            {"ticks": symbol, "subscribe": 1},
            timeout=15.0,
            stream_callback=callback,
        )
        sub_id = str((resp.get("subscription") or {}).get("id", ""))
        if sub_id:
            self._sub_callbacks[sub_id] = callback
        return sub_id

    async def subscribe_candles(self, symbol: str, granularity: int, callback: CallbackType) -> str:
        resp = await self.send_request(
            {
                "ticks_history": symbol,
                "adjust_start_time": 1,
                "count": 2,
                "end": "latest",
                "granularity": int(granularity),
                "style": "candles",
                "subscribe": 1,
            },
            timeout=15.0,
            stream_callback=callback,
        )
        sub_id = str((resp.get("subscription") or {}).get("id", ""))
        if sub_id:
            self._sub_callbacks[sub_id] = callback
        return sub_id

    async def proposal(
        self,
        symbol: str,
        contract_type: str,
        amount: float,
        multiplier: int,
        currency: str = "USD",
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
    ) -> dict[str, Any]:
        """Request a Multiplier (`MULTUP`/`MULTDOWN`) proposal."""
        payload: dict[str, Any] = {
            "proposal": 1,
            "amount": round(float(amount), 2),
            "basis": "stake",
            "contract_type": contract_type.upper(),
            "currency": currency.upper(),
            "multiplier": int(multiplier),
            "underlying_symbol": symbol,
        }
        limit_order: dict[str, float] = {}
        if stop_loss is not None and stop_loss > 0.0:
            limit_order["stop_loss"] = round(float(stop_loss), 2)
        if take_profit is not None and take_profit > 0.0:
            limit_order["take_profit"] = round(float(take_profit), 2)
        if limit_order:
            payload["limit_order"] = limit_order
        try:
            return await self.send_request(payload, timeout=15.0)
        except DerivAPIError as exc:
            if exc.code == "InputValidationFailed" and "underlying_symbol" in exc.message:
                payload.pop("underlying_symbol", None)
                payload["symbol"] = symbol
                return await self.send_request(payload, timeout=15.0)
            raise

    async def proposal_tick(
        self,
        symbol: str,
        contract_type: str,
        amount: float,
        duration: int = 5,
        duration_unit: str = "t",
        barrier: Optional[str] = None,
        currency: str = "USD",
    ) -> dict[str, Any]:
        """Request a tick-based contract proposal (Rise/Fall or Digits)."""
        payload: dict[str, Any] = {
            "proposal": 1,
            "amount": round(float(amount), 2),
            "basis": "stake",
            "contract_type": contract_type.upper(),
            "currency": currency.upper(),
            "duration": int(duration),
            "duration_unit": duration_unit,
            "underlying_symbol": symbol,
        }
        if barrier is not None:
            payload["barrier"] = str(barrier)
        try:
            return await self.send_request(payload, timeout=15.0)
        except DerivAPIError as exc:
            if exc.code == "InputValidationFailed" and "underlying_symbol" in exc.message:
                payload.pop("underlying_symbol", None)
                payload["symbol"] = symbol
                return await self.send_request(payload, timeout=15.0)
            raise

    async def buy(self, proposal_id: str, price: float) -> dict[str, Any]:
        """Execute a contract buy from a valid `proposal_id`."""
        return await self.send_request(
            {"buy": str(proposal_id), "price": round(float(price), 2)},
            timeout=15.0,
        )

    async def sell(self, contract_id: Union[int, str], price: float = 0.0) -> dict[str, Any]:
        """Immediately sell/close an open contract (price=0 for market sell)."""
        cid: Union[int, str] = int(contract_id) if str(contract_id).isdigit() else str(contract_id)
        return await self.send_request(
            {"sell": cid, "price": round(float(price), 2)},
            timeout=15.0,
        )

    async def subscribe_open_contract(
        self,
        contract_id: Union[int, str],
        callback: CallbackType,
    ) -> dict[str, Any]:
        """Subscribe to real-time `proposal_open_contract` updates for a contract."""
        cid: Union[int, str] = int(contract_id) if str(contract_id).isdigit() else str(contract_id)
        self._contract_callbacks[str(contract_id)] = callback
        resp = await self.send_request(
            {"proposal_open_contract": 1, "contract_id": cid, "subscribe": 1},
            timeout=15.0,
            stream_callback=callback,
        )
        sub_id = str((resp.get("subscription") or {}).get("id", ""))
        if sub_id:
            self._sub_callbacks[sub_id] = callback
        return resp

    async def get_open_contract(self, contract_id: Union[int, str]) -> dict[str, Any]:
        cid: Union[int, str] = int(contract_id) if str(contract_id).isdigit() else str(contract_id)
        return await self.send_request(
            {"proposal_open_contract": 1, "contract_id": cid},
            timeout=15.0,
        )

    async def portfolio(self) -> list[dict[str, Any]]:
        """Retrieve all currently open contracts on the authenticated Deriv account."""
        resp = await self.send_request({"portfolio": 1}, timeout=15.0)
        return list((resp.get("portfolio") or {}).get("contracts") or [])

    async def get_balance(
        self,
        subscribe: bool = False,
        callback: Optional[CallbackType] = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"balance": 1}
        if subscribe:
            payload["subscribe"] = 1
        resp = await self.send_request(payload, timeout=15.0, stream_callback=callback)
        return resp.get("balance") or {}

    async def forget(self, subscription_id: str) -> dict[str, Any]:
        self._sub_callbacks.pop(subscription_id, None)
        return await self.send_request({"forget": subscription_id}, timeout=10.0)
