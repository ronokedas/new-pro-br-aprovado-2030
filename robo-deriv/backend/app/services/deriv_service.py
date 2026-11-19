import asyncio
import json
import logging
import math
from typing import Any, Optional

import websockets

from app.core.redis_client import redis_get_json, redis_set_json
from shared.config import get_settings
from shared.redis_keys import KEY_MARKET_SYMBOLS, key_market_candles, key_market_channel

logger = logging.getLogger(__name__)

FORBIDDEN_SCOPES = {"payments", "admin"}

MARKET_CATEGORY_MAP = {
    "cryptocurrency": "Cripto",
    "forex": "Forex",
    "synthetic_index": "Sintéticos",
    "commodities": "Commodities",
    "indices": "Índices",
}

CURATED_MULTIPLIER_SYMBOLS: list[dict[str, Any]] = [
    # Crypto
    {
        "symbol": "cryBTCUSD",
        "display_name": "BTC/USD",
        "market": "cryptocurrency",
        "market_display_name": "Cryptocurrencies",
        "category": "Cripto",
        "submarket": "non_stable_coin",
        "exchange_is_open": True,
        "min_stake": 1.0,
        "multipliers": [100, 200, 300, 500, 800],
        "default_multiplier": 100,
        "contract_types": ["MULTUP", "MULTDOWN"],
    },
    {
        "symbol": "cryETHUSD",
        "display_name": "ETH/USD",
        "market": "cryptocurrency",
        "market_display_name": "Cryptocurrencies",
        "category": "Cripto",
        "submarket": "non_stable_coin",
        "exchange_is_open": True,
        "min_stake": 1.0,
        "multipliers": [100, 200, 300, 500],
        "default_multiplier": 100,
        "contract_types": ["MULTUP", "MULTDOWN"],
    },
    # Forex
    {
        "symbol": "frxEURUSD",
        "display_name": "EUR/USD",
        "market": "forex",
        "market_display_name": "Forex",
        "category": "Forex",
        "submarket": "major_pairs",
        "exchange_is_open": True,
        "min_stake": 1.0,
        "multipliers": [50, 100, 200, 300, 500, 1000],
        "default_multiplier": 50,
        "contract_types": ["MULTUP", "MULTDOWN"],
    },
    {
        "symbol": "frxGBPUSD",
        "display_name": "GBP/USD",
        "market": "forex",
        "market_display_name": "Forex",
        "category": "Forex",
        "submarket": "major_pairs",
        "exchange_is_open": True,
        "min_stake": 1.0,
        "multipliers": [50, 100, 200, 300, 500, 1000],
        "default_multiplier": 50,
        "contract_types": ["MULTUP", "MULTDOWN"],
    },
    {
        "symbol": "frxUSDJPY",
        "display_name": "USD/JPY",
        "market": "forex",
        "market_display_name": "Forex",
        "category": "Forex",
        "submarket": "major_pairs",
        "exchange_is_open": True,
        "min_stake": 1.0,
        "multipliers": [50, 100, 200, 300, 500, 1000],
        "default_multiplier": 50,
        "contract_types": ["MULTUP", "MULTDOWN"],
    },
    {
        "symbol": "frxAUDUSD",
        "display_name": "AUD/USD",
        "market": "forex",
        "market_display_name": "Forex",
        "category": "Forex",
        "submarket": "major_pairs",
        "exchange_is_open": True,
        "min_stake": 1.0,
        "multipliers": [50, 100, 200, 300, 500],
        "default_multiplier": 50,
        "contract_types": ["MULTUP", "MULTDOWN"],
    },
    {
        "symbol": "frxEURGBP",
        "display_name": "EUR/GBP",
        "market": "forex",
        "market_display_name": "Forex",
        "category": "Forex",
        "submarket": "minor_pairs",
        "exchange_is_open": True,
        "min_stake": 1.0,
        "multipliers": [50, 100, 200, 300, 500],
        "default_multiplier": 50,
        "contract_types": ["MULTUP", "MULTDOWN"],
    },
    # Synthetics
    {
        "symbol": "R_10",
        "display_name": "Volatility 10 Index",
        "market": "synthetic_index",
        "market_display_name": "Derived",
        "category": "Sintéticos",
        "submarket": "random_index",
        "exchange_is_open": True,
        "min_stake": 1.0,
        "multipliers": [40, 100, 200, 300, 400],
        "default_multiplier": 40,
        "contract_types": ["MULTUP", "MULTDOWN"],
    },
    {
        "symbol": "R_25",
        "display_name": "Volatility 25 Index",
        "market": "synthetic_index",
        "market_display_name": "Derived",
        "category": "Sintéticos",
        "submarket": "random_index",
        "exchange_is_open": True,
        "min_stake": 1.0,
        "multipliers": [20, 40, 60, 100, 160],
        "default_multiplier": 20,
        "contract_types": ["MULTUP", "MULTDOWN"],
    },
    {
        "symbol": "R_50",
        "display_name": "Volatility 50 Index",
        "market": "synthetic_index",
        "market_display_name": "Derived",
        "category": "Sintéticos",
        "submarket": "random_index",
        "exchange_is_open": True,
        "min_stake": 1.0,
        "multipliers": [20, 40, 60, 80, 100],
        "default_multiplier": 20,
        "contract_types": ["MULTUP", "MULTDOWN"],
    },
    {
        "symbol": "R_75",
        "display_name": "Volatility 75 Index",
        "market": "synthetic_index",
        "market_display_name": "Derived",
        "category": "Sintéticos",
        "submarket": "random_index",
        "exchange_is_open": True,
        "min_stake": 1.0,
        "multipliers": [20, 50, 100, 200],
        "default_multiplier": 20,
        "contract_types": ["MULTUP", "MULTDOWN"],
    },
    {
        "symbol": "R_100",
        "display_name": "Volatility 100 Index",
        "market": "synthetic_index",
        "market_display_name": "Derived",
        "category": "Sintéticos",
        "submarket": "random_index",
        "exchange_is_open": True,
        "min_stake": 1.0,
        "multipliers": [10, 20, 40, 60, 100],
        "default_multiplier": 10,
        "contract_types": ["MULTUP", "MULTDOWN"],
    },
    {
        "symbol": "1HZ10V",
        "display_name": "Volatility 10 (1s) Index",
        "market": "synthetic_index",
        "market_display_name": "Derived",
        "category": "Sintéticos",
        "submarket": "random_index",
        "exchange_is_open": True,
        "min_stake": 1.0,
        "multipliers": [40, 100, 200, 300, 400],
        "default_multiplier": 40,
        "contract_types": ["MULTUP", "MULTDOWN"],
    },
    {
        "symbol": "1HZ25V",
        "display_name": "Volatility 25 (1s) Index",
        "market": "synthetic_index",
        "market_display_name": "Derived",
        "category": "Sintéticos",
        "submarket": "random_index",
        "exchange_is_open": True,
        "min_stake": 1.0,
        "multipliers": [20, 40, 60, 100, 160],
        "default_multiplier": 20,
        "contract_types": ["MULTUP", "MULTDOWN"],
    },
    {
        "symbol": "1HZ50V",
        "display_name": "Volatility 50 (1s) Index",
        "market": "synthetic_index",
        "market_display_name": "Derived",
        "category": "Sintéticos",
        "submarket": "random_index",
        "exchange_is_open": True,
        "min_stake": 1.0,
        "multipliers": [20, 40, 60, 80, 100],
        "default_multiplier": 20,
        "contract_types": ["MULTUP", "MULTDOWN"],
    },
    {
        "symbol": "1HZ75V",
        "display_name": "Volatility 75 (1s) Index",
        "market": "synthetic_index",
        "market_display_name": "Derived",
        "category": "Sintéticos",
        "submarket": "random_index",
        "exchange_is_open": True,
        "min_stake": 1.0,
        "multipliers": [20, 50, 100, 200],
        "default_multiplier": 20,
        "contract_types": ["MULTUP", "MULTDOWN"],
    },
    {
        "symbol": "1HZ100V",
        "display_name": "Volatility 100 (1s) Index",
        "market": "synthetic_index",
        "market_display_name": "Derived",
        "category": "Sintéticos",
        "submarket": "random_index",
        "exchange_is_open": True,
        "min_stake": 1.0,
        "multipliers": [10, 20, 40, 60, 100],
        "default_multiplier": 10,
        "contract_types": ["MULTUP", "MULTDOWN"],
    },
    # Commodities
    {
        "symbol": "frxXAUUSD",
        "display_name": "Gold/USD",
        "market": "commodities",
        "market_display_name": "Commodities",
        "category": "Commodities",
        "submarket": "metals",
        "exchange_is_open": True,
        "min_stake": 1.0,
        "multipliers": [20, 50, 100, 200, 300],
        "default_multiplier": 20,
        "contract_types": ["MULTUP", "MULTDOWN"],
    },
]

