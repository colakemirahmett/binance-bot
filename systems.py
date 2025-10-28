import requests,hmac,hashlib
import pandas as pd
import talib
import time
from datetime import datetime
from urllib.parse import urlencode

BASE = "https://fapi.binance.com"
api_key = "GdTkvvxisxiE1UTsGj01UaDNUsyIebZ7Eaxs6SpYSAJp3Z5cgcHIvABqvO5jPIB2"
secret_key = "fbAUIFRGrzvz82UgCFjBXC7adFNI3Gznwy7Rpn5TIwkoLUvzfdHzQCWxiu4HzNsM"   
limit = 250

# ✅ EK 1: 24 saatlik short sinyal takibi
short_signal_ts = {}
SIGNAL_TTL = 86400  # 24 saat (saniye)

def request_signature(yon,endpoint,params):
    params["timestamp"] = int(time.time() * 1000)
    query_string=urlencode(params)
    signature = hmac.new(secret_key.encode(), query_string.encode(), hashlib.sha256).hexdigest()
    query_string += f"&signature={signature}"
    headers = {"X-MBX-APIKEY": api_key}
    url = f"{BASE}{endpoint}?{query_string}"
    r = requests.request(yon, url, headers=headers)
    return r.json()

def short_position(symbol,entry_price,lavarege=10,stop_loss=0.02,take_profit=0.04):
    request_signature("POST","/fapi/v1/leverage",{
        "symbol":symbol,
        "leverage":lavarege
    })

    headers = {"X-MBX-APIKEY": api_key}
    account_info = requests.get(f"{BASE}/fapi/v2/account", headers=headers).json()
    usdt_balance = float([x for x in account_info["assets"] if x["asset"] == "USDT"][0]["availableBalance"])

    margin = usdt_balance * lavarege
    miktar = round(margin / entry_price, 3)

    request_signature("POST", "/fapi/v1/order", {
        "symbol": symbol,
        "side": "SELL",
        "type": "MARKET",
        "quantity": miktar
    })

    stop = round(entry_price * (1 + stop_loss), 4)
    tp = round(entry_price * (1 - take_profit), 4)

    request_signature("POST", "/fapi/v1/order", {
        "symbol": symbol,
        "side": "BUY",
        "type": "STOP_MARKET",
        "stopPrice": stop,
        "closePosition": "true",
        "timeInForce": "GTC"
    })

    request_signature("POST", "/fapi/v1/order", {
        "symbol": symbol,
        "side": "BUY",
        "type": "TAKE_PROFIT_MARKET",
        "stopPrice": tp,
        "closePosition": "true",
        "timeInForce": "GTC"
    })

    while True:
        pozition = request_signature("GET", "/fapi/v2/positionRisk", {"symbol": symbol})
        poz_actif = float(pozition[0]["positionAmt"])
        now = datetime.now().strftime("%H:%M:%S")
        if poz_actif == 0:
            break
        else:
            print("İşlem açık Bekleniyor.")
            time.sleep(3)

    # ✅ EK 3: işlem açıldıktan sonra sinyali temizle
    short_signal_ts.pop(symbol, None)


print("🔍 Binance USDT-M semboller alınıyor...")
info = requests.get(f"{BASE}/fapi/v1/exchangeInfo").json()
symbols = [s["symbol"] for s in info["symbols"]
           if s["quoteAsset"] == "USDT" and s["contractType"] == "PERPETUAL"]

symbols = symbols[:30]
print(f"Toplam {len(symbols)} sembol analiz edilecek.\n")

