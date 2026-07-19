"""Regulatory endpoints (§18): read-only reads over the FINRA short-interest
and short-sale-volume Silver products. These serve existing Silver; ingestion
is date/file-oriented (a daily file covers all symbols) and runs on a
schedule/admin path rather than being triggered per-symbol here.
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from catalystiq.auth import verify_action_key
from catalystiq.db.base import get_db
from catalystiq.pipelines import regulatory_pipeline as rp
from catalystiq.schemas.regulatory import ShortInterest, ShortSaleVolume

router = APIRouter(tags=["regulatory"], dependencies=[Depends(verify_action_key)])


def _utc(value: dt.datetime | None) -> dt.datetime | None:
    return value.replace(tzinfo=dt.timezone.utc) if value is not None else None


@router.get("/short-interest/{symbol}", response_model=list[ShortInterest])
def get_short_interest(symbol: str, db: Session = Depends(get_db)):
    return [
        ShortInterest(
            symbol=r.symbol, settlement_date=r.settlement_date,
            publication_date=r.publication_date,
            short_interest_quantity=r.short_interest_quantity,
            previous_short_interest_quantity=r.previous_short_interest_quantity,
            average_daily_volume=r.average_daily_volume, days_to_cover=r.days_to_cover,
            file_version=r.file_version, source=r.provider, retrieved_at=_utc(r.retrieved_at),
        )
        for r in rp.get_short_interest(db, symbol)
    ]


@router.get("/short-sale-volume/{symbol}", response_model=list[ShortSaleVolume])
def get_short_sale_volume(symbol: str, db: Session = Depends(get_db)):
    return [
        ShortSaleVolume(
            symbol=r.symbol, trade_date=r.trade_date, short_volume=r.short_volume,
            short_exempt_volume=r.short_exempt_volume, total_volume=r.total_volume,
            reporting_facility=r.reporting_facility, file_version=r.file_version,
            source=r.provider, retrieved_at=_utc(r.retrieved_at),
        )
        for r in rp.get_short_sale_volume(db, symbol)
    ]