PRIORITY_SYMBOL_CODES = [item["symbol"] for item in CURATED_MULTIPLIER_SYMBOLS]


class DerivAPIError(Exception):
    """Raised when Deriv WebSocket returns an error or cannot be reached."""


class DerivScopeError(DerivAPIError):
    """Raised when a Deriv API token includes forbidden scopes (payments/admin)."""


def _get_auth_ws_urls() -> list[str]:
    settings = get_settings()
    app_id = settings.DERIV_APP_ID.strip() or "1089"
    urls = [
        f"wss://red.binaryws.com/websockets/v3?app_id={app_id}",
        f"wss://ws.binaryws.com/websockets/v3?app_id={app_id}",
        f"wss://frontend.binaryws.com/websockets/v3?app_id={app_id}",
        f"wss://ws.derivws.com/websockets/v3?app_id={app_id}",
    ]
    base = settings.DERIV_AUTH_WS_BASE.rstrip("/")
    custom = f"{base}?app_id={app_id}"
    if custom not in urls:
        urls.insert(1, custom)
    return urls


def _get_auth_ws_url() -> str:
    urls = _get_auth_ws_urls()
    return urls[0] if urls else "wss://red.binaryws.com/websockets/v3?app_id=1089"


def _get_public_ws_urls() -> list[str]:
    settings = get_settings()
    urls = []
    if settings.DERIV_PUBLIC_WS:
        urls.append(settings.DERIV_PUBLIC_WS)
    for u in _get_auth_ws_urls():
        if u not in urls:
            urls.append(u)
    return urls