results = []
while True:
    for symbol in symbols:
        try:
            price_data = requests.get(f"{BASE}/fapi/v1/ticker/price?symbol={symbol}").json()
            current_price = float(price_data["price"])

            url_4h = f"{BASE}/fapi/v1/klines?symbol={symbol}&interval=4h&limit={limit}"
            data_4h = requests.get(url_4h).json()
            df_4h = pd.DataFrame(data_4h, columns=[
                "open_time","open","high","low","close","volume",
                "close_time","qv","ntrades","tbbase","tbquote","ignore"
            ])
            df_4h = df_4h.astype({"open": float, "high": float, "low": float, "close": float, "volume": float})

            close_4h = df_4h["close"]
            ema50 = talib.EMA(close_4h, 50)
            ema200 = talib.EMA(close_4h, 200)
            rsi_4h = talib.RSI(close_4h, 14)
            vol_4h = df_4h["volume"]

            trend = "UP" if ema50.iloc[-1] > ema200.iloc[-1] else "DOWN"
            rsi_4h_value = rsi_4h.iloc[-1]

            url_1h = f"{BASE}/fapi/v1/klines?symbol={symbol}&interval=1h&limit=50"
            data_1h = requests.get(url_1h).json()
            df_1h = pd.DataFrame(data_1h, columns=[
                "open_time","open","high","low","close","volume",
                "close_time","qv","ntrades","tbbase","tbquote","ignore"
            ])
            df_1h["close"] = df_1h["close"].astype(float)
            rsi_1h = talib.RSI(df_1h["close"], 14)

            rsi_1h_prev = rsi_1h.iloc[-3]
            rsi_1h_last = rsi_1h.iloc[-1]

            confirm_long = rsi_1h_last > rsi_1h_prev
            confirm_short = rsi_1h_prev > rsi_1h_last

            score = 0
            reason = []

            last_high = df_4h["high"].iloc[-5:].max()
            last_low = df_4h["low"].iloc[-5:].min()
            vol_last = vol_4h.iloc[-1]
            vol_prev = vol_4h.iloc[-2]

            if vol_last > vol_prev * 1.2:
                  volume_ok = True
            else:
                volume_ok = False

            if trend == "UP":
                reason.append("Trend yukarı")
                if rsi_4h_value < 30 and confirm_long:
                    score = 3
                    reason.append("RSI<30 GÜÇLÜ LONG")

            elif trend == "DOWN":
                reason.append("Trend aşağı")
                if rsi_4h_value > 70 and confirm_short and volume_ok:
                    score = 3
                    short_signal_ts[symbol] = time.time()

                # sinyal önceden varsa 24 saat boyunca aktif kalsın
                elif symbol in short_signal_ts:
                    elapsed = time.time() - short_signal_ts[symbol]
                    if elapsed <= SIGNAL_TTL:
                        score = 3
                    else:
                        del short_signal_ts[symbol]

            results.append({
                "symbol": symbol,
                "trend": trend,
                "rsi_4h": round(rsi_4h_value, 2),
                "rsi_1h_prev": round(rsi_1h_prev, 2),
                "rsi_1h_last": round(rsi_1h_last, 2),
                "score": score,
                "reason": " | ".join(reason)
            })

            buffer = 0.0025
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"✅ [{now}] {symbol} analiz edildi. Trend: {trend}, Puan: {score}, RSI={round(rsi_4h_value,2)},Confirm Short :{confirm_short},Volume Oke:{volume_ok}")

            time.sleep(0.05)

            if score == 3 and trend == "DOWN":
                while True:
                    try:
                        price_data = requests.get(f"{BASE}/fapi/v1/ticker/price?symbol={symbol}").json()
                        current_price = float(price_data["price"])
                        recenty_price = last_high * (1 - buffer)

                        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                        distance=abs(current_price-recenty_price)/recenty_price
                        
                        if current_price >= recenty_price:
                            short_position(symbol=symbol,entry_price=current_price)
                        elif (distance<=0.005):
                             print(f"⏳ [{now}] {symbol} Bekleniyor... Güncel: {current_price}, Hedef: {recenty_price}")
                             time.sleep(1)
                        else:
                             print("HEDEFTEN UZAK!!")    
                             break

                    except Exception as e:
                        print(f"❌ Hata: {symbol} ({e})")

        except Exception as e:
            print(f"❌ Hata: {symbol} ({e})")
