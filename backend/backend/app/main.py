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

# HEM HİSSE HEM KRİPTO VERİLERİNİ ASLA ENGELLENMEYECEK ŞEKİLDE ÇEKEN SINIF
class GlobalFinancialData:
    async def time_series(self, symbol: str):
        try:
            # Gelen sembolü temizle (Örn: 'NASDAQ:AAPL' gelirse 'AAPL' yap)
            if ":" in symbol:
                symbol = symbol.split(":")[-1]
            symbol = symbol.strip().upper()
            
            # Kripto paralar için formatı Yahoo standartına eşitle (Örn: BTCUSD gelirse BTC-USD yap)
            if (symbol.endswith("USD") or symbol.endswith("USDT")) and len(symbol) > 4:
                base_coin = symbol.replace("USDT", "").replace("USD", "")
                symbol = f"{base_coin}-USD"

            # Bulut sunucu engellerini aşmak için doğrudan Yahoo Finance ham API kanalını kullanıyoruz
            url = f"https://yahoo.com{symbol}"
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }
            params = {
                "range": "1y", # Sinyal hesaplama hatasını çözmek için 1 yıllık veri çekiyoruz
                "interval": "1d"
            }
            
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.get(url, headers=headers, params=params)
                if r.status_code != 200:
                    print(f"Finans API HTTP Hatası ({symbol}): {r.status_code}")
                    return None
                res_data = r.json()

            chart_result = res_data.get("chart", {}).get("result")
            if not chart_result:
                return None
                
            result = chart_result[0]
            timestamps = result.get("timestamp", [])
            indicators = result.get("indicators", {}).get("quote", [{}])[0]
            
            # Verileri dataframe yapısına dönüştürüyoruz
            formatted_values = []
            for i, ts in enumerate(timestamps):
                if i >= len(indicators.get("close", [])) or indicators.get("close")[i] is None:
                    continue
                formatted_values.append({
                    "datetime": pd.to_datetime(ts, unit='s').strftime('%Y-%m-%d %H:%M:%S'),
                    "open": float(indicators.get("open")[i]),
                    "high": float(indicators.get("high")[i]),
                    "low": float(indicators.get("low")[i]),
                    "close": float(indicators.get("close")[i]),
                    "volume": float(indicators.get("volume")[i] if indicators.get("volume")[i] else 0)
                })
            
            df = pd.DataFrame(formatted_values)
            if df.empty:
                return None
            return df.sort_values("datetime").reset_index(drop=True)
        except Exception as e:
            print(f"Küresel Veri Çekme Hatası ({symbol}): {e}")
            return None

def watchlist():
    return [x.strip() for x in os.getenv("WATCHLIST", "").split(",") if x.strip()]

class Subscription(BaseModel):
    endpoint: str
    keys: dict

async def scan_once():
    gf_data = GlobalFinancialData()
    symbols = watchlist()
    if not symbols:
        return
    for symbol in symbols:
        try:
            data = await gf_data.time_series(symbol)
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
    gf_data = GlobalFinancialData()
    data = await gf_data.time_series(symbol)
    if data is None or data.empty:
        raise HTTPException(status_code=404, detail="Finansal veri çekilemedi.")
    score_data = analyze(data)
    LAST[symbol] = score_data
    return score_data

@app.get("/chart/{symbol}")
async def get_chart(symbol: str):
    symbol = urllib.parse.unquote(symbol)
    gf_data = GlobalFinancialData()
    data = await gf_data.time_series(symbol)
    if data is None or data.empty:
        raise HTTPException(status_code=404, detail="Grafik verisi bulunamadı.")
    return data.to_dict(orient="records")

@app.post("/subscribe")
async def subscribe(sub: Subscription):
    save_subscription(sub.dict())
    return {"status": "ok"}
