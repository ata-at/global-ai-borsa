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
PROCESSED_NEWS = set()  # Aynı haberlerin tekrar analiz edilmesini önleyen hafıza
task = None
news_task = None

# HEM HİSSE HEM KRİPTO VERİLERİNİ ASLA ENGELLENMEYECEK ŞEKİLDE ÇEKEN SINIF
class GlobalFinancialData:
    async def time_series(self, symbol: str):
        try:
            # Gelen sembolü decode et ve temizle
            symbol = urllib.parse.unquote(symbol)
            if ":" in symbol:
                symbol = symbol.split(":")[-1]
            symbol = symbol.strip().upper()
            
            # Eğer sembol zaten düzgün bir Yahoo formatıysa (Örn: BTC-USD) doğrudan kullan
            if "-" in symbol:
                pass
            # BTCUSD veya BTCUSDT gibi birleşik geldiyse araya tire (-) koy
            elif (symbol.endswith("USD") or symbol.endswith("USDT")) and len(symbol) > 4:
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
            if not chart_result or len(chart_result) == 0:
                print(f"Yahoo Verisi Boş Döndü: {symbol}")
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

# =====================================================================
# YAPAY ZEKA DESTEKLİ HABER VE SOSYAL MEDYA ANALİZ MODÜLÜ
# =====================================================================

async def ask_ai_for_news_impact(title: str, content: str) -> dict:
    """
    Haberleri OpenAI / DeepSeek / Claude API'ye göndererek 
    piyasaya etkisini ve yönünü (YUKARI/AŞAĞI) analiz ettirir.
    """
    ai_api_key = os.getenv("AI_API_KEY")
    ai_base_url = os.getenv("AI_BASE_URL", "https://openai.com")
    
    if not ai_api_key:
        # API anahtarı girilmediyse kural tabanlı hızlı kelime analizi
        lower_title = title.lower()
        if any(w in lower_title for w in ["yasak", "düştü", "ceza", "faiz artış", "düşüş", "fud", "dava"]):
            return {"direction": "AŞAĞI 🔴", "summary": "Negatif haber akışı tespit edildi."}
        if any(w in lower_title for w in ["ortaklık", "satın aldı", "yükseldi", "destek", "boğa", "listeleme"]):
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
    """
    Borsa ve coin haber sitelerinin RSS servislerini 7/24 tarar.
    """
    urls = [
        "https://cointelegraph.com", 
        "https://coindesk.com",
        "https://uzmancoin.com"
    ]
    
    async with httpx.AsyncClient(timeout=10.0) as client:
        for url in urls:
            try:
                headers = {"User-Agent": "Mozilla/5.0"}
                response = await client.get(url, headers=headers)
                if response.status_code != 200:
                    continue
                
                root = ET.fromstring(response.text)
                for item in root.findall(".//item")[:3]: # Son 3 haberi kontrol et
                    title = item.find("title").text if item.find("title") is not None else ""
                    link = item.find("link").text if item.find("link") is not None else ""
                    desc = item.find("description").text if item.find("description") is not None else ""
                    
                    if not title or link in PROCESSED_NEWS:
                        continue
                        
                    PROCESSED_NEWS.add(link)
                    
                    # Yapay zekaya haberi yorumlat
                    ai_result = await ask_ai_for_news_impact(title, desc)
                    
                    # Telegram'a anlık analiz bildirimi gönder
                    telegram_msg = (
                        f"📰 *YAPAY ZEKA ANLIK HABER ALARMI*\n\n"
                        f"📌 *Başlık:* {title}\n"
                        f"🤖 *AI Özet:* {ai_result['summary']}\n"
                        f"📊 *Tahmini Piyasa Yönü:* {ai_result['direction']}\n\n"
                        f"🔗 [Habere Git]({link})"
                    )
                    await send_telegram_message(telegram_msg)
            except Exception as e:
                print(f"Haber Kaynağı Tarama Hatası ({url}): {e}")

async def news_and_social_ai_loop():
    """
    Haber akışını her 2 dakikada bir kontrol eden döngü.
    """
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