async def _ws_single_request(url: str, payload: dict[str, Any], timeout: float = 10.0) -> dict[str, Any]:
    async with websockets.connect(url, open_timeout=timeout, close_timeout=3) as ws:
        await ws.send(json.dumps(payload))
        raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
        return json.loads(raw)


async def _public_ws_request(payload: dict[str, Any], timeout: float = 10.0) -> dict[str, Any]:
    last_exc: Optional[Exception] = None
    for url in _get_public_ws_urls():
        try:
            return await _ws_single_request(url, payload, timeout=timeout)
        except Exception as exc:
            last_exc = exc
            logger.debug("Deriv public WS request failed on %s: %s", url, exc)
    raise DerivAPIError(f"Falha ao conectar à API pública da Deriv: {last_exc}")


async def verify_deriv_token(token: str) -> dict[str, Any]:
    """
    Validate a Deriv API/OAuth token via `authorize` (or REST Options API for pat_ tokens).
    Rejects tokens containing `payments` or `admin` scopes.
    NEVER logs or includes the raw token in error messages.
    """
    cleaned = (token or "").strip()
    if not cleaned or len(cleaned) < 8:
        raise DerivAPIError("Token da Deriv inválido ou vazio.")

    settings = get_settings()

    if cleaned.startswith("pat_"):
        import httpx
        app_id = settings.DERIV_APP_ID.strip() or "34ZYYnkkxGGXQTva4v3lB"
        url = "https://api.derivws.com/trading/v1/options/accounts"
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                res = await client.get(
                    url,
                    headers={
                        "Authorization": f"Bearer {cleaned}",
                        "Deriv-App-ID": app_id,
                        "User-Agent": "Mozilla/5.0",
                    },
                )
            except Exception as exc:
                raise DerivAPIError(f"Falha ao conectar à API da Deriv: {exc}")

            if res.status_code != 200:
                try:
                    err_json = res.json()
                    err_msg = err_json.get("error", {}).get("message") or err_json.get("message") or res.text
                except Exception:
                    err_msg = res.text
                raise DerivAPIError(f"Token PAT recusado pela Deriv ({res.status_code}): {err_msg}")

            body = res.json()
            accounts_data = body.get("data") or []
            if not accounts_data:
                raise DerivAPIError("Nenhuma conta vinculada a este Token PAT.")

            demo_acc = next((a for a in accounts_data if str(a.get("account_type", "")).lower() == "demo"), None)
            chosen = demo_acc or accounts_data[0]
            account_id = str(chosen.get("account_id") or "")
            balance = float(chosen.get("balance", 0.0) or 0.0)
            currency = str(chosen.get("currency") or "USD").upper()
            is_virtual = (
                str(chosen.get("account_type", "")).lower() == "demo"
                or account_id.upper().startswith("VRTC")
                or account_id.upper().startswith("DOT")
            )

            return {
                "loginid": account_id,
                "currency": currency,
                "balance": balance,
                "is_virtual": is_virtual,
                "account_type": "DEMO" if is_virtual else "REAL",
                "scopes": ["read", "trade"],
                "email": None,
                "fullname": f"Conta Deriv {account_id}",
                "raw_accounts": accounts_data,
            }

    resp = None
    last_exc: Optional[Exception] = None
    for url in _get_auth_ws_urls():
        try:
            resp = await _ws_single_request(url, {"authorize": cleaned}, timeout=10.0)
            if resp:
                break
        except DerivAPIError:
            raise
        except Exception as exc:
            last_exc = exc
            logger.debug("Deriv authorize connection failed on %s: %s", url, exc)
            continue

    if resp is None:
        raise DerivAPIError(f"Não foi possível validar o token na API da Deriv: {last_exc}")

    if resp.get("error"):
        err_msg = resp["error"].get("message") or "Token rejeitado pela Deriv."
        raise DerivAPIError(err_msg)

    auth = resp.get("authorize") or {}
    loginid = str(auth.get("loginid") or "").strip()
    if not loginid:
        raise DerivAPIError("Resposta inválida da Deriv (loginid ausente).")

    scopes_raw = auth.get("scopes") or []
    scopes = [str(s).strip().lower() for s in scopes_raw]
    forbidden_found = FORBIDDEN_SCOPES.intersection(scopes)
    if forbidden_found:
        raise DerivScopeError(
            "Token rejeitado por segurança: contém permissão proibida "
            f"({', '.join(sorted(forbidden_found))}). Gere um token apenas com Read e Trade."
        )

    is_virtual_flag = int(auth.get("is_virtual", 0) or 0)
    is_virtual = is_virtual_flag == 1 or loginid.upper().startswith("VRTC")
    account_type = "DEMO" if is_virtual else "REAL"
    currency = str(auth.get("currency") or "USD").upper()
    balance = float(auth.get("balance", 0.0) or 0.0)

    return {
        "loginid": loginid,
        "currency": currency,
        "balance": balance,
        "is_virtual": is_virtual,
        "account_type": account_type,
        "scopes": scopes,
        "email": auth.get("email"),
        "fullname": auth.get("fullname"),
    }


