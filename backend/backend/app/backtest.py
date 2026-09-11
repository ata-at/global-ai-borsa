from .scoring import analyze

def walk_forward(df, horizon=10, threshold=.02, min_train=100):
    if len(df) < min_train + horizon + 10:
        return {"trades":0,"wins":0,"hit_rate":None,"message":"Yetersiz geçmiş veri."}
    wins = trades = 0
    for i in range(min_train, len(df)-horizon):
        try:
            s = analyze(df.iloc[:i].copy())
        except Exception:
            continue
        if s["signal"] not in ("BUY_ZONE","SELL_RISK"):
            continue
        p0 = float(df.iloc[i].close)
        p1 = float(df.iloc[i+horizon].close)
        ret = (p1-p0)/p0
        win = ret >= threshold if s["signal"] == "BUY_ZONE" else ret <= -threshold
        wins += int(win)
        trades += 1
    return {
        "trades":trades,
        "wins":wins,
        "hit_rate":round(100*wins/trades,2) if trades else None,
        "horizon_bars":horizon,
        "threshold":threshold
    }
