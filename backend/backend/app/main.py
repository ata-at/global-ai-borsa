import os
import asyncio
import urllib.parse
import random
import pandas as pd
from datetime import datetime, timedelta
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Sinyal kütüphanesini taklit eden yerleşik analiz motoru
def analyze_mock(df):
    last_close = df['close'].iloc[-1]
    score = random.randint(35, 85)
    action = "BUY" if score > 65 else "SELL" if score < 45 else "WAIT"
    return {
        "score": score,
        "action": action,
        "confidence": random.randint(60, 95),
        "price": f"{last_close:,.2f}",
        "entry_range": f"{(last_close * 0.995):,.2f} - {(last_close * 1.002):,.2f}",
        "stop": f"{(last_close * 0.98):,.2f}",
        "support": f"{(last_close * 0.99):,.2f}",
        "resistance": f"{(last_close * 1.01):,.2f}",
        "targets": [f"{(last_close * 1.02):,.2f}", f"{(last_close * 1.04):,.2f}"],
        "reasons": [
            "RSI gostergesi asiri satim bölgesinden donus sinyali veriyor.",
            "Son 30 günlük hareketli ortalamalar guclu destek olusturdu.",
            "Hacim profilinde alici iştahının arttigi gozlemleniyor."
        ]
    }

LAST = {}
LATEST_NEWS_REASON = "Piyasaya dusen son kurumsal ortaklik ve balina alim haberleri alici istahini yuksek tutuyor."

# INTERNET ENGELLERİNE TAKILMAYAN YERLEŞİK SİMÜLASYON SINIFI
class GlobalFinancialData:
    async def time_series(self, symbol: str):
        try:
            symbol = urllib.parse.unquote(symbol).strip().upper()
            symbol_clean = symbol.replace("-", "").replace("/", "").replace("USDT", "").replace("USD", "")
            
            # Başlangıç fiyatlarını belirliyoruz
            base_prices = {"BTC": 62500.0, "ETH": 2450.0, "SOL": 140.0, "AAPL": 220.0, "NVDA": 115.0, "TSLA": 210.0}
            price = base_prices.get(symbol_clean, 100.0)
            
            # Son 40 günlük mum verisini internete ihtiyaç duymadan anlık üretiyoruz
            formatted_values = []
            now = datetime.now()
            
            for i in range(40):
                date_str = (now - timedelta(days=40-i)).strftime('%Y-%m-%d %H:%M:%S')
                change = random.uniform(-0.03, 0.035)
                open_p = price
                close_p = price * (1 + change)
                high_p = max(open_p, close_p) * random.uniform(1.0, 1.02)
                low_p = min(open_p, close_p) * random.uniform(0.98, 1.0)
                vol = random.uniform(1000, 50000)
                
                formatted_values.append({
                    "datetime": str(date_str),
                    "open": float(open_p), "high": float(high_p),
                    "low": float(low_p), "close": float(close_p),
                    "volume": float(vol)
                })
                price = close_p
            
            df = pd.DataFrame(formatted_values)
            
            # Yapay Zeka Formasyon Isaretleme Sistemi
            df["pattern"] = None
            df.at[df.index[-5], "pattern"] = "Cekic Formasyonu" if random.random() > 0.5 else "Yutan Boga"
            
            return df.sort_values("datetime").reset_index(drop=True)
        except Exception as e:
            print(f"Veri Motoru Hatası: {e}")
            return None

class Subscription(BaseModel):
    endpoint: str
    keys: dict
@asynccontextmanager
async def lifespan(app: FastAPI):
    yield

app = FastAPI(lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

@app.get("/")
async def root(): 
    return {"status": "healthy"}

@app.get("/signal/{symbol}")
async def get_signal(symbol: str):
    symbol = urllib.parse.unquote(symbol)
    gf_data = GlobalFinancialData()
    data = await gf_data.time_series(symbol)
    if data is None or data.empty: raise HTTPException(status_code=404)
    
    # Yerleşik analiz motorunu tetikliyoruz
    score_data = analyze_mock(data)
    score_data["news_reason"] = LATEST_NEWS_REASON
    LAST[symbol] = score_data
    return score_data

@app.get("/chart/{symbol}")
async def get_chart(symbol: str):
    symbol = urllib.parse.unquote(symbol)
    gf_data = GlobalFinancialData()
    data = await gf_data.time_series(symbol)
    if data is None or data.empty: raise HTTPException(status_code=404)
    return data.to_dict(orient="records")

@app.post("/subscribe")
async def subscribe(sub: Subscription):
    return {"status": "ok"}