async def fetch_account_balance(token: str) -> dict[str, Any]:
    """Authorize and fetch live balance from Deriv authenticated WS."""
    cleaned = (token or "").strip()
    if not cleaned:
        raise DerivAPIError("Token vazio.")

    settings = get_settings()

    if cleaned.startswith("pat_"):
        import httpx
        app_id = settings.DERIV_APP_ID.strip() or "34ZYYnkkxGGXQTva4v3lB"
        url = "https://api.derivws.com/trading/v1/options/accounts"
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                res = await client.get(
                    url,
                    headers={
                        "Authorization": f"Bearer {cleaned}",
                        "Deriv-App-ID": app_id,
                        "User-Agent": "Mozilla/5.0",
                    },
                )
                if res.status_code == 200:
                    body = res.json()
                    accounts_data = body.get("data") or []
                    if accounts_data:
                        demo_acc = next((a for a in accounts_data if str(a.get("account_type", "")).lower() == "demo"), None)
                        chosen = demo_acc or accounts_data[0]
                        account_id = str(chosen.get("account_id") or "")
                        balance = float(chosen.get("balance", 0.0) or 0.0)
                        currency = str(chosen.get("currency") or "USD").upper()
                        is_virtual = (
                            str(chosen.get("account_type", "")).lower() == "demo"
                            or account_id.upper().startswith("VRTC")
                            or account_id.upper().startswith("DOT")
                        )
                        return {
                            "loginid": account_id,
                            "currency": currency,
                            "balance": balance,
                            "is_virtual": is_virtual,
                            "account_type": "DEMO" if is_virtual else "REAL",
                        }
            except Exception as exc:
                logger.warning("PAT balance sync error: %s", exc)

    last_exc: Optional[Exception] = None
    for url in _get_auth_ws_urls():
        try:
            async with websockets.connect(url, open_timeout=10.0, close_timeout=3) as ws:
                await ws.send(json.dumps({"authorize": cleaned}))
                auth_resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=10.0))
                if auth_resp.get("error"):
                    raise DerivAPIError(auth_resp["error"].get("message", "Erro ao autorizar token."))

                auth = auth_resp.get("authorize") or {}
                loginid = str(auth.get("loginid") or "")
                currency = str(auth.get("currency") or "USD").upper()
                balance = float(auth.get("balance", 0.0) or 0.0)
                is_virtual = int(auth.get("is_virtual", 0) or 0) == 1 or loginid.upper().startswith("VRTC")

                await ws.send(json.dumps({"balance": 1}))
                bal_resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=10.0))
                if not bal_resp.get("error") and bal_resp.get("balance"):
                    bal_obj = bal_resp["balance"]
                    balance = float(bal_obj.get("balance", balance) or balance)
                    currency = str(bal_obj.get("currency") or currency).upper()

                return {
                    "loginid": loginid,
                    "currency": currency,
                    "balance": balance,
                    "is_virtual": is_virtual,
                    "account_type": "DEMO" if is_virtual else "REAL",
                }
        except DerivAPIError:
            raise
        except Exception as exc:
            last_exc = exc
            logger.debug("Deriv balance sync failed on %s: %s", url, exc)
            continue

    raise DerivAPIError(f"Falha ao sincronizar saldo na Deriv: {last_exc}")


