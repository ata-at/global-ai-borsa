import os, asyncio
import urllib.parse
import yfinance as yf
import pandas as pd
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from .scoring import analyze
from .push import save_subscription, send_push

LAST = {}
task = None

# YAHOO FINANCE VERİ ÇEKME SINIFI (API ANAHTARI GEREKTİRMEZ)
class YahooFinanceData:
    async def time_series(self, symbol: str, period="1mo", interval="1d"):
        try:
            # Eğer arayüzden 'NASDAQ:AAPL' gelirse 'AAPL' yap, 'BINANCE:BTCUSD' gelirse 'BTC-USD' yap
            if ":" in symbol:
                symbol = symbol.split(":")[-1]
            if symbol.endswith("USD") and len(symbol) > 4:
                symbol = symbol.replace("USD", "-USD")
                
            # Yahoo Finance çağrısını asenkron döngüyü kilitlememesi için thread içinde çalıştırıyoruz
            loop = asyncio.get_event_loop()
            ticker = yf.Ticker(symbol)
            df = await loop.run_in_executor(None, lambda: ticker.history(period=period, interval=interval))
            
            if df.empty:
                return None
                
            # Veri yapısını ChatGPT'nin hazırladığı 'analyze' fonksiyonuna uyumlu hale getiriyoruz
            df = df.reset_index()
            df = df.rename(columns={
                "Date": "datetime",
                "Open": "open",
                "High": "high",
                "Low": "low",
                "Close": "close",
                "Volume": "volume"
            })
            df["datetime"] = pd.to_datetime(df["datetime"]).dt.strftime('%Y-%m-%d %H:%M:%S')
            return df.sort_values("datetime").reset_index(drop=True)
        except Exception as e:
            print(f"Yahoo Finance Veri Çekme Hatası ({symbol}): {e}")
            return None

def watchlist():
    return [x.strip() for x in os.getenv("WATCHLIST", "").split(",") if x.strip()]

class Subscription(BaseModel):
    endpoint: str
    keys: dict

async def scan_once():
    yf_data = YahooFinanceData()
    symbols = watchlist()
    if not symbols:
        return
    for symbol in symbols:
        try:
            data = await yf_data.time_series(symbol)
            if data is None or data.empty:
                continue
            score_data = analyze(data)
            LAST[symbol] = score_data
            if score_data.get("action") in ["BUY", "SELL"]:
                await send_push(f"{symbol}: {score_data['action']} Sinyali! AI Skor: {score_data['score']}")
        except Exception as e:
            print(f"Tarama Hatası ({symbol}): {e}")

async def scanner_loop():
    while True:
        try:
            await scan_once()
        except Exception as e:
            print(f"Tarayıcı Döngü Hatası: {e}")
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
    yf_data = YahooFinanceData()
    data = await yf_data.time_series(symbol)
    if data is None or data.empty:
        raise HTTPException(status_code=404, detail="Finansal veri çekilemedi.")
    score_data = analyze(data)
    LAST[symbol] = score_data
    return score_data

@app.get("/chart/{symbol}")
async def get_chart(symbol: str):
    symbol = urllib.parse.unquote(symbol)
    yf_data = YahooFinanceData()
    data = await yf_data.time_series(symbol)
    if data is None or data.empty:
        raise HTTPException(status_code=404, detail="Grafik verisi bulunamadı.")
    return data.to_dict(orient="records")

@app.post("/subscribe")
async def subscribe(sub: Subscription):
    save_subscription(sub.dict())
    return {"status": "ok"}
