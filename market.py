import os
import httpx
import pandas as pd

BASE = "https://api.twelvedata.com"

class TwelveData:
    def __init__(self):
        self.key = os.getenv("TWELVE_DATA_API_KEY", "").strip()
        if not self.key:
            raise RuntimeError("TWELVE_DATA_API_KEY eksik")

    async def time_series(self, symbol, interval="1min", outputsize=250):
        params = {
            "symbol": symbol,
            "interval": interval,
            "outputsize": outputsize,
            "apikey": self.key,
            "format": "JSON",
        }
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(f"{BASE}/time_series", params=params)
            r.raise_for_status()
            data = r.json()
        if data.get("status") == "error":
            raise RuntimeError(data.get("message", "Twelve Data error"))
        df = pd.DataFrame(data.get("values", []))
        if df.empty:
            return df
        df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
        for c in ["open","high","low","close","volume"]:
            if c in df:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        return df.sort_values("datetime").reset_index(drop=True)