def _parse_contracts_for_multiplier(contracts_resp: dict[str, Any]) -> Optional[dict[str, Any]]:
    available = (contracts_resp.get("contracts_for") or {}).get("available") or []
    mult_contracts = [c for c in available if str(c.get("contract_category", "")).lower() == "multiplier"]
    if not mult_contracts:
        return None

    multipliers_set: set[int] = set()
    contract_types_set: set[str] = set()
    min_stakes: list[float] = []

    for c in mult_contracts:
        ctype = str(c.get("contract_type") or "").upper()
        if ctype in ("MULTUP", "MULTDOWN"):
            contract_types_set.add(ctype)
        for m in c.get("multiplier_range") or []:
            try:
                multipliers_set.add(int(m))
            except (TypeError, ValueError):
                pass
        for field in ("min_contract_stake", "min_stake"):
            val = c.get(field)
            if val is not None:
                try:
                    min_stakes.append(float(val))
                except (TypeError, ValueError):
                    pass

    multipliers = sorted(multipliers_set)
    if not multipliers:
        return None

    min_stake = min(min_stakes) if min_stakes else 1.0
    contract_types = sorted(contract_types_set) or ["MULTUP", "MULTDOWN"]

    return {
        "min_stake": round(min_stake, 2),
        "multipliers": multipliers,
        "default_multiplier": multipliers[0],
        "contract_types": contract_types,
    }


