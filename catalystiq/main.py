import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from catalystiq.config import get_settings
from catalystiq.db.base import SessionLocal
from catalystiq.providers.broker import BrokerError, get_broker_provider
from catalystiq.routers import broker, market_data
from catalystiq.scheduler import scheduler_loop


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(scheduler_loop(SessionLocal, get_broker_provider))
    try:
        yield
    finally:
        task.cancel()


app = FastAPI(
    title="Catalyst IQ API",
    version="0.2.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in get_settings().cors_allow_origins.split(",") if o.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(broker.router)
app.include_router(market_data.router)


@app.exception_handler(BrokerError)
def handle_broker_error(request: Request, exc: BrokerError) -> JSONResponse:
    """Catches BrokerError raised while *constructing* a broker provider
    (get_broker_provider() runs as a FastAPI dependency, so that failure
    happens before a router's own try/except ever gets a chance to run).
    Without this handler it surfaces as an unhandled 500 that bypasses
    CORSMiddleware entirely - the browser reports it as a CORS failure,
    which hides the real "credentials not configured" error."""
    return JSONResponse(status_code=502, content={"detail": str(exc)})


@app.get("/")
def root():
    return {
        "status": "online",
        "service": "Catalyst IQ API",
        "paper_trading": True,
    }


@app.get("/health")
def health():
    return {"status": "healthy"}
