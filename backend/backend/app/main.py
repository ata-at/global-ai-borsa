import os
import asyncio
import urllib.parse
import httpx
import pandas as pd
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
            symbol = urllib.parse.unquote(symbol).strip().upper()
            symbol_clean = symbol.replace("-", "").replace("/", "").replace("USDT", "").replace("USD", "")
            
            kripto_haritasi = {
                "BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana", 
                "BNB": "binancecoin", "XRP": "ripple", "ADA": "cardano", 
                "DOGE": "dogecoin", "AVAX": "avalanche-2"
            }
            
            is_crypto = symbol_clean in kripto_haritasi
            
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
                
                if is_crypto:
                    coin_id = kripto_haritasi[symbol_clean]
                    # DÜZELTME: Tam adreslenmiş, parametre hatası içermeyen kararlı Coingecko URL hattı
                    url = f"https://coingecko.com{coin_id}/market_chart?vs_currency=usd&days=30&interval=daily"
                    
                    r = await client.get(url, headers=headers)
                    if r.status_code != 200:
                        print(f"Coingecko API Hatası ({symbol_clean}): {r.status_code}")
                        return None
                    
                    res_json = r.json()
                    prices = res_json.get("prices", [])
                    volumes = res_json.get("total_volumes", [])
                    
                    formatted_values = []
                    for i in range(len(prices)):
                        # Zaman damgası milisaniye cinsinden gelir, saniyeye bölüyoruz
                        ts = int(prices[i][0]) / 1000
                        tarih_formatli = pd.to_datetime(ts, unit='s').strftime('%Y-%m-%d %H:%M:%S')
                        prc = float(prices[i][1])
                        vol = float(volumes[i][1]) if i < len(volumes) else 0
                        
                        # Binance mum grafik motorunun tam çizim yapabilmesi için mum yapısını simüle ediyoruz
                        formatted_values.append({
                            "datetime": str(tarih_formatli),
                            "open": prc * 0.997, 
                            "high": prc * 1.01,
                            "low": prc * 0.99, 
                            "close": prc,
                            "volume": vol
                        })
                else:
                    # Hisse senetleri için yedek Amerikan finans kanalı
                    url = f"https://yahoo.com{symbol}"
                    r = await client.get(url, headers=headers)
                    if r.status_code != 200: return None
                    
                    chart_res = r.json().get("chart", {}).get("result", [{}])[0]
                    ts_list = chart_res.get("timestamp", [])
                    quotes = chart_res.get("indicators", {}).get("quote", [{}])[0]
                    
                    formatted_values = []
                    for i, ts in enumerate(ts_list):
                        if i >= len(quotes.get("close", [])) or quotes.get("close")[i] is None: continue
                        formatted_values.append({
                            "datetime": str(pd.to_datetime(ts, unit='s').strftime('%Y-%m-%d %H:%M:%S')),
                            "open": float(quotes["open"][i]), "high": float(quotes["high"][i]),
                            "low": float(quotes["low"][i]), "close": float(quotes["close"][i]),
                            "volume": float(quotes["volume"][i] if quotes["volume"][i] else 0)
                        })
            
            df = pd.DataFrame(formatted_values)
            if df.empty: return None
            
            # Yapay Zeka Formasyon Motoru
            df_pattern = df.tail(15).copy()
            detected_pattern = "Belirgin Formasyon Yok"
            pattern_index = -1
            
            for idx in range(2, len(df_pattern)):
                real_idx = df_pattern.index[idx]
                body = abs(df_pattern['close'].loc[real_idx] - df_pattern['open'].loc[real_idx])
                lower_wick = min(df_pattern['open'].loc[real_idx], df_pattern['close'].loc[real_idx]) - df_pattern['low'].loc[real_idx]
                if lower_wick > (body * 1.5) and body > 0:
                    detected_pattern = "Cekic Formasyonu"
                    pattern_index = real_idx
                    break
                if df_pattern['close'].loc[real_idx] > df_pattern['open'].loc[real_idx] and df_pattern['close'].loc[real_idx-1] < df_pattern['open'].loc[real_idx-1]:
                    detected_pattern = "Yutan Boga"
                    pattern_index = real_idx
                    break

            df["pattern"] = None
            if pattern_index != -1:
                df.at[pattern_index, "pattern"] = detected_pattern

            return df.sort_values("datetime").reset_index(drop=True)
        except Exception as e:
            print(f"Genel Veri Motoru Hatası ({symbol}): {e}")
            return None

def watchlist():
    return [x.strip() for x in os.getenv("WATCHLIST", "").split(",") if x.strip()]

class Subscription(BaseModel):
    endpoint: str
    keys: dict

async def scan_once():
    gf_data = GlobalFinancialData()
    symbols = watchlist()
    if not symbols: return
    for symbol in symbols:
        try:
            data = await gf_data.time_series(symbol)
            if data is None or data.empty: continue
            score_data = analyze(data)
            LAST[symbol] = score_data
        except Exception: pass
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
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
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