async def fetch_multiplier_symbols(force_refresh: bool = False) -> list[dict[str, Any]]:
    """
    Query Deriv public WS for active Multiplier-capable symbols across Crypto, Forex,
    Synthetics, and Commodities. Caches results in Redis `KEY_MARKET_SYMBOLS` for 10 min.
    Falls back cleanly to curated symbol catalog if Deriv WS is unreachable or slow.
    """
    if not force_refresh:
        cached = redis_get_json(KEY_MARKET_SYMBOLS)
        if isinstance(cached, list) and cached:
            return cached

    curated_by_code = {item["symbol"]: dict(item) for item in CURATED_MULTIPLIER_SYMBOLS}

    try:
        active_resp = await _public_ws_request(
            {"active_symbols": "brief", "product_type": "basic"},
            timeout=8.0,
        )
        raw_symbols = active_resp.get("active_symbols") or []
        if not raw_symbols:
            raise DerivAPIError("Empty active_symbols response")

        active_map: dict[str, dict[str, Any]] = {}
        for item in raw_symbols:
            code = item.get("symbol")
            if code:
                active_map[code] = item

        # Build candidate list: priority symbols first + extra liquid symbols from target markets
        candidates: list[str] = []
        for code in PRIORITY_SYMBOL_CODES:
            if code in active_map:
                candidates.append(code)

        for item in raw_symbols:
            code = item.get("symbol")
            market = item.get("market")
            if (
                code
                and code not in candidates
                and market in ("cryptocurrency", "forex", "synthetic_index", "commodities")
                and len(candidates) < 28
            ):
                candidates.append(code)

        # Query contracts_for over a single pipelined WS connection for speed
        results: list[dict[str, Any]] = []
        url = _get_public_ws_urls()[0]
        try:
            async with websockets.connect(url, open_timeout=6.0, close_timeout=3) as ws:
                for idx, sym in enumerate(candidates, start=1):
                    await ws.send(json.dumps({"contracts_for": sym, "req_id": idx}))

                received = 0
                contracts_by_sym: dict[str, dict[str, Any]] = {}
                deadline = asyncio.get_running_loop().time() + 7.0

                while received < len(candidates):
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        break
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
                        msg = json.loads(raw)
                    except asyncio.TimeoutError:
                        break

                    received += 1
                    echo = msg.get("echo_req") or {}
                    sym_code = echo.get("contracts_for")
                    if sym_code and not msg.get("error"):
                        contracts_by_sym[sym_code] = msg
        except Exception as exc:
            logger.debug("Batch contracts_for failed: %s", exc)
            contracts_by_sym = {}

        for sym_code in candidates:
            meta = active_map.get(sym_code, {})
            market = str(meta.get("market") or curated_by_code.get(sym_code, {}).get("market", "other"))
            parsed = _parse_contracts_for_multiplier(contracts_by_sym.get(sym_code, {}))

            if parsed is None:
                # If live contracts_for didn't return multipliers, keep only if curated fallback exists
                if sym_code in curated_by_code:
                    entry = dict(curated_by_code[sym_code])
                    entry["exchange_is_open"] = bool(meta.get("exchange_is_open", 1))
                    if meta.get("display_name"):
                        entry["display_name"] = meta["display_name"]
                    results.append(entry)
                continue

            results.append(
                {
                    "symbol": sym_code,
                    "display_name": meta.get("display_name") or sym_code,
                    "market": market,
                    "market_display_name": meta.get("market_display_name") or market.title(),
                    "category": MARKET_CATEGORY_MAP.get(market, "Outros"),
                    "submarket": meta.get("submarket") or "",
                    "exchange_is_open": bool(meta.get("exchange_is_open", 0)),
                    "min_stake": parsed["min_stake"],
                    "multipliers": parsed["multipliers"],
                    "default_multiplier": parsed["default_multiplier"],
                    "contract_types": parsed["contract_types"],
                }
            )

        if results:
            redis_set_json(KEY_MARKET_SYMBOLS, results, ttl=600)
            return results
    except Exception as exc:
        logger.warning("Falling back to curated multiplier symbols: %s", exc)

    fallback = [dict(item) for item in CURATED_MULTIPLIER_SYMBOLS]
    redis_set_json(KEY_MARKET_SYMBOLS, fallback, ttl=120)
    return fallback


