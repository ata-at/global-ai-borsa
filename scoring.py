import pandas as pd
from .indicators import indicators

def clip(v, lo=0, hi=100):
    return max(lo, min(hi, float(v)))

def levels(df, lookback=80):
    x = df.tail(lookback)
    last = float(x.close.iloc[-1])
    support = float(x.low.min())
    resistance = float(x.high.max())
    for i in range(2, len(x)-2):
        if x.low.iloc[i] <= x.low.iloc[i-2:i+3].min() and x.low.iloc[i] <= last:
            support = max(support, float(x.low.iloc[i]))
        if x.high.iloc[i] >= x.high.iloc[i-2:i+3].max() and x.high.iloc[i] >= last:
            resistance = min(resistance, float(x.high.iloc[i]))
    return support, resistance

def analyze(df):
    if len(df) < 80:
        raise ValueError("Sinyal için yeterli mum yok.")
    x = indicators(df).dropna().reset_index(drop=True)
    r = x.iloc[-1]
    price = float(r.close)
    score = 50
    reasons = []

    if r.ema20 > r.ema50:
        score += 8; reasons.append("EMA20 > EMA50")
    else:
        score -= 8; reasons.append("EMA20 < EMA50")

    if r.ema50 > r.ema200:
        score += 7; reasons.append("EMA50 > EMA200")
    else:
        score -= 7; reasons.append("EMA50 < EMA200")

    if r.rsi14 < 35:
        score += 10; reasons.append("RSI aşırı satıma yakın")
    elif r.rsi14 > 70:
        score -= 10; reasons.append("RSI aşırı alıma yakın")
    elif r.rsi14 >= 50:
        score += 4; reasons.append("RSI 50 üzerinde")
    else:
        score -= 2; reasons.append("RSI 50 altında")

    if r.macd_hist > 0:
        score += 8; reasons.append("MACD histogram pozitif")
    else:
        score -= 8; reasons.append("MACD histogram negatif")

    if "vol_ratio" in r and pd.notna(r.vol_ratio):
        if r.vol_ratio >= 1.5:
            score += 8; reasons.append("Hacim güçlü")
        elif r.vol_ratio < 0.7:
            score -= 3; reasons.append("Hacim zayıf")

    support, resistance = levels(df)
    atr = float(r.atr14)
    score = clip(score)
    confidence = clip(50 + abs(score-50)*1.25)
    signal = "BUY_ZONE" if score >= 70 else "SELL_RISK" if score <= 30 else "WAIT"

    entry_low = max(0, min(price, support + .20*atr))
    entry_high = max(entry_low, min(price + .35*atr, resistance))
    stop = max(0, support - atr)
    target1 = price + 1.5*atr
    target2 = price + 2.5*atr

    return {
        "price": round(price,4),
        "score": round(score,1),
        "confidence": round(confidence,1),
        "signal": signal,
        "entry_zone": [round(entry_low,4), round(entry_high,4)],
        "support": round(support,4),
        "resistance": round(resistance,4),
        "stop": round(stop,4),
        "targets": [round(target1,4), round(target2,4)],
        "rsi": round(float(r.rsi14),2),
        "macd_hist": round(float(r.macd_hist),6),
        "ema20": round(float(r.ema20),4),
        "ema50": round(float(r.ema50),4),
        "ema200": round(float(r.ema200),4),
        "atr": round(atr,4),
        "reasons": reasons
    }
