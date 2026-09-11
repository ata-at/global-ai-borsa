import os, asyncio
import urllib.parse
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from .market import TwelveData
from .scoring import analyze
from .push import save_subscription, send_push

LAST = {}
task = None

def watchlist():
    return [x.strip() for x in os.getenv("WATCHLIST", "").split(",") if x.strip()]

class Subscription(BaseModel):
    endpoint: str
    keys: dict

async def scan_once():
    td = TwelveData()
    symbols = watchlist()
    if not symbols:
        return
    for symbol in symbols:
        try:
            data = td.get_prices(symbol)
            if not data:
                continue
            score_data = analyze(data)
            LAST[symbol] = score_data
            if score_data.get("action") in ["BUY", "SELL"]:
                await send_push(f"{symbol}: {score_data['action']} Signal! AI Score: {score_data['score']}")
        except Exception as e:
            print(f"Error scanning {symbol}: {e}")

async def scanner_loop():
    while True:
        try:
            await scan_once()
        except Exception as e:
            print(f"Scanner error: {e}")
        await asyncio.sleep(60)

@asynccontextmanager
async def lifespan(app: FastAPI):
    global task
    task = asyncio.create_task(scanner_loop())
    yield
    if task:
        task.cancel()

app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/signal/{symbol}")
async def get_signal(symbol: str):
    symbol = urllib.parse.unquote(symbol)
    if symbol in LAST:
        return LAST[symbol]
    td = TwelveData()
    data = td.get_prices(symbol)
    if not data:
        raise HTTPException(status_code=404, detail="Symbol not found")
    score_data = analyze(data)
    LAST[symbol] = score_data
    return score_data

@app.get("/chart/{symbol}")
async def get_chart(symbol: str):
    symbol = urllib.parse.unquote(symbol)
    td = TwelveData()
    data = td.get_prices(symbol)
    if not data:
        raise HTTPException(status_code=404, detail="Symbol not found")
    return data

@app.post("/subscribe")
async def subscribe(sub: Subscription):
    save_subscription(sub.dict())
    return {"status": "ok"}
