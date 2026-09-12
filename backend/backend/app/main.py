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
task = None
news_task = None

# YAHOO BULUT SUNUCU ENGELLERİNİ VE SEMBOL HATALARINI ÇÖZEN SINIF
class GlobalFinancialData:
    async def time_series(self, symbol: str):
        try:
            symbol = urllib.parse.unquote(symbol)
            if ":" in symbol:
                symbol = symbol.split(":")[-1]
            symbol = symbol.strip().upper()
            
            # ÇİFT TİRE HATASI DÜZELTMESİ:
            # Eğer gelen sembol zaten tire içeriyorsa (Örn: BTC-USD) doğrudan kullan, elleme.
            if "-" in symbol:
                pass
            # Eğer BTCUSD veya BTCUSDT gibi birleşik geldiyse araya tek bir tire koy
            elif (symbol.endswith("USD") or symbol.endswith("USDT")) and len(symbol) > 4:
                base_coin = symbol.replace("USDT", "").replace("USD", "")
                symbol = f"{base_coin}-USD"

            loop = asyncio.get_event_loop()
            ticker = yf.Ticker(symbol)
            hist = await loop.run_in_executor(None, lambda: ticker.history(period="1y", interval="1d"))
            
            if hist.empty:
                print(f"Finans Verisi Boş Döndü Veya Sembol Hatalı: {symbol}")
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
            return df.sort_values("datetime").reset_index(drop=True)
        except Exception as e:
            print(f"Küresel Veri Çekme Motoru Hatası ({symbol}): {e}")
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

# =====================================================================
# YAPAY ZEKA DESTEKLİ HABER VE SOSYAL MEDYA ANALİZ MODÜLÜ
# =====================================================================

async def ask_ai_for_news_impact(title: str, content: str) -> dict:
    ai_api_key = os.getenv("AI_API_KEY")
    ai_base_url = os.getenv("AI_BASE_URL", "https://openai.com")
    
    if not ai_api_key:
        lower_title = title.lower()
        if any(w in lower_title for w in ["yasak", "düştü", "ceza", "faiz artış", "düşüş", "fud", "dava", "satıyor"]):
            return {"direction": "AŞAĞI 🔴", "summary": "Negatif haber akışı tespit edildi."}
        if any(w in lower_title for w in ["ortaklık", "satın aldı", "yükseldi", "destek", "boğa", "listeleme", "alıyor"]):
            return {"direction": "YUKARI 🟢", "summary": "Pozitif haber akışı veya kurumsal destek tespit edildi."}
        return {"direction": "NÖTR ⚪", "summary": "Piyasayı doğrudan etkileyecek sert bir ibare bulunamadı."}

    try:
        headers = {"Authorization": f"Bearer {ai_api_key}", "Content-Type": "application/json"}
        prompt = (
            f"Aşağıdaki finansal haberi/söylemi incele. Bu haber borsa veya kripto piyasasını nasıl etkiler?\n"
            f"Haber Başlığı: {title}\nİçerik: {content}\n\n"
            f"Lütfen SADECE şu JSON formatında yanıt ver:\n"
            f'{{"direction": "YUKARI" veya "AŞAĞI" veya "NÖTR", "summary": "Haberin kısa tek cümlelik özeti"}}'
        )
        payload = {
            "model": os.getenv("AI_MODEL_NAME", "gpt-4o-mini"),
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"}
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.post(f"{ai_base_url}/chat/completions", headers=headers, json=payload)
            if res.status_code == 200:
                import json
                ai_response = json.loads(res.json()["choices"]["message"]["content"])
                dir_emoji = "🟢 YUKARI" if ai_response.get("direction") == "YUKARI" else "🔴 AŞAĞI" if ai_response.get("direction") == "AŞAĞI" else "⚪ NÖTR"
                return {"direction": dir_emoji, "summary": ai_response.get("summary", "Özet alınamadı.")}
    except Exception as e:
        print(f"Yapay Zeka API Hatası: {e}")
    return {"direction": "BİLİNMİYOR 🟡", "summary": "Yapay zeka analiz yaparken hata oluştu."}

async def fetch_news_feeds():
    urls = [
        "https://cointelegraph.com", 
        "https://coindesk.com",
        "https://uzmancoin.com"
    ]
    
    urls.append("https://rsshub.app")
    urls.append("https://rsshub.app")
    
    async with httpx.AsyncClient(timeout=10.0) as client:
        for url in urls:
            try:
                headers = {"User-Agent": "Mozilla/5.0"}
                response = await client.get(url, headers=headers)
                if response.status_code != 200:
                    continue
                
                root = ET.fromstring(response.text)
                for item in root.findall(".//item")[:3]:
                    title = item.find("title").text if item.find("title") is not None else ""
                    link = item.find("link").text if item.find("link") is not None else ""
                    desc = item.find("description").text if item.find("description") is not None else ""
                    
                    if not title or link in PROCESSED_NEWS:
                        continue
                        
                    PROCESSED_NEWS.add(link)
                    if len(PROCESSED_NEWS) > 500:
                        PROCESSED_NEWS.pop()
                    
                    ai_result = await ask_ai_for_news_impact(title, desc)
                    
                    telegram_msg = (
                        f"📰 *YAPAY ZEKA ANLIK HABER ALARMI*\n\n"
                        f"📌 *Kaynak/Başlık:* {title}\n"
                        f"🤖 *AI Özet:* {ai_result['summary']}\n"
                        f"📊 *Tahmini Piyasa Yönü:* {ai_result['direction']}\n\n"
                        f"🔗 [Habere/Tweete Git]({link})"
                    )
                    await send_telegram_message(telegram_msg)
            except Exception as e:
                print(f"Haber Kaynağı Tarama Hatası ({url}): {e}")

async def news_and_social_ai_loop():
    while True:
        try:
            await fetch_news_feeds()
        except Exception as e:
            print(f"Haber Yapay Zeka Döngü Hatası: {e}")
        await asyncio.sleep(120)

# =====================================================================

async def scanner_loop():
    while True:
        try:
            await scan_once()
        except Exception as e:
            print(f"Tarayıcı Döngü Hatası: {e}")
        await asyncio.sleep(60)

@asynccontextmanager
async def lifespan(app: FastAPI):
    global task, news_task
    task = asyncio.create_task(scanner_loop())
    news_task = asyncio.create_task(news_and_social_ai_loop())
    yield
    if task:
        task.cancel()
    if news_task:
        news_task.cancel()

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
    payload = {"chat_id": chat_id, "text": message, "parse_mode": "Markdown", "disable_web_page_preview": True}
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            await client.post(url, json=payload)
        except Exception as e:
            print(f"Telegram gönderme hatası: {e}")

@app.get("/")
async def root():
    return {"status": "healthy", "message": "AI Engine is running 7/24"}

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
