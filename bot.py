import requests
import pandas as pd
import numpy as np
import time
import json
from datetime import datetime
import yfinance as yf
 
# === KONFİGÜRASYON ===
TELEGRAM_TOKEN = "8600853087:AAFLY8Y-0zrKk8g6qCej6XkjwpVpvkkiKKw"
CHAT_ID = 1885325032  # Integer olarak
 
# yfinance sembolleri
SYMBOLS = {
    "BTC": "BTC-USD",
    "XAG": "SI=F",    # Gümüş Futures
    "XAU": "GC=F",    # Altın Futures
}
 
MIN_RR = 1.5
CHECK_INTERVAL = 900  # 15 dakika
last_signals = {}
 
def send_telegram(message):
    """Telegram mesaj gönder"""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    }
    try:
        r = requests.post(url, json=payload, timeout=15)
        result = r.json()
        if result.get('ok'):
            print(f"✅ Telegram OK")
        else:
            print(f"❌ Telegram hata: {result}")
        return result
    except Exception as e:
        print(f"❌ Telegram exception: {e}")
 
def get_data(symbol, interval, period):
    """yfinance ile veri al"""
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period=period, interval=interval)
        if df is not None and len(df) > 20:
            df.columns = [c.lower() for c in df.columns]
            print(f"✅ {symbol} veri alındı ({len(df)} mum)")
            return df
        else:
            print(f"⚠️ {symbol} yeterli veri yok")
            return None
    except Exception as e:
        print(f"❌ {symbol} veri hata: {e}")
        return None
 
def calculate_rsi(closes, period=14):
    """RSI hesapla"""
    delta = closes.diff()
    gain = delta.where(delta > 0, 0).rolling(window=period).mean()
    loss = -delta.where(delta < 0, 0).rolling(window=period).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi
 
def calculate_ote(swing_low, swing_high):
    """OTE zonları hesapla"""
    diff = swing_high - swing_low
    return {
        "upper": swing_high - diff * 0.62,
        "ideal": swing_high - diff * 0.705,
        "lower": swing_high - diff * 0.79,
    }
 
def analyze_symbol(name, yf_symbol):
    """ICT/OTE/SMC analizini yap"""
    try:
        # Veri çek
        df_4h = get_data(yf_symbol, "1h", "30d")   # 4H yerine 1H kullan, yeterli
        df_1h = get_data(yf_symbol, "1h", "7d")
        df_15m = get_data(yf_symbol, "15m", "2d")
 
        if df_4h is None or df_1h is None or df_15m is None:
            return None
 
        # RSI hesapla
        rsi_4h = calculate_rsi(df_4h['close']).iloc[-1]
        rsi_1h = calculate_rsi(df_1h['close']).iloc[-1]
        rsi_15m = calculate_rsi(df_15m['close']).iloc[-1]
        current_price = df_15m['close'].iloc[-1]
 
        # Swing noktaları
        swing_high = df_4h['high'].tail(60).max()
        swing_low = df_4h['low'].tail(60).min()
 
        # OTE zonu
        ote = calculate_ote(swing_low, swing_high)
 
        trade_signal = None
        alerts = []
 
        # LONG sinyali
        if (ote['lower'] <= current_price <= ote['upper'] and
                rsi_4h < 65 and rsi_1h < 60 and rsi_15m < 58):
 
            sl = swing_low * 0.994
            tp1 = swing_high * 1.01
            tp2 = swing_high * 1.025
 
            risk = abs(current_price - sl)
            reward = abs(tp1 - current_price)
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
 
        # Uyarılar
        if rsi_4h > 75 and rsi_1h > 72:
            alerts.append("⚠️ AŞIRI ALIM — Yeni LONG açma!")
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
            "ote_zone": f"{round(ote['lower'],2)} - {round(ote['upper'],2)}",
            "swing_low": round(swing_low, 2),
            "swing_high": round(swing_high, 2),
        }
 
    except Exception as e:
        print(f"❌ {name} analiz hata: {e}")
        return None
 
def format_signal(s):
    """Telegram sinyal mesajı"""
    emoji_map = {"BTC": "₿", "XAG": "🥈", "XAU": "🥇"}
    emoji = emoji_map.get(s['symbol'], "📊")
    t = s['trade_signal']
 
    return f"""🚨 <b>GİRİŞ FIRSATI!</b> 🚨
 
{emoji} <b>{s['symbol']}/USDT</b>
⏰ {datetime.now().strftime('%d.%m.%Y %H:%M')}
 
💰 <b>Fiyat:</b> {s['price']}
🎯 <b>Yön:</b> {t['type']} — 2x Kaldıraç
 
📍 <b>Entry:</b> {t['entry']}
🛑 <b>Stop Loss:</b> {t['sl']}
✅ <b>TP1:</b> {t['tp1']}
✅ <b>TP2:</b> {t['tp2']}
📊 <b>R:R:</b> {t['rr']}:1
 
📈 RSI → 4H:{s['rsi_4h']} | 1H:{s['rsi_1h']} | 15D:{s['rsi_15m']}
🎯 OTE Zonu: {s['ote_zone']}
💼 İşlem: 10.000 USDT (2x)
 
⚡ Binance'e git, emri koy!"""
 
def should_notify(symbol, key, cooldown=14400):
    """4 saatte bir aynı sinyali tekrarlama"""
    k = f"{symbol}_{key}"
    now = time.time()
    if k in last_signals and now - last_signals[k] < cooldown:
        return False
    last_signals[k] = now
    return True
 
def run_analysis():
    """Tüm sembolleri analiz et"""
    print(f"\n🔍 Analiz: {datetime.now().strftime('%H:%M:%S')}")
 
    for name, yf_symbol in SYMBOLS.items():
        result = analyze_symbol(name, yf_symbol)
        if result is None:
            continue
 
        print(f"  {name}: ${result['price']} | RSI 4H:{result['rsi_4h']} 1H:{result['rsi_1h']} 15m:{result['rsi_15m']}")
 
        if result['trade_signal']:
            if should_notify(name, "LONG"):
                msg = format_signal(result)
                send_telegram(msg)
                print(f"  📱 {name} sinyal gönderildi!")
 
        for alert in result['alerts']:
            if should_notify(name, alert[:15]):
                send_telegram(f"⚠️ <b>{name}</b> — {alert}\nFiyat: {result['price']}")
 
        time.sleep(2)
 
def main():
    print("🚀 Trader Bot başlatılıyor...")
 
    # Başlangıç mesajı
    send_telegram("""🤖 <b>Trader Asistanı Aktif!</b>
 
₿ BTC | 🥈 XAG | 🥇 XAU izleniyor
📊 ICT / OTE / SMC metodolojisi
⏰ Her 15 dakikada analiz yapılıyor
🔔 Fırsat görünce haber vereceğim!
 
Hayırlı kazançlar! 🎯""")
 
    last_status_day = -1
 
    while True:
        try:
            now = datetime.now()
 
            # Sabah özeti
            if now.hour == 8 and now.minute < 15 and now.day != last_status_day:
                send_telegram(f"🌅 <b>Günaydın!</b>\nSistem aktif ✅\n⏰ {now.strftime('%d.%m.%Y %H:%M')}")
                last_status_day = now.day
 
            run_analysis()
            print(f"⏳ 15 dakika bekleniyor...")
            time.sleep(CHECK_INTERVAL)
 
        except KeyboardInterrupt:
            send_telegram("🔴 Bot durduruldu.")
            break
        except Exception as e:
            print(f"❌ Hata: {e}")
            time.sleep(60)
 
if __name__ == "__main__":
    main()
