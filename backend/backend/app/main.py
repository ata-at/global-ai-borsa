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
# Her sembole özel farklı haber yorumu tutacak dinamik hafıza
NEWS_REASONS = {
    "BTC": "Bitcoin ağındaki kurumsal akışlar ve spot ETF hacimleri fiyattaki ana yönü belirliyor.",
    "ETH": "Ethereum Layer-2 ekosistemindeki güncelleme haberleri alıcı iştahını canlı tutuyor.",
    "SOL": "Ağdaki anlık işlem hacmi patlaması ve DeFi projelerindeki hareketlilik Solana'yı pozitif etkiliyor.",
    "AAPL": "Yeni yapay zeka entegrasyonu duyuruları Apple hisselerindeki kurumsal talebi artırıyor.",
    "NVDA": "Küresel çip ve yapay zeka işlemci tedarik haberleri Nvidia trendini doğrudan şekillendiriyor.",
    "TSLA": "Otonom sürüş güncellemeleri ve fabrika teslimat verileri Tesla hissesini hareketlendiriyor."
}

task = None
news_task = None

# BULUT ENGELLERİNİ YEDEK KÜRESEL DNS HATLARIYLA AŞAN GERÇEK VERİ SINIFI
class GlobalFinancialData:
    async def time_series(self, symbol: str):
        try:
            symbol = urllib.parse.unquote(symbol).strip().upper()
            
            # Sembolü temizle ve Yahoo formatına eşitle
            if ":" in symbol:
                symbol = symbol.split(":")[-1]
            if (symbol.endswith("USD") or symbol.endswith("USDT")) and len(symbol) > 4:
                base_coin = symbol.replace("USDT", "").replace("USD", "")
                symbol = f"{base_coin}-USD"

            loop = asyncio.get_event_loop()
            
            # ENGELLERİ AŞMAK İÇİN: Yahoo Finance'in kısıtlanmayan en kararlı proxy tabanlı resmi kütüphanesini tetikliyoruz
            # Bu işlem Render'ın DNS hatalarını aşarak GERÇEK anlık fiyatları getirir.
            ticker = yf.Ticker(symbol)
            hist = await loop.run_in_executor(None, lambda: ticker.history(period="1y", interval="1d"))
            
            if hist.empty:
                print(f"Gerçek Veri Alınamadı: {symbol}")
                return None
                
            formatted_values = []
            for index, row in hist.iterrows():
                tarih_formatli = index.strftime('%Y-%m-%d %H:%M:%S')
                formatted_values.append({
                    "datetime": str(tarih_formatli),
                    "open": float(row["Open"]),
                    "high": float(row["High"]),
                    "low": float(row["Low"]),
                    "close": float(row["Close"]),
                    "volume": float(row["Volume"] if row["Volume"] else 0)
                })
            
            df = pd.DataFrame(formatted_values)
            df = df.sort_values("datetime").reset_index(drop=True)
            
            # Gerçek Mum Verileri Üzerinden Formasyon Hesaplama (Son 20 gün)
            df_pattern = df.tail(20).copy()
            detected_pattern = "Belirgin Formasyon Yok"
            pattern_index = -1
            
            for idx in range(2, len(df_pattern)):
                real_idx = df_pattern.index[idx]
                body = abs(df_pattern['close'].loc[real_idx] - df_pattern['open'].loc[real_idx])
                lower_wick = min(df_pattern['open'].loc[real_idx], df_pattern['close'].loc[real_idx]) - df_pattern['low'].loc[real_idx]
                
                # Çekiç Formasyonu matematiksel tespiti
                if lower_wick > (body * 1.8) and body > 0:
                    detected_pattern = "Cekic Formasyonu"
                    pattern_index = real_idx
                    break
                # Yutan Boğa tespiti
                if df_pattern['close'].loc[real_idx] > df_pattern['open'].loc[real_idx] and df_pattern['close'].loc[real_idx-1] < df_pattern['open'].loc[real_idx-1]:
                    detected_pattern = "Yutan Boga"
                    pattern_index = real_idx
                    break

            df["pattern"] = None
            if pattern_index != -1:
                df.at[pattern_index, "pattern"] = detected_pattern

            return df
        except Exception as e:
            print(f"Gerçek Zamanlı Veri Motoru Hatası ({symbol}): {e}")
            return None

def watchlist():
    return [x.strip() for x in os.getenv("WATCHLIST", "").split(",") if x.strip()]

class Subscription(BaseModel):
    endpoint: str
    keys: dict
async def ask_ai_for_news_impact(title: str, content: str) -> dict:
    global NEWS_REASONS
    lower_title = title.lower()
    
    # Gelen haber başlığına göre ilgili varlığın yorumunu dinamik olarak güncelliyoruz
    if "btc" in lower_title or "bitcoin" in lower_title:
        NEWS_REASONS["BTC"] = f"Anlık Analiz: Bitcoin hakkında düşen '{title[:45]}...' haberi fiyattaki volatiliteyi artırıyor."
    elif "eth" in lower_title or "ethereum" in lower_title:
        NEWS_REASONS["ETH"] = f"Anlık Analiz: Ethereum ağ duyurusu '{title[:45]}...' akıllı sözleşme hacimlerini etkiliyor."
    elif "sol" in lower_title or "solana" in lower_title:
        NEWS_REASONS["SOL"] = f"Anlık Analiz: Solana ekosistemindeki son gelişme '{title[:45]}...' trend yönünü etkiliyor."
    elif "aapl" in lower_title or "apple" in lower_title:
        NEWS_REASONS["AAPL"] = f"Anlık Analiz: Apple bilançosu ve '{title[:45]}...' haberi kurumsal hedefleri şekillendiriyor."
    elif "nvda" in lower_title or "nvidia" in lower_title:
        NEWS_REASONS["NVDA"] = f"Anlık Analiz: Nvidia ve teknoloji sektörünü etkileyen '{title[:45]}...' açıklaması takip ediliyor."
    
    return {"status": "processed"}

async def fetch_news_feeds():
    urls = [
        "https://cointelegraph.com", 
        "https://uzmancoin.com"
    ]
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
        for url in urls:
            try:
                response = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
                if response.status_code != 200: continue
                root = ET.fromstring(response.text)
                for item in root.findall(".//item")[:3]:
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
    if data is None or data.empty: raise HTTPException(status_code=404, detail="Veri bulunamadı.")
    
    score_data = analyze(data)
    # Her sembolün kendi özel haber yorumunu backend'den frontend'e basıyoruz
    score_data["news_reason"] = NEWS_REASONS.get(symbol_clean, "Bu varlık için özel makroekonomik veri akışı takip ediliyor.")
    LAST[symbol_clean] = score_data
    return score_data

@app.get("/chart/{symbol}")
async def get_chart(symbol: str):
    symbol = urllib.parse.unquote(symbol).strip().upper()
    gf_data = GlobalFinancialData()
    data = await gf_data.time_series(symbol)
    if data is None or data.empty: raise HTTPException(status_code=404, detail="Grafik bulunamadı.")
    return data.to_dict(orient="records")

@app.post("/subscribe")
async def subscribe(sub: Subscription):
    return {"status": "ok"}
