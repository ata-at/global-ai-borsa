import os, asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from .market import TwelveData
from .scoring import analyze
from .push import save_subscription, send_push

LAST = {}
task = None

def watchlist():
    return [x.strip() for x in os.getenv("WATCHLIST","").split(",") if x.strip()]

class Subscription(BaseModel):
    endpoint: str
    keys: dict

async def scan_once():
    td = TwelveData()
    threshold = float(os.getenv("MIN_SCORE_FOR_PUSH","78"))
    for symbol in watchlist():
        try:
            df = await td.time_series(symbol, "1min", 250)
            result = analyze(df)
            LAST[symbol] = result
            if result["signal"] == "BUY_ZONE" and result["score"] >= threshold:
                send_push(
                    f"AI Borsa: {symbol}",
                    f"Skor {result['score']} | Giriş {result['entry_zone'][0]}–{result['entry_zone'][1]}",
                    f"/?symbol={symbol}"
                )
        except Exception as e:
            LAST[symbol] = {"error": str(e)}

async def loop():
    interval = int(os.getenv("SCAN_INTERVAL_SECONDS","60"))
    while True:
        try: await scan_once()
        except Exception as e: print("scanner:", e)
        await asyncio.sleep(interval)

@asynccontextmanager
async def lifespan(app):
    global task
    task = asyncio.create_task(loop())
    yield
    task.cancel()

app = FastAPI(title="Global AI Borsa API", lifespan=lifespan)

origins = [x.strip() for x in os.getenv("ALLOWED_ORIGINS","*").split(",") if x.strip()]
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

@app.get("/health")
async def health():
    return {"ok":True,"watchlist":watchlist(),"cached":len(LAST)}

@app.get("/signals")
async def signals():
    return {"signals":LAST}

@app.get("/signal/{symbol:path}")
async def signal(symbol):
    if symbol in LAST and "error" not in LAST[symbol]:
        return LAST[symbol]
    try:
        df = await TwelveData().time_series(symbol, "1min", 250)
        result = analyze(df); LAST[symbol] = result
        return result
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/chart/{symbol:path}")
async def chart(symbol):
    try:
        df = await TwelveData().time_series(symbol, "1min", 250)
        result = analyze(df)
        candles = [{
            "time":int(r.datetime.timestamp()),
            "open":float(r.open),"high":float(r.high),
            "low":float(r.low),"close":float(r.close),
            "volume":float(r.volume) if hasattr(r,"volume") and r.volume == r.volume else 0
        } for r in df.tail(180).itertuples(index=False)]
        return {"symbol":symbol,"candles":candles,"levels":result}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/push/subscribe")
async def subscribe(sub: Subscription):
    save_subscription(sub.model_dump())
    return {"ok":True}

@app.post("/scan")
async def manual_scan():
    await scan_once()
    return {"ok":True,"signals":LAST}
