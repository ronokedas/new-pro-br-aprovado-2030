from typing import Literal, Optional

from fastapi import APIRouter, Depends, Query

from app.core.security import get_current_user
from app.services.deriv_service import (
    fetch_candles,
    fetch_multiplier_symbols,
    fetch_proposal_preview,
)
from shared.models import User

router = APIRouter(prefix="/api/market", tags=["Market"])


@router.get("/symbols")
async def get_market_symbols(
    refresh: bool = Query(default=False),
    category: Optional[str] = Query(default=None),
    search: Optional[str] = Query(default=None),
    _user: User = Depends(get_current_user),
):
    symbols = await fetch_multiplier_symbols(force_refresh=refresh)

    if category:
        cat_norm = category.strip().lower()
        symbols = [
            s
            for s in symbols
            if str(s.get("category", "")).lower() == cat_norm
            or str(s.get("market", "")).lower() == cat_norm
        ]

    if search:
        q = search.strip().lower()
        symbols = [
            s
            for s in symbols
            if q in str(s.get("symbol", "")).lower()
            or q in str(s.get("display_name", "")).lower()
            or q in str(s.get("category", "")).lower()
        ]

    return {"symbols": symbols, "count": len(symbols)}


@router.get("/proposal-preview")
async def get_proposal_preview(
    symbol: str = Query(..., min_length=2, max_length=64),
    amount: float = Query(default=1.0, ge=0.35, le=10000.0),
    multiplier: int = Query(default=100, ge=1, le=5000),
    contract_type: Literal["MULTUP", "MULTDOWN"] = Query(default="MULTUP"),
    currency: str = Query(default="USD", min_length=2, max_length=16),
    _user: User = Depends(get_current_user),
):
    return await fetch_proposal_preview(
        symbol=symbol,
        amount=amount,
        multiplier=multiplier,
        contract_type=contract_type,
        currency=currency,
    )


@router.get("/chart")
async def get_market_chart(
    symbol: str = Query(default="cryBTCUSD", min_length=2, max_length=64),
    granularity: int = Query(default=300, ge=60, le=86400),
    count: int = Query(default=200, ge=20, le=500),
    _user: User = Depends(get_current_user),
):
    return await fetch_candles(symbol=symbol, granularity=granularity, count=count)


@router.get("/scanner")
async def get_market_scanner(
    symbols: Optional[str] = Query(default=None),
    granularity: int = Query(default=300, ge=60, le=86400),
    _user: User = Depends(get_current_user),
):
    """Scan candidate market pairs simultaneously, returning channel distances and trigger status."""
    import asyncio, json
    sym_list = []
    if symbols:
        sym_list = [s.strip() for s in symbols.split(",") if s.strip()]
    if not sym_list:
        sym_list = ["cryBTCUSD", "cryETHUSD", "frxEURUSD", "frxGBPUSD", "frxUSDJPY", "1HZ100V", "R_75"]

    async def scan_single(sym: str) -> dict:
        try:
            data = await fetch_candles(symbol=sym, granularity=granularity, count=60)
            candles = data.get("candles") or []
            channel = data.get("channel") or {}
            spot = float(candles[-1]["close"]) if candles else 0.0
            upper = float(channel.get("upper") or 0.0)
            center = float(channel.get("center") or channel.get("mid") or 0.0)
            lower = float(channel.get("lower") or 0.0)
            atr = float(channel.get("atr14") or channel.get("atr") or 0.0)
            r2 = float(channel.get("r2") or 0.0)
            slope = float(channel.get("slope") or 0.0)

            dist_upper_pct = round(((upper - spot) / spot * 100), 2) if spot > 0 and upper > 0 else 999.0
            dist_lower_pct = round(((spot - lower) / spot * 100), 2) if spot > 0 and lower > 0 else 999.0

            # State evaluation
            status = "MONITORING"
            armed_direction = None
            if spot > 0 and upper > 0 and lower > 0:
                if spot >= upper:
                    status = "TOUCH_UPPER"
                    armed_direction = "MULTDOWN"
                elif spot <= lower:
                    status = "TOUCH_LOWER"
                    armed_direction = "MULTUP"
                elif dist_upper_pct <= 0.25:
                    status = "NEAR_UPPER"
                    armed_direction = "MULTDOWN"
                elif dist_lower_pct <= 0.25:
                    status = "NEAR_LOWER"
                    armed_direction = "MULTUP"

            return {
                "symbol": sym,
                "spot": spot,
                "upper": upper,
                "center": center,
                "lower": lower,
                "atr": atr,
                "r2": r2,
                "slope": slope,
                "dist_upper_pct": dist_upper_pct,
                "dist_lower_pct": dist_lower_pct,
                "status": status,
                "armed_direction": armed_direction,
            }
        except Exception as exc:
            return {
                "symbol": sym,
                "spot": 0.0,
                "status": "ERROR",
                "error": str(exc),
            }

    tasks = [scan_single(sym) for sym in sym_list[:12]]
    items = await asyncio.gather(*tasks, return_exceptions=False)
    # Sort items: touches or near first, then smallest distance to either band
    def sort_key(item: dict) -> float:
        if item.get("status") in ("TOUCH_UPPER", "TOUCH_LOWER"):
            return -100.0
        if item.get("status") in ("NEAR_UPPER", "NEAR_LOWER"):
            return -50.0
        return min(abs(float(item.get("dist_upper_pct", 999))), abs(float(item.get("dist_lower_pct", 999))))

    items_sorted = sorted(items, key=sort_key)
    return {"items": items_sorted, "count": len(items_sorted)}
