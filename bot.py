import requests
import pandas as pd
import numpy as np
import time
import json
from datetime import datetime
 
# === KONFİGÜRASYON ===
TELEGRAM_TOKEN = "8463347837:AAExccjnipYt0Tvx2RurZM2GV4zF8YBUizQ"
CHAT_ID = "1885325032"
 
SYMBOLS = {
    "BTC": "BTCUSDT",
    "XAG": "XAGUSDT",
    "XAU": "XAUUSDT"
}
 
LEVERAGE = 2
MIN_RR = 1.5
CHECK_INTERVAL = 900  # 15 dakika
 
last_signals = {}
 
# Farklı API endpointleri dene
BINANCE_ENDPOINTS = [
    "https://api.binance.com/api/v3/klines",
    "https://api1.binance.com/api/v3/klines",
    "https://api2.binance.com/api/v3/klines",
    "https://api3.binance.com/api/v3/klines",
]
 
def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    }
    try:
        response = requests.post(url, data=data, timeout=15)
        result = response.json()
        if result.get('ok'):
            print(f"✅ Telegram mesajı gönderildi")
        else:
            print(f"❌ Telegram hata: {result}")
        return result
    except Exception as e:
        print(f"❌ Telegram bağlantı hata: {e}")
 
def get_binance_klines(symbol, interval, limit=100):
    params = {
        "symbol": symbol,
        "interval": interval,
        "limit": limit
    }
    
    # Tüm endpointleri dene
    for endpoint in BINANCE_ENDPOINTS:
        try:
            response = requests.get(endpoint, params=params, timeout=15)
            if response.status_code == 200:
                data = response.json()
                if isinstance(data, list) and len(data) > 0:
                    df = pd.DataFrame(data, columns=[
                        'timestamp', 'open', 'high', 'low', 'close', 'volume',
                        'close_time', 'quote_volume', 'trades', 'taker_buy_base',
                        'taker_buy_quote', 'ignore'
                    ])
                    for col in ['open', 'high', 'low', 'close', 'volume']:
                        df[col] = pd.to_numeric(df[col])
                    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
                    print(f"✅ {symbol} veri alındı ({endpoint.split('/')[2]})")
                    return df
        except Exception as e:
            print(f"⚠️ {endpoint.split('/')[2]} hata: {e}")
            continue
    
    # CoinGecko fallback - BTC için
    try:
        if "BTC" in symbol:
            print(f"🔄 CoinGecko fallback deneniyor...")
            cg_url = "https://api.coingecko.com/api/v3/coins/bitcoin/ohlc"
            params_cg = {"vs_currency": "usd", "days": "7"}
            r = requests.get(cg_url, params=params_cg, timeout=15)
            if r.status_code == 200:
                ohlc = r.json()
                df = pd.DataFrame(ohlc, columns=['timestamp', 'open', 'high', 'low', 'close'])
                df['volume'] = 0
                df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
                print(f"✅ BTC CoinGecko verisi alındı")
                return df
    except Exception as e:
        print(f"⚠️ CoinGecko hata: {e}")
    
    print(f"❌ {symbol} için tüm kaynaklar başarısız")
    return None
 
def calculate_rsi(closes, period=14):
    delta = closes.diff()
    gain = delta.where(delta > 0, 0).rolling(window=period).mean()
    loss = -delta.where(delta < 0, 0).rolling(window=period).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi
 
def calculate_ote_zones(swing_low, swing_high):
    diff = swing_high - swing_low
    return {
        "ote_upper": swing_high - diff * 0.62,
        "ote_ideal": swing_high - diff * 0.705,
        "ote_lower": swing_high - diff * 0.79,
    }
 
def find_swing_points(df, lookback=10):
    recent = df.tail(50)
    swing_high = recent['high'].max()
    swing_low = recent['low'].min()
    return swing_low, swing_high
 
