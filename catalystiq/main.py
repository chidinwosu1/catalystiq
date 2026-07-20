import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from catalystiq.config import get_settings, validate_settings
from catalystiq.db.base import SessionLocal
from catalystiq.providers.broker import BrokerError
from catalystiq.routers import (
    analysis,
    auth,
    broker,
    calendar,
    data_quality,
    data_sources,
    fred,
    fundamentals,
    macro,
    market_data,
    ml,
    regulatory,
)
from catalystiq.scheduler import scheduler_loop
from catalystiq.validation.reference.scheduler import reference_validation_loop

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    # Fail fast on an enabled-but-misconfigured data source (§2). Raises
    # ConfigurationError listing offending setting names only, never values.
    validate_settings(settings)
    tasks = [
        asyncio.create_task(scheduler_loop(SessionLocal)),
        asyncio.create_task(
            reference_validation_loop(
                SessionLocal,
                settings.reference_validation_sample_rate,
                settings.reference_validation_interval_seconds,
            )
        ),
    ]
    # Keep the opportunity-scan universe warm in the background so the
    # user-facing scan reads fresh Silver instead of doing a cold multi-fetch
    # ingest inline (catalystiq/pipelines/universe_warmer.py).
    if settings.enable_universe_warmer:
        from catalystiq.pipelines.universe_warmer import universe_warm_loop
        from catalystiq.providers.market_data import get_market_data_provider

        tasks.append(
            asyncio.create_task(
                universe_warm_loop(
                    SessionLocal,
                    get_market_data_provider,
                    settings.universe_warm_interval_seconds,
                )
            )
        )
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()


app = FastAPI(
    title="Catalyst IQ API",
    version="0.2.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in get_settings().cors_allow_origins.split(",") if o.strip()],
    # Credentials must be allowed so the browser sends/receives the session
    # cookie cross-origin (dev: Vite :5173 -> API :8000). Requires explicit
    # origins above, never "*".
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(broker.router)
app.include_router(market_data.router)
app.include_router(analysis.router)
app.include_router(calendar.router)
app.include_router(macro.router)
app.include_router(fred.router)
app.include_router(fundamentals.router)
app.include_router(regulatory.router)
app.include_router(data_quality.router)
app.include_router(data_sources.router)
app.include_router(ml.router)


@app.exception_handler(BrokerError)
def handle_broker_error(request: Request, exc: BrokerError) -> JSONResponse:
    """Catches BrokerError raised while *constructing* a broker provider
    (get_broker_provider() runs as a FastAPI dependency, so that failure
    happens before a router's own try/except ever gets a chance to run).
    Without this handler it surfaces as an unhandled 500 that bypasses
    CORSMiddleware entirely - the browser reports it as a CORS failure,
    which hides the real "credentials not configured" error."""
    return JSONResponse(status_code=502, content={"detail": str(exc)})


def _error_cors_headers(request: Request) -> dict[str, str]:
    """CORS headers to attach to an error response the CORS middleware won't
    add itself. An otherwise-unhandled 500 is produced by Starlette's
    ServerErrorMiddleware, which sits OUTSIDE CORSMiddleware, so without these
    the browser can't read the response and misreports it as "Could not reach
    the API" - hiding the real server error. Only echoes an allowed Origin."""
    origin = request.headers.get("origin")
    allowed = [o.strip() for o in get_settings().cors_allow_origins.split(",") if o.strip()]
    if origin and origin in allowed:
        return {
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Credentials": "true",
            "Vary": "Origin",
        }
    return {}


@app.exception_handler(Exception)
def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    """Return a CORS-headed 500 for any otherwise-unhandled error, and log the
    full traceback. Without this, such errors bypass CORSMiddleware and the
    browser reports them as "Could not reach the API", masking the real cause
    (e.g. a response-validation error on /paper/account). The response names
    the exception type (diagnostic, no secrets); the traceback goes to the
    server logs."""
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": f"Internal server error ({type(exc).__name__}). See server logs."},
        headers=_error_cors_headers(request),
    )


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
