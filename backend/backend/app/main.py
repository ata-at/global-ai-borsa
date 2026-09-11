import os, asyncio
import urllib.parse
import httpx
import pandas as pd
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from .scoring import analyze
from .push import save_subscription, send_push

# DÖNGÜSEL KİLİTLENMEYİ ÖNLEMEK İÇİN TWELVEDATA SINIFINI DOĞRUDAN BURAYA ALDIK
BASE = "https://twelvedata.com"

class TwelveData:
    def __init__(self):
        self.key = os.getenv("TWELVE_DATA_API_KEY")
        if not self.key:
            raise RuntimeError("TWELVE_DATA_API_KEY bulunamadı!")
            
    async def time_series(self, symbol, interval="1d", outputsize=30):
        params = {
            "symbol": symbol,
            "interval": interval,
            "outputsize": outputsize,
            "apikey": self.key,
            "format": "JSON",
        }
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(f"{BASE}/time_series", params=params)
            r.raise_for_status()
            data = r.json()
        if data.get("status") == "error":
            raise RuntimeError(data.get("message", "Twelve Data API Hatası"))
        df = pd.DataFrame(data.get("values", []))
        if df.empty:
            return df
        df["datetime"] = pd.to_dict = pd.to_datetime(df["datetime"])
        for c in ["open", "high", "low", "close", "volume"]:
            if c in df:
                df[c] = pd.to_numeric(df[c])
        return df.sort_values("datetime").reset_index(drop=True)

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
            data = await td.time_series(symbol)
            if data is None or data.empty:
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
    data = await td.time_series(symbol)
    if data is None or data.empty:
        raise HTTPException(status_code=404, detail="Symbol not found")
    score_data = analyze(data)
    LAST[symbol] = score_data
    return score_data

@app.get("/chart/{symbol}")
async def get_chart(symbol: str):
    symbol = urllib.parse.unquote(symbol)
    td = TwelveData()
    data = await td.time_series(symbol)
    if data is None or data.empty:
        raise HTTPException(status_code=404, detail="Symbol not found")
    return data.to_dict(orient="records")

@app.post("/subscribe")
async def subscribe(sub: Subscription):
    save_subscription(sub.dict())
    return {"status": "ok"}
