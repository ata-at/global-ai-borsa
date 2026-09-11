import numpy as np
import pandas as pd

def indicators(df):
    x = df.copy()
    close, high, low = x["close"], x["high"], x["low"]

    x["ema20"] = close.ewm(span=20, adjust=False).mean()
    x["ema50"] = close.ewm(span=50, adjust=False).mean()
    x["ema200"] = close.ewm(span=200, adjust=False).mean()

    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    x["rsi14"] = 100 - 100 / (1 + rs)

    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    x["macd"] = ema12 - ema26
    x["macd_signal"] = x["macd"].ewm(span=9, adjust=False).mean()
    x["macd_hist"] = x["macd"] - x["macd_signal"]

    tr = pd.concat([
        high-low, (high-close.shift()).abs(), (low-close.shift()).abs()
    ], axis=1).max(axis=1)
    x["atr14"] = tr.ewm(alpha=1/14, adjust=False).mean()

    mid = close.rolling(20).mean()
    std = close.rolling(20).std()
    x["bb_mid"] = mid
    x["bb_upper"] = mid + 2*std
    x["bb_lower"] = mid - 2*std

    if "volume" in x:
        x["vol_ma20"] = x["volume"].rolling(20).mean()
        x["vol_ratio"] = x["volume"] / x["vol_ma20"].replace(0, np.nan)

    return x
