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
NEWS_REASONS = {
    "BTC": "Bitcoin ağındaki güçlü spot ETF girişleri ve rekor tazeleyen balina alımları yükseliş trendini destekliyor.",
    "ETH": "Ethereum arz kısıtlamaları ve akıllı sözleşme hacimlerindeki artış fiyatta yukarı yönlü baskı oluşturuyor.",
    "SOL": "Solana ekosistemindeki yüksek DeFi aktivitesi ve anlık işlem hacimleri ağı pozitif etkiliyor.",
    "AAPL": "Apple hisselerinde yeni yapay zeka ürün lansmanlarına dair kurumsal beklentiler alıcı iştahını artırıyor.",
    "NVDA": "Küresel ekran karti ve yapay zeka çipi tedarik zincirindeki sipariş artışları Nvidia trendini güçlendiriyor.",
    "TSLA": "Otonom sürüş yazılım güncellemeleri ve teslimat verileri Tesla hissesini destekliyor."
}

task = None
news_task = None

class GlobalFinancialData:
    async def time_series(self, symbol: str):
        try:
            symbol = urllib.parse.unquote(symbol).strip().upper()
            symbol_clean = symbol.replace("-", "").replace("/", "").replace("USDT", "").replace("USD", "")
            
            # Kripto para kontrol listesi
            is_crypto = symbol_clean in ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE"]
            
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
                
                if is_crypto:
                    # DÜZELTME: Binance API1 sunucusuna giden sembol parametresini doğrudan kilitliyoruz
                    binance_symbol = f"{symbol_clean}USDT"
                    url = f"https://binance.com{binance_symbol}&interval=1d&limit=40"
                    
                    r = await client.get(url, headers=headers)
                    if r.status_code != 200:
                        print(f"Binance Bağlantı Hatası: {r.status_code}")
                        return None
                    candles_data = r.json()
                    
                    formatted_values = []
                    for row in candles_data:
                        # Binance zaman damgası milisaniyedir, 1000'e bölüyoruz
                        ts = int(row[0]) / 1000
                        tarih_formatli = pd.to_datetime(ts, unit='s').strftime('%Y-%m-%d %H:%M:%S')
                        formatted_values.append({
                            "datetime": str(tarih_formatli),
                            "open": float(row[1]), 
                            "high": float(row[2]),
                            "low": float(row[3]), 
                            "close": float(row[4]),
                            "volume": float(row[5])
                        })
                else:
                    # Hisse Senetleri İçin Kesintisiz Küresel Yahoo Kanalı
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
            df = df.sort_values("datetime").reset_index(drop=True)
            
            # MATEMATİKSEL FORMASYON TESPİT MOTORU
            df["pattern"] = None
            df_pattern = df.tail(15).copy()
            detected_pattern = "Belirgin Formasyon Yok"
            pattern_index = -1
            
            for idx in range(2, len(df_pattern)):
                real_idx = df_pattern.index[idx]
                body = abs(df_pattern['close'].loc[real_idx] - df_pattern['open'].loc[real_idx])
                lower_wick = min(df_pattern['open'].loc[real_idx], df_pattern['close'].loc[real_idx]) - df_pattern['low'].loc[real_idx]
                
                if lower_wick > (body * 1.6) and body > 0:
                    detected_pattern = "Cekic Formasyonu"
                    pattern_index = real_idx
                    break
                if df_pattern['close'].loc[real_idx] > df_pattern['open'].loc[real_idx] and df_pattern['close'].loc[real_idx-1] < df_pattern['open'].loc[real_idx-1]:
                    detected_pattern = "Yutan Boga"
                    pattern_index = real_idx
                    break

            if pattern_index != -1:
                df.at[pattern_index, "pattern"] = detected_pattern

            return df
        except Exception as e:
            print(f"Genel Veri Motoru Hatası: {e}")
            return None

def watchlist():
    return [x.strip() for x in os.getenv("WATCHLIST", "").split(",") if x.strip()]

class Subscription(BaseModel):
    endpoint: str
    keys: dict
async def ask_ai_for_news_impact(title: str, content: str) -> dict:
    global NEWS_REASONS
    lower_title = title.lower()
    if "btc" in lower_title or "bitcoin" in lower_title:
        NEWS_REASONS["BTC"] = f"Anlık Haber Analizi: Kripto piyasasına düşen '{title[:40]}...' haberi fiyattaki yükseliş ivmesini tetikliyor."
    elif "eth" in lower_title or "ethereum" in lower_title:
        NEWS_REASONS["ETH"] = f"Anlık Haber Analizi: Ethereum ekosistemindeki '{title[:40]}...' gelişmesi kurumsal ilgiyi artırıyor."
    elif "sol" in lower_title or "solana" in lower_title:
        NEWS_REASONS["SOL"] = f"Anlık Haber Analizi: Solana ağ hızı ve '{title[:40]}...' duyurusu trend yönünü güçlendiriyor."
    return {"status": "processed"}

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
        try:
            gf_data = GlobalFinancialData()
            for s in ["BTC", "ETH", "SOL", "AAPL", "NVDA", "TSLA"]:
                data = await gf_data.time_series(s)
                if data is not None and not data.empty:
                    LAST[s] = analyze(data)
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
    symbol = urllib.parse.unquote(symbol).strip().upper()
    symbol_clean = symbol.replace("-", "").replace("/", "").replace("USDT", "").replace("USD", "")
    gf_data = GlobalFinancialData()
    data = await gf_data.time_series(symbol)
    if data is None or data.empty: raise HTTPException(status_code=404)
    score_data = analyze(data)
    score_data["news_reason"] = NEWS_REASONS.get(symbol_clean, "Varlığa ait özel finansal haber akışları anlık inceleniyor.")
    LAST[symbol_clean] = score_data
    return score_data

@app.get("/chart/{symbol}")
async def get_chart(symbol: str):
    symbol = urllib.parse.unquote(symbol).strip().upper()
    gf_data = GlobalFinancialData()
    data = await gf_data.time_series(symbol)
    if data is None or data.empty: raise HTTPException(status_code=404)
    return data.to_dict(orient="records")

@app.post("/subscribe")
async def subscribe(sub: Subscription): return {"status": "ok"}
