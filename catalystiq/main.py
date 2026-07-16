from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from catalystiq.config import get_settings
from catalystiq.routers import broker, market_data

app = FastAPI(
    title="Catalyst IQ API",
    version="0.2.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in get_settings().cors_allow_origins.split(",") if o.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(broker.router)
app.include_router(market_data.router)


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
