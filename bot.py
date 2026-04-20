import requests
import pandas as pd
import numpy as np
import time
import json
from datetime import datetime

# === KONFİGÜRASYON ===
TELEGRAM_TOKEN = "8463347837:AAExccjnipYt0Tvx2RurZM2GV4zF8YBUizQ"
CHAT_ID = "1885325032"
ANTHROPIC_API_KEY = ""  # Kullanıcı dolduracak

SYMBOLS = {
    "BTC": "BTCUSDT",
    "XAG": "XAGUSDT", 
    "XAU": "XAUUSDT"
}

LEVERAGE = 2
POSITION_SIZE = 10000  # USDT
MIN_RR = 1.5
CHECK_INTERVAL = 900  # 15 dakikada bir kontrol

# Son sinyal takibi (spam önleme)
last_signals = {}

def send_telegram(message):
    """Telegram'a mesaj gönder"""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    }
    try:
        response = requests.post(url, data=data, timeout=10)
        return response.json()
    except Exception as e:
        print(f"Telegram hata: {e}")

def get_binance_klines(symbol, interval, limit=100):
    """Binance'den mum verisi çek"""
    url = "https://api.binance.com/api/v3/klines"
    params = {
        "symbol": symbol,
        "interval": interval,
        "limit": limit
    }
    try:
        response = requests.get(url, params=params, timeout=10)
        data = response.json()
        if isinstance(data, list):
            df = pd.DataFrame(data, columns=[
                'timestamp', 'open', 'high', 'low', 'close', 'volume',
                'close_time', 'quote_volume', 'trades', 'taker_buy_base',
                'taker_buy_quote', 'ignore'
            ])
            for col in ['open', 'high', 'low', 'close', 'volume']:
                df[col] = pd.to_numeric(df[col])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            return df
        return None
    except Exception as e:
        print(f"Binance veri hata ({symbol}): {e}")
        return None

def calculate_rsi(closes, period=14):
    """RSI hesapla"""
    delta = closes.diff()
    gain = delta.where(delta > 0, 0).rolling(window=period).mean()
    loss = -delta.where(delta < 0, 0).rolling(window=period).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi

def calculate_ote_zones(swing_low, swing_high):
    """OTE zonlarını hesapla"""
    diff = swing_high - swing_low
    return {
        "equilibrium": swing_high - diff * 0.5,
        "ote_upper": swing_high - diff * 0.62,
        "ote_ideal": swing_high - diff * 0.705,
        "ote_lower": swing_high - diff * 0.79,
    }

def find_swing_points(df, lookback=20):
    """Swing high ve low bul"""
    highs = df['high'].rolling(lookback, center=True).max()
    lows = df['low'].rolling(lookback, center=True).min()
    
    swing_high = df['high'][df['high'] == highs].iloc[-1] if len(df['high'][df['high'] == highs]) > 0 else df['high'].max()
    swing_low = df['low'][df['low'] == lows].iloc[-1] if len(df['low'][df['low'] == lows]) > 0 else df['low'].min()
    
    return swing_low, swing_high

def analyze_market_structure(df_4h, df_1h, df_15m, symbol):
    """ICT/SMC/OTE metodolojisine göre analiz"""
    try:
        # RSI hesapla
        rsi_4h = calculate_rsi(df_4h['close']).iloc[-1]
        rsi_1h = calculate_rsi(df_1h['close']).iloc[-1]
        rsi_15m = calculate_rsi(df_15m['close']).iloc[-1]
        
        current_price = df_15m['close'].iloc[-1]
        
        # 4H yapısal analiz
        recent_4h = df_4h.tail(30)
        swing_low_4h, swing_high_4h = find_swing_points(recent_4h)
        
        # Trend belirleme (4H)
        ema_20 = df_4h['close'].ewm(span=20).mean().iloc[-1]
        ema_50 = df_4h['close'].ewm(span=50).mean().iloc[-1]
        
        bullish_4h = current_price > ema_20 and ema_20 > ema_50
        bearish_4h = current_price < ema_20 and ema_20 < ema_50
        
        # OTE hesapla
        ote = calculate_ote_zones(swing_low_4h, swing_high_4h)
        
        # Sinyaller
        signals = []
        trade_signal = None
        
        # LONG senaryosu
        if (ote['ote_lower'] <= current_price <= ote['ote_upper'] and
            rsi_4h < 65 and rsi_1h < 60 and rsi_15m < 55 and
            not bearish_4h):
            
            # SL hesapla (swing low altı)
            sl = swing_low_4h * 0.995
            tp1 = swing_high_4h * 1.005
            tp2 = swing_high_4h * 1.02
            
            risk = current_price - sl
            reward1 = tp1 - current_price
            rr1 = reward1 / risk if risk > 0 else 0
            
            if rr1 >= MIN_RR:
                trade_signal = {
                    "type": "LONG",
                    "entry": round(current_price, 2),
                    "sl": round(sl, 2),
                    "tp1": round(tp1, 2),
                    "tp2": round(tp2, 2),
                    "rr": round(rr1, 2),
                    "rsi_4h": round(rsi_4h, 1),
                    "rsi_1h": round(rsi_1h, 1),
                    "rsi_15m": round(rsi_15m, 1),
                    "ote_zone": f"{round(ote['ote_lower'],2)} - {round(ote['ote_upper'],2)}"
                }
        
        # Aşırı alım uyarısı
        if rsi_4h > 75 and rsi_1h > 75:
            signals.append("⚠️ AŞIRI ALIM — Yeni LONG açma!")
        
        # Aşırı satım fırsatı
        if rsi_4h < 35 and rsi_1h < 35:
            signals.append("⚡ AŞIRI SATIM — LONG fırsatı yaklaşıyor!")
        
        return {
            "symbol": symbol,
            "price": current_price,
            "rsi_4h": round(rsi_4h, 1),
            "rsi_1h": round(rsi_1h, 1),
            "rsi_15m": round(rsi_15m, 1),
            "trade_signal": trade_signal,
            "alerts": signals,
            "ote_zone": f"{round(ote['ote_lower'],2)} - {round(ote['ote_upper'],2)}",
            "swing_low": round(swing_low_4h, 2),
            "swing_high": round(swing_high_4h, 2)
        }
    
    except Exception as e:
        print(f"Analiz hata ({symbol}): {e}")
        return None

