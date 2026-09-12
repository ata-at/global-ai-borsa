import os
import asyncio
import urllib.parse
import httpx
import pandas as pd
import yfinance as yf
import xml.etree.ElementTree as ET
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from .scoring import analyze
from .push import save_subscription, send_push

LAST = {}
PROCESSED_NEWS = set()
LATEST_NEWS_REASON = "Piyasada şu an için nötr bir haber akışı hakim."
task = None
news_task = None

class GlobalFinancialData:
    async def time_series(self, symbol: str):
        try:
            symbol = urllib.parse.unquote(symbol)
            if ":" in symbol:
                symbol = symbol.split(":")[-1]
            symbol = symbol.strip().upper()
            
            if "-" in symbol:
                pass
            elif (symbol.endswith("USD") or symbol.endswith("USDT")) and len(symbol) > 4:
                base_coin = symbol.replace("USDT", "").replace("USD", "")
                symbol = f"{base_coin}-USD"

            loop = asyncio.get_event_loop()
            ticker = yf.Ticker(symbol)
            hist = await loop.run_in_executor(None, lambda: ticker.history(period="1y", interval="1d"))
            
            if hist.empty:
                return None
                
            df_pattern = hist.tail(30).copy()
            detected_pattern = "Belirgin Formasyon Yok"
            pattern_index = -1
            
            for idx in range(2, len(df_pattern)):
                body = abs(df_pattern['Close'].iloc[idx] - df_pattern['Open'].iloc[idx])
                lower_wick = min(df_pattern['Open'].iloc[idx], df_pattern['Close'].iloc[idx]) - df_pattern['Low'].iloc[idx]
                if lower_wick > (body * 2) and body > 0:
                    detected_pattern = "Cekic Formasyonu"
                    pattern_index = int(idx + (len(hist) - 30))
                    break
                if df_pattern['Close'].iloc[idx] > df_pattern['Open'].iloc[idx] and df_pattern['Close'].iloc[idx-1] < df_pattern['Open'].iloc[idx-1]:
                    if df_pattern['Close'].iloc[idx] > df_pattern['Open'].iloc[idx-1] and df_pattern['Open'].iloc[idx] < df_pattern['Close'].iloc[idx-1]:
                        detected_pattern = "Yutan Boga"
                        pattern_index = int(idx + (len(hist) - 30))
                        break

            formatted_values = []
            for i, (index, row) in enumerate(hist.iterrows()):
                tarih_formatli = index.strftime('%Y-%m-%d %H:%M:%S')
                has_pattern = detected_pattern if i == pattern_index else None
                
                formatted_values.append({
                    "datetime": str(tarih_formatli),
                    "open": float(row["Open"]),
                    "high": float(row["High"]),
                    "low": float(row["Low"]),
                    "close": float(row["Close"]),
                    "volume": float(row["Volume"] if row["Volume"] else 0),
                    "pattern": has_pattern
                })
            
            df = pd.DataFrame(formatted_values)
            return df.sort_values("datetime").reset_index(drop=True)
        except Exception as e:
            print(f"Veri Motoru Hatası: {e}")
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
        except Exception:
            pass
async def ask_ai_for_news_impact(title: str, content: str) -> dict:
    global LATEST_NEWS_REASON
    lower_title = title.lower()
    if any(w in lower_title for w in ["yasak", "dustu", "ceza", "faiz", "dusus", "fud", "dava"]):
        LATEST_NEWS_REASON = f"Piyasaya dusen son kısıtlama ve dava haberleri ({title[:40]}...) sebebiyle satis baskisi hakim."
        return {"direction": "ASAGI", "summary": LATEST_NEWS_REASON}
    if any(w in lower_title for w in ["ortaklik", "satin aldi", "yukseldi", "destek", "boga", "listeleme"]):
        LATEST_NEWS_REASON = f"Gelen kurumsal alım ortaklık haberleri ({title[:40]}...) lider tweetleri ile alici istahini tetikliyor."
        return {"direction": "YUKARI", "summary": LATEST_NEWS_REASON}
    return {"direction": "NOTR", "summary": "Piyasada stabil haber akisi mevcut."}

async def fetch_news_feeds():
    urls = ["https://cointelegraph.com", "https://uzmancoin.com"]
    async with httpx.AsyncClient(timeout=10.0) as client:
        for url in urls:
            try:
                response = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
                if response.status_code != 200: continue
                root = ET.fromstring(response.text)
                for item in root.findall(".//item")[:2]:
                    title = item.find("title").text if item.find("title") is not None else ""
                    desc = item.find("description").text if item.find("description") is not None else ""
                    if title and title not in PROCESSED_NEWS:
                        PROCESSED_NEWS.add(title)
                        await ask_ai_for_news_impact(title, desc)
            except Exception: pass

async def news_and_social_ai_loop():
    while True:
        try: await fetch_news_feeds()
        except Exception: pass
        await asyncio.sleep(120)

async def scanner_loop():
    while True:
        try: await scan_once()
        except Exception: pass
        await asyncio.sleep(60)

@asynccontextmanager
async def lifespan(app: FastAPI):
    global task, news_task
    task = asyncio.create_task(scanner_loop())
    news_task = asyncio.create_task(news_and_social_ai_loop())
    yield
    if task: task.cancel()
    if news_task: news_task.cancel()

app = FastAPI(lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

@app.get("/")
async def root(): return {"status": "healthy"}

@app.get("/signal/{symbol}")
async def get_signal(symbol: str):
    symbol = urllib.parse.unquote(symbol)
    gf_data = GlobalFinancialData()
    data = await gf_data.time_series(symbol)
    if data is None or data.empty: raise HTTPException(status_code=404)
    score_data = analyze(data)
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
    save_subscription(sub.dict())
    return {"status": "ok"}