def analyze_symbol(name, symbol):
    try:
        df_4h = get_binance_klines(symbol, "4h", 100)
        df_1h = get_binance_klines(symbol, "1h", 100)
        df_15m = get_binance_klines(symbol, "15m", 100)
 
        if df_4h is None or df_1h is None or df_15m is None:
            return None
 
        rsi_4h = calculate_rsi(df_4h['close']).iloc[-1]
        rsi_1h = calculate_rsi(df_1h['close']).iloc[-1]
        rsi_15m = calculate_rsi(df_15m['close']).iloc[-1]
        current_price = df_15m['close'].iloc[-1]
 
        swing_low, swing_high = find_swing_points(df_4h)
        ote = calculate_ote_zones(swing_low, swing_high)
 
        trade_signal = None
        alerts = []
 
        # LONG sinyali — OTE zonunda + RSI uygun
        if (ote['ote_lower'] <= current_price <= ote['ote_upper'] and
                rsi_4h < 65 and rsi_1h < 60 and rsi_15m < 58):
 
            sl = swing_low * 0.994
            tp1 = swing_high * 1.005
            tp2 = swing_high * 1.025
 
            risk = current_price - sl
            reward = tp1 - current_price
            rr = reward / risk if risk > 0 else 0
 
            if rr >= MIN_RR:
                trade_signal = {
                    "type": "LONG",
                    "entry": round(current_price, 2),
                    "sl": round(sl, 2),
                    "tp1": round(tp1, 2),
                    "tp2": round(tp2, 2),
                    "rr": round(rr, 2),
                }
 
        # Aşırı alım uyarısı
        if rsi_4h > 75 and rsi_1h > 72:
            alerts.append("⚠️ AŞIRI ALIM — Yeni LONG açma!")
 
        # Aşırı satım fırsatı
        if rsi_4h < 32 and rsi_1h < 35:
            alerts.append("⚡ AŞIRI SATIM — LONG fırsatı yaklaşıyor!")
 
        return {
            "symbol": name,
            "price": round(current_price, 2),
            "rsi_4h": round(rsi_4h, 1),
            "rsi_1h": round(rsi_1h, 1),
            "rsi_15m": round(rsi_15m, 1),
            "trade_signal": trade_signal,
            "alerts": alerts,
            "ote_zone": f"{round(ote['ote_lower'],2)} - {round(ote['ote_upper'],2)}",
            "swing_low": round(swing_low, 2),
            "swing_high": round(swing_high, 2),
        }
 
    except Exception as e:
        print(f"❌ {name} analiz hata: {e}")
        return None
 
def format_trade_message(s):
    emoji_map = {"BTC": "₿", "XAG": "🥈", "XAU": "🥇"}
    emoji = emoji_map.get(s['symbol'], "📊")
    t = s['trade_signal']
 
    return f"""
🚨 <b>GİRİŞ FIRSATI!</b> 🚨
 
{emoji} <b>{s['symbol']}/USDT</b>
⏰ {datetime.now().strftime('%d.%m.%Y %H:%M')}
 
💰 <b>Fiyat:</b> {s['price']}
🎯 <b>Yön:</b> {t['type']} — 2x Kaldıraç
 
📍 <b>Entry:</b> {t['entry']}
🛑 <b>Stop Loss:</b> {t['sl']}
✅ <b>TP1:</b> {t['tp1']}
✅ <b>TP2:</b> {t['tp2']}
📊 <b>R:R:</b> {t['rr']}:1
 
📈 <b>RSI:</b> 4H:{s['rsi_4h']} | 1H:{s['rsi_1h']} | 15D:{s['rsi_15m']}
🎯 <b>OTE Zonu:</b> {s['ote_zone']}
💼 <b>İşlem:</b> 10.000 USDT
 
⚡ Binance'e git, emri koy!
""".strip()
 
def should_notify(symbol, signal_type, cooldown=14400):
    key = f"{symbol}_{signal_type}"
    now = time.time()
    if key in last_signals and now - last_signals[key] < cooldown:
        return False
    last_signals[key] = now
    return True
 
def run_analysis():
    print(f"\n🔍 Analiz: {datetime.now().strftime('%H:%M:%S')}")
    for name, symbol in SYMBOLS.items():
        result = analyze_symbol(name, symbol)
        if result is None:
            continue
 
        print(f"  {name}: {result['price']} | RSI 4H:{result['rsi_4h']} 1H:{result['rsi_1h']} 15m:{result['rsi_15m']}")
 
        if result['trade_signal'] and should_notify(name, "LONG"):
            msg = format_trade_message(result)
            send_telegram(msg)
            print(f"  📱 {name} sinyal gönderildi!")
 
        for alert in result['alerts']:
            if should_notify(name, alert[:10]):
                send_telegram(f"⚠️ <b>{name}</b>\n{alert}\nFiyat: {result['price']}")
 
        time.sleep(2)
 
def main():
    print("🚀 Trader Bot başlatılıyor...")
    send_telegram("""🤖 <b>Trader Asistanı Aktif!</b>
 
₿ BTC | 🥈 XAG | 🥇 XAU izleniyor
📊 ICT / OTE / SMC metodolojisi
⏰ Her 15 dakikada analiz
🔔 Fırsat görünce haber vereceğim!
 
Hayırlı kazançlar! 🎯""")
 
    last_status_day = -1
 
    while True:
        try:
            now = datetime.now()
 
            # Sabah 08:00 özeti
            if now.hour == 8 and now.minute < 15 and now.day != last_status_day:
                send_telegram(f"🌅 <b>Günaydın!</b>\nSistem aktif, piyasa izleniyor...\n⏰ {now.strftime('%d.%m.%Y %H:%M')}")
                last_status_day = now.day
 
            run_analysis()
            print(f"⏳ 15 dakika bekleniyor...")
            time.sleep(CHECK_INTERVAL)
 
        except KeyboardInterrupt:
            send_telegram("🔴 Bot durduruldu.")
            break
        except Exception as e:
            print(f"❌ Ana döngü hata: {e}")
            time.sleep(60)
 
if __name__ == "__main__":
    main()