def format_signal_message(analysis):
    """Telegram mesajı formatla"""
    s = analysis
    symbol = s['symbol']
    
    emoji_map = {"BTC": "₿", "XAG": "🥈", "XAU": "🥇"}
    emoji = emoji_map.get(symbol, "📊")
    
    if s['trade_signal']:
        t = s['trade_signal']
        msg = f"""
🚨 <b>GİRİŞ FIRSATI!</b> 🚨

{emoji} <b>{symbol}/USDT</b>
⏰ {datetime.now().strftime('%d.%m.%Y %H:%M')}

💰 <b>Fiyat:</b> {s['price']}
🎯 <b>Sinyal:</b> {t['type']} (2x)

📍 <b>Entry:</b> {t['entry']}
🛑 <b>SL:</b> {t['sl']}
✅ <b>TP1:</b> {t['tp1']}
✅ <b>TP2:</b> {t['tp2']}
📊 <b>R:R:</b> {t['rr']}:1

📈 <b>RSI Durumu:</b>
  • 4H: {t['rsi_4h']}
  • 1H: {t['rsi_1h']}
  • 15D: {t['rsi_15m']}

🎯 <b>OTE Zonu:</b> {t['ote_zone']}
💼 <b>İşlem:</b> 10.000 USDT

⚡ Binance'e gir, emri koy!
"""
    elif s['alerts']:
        msg = f"""
⚠️ <b>{symbol}/USDT UYARI</b>
⏰ {datetime.now().strftime('%d.%m.%Y %H:%M')}

💰 Fiyat: {s['price']}
{chr(10).join(s['alerts'])}

RSI 4H: {s['rsi_4h']} | 1H: {s['rsi_1h']}
"""
    else:
        return None  # Bildirim gönderme
    
    return msg.strip()

def should_send_signal(symbol, signal_type):
    """Aynı sinyali tekrar gönderme (4 saat bekleme)"""
    key = f"{symbol}_{signal_type}"
    now = time.time()
    
    if key in last_signals:
        if now - last_signals[key] < 14400:  # 4 saat
            return False
    
    last_signals[key] = now
    return True

def run_analysis():
    """Tüm sembolleri analiz et"""
    print(f"\n🔍 Analiz başlıyor... {datetime.now().strftime('%H:%M:%S')}")
    
    for name, symbol in SYMBOLS.items():
        try:
            # Veri çek
            df_4h = get_binance_klines(symbol, "4h", 100)
            df_1h = get_binance_klines(symbol, "1h", 100)
            df_15m = get_binance_klines(symbol, "15m", 100)
            
            if df_4h is None or df_1h is None or df_15m is None:
                print(f"⚠️ {name} veri alınamadı")
                continue
            
            # Analiz
            analysis = analyze_market_structure(df_4h, df_1h, df_15m, name)
            
            if analysis is None:
                continue
            
            print(f"✅ {name}: {analysis['price']} | RSI 4H:{analysis['rsi_4h']} 1H:{analysis['rsi_1h']}")
            
            # Sinyal varsa gönder
            if analysis['trade_signal']:
                if should_send_signal(name, "LONG"):
                    msg = format_signal_message(analysis)
                    if msg:
                        send_telegram(msg)
                        print(f"📱 {name} sinyali gönderildi!")
            
            elif analysis['alerts']:
                if should_send_signal(name, "ALERT"):
                    msg = format_signal_message(analysis)
                    if msg:
                        send_telegram(msg)
            
            time.sleep(1)  # Rate limit
            
        except Exception as e:
            print(f"❌ {name} hata: {e}")

def send_status():
    """Sabah durum özeti gönder"""
    msg = f"""
🌅 <b>GÜNLÜK DURUM ÖZETİ</b>
⏰ {datetime.now().strftime('%d.%m.%Y %H:%M')}

Sistem aktif ve çalışıyor ✅
BTC, XAG, XAU izleniyor 👁️

ICT/SMC/OTE metodolojisi aktif 📊
Min R:R: 1.5:1 | Kaldıraç: 2x

Fırsat görünce haber vereceğim! 🎯
"""
    send_telegram(msg.strip())

def main():
    """Ana döngü"""
    print("🚀 Trader Bot başlatılıyor...")
    send_telegram("🤖 <b>Trader Asistanı Aktif!</b>\n\nBTC, XAG, XAU izleniyor...\nICT/OTE metodolojisi ile analiz yapılıyor.\n\nFırsat görünce haber vereceğim! 🎯")
    
    last_status_day = -1
    
    while True:
        try:
            # Sabah 08:00'de durum özeti
            now = datetime.now()
            if now.hour == 8 and now.minute < 15 and now.day != last_status_day:
                send_status()
                last_status_day = now.day
            
            # Analiz yap
            run_analysis()
            
            # Bekle
            print(f"⏳ {CHECK_INTERVAL//60} dakika bekleniyor...")
            time.sleep(CHECK_INTERVAL)
            
        except KeyboardInterrupt:
            print("Bot durduruldu.")
            send_telegram("🔴 Trader Bot durduruldu.")
            break
        except Exception as e:
            print(f"Ana döngü hata: {e}")
            time.sleep(60)

if __name__ == "__main__":
    main()