async def fetch_proposal_preview(
    symbol: str,
    amount: float = 1.0,
    multiplier: int = 100,
    contract_type: str = "MULTUP",
    currency: str = "USD",
) -> dict[str, Any]:
    """Query live Deriv public WS `proposal` for real commission & stop-out distance."""
    ctype = contract_type.upper()
    if ctype not in ("MULTUP", "MULTDOWN"):
        ctype = "MULTUP"

    payload = {
        "proposal": 1,
        "amount": round(float(amount), 2),
        "basis": "stake",
        "contract_type": ctype,
        "currency": currency.upper(),
        "multiplier": int(multiplier),
        "underlying_symbol": symbol,
    }

    try:
        resp = await _public_ws_request(payload, timeout=10.0)
    except DerivAPIError as exc:
        return {
            "symbol": symbol,
            "amount": float(amount),
            "multiplier": int(multiplier),
            "contract_type": ctype,
            "currency": currency.upper(),
            "ask_price": float(amount),
            "commission": 0.0,
            "stop_out_value": None,
            "spot": None,
            "stop_out_pct": None,
            "error": str(exc),
        }

    if resp.get("error"):
        return {
            "symbol": symbol,
            "amount": float(amount),
            "multiplier": int(multiplier),
            "contract_type": ctype,
            "currency": currency.upper(),
            "ask_price": float(amount),
            "commission": 0.0,
            "stop_out_value": None,
            "spot": None,
            "stop_out_pct": None,
            "error": resp["error"].get("message", "Erro na proposta da Deriv."),
        }

    prop = resp.get("proposal") or {}
    ask_price = float(prop.get("ask_price", amount) or amount)
    commission = float(prop.get("commission", 0.0) or 0.0)
    spot = float(prop.get("spot", 0.0) or 0.0) if prop.get("spot") is not None else None

    limit_order = prop.get("limit_order") or {}
    stop_out = limit_order.get("stop_out") or {}
    raw_so_val = stop_out.get("value")
    stop_out_value: Optional[float] = None
    if raw_so_val is not None:
        try:
            stop_out_value = float(raw_so_val)
        except (TypeError, ValueError):
            stop_out_value = None

    stop_out_pct: Optional[float] = None
    if spot and spot > 0 and stop_out_value is not None and stop_out_value > 0:
        # Check if stop_out_value looks like a price level (within 50% of spot)
        rel_diff = abs(spot - stop_out_value) / spot
        if rel_diff < 0.5:
            stop_out_pct = round(rel_diff * 100.0, 4)

    if stop_out_pct is None and amount > 0 and multiplier > 0:
        effective_stake = max(0.0, float(amount) - commission)
        stop_out_pct = round((effective_stake / (float(amount) * float(multiplier))) * 100.0, 4)
        if spot and spot > 0 and stop_out_value is None:
            factor = 1.0 - (stop_out_pct / 100.0) if ctype == "MULTUP" else 1.0 + (stop_out_pct / 100.0)
            stop_out_value = round(spot * factor, 5)

    return {
        "symbol": symbol,
        "amount": float(amount),
        "multiplier": int(multiplier),
        "contract_type": ctype,
        "currency": currency.upper(),
        "ask_price": round(ask_price, 4),
        "commission": round(commission, 4),
        "stop_out_value": stop_out_value,
        "spot": spot,
        "stop_out_pct": stop_out_pct,
        "error": None,
    }


def _compute_ema(values: list[float], period: int) -> Optional[float]:
    if len(values) < period or period <= 0:
        return None
    k = 2.0 / (period + 1.0)
    ema = sum(values[:period]) / period
    for v in values[period:]:
        ema = (v * k) + (ema * (1.0 - k))
    return ema


def _compute_rsi(closes: list[float], period: int = 14) -> Optional[float]:
    if len(closes) <= period:
        return None
    gains: list[float] = []
    losses: list[float] = []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0.0))
        losses.append(max(-diff, 0.0))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _compute_atr(candles: list[dict[str, Any]], period: int = 14) -> Optional[float]:
    if len(candles) <= period:
        return None
    trs: list[float] = []
    for i in range(1, len(candles)):
        h = float(candles[i]["high"])
        l = float(candles[i]["low"])
        prev_c = float(candles[i - 1]["close"])
        trs.append(max(h - l, abs(h - prev_c), abs(l - prev_c)))
    atr = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
    return atr


