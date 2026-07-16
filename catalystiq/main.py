from fastapi import FastAPI

from catalystiq.routers import broker, market_data

app = FastAPI(
    title="Catalyst IQ API",
    version="0.2.0",
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
