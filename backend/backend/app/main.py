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

LAST = {}
task = None

class BinanceData:
    async def time_series(self, symbol: str, interval="1d", limit=100):
        try:
            if ":" in symbol:
                symbol = symbol.split(":")[-1]
            symbol = symbol.replace("-", "").replace("_", "").upper()
            if symbol in ["BTC", "ETH", "SOL", "XRP", "ADA"]:
                symbol = f"{symbol}USDT"
            if not symbol.endswith("USDT") and not symbol.endswith("BUSD"):
                symbol = f"{symbol}USDT"

            # 301 YÖNLENDİRME HATASINI AŞMAK İÇİN DOĞRUDAN API ADRESİNİ GÜNCELLEDİK
            url = "https://binance.com"
            params = {
                "symbol": symbol,
                "interval": interval,
                "limit": limit
            }
            
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                r = await client.get(url, params=params)
                if r.status_code != 200:
                    print(f"Binance HTTP Hatası: {r.status_code}")
                    return None
                data = r.json()

            formatted_values = []
            for item in data:
                formatted_values.append({
                    "datetime": pd.to_datetime(item[0], unit='ms').strftime('%Y-%m-%d %H:%M:%S'),
                    "open": float(item[1]),
                    "high": float(item[2]),
                    "low": float(item[3]),
                    "close": float(item[4]),
                    "volume": float(item[5])
                })
            
            df = pd.DataFrame(formatted_values)
            if df.empty:
                return None
            return df.sort_values("datetime").reset_index(drop=True)
        except Exception as e:
            print(f"Binance Veri Çekme Hatası ({symbol}): {e}")
            return None

def watchlist():
    return [x.strip() for x in os.getenv("WATCHLIST", "").split(",") if x.strip()]

class Subscription(BaseModel):
    endpoint: str
    keys: dict

async def scan_once():
    bn_data = BinanceData()
    symbols = watchlist()
    if not symbols:
        return
    for symbol in symbols:
        try:
            data = await bn_data.time_series(symbol)
            if data is None or data.empty:
                continue
            score_data = analyze(data)
            LAST[symbol] = score_data
            
            if score_data.get("action") in ["BUY", "SELL"]:
                msg = f"🎯 *AI SİNYAL MOTORU ALARM:* {symbol}\n📊 Skor: {score_data.get('score')}\n🟢 Aksiyon: {score_data.get('action')}"
                await send_telegram_message(msg)
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

async def send_telegram_message(message: str):
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not bot_token or not chat_id:
        return
    url = f"https://telegram.org{bot_token}/sendMessage"
    payload = {"chat_id": chat_id, "text": message, "parse_mode": "Markdown"}
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            await client.post(url, json=payload)
        except Exception as e:
            print(f"Telegram gönderme hatası: {e}")

@app.get("/signal/{symbol}")
async def get_signal(symbol: str):
    symbol = urllib.parse.unquote(symbol)
    if symbol in LAST:
        return LAST[symbol]
    bn_data = BinanceData()
    data = await bn_data.time_series(symbol)
    if data is None or data.empty:
        raise HTTPException(status_code=404, detail="Binance veri çekilemedi.")
    score_data = analyze(data)
    LAST[symbol] = score_data
    return score_data

@app.get("/chart/{symbol}")
async def get_chart(symbol: str):
    symbol = urllib.parse.unquote(symbol)
    bn_data = BinanceData()
    data = await bn_data.time_series(symbol)
    if data is None or data.empty:
        raise HTTPException(status_code=404, detail="Binance grafik verisi bulunamadı.")
    return data.to_dict(orient="records")

@app.post("/subscribe")
async def subscribe(sub: Subscription):
    save_subscription(sub.dict())
    return {"status": "ok"}