def compute_causal_channel(candles: list[dict[str, Any]], window: int = 80) -> Optional[dict[str, Any]]:
    """
    Compute a 100% causal linear regression channel over the latest `window` candles.
    Used as an immediate fallback whenever the worker hasn't cached `key_market_channel` yet.
    """
    if not candles or len(candles) < 10:
        return None

    segment = candles[-min(len(candles), window):]
    n = len(segment)
    closes_seg = [float(c["close"]) for c in segment]
    all_closes = [float(c["close"]) for c in candles]

    x_mean = (n - 1) / 2.0
    y_mean = sum(closes_seg) / n

    ss_xx = sum((i - x_mean) ** 2 for i in range(n))
    if ss_xx == 0:
        return None
    ss_xy = sum((i - x_mean) * (closes_seg[i] - y_mean) for i in range(n))
    slope = ss_xy / ss_xx
    intercept = y_mean - slope * x_mean

    fitted = [intercept + slope * i for i in range(n)]
    residuals = [closes_seg[i] - fitted[i] for i in range(n)]
    ss_res = sum(r * r for r in residuals)
    ss_tot = sum((y - y_mean) ** 2 for y in closes_seg)
    r2 = 1.0 - (ss_res / ss_tot) if ss_tot > 0 else 0.0
    r2 = max(0.0, min(1.0, r2))
    sigma = math.sqrt(ss_res / n)

    points = []
    for i, c in enumerate(segment):
        mid = fitted[i]
        points.append(
            {
                "time": int(c["time"]),
                "upper": round(mid + 2.0 * sigma, 6),
                "mid": round(mid, 6),
                "lower": round(mid - 2.0 * sigma, 6),
            }
        )

    ema50 = _compute_ema(all_closes, 50)
    ema200 = _compute_ema(all_closes, 200)
    rsi14 = _compute_rsi(all_closes, 14)
    atr14 = _compute_atr(candles, 14)

    if slope > 0 and r2 >= 0.45:
        trend = "UP"
    elif slope < 0 and r2 >= 0.45:
        trend = "DOWN"
    else:
        trend = "SIDEWAYS"

    last_point = points[-1]
    upper_line = [{"time": p["time"], "value": p["upper"]} for p in points]
    center_line = [{"time": p["time"], "value": p["mid"]} for p in points]
    lower_line = [{"time": p["time"], "value": p["lower"]} for p in points]

    return {
        "window": n,
        "slope": slope,
        "intercept": intercept,
        "r2": round(r2, 4),
        "sigma": round(sigma, 6),
        "trend": trend,
        "upper": last_point["upper"],
        "mid": last_point["mid"],
        "center": last_point["mid"],
        "lower": last_point["lower"],
        "upper_line": upper_line,
        "center_line": center_line,
        "lower_line": lower_line,
        "ema50": round(ema50, 6) if ema50 is not None else None,
        "ema200": round(ema200, 6) if ema200 is not None else None,
        "rsi14": round(rsi14, 2) if rsi14 is not None else None,
        "atr14": round(atr14, 6) if atr14 is not None else None,
        "points": points,
        "series": points,
    }


async def fetch_candles(symbol: str, granularity: int = 300, count: int = 200) -> dict[str, Any]:
    """
    Return candles + causal channel overlay for charting.
    1. Checks Redis `key_market_candles(symbol, granularity)` and `key_market_channel(symbol, granularity)`.
    2. Falls back to live Deriv public WS `ticks_history` (`style="candles"`).
    3. Computes causal channel overlay if worker hasn't cached `key_market_channel` yet.
    """
    candles_key = key_market_candles(symbol, granularity)
    channel_key = key_market_channel(symbol, granularity)

    candles = redis_get_json(candles_key)
    source = "redis"

    if not isinstance(candles, list) or not candles:
        source = "live_deriv"
        payload = {
            "ticks_history": symbol,
            "adjust_start_time": 1,
            "count": max(20, min(int(count), 1000)),
            "end": "latest",
            "granularity": int(granularity),
            "style": "candles",
        }
        try:
            resp = await _public_ws_request(payload, timeout=10.0)
            if resp.get("error"):
                raise DerivAPIError(resp["error"].get("message", "Erro ao buscar candles."))
            raw_candles = resp.get("candles") or []
            candles = [
                {
                    "time": int(c["epoch"]),
                    "open": float(c["open"]),
                    "high": float(c["high"]),
                    "low": float(c["low"]),
                    "close": float(c["close"]),
                }
                for c in raw_candles
            ]
            if candles:
                redis_set_json(candles_key, candles, ttl=30)
        except Exception as exc:
            logger.warning("Failed to fetch live candles for %s/%s: %s", symbol, granularity, exc)
            candles = []
            source = "unavailable"

    channel = redis_get_json(channel_key)
    if not isinstance(channel, dict) or not (channel.get("upper_line") or channel.get("series") or channel.get("points")):
        channel = compute_causal_channel(candles)
    elif channel:
        # Guarantee upper_line, center_line, lower_line exist
        if not channel.get("upper_line") and channel.get("series"):
            channel["upper_line"] = [{"time": pt["time"], "value": pt.get("upper")} for pt in channel["series"]]
            channel["center_line"] = [{"time": pt["time"], "value": pt.get("center", pt.get("mid"))} for pt in channel["series"]]
            channel["lower_line"] = [{"time": pt["time"], "value": pt.get("lower")} for pt in channel["series"]]
        if "center" not in channel and "mid" in channel:
            channel["center"] = channel["mid"]

    return {
        "symbol": symbol,
        "granularity": int(granularity),
        "candles": candles,
        "channel": channel,
        "source": source,
    }
