import csv
import io
import math
from collections import defaultdict
from datetime import timedelta
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.services.subscription import ensure_utc, iso_utc, serialize_basket
from shared.database import get_db
from shared.models import Basket, User, utcnow

router = APIRouter(prefix="/api/reports", tags=["Reports"])


def _query_closed_baskets(
    db: Session,
    user_id: int,
    account_type: Literal["DEMO", "REAL"],
    days: int,
    symbol: Optional[str],
):
    cutoff = utcnow() - timedelta(days=days)
    query = db.query(Basket).filter(
        Basket.user_id == user_id,
        Basket.account_type == account_type,
        Basket.status == "CLOSED",
        Basket.opened_at >= cutoff,
    )
    if symbol and symbol.strip():
        query = query.filter(Basket.symbol == symbol.strip())
    return query


@router.get("/summary")
def get_report_summary(
    account_type: Literal["DEMO", "REAL"] = Query(default="DEMO"),
    days: int = Query(default=30, ge=1, le=3650),
    symbol: Optional[str] = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    baskets = (
        _query_closed_baskets(db, current_user.id, account_type, days, symbol)
        .order_by(Basket.closed_at.asc(), Basket.id.asc())
        .all()
    )

    total_cycles = len(baskets)
    winning_cycles = 0
    losing_cycles = 0
    breakeven_cycles = 0

    total_pnl = 0.0
    today_pnl = 0.0
    gross_profit = 0.0
    gross_loss = 0.0
    total_commission = 0.0
    total_stake = 0.0

    today_str = utcnow().strftime("%Y-%m-%d")
    cumulative = 0.0
    peak_equity = 0.0
    max_drawdown = 0.0

    equity_curve = []
    daily_map: dict[str, dict] = defaultdict(lambda: {"pnl_usd": 0.0, "cycles": 0, "wins": 0})
    layers_map: dict[int, int] = defaultdict(int)
    symbol_map: dict[str, dict] = defaultdict(lambda: {"cycles": 0, "wins": 0, "total_pnl_usd": 0.0})

    for b in baskets:
        pnl = float(b.realized_pnl_usd or 0.0)
        total_pnl += pnl
        total_commission += float(b.total_commission_usd or 0.0)
        total_stake += float(b.total_stake_usd or 0.0)

        if pnl > 1e-9:
            winning_cycles += 1
            gross_profit += pnl
        elif pnl < -1e-9:
            losing_cycles += 1
            gross_loss += abs(pnl)
        else:
            breakeven_cycles += 1

        cumulative += pnl
        if cumulative > peak_equity:
            peak_equity = cumulative
        dd = peak_equity - cumulative
        if dd > max_drawdown:
            max_drawdown = dd

        ref_dt = ensure_utc(b.closed_at) or ensure_utc(b.opened_at) or utcnow()
        day_key = ref_dt.strftime("%Y-%m-%d")
        if day_key == today_str:
            today_pnl += pnl

        equity_curve.append(
            {
                "timestamp": ref_dt.isoformat(),
                "cycle_id": b.id,
                "symbol": b.symbol,
                "pnl_usd": round(pnl, 4),
                "cumulative_pnl_usd": round(cumulative, 4),
            }
        )

        daily_map[day_key]["pnl_usd"] += pnl
        daily_map[day_key]["cycles"] += 1
        if pnl > 1e-9:
            daily_map[day_key]["wins"] += 1

        layers_map[int(b.layers_count or 1)] += 1

        sym_entry = symbol_map[b.symbol]
        sym_entry["cycles"] += 1
        if pnl > 1e-9:
            sym_entry["wins"] += 1
        sym_entry["total_pnl_usd"] += pnl

    win_rate_pct = round((winning_cycles / total_cycles) * 100.0, 2) if total_cycles > 0 else 0.0
    avg_pnl = round(total_pnl / total_cycles, 4) if total_cycles > 0 else 0.0
    if gross_loss > 0:
        profit_factor: Optional[float] = round(gross_profit / gross_loss, 3)
    elif gross_profit > 0:
        profit_factor = 999.0
    else:
        profit_factor = 0.0

    daily_pnl = [
        {
            "date": day,
            "pnl_usd": round(vals["pnl_usd"], 4),
            "cycles": vals["cycles"],
            "wins": vals["wins"],
        }
        for day, vals in sorted(daily_map.items())
    ]

    layers_distribution = [
        {"layers": layer_num, "count": count}
        for layer_num, count in sorted(layers_map.items())
    ]

    by_symbol = [
        {
            "symbol": sym,
            "cycles": vals["cycles"],
            "wins": vals["wins"],
            "win_rate_pct": round((vals["wins"] / vals["cycles"]) * 100.0, 2) if vals["cycles"] else 0.0,
            "total_pnl_usd": round(vals["total_pnl_usd"], 4),
        }
        for sym, vals in sorted(symbol_map.items(), key=lambda x: x[1]["total_pnl_usd"], reverse=True)
    ]

    return {
        "account_type": account_type,
        "days": days,
        "symbol": symbol,
        "total_cycles": total_cycles,
        "winning_cycles": winning_cycles,
        "losing_cycles": losing_cycles,
        "breakeven_cycles": breakeven_cycles,
        "win_rate_pct": win_rate_pct,
        "total_pnl_usd": round(total_pnl, 4),
        "today_pnl_usd": round(today_pnl, 4),
        "gross_profit_usd": round(gross_profit, 4),
        "gross_loss_usd": round(gross_loss, 4),
        "profit_factor": profit_factor,
        "avg_pnl_usd": avg_pnl,
        "total_commission_usd": round(total_commission, 4),
        "total_stake_usd": round(total_stake, 4),
        "max_drawdown_usd": round(max_drawdown, 4),
        "equity_curve": equity_curve,
        "daily_pnl": daily_pnl,
        "layers_distribution": layers_distribution,
        "by_symbol": by_symbol,
    }


@router.get("/cycles")
def get_report_cycles(
    account_type: Literal["DEMO", "REAL"] = Query(default="DEMO"),
    days: int = Query(default=30, ge=1, le=3650),
    symbol: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = _query_closed_baskets(db, current_user.id, account_type, days, symbol)
    total = query.count()
    total_pages = max(1, math.ceil(total / page_size)) if total > 0 else 1

    items = (
        query.order_by(Basket.closed_at.desc(), Basket.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    return {
        "account_type": account_type,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
        "items": [serialize_basket(b, include_contracts=True) for b in items],
    }


@router.get("/export.csv")
def export_report_csv(
    account_type: Literal["DEMO", "REAL"] = Query(default="DEMO"),
    days: int = Query(default=30, ge=1, le=3650),
    symbol: Optional[str] = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    baskets = (
        _query_closed_baskets(db, current_user.id, account_type, days, symbol)
        .order_by(Basket.closed_at.desc(), Basket.id.desc())
        .all()
    )

    def _csv_generator():
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(
            [
                "id",
                "account_type",
                "symbol",
                "direction",
                "layers_count",
                "total_stake_usd",
                "total_commission_usd",
                "avg_entry_price",
                "realized_pnl_usd",
                "close_reason",
                "opened_at",
                "closed_at",
            ]
        )
        yield buf.getvalue()
        buf.seek(0)
        buf.truncate(0)

        for b in baskets:
            writer.writerow(
                [
                    b.id,
                    b.account_type,
                    b.symbol,
                    b.direction,
                    b.layers_count,
                    f"{float(b.total_stake_usd or 0.0):.4f}",
                    f"{float(b.total_commission_usd or 0.0):.4f}",
                    f"{float(b.avg_entry_price or 0.0):.6f}",
                    f"{float(b.realized_pnl_usd or 0.0):.4f}",
                    b.close_reason or "",
                    iso_utc(b.opened_at) or "",
                    iso_utc(b.closed_at) or "",
                ]
            )
            yield buf.getvalue()
            buf.seek(0)
            buf.truncate(0)

    filename = f"relatorio_{account_type.lower()}_{days}d.csv"
    return StreamingResponse(
        _csv_generator(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
