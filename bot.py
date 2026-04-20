import os
import requests
import pandas as pd
import numpy as np
import time
from datetime import datetime
import pytz
import yfinance as yf
 
# === KONFİGÜRASYON ===
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "8667342978:AAE-1qoJY3nRHaelNqEbgDeV9j2pcLFgD10")
CHAT_ID = int(os.environ.get("CHAT_ID", "1885325032"))
TZ = pytz.timezone('Europe/Istanbul')
 
SYMBOLS = {
    "BTC": "BTC-USD",
    "XAG": "SI=F",
    "XAU": "GC=F",
}
 
MIN_RR = 1.5
CHECK_INTERVAL = 900  # 15 dakika
last_signals = {}
 
# === PIYASA SAATLERİ (Türkiye) ===
MARKET_SESSIONS = {
    "Asya":    {"start": 2,  "end": 9},
    "Avrupa":  {"start": 10, "end": 16},
    "ABD":     {"start": 16, "end": 23},
}
 
# === MAKRO HABER SAATLERİ (Türkiye) ===
HIGH_IMPACT_HOURS = [15, 16, 17, 18]  # Fed, NFP, CPI genelde bu saatlerde
 
# === TATIL GÜNLERİ (ay, gün) ===
HOLIDAYS = [
    (1, 1),   # Yılbaşı
    (4, 23),  # 23 Nisan
    (5, 1),   # İşçi Bayramı
    (5, 19),  # 19 Mayıs
    (7, 15),  # 15 Temmuz
    (8, 30),  # 30 Ağustos
    (10, 29), # Cumhuriyet Bayramı
]
 
def now_tr():
    """Türkiye saatini döndür"""
    return datetime.now(TZ)
 
def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        r = requests.post(url, json=payload, timeout=15)
        result = r.json()
        if result.get('ok'):
            print(f"✅ Telegram gönderildi")
        else:
            print(f"❌ Telegram hata: {result}")
    except Exception as e:
        print(f"❌ Telegram exception: {e}")
 
def get_market_session():
    """Hangi seansta olduğumuzu bul"""
    hour = now_tr().hour
    for session, times in MARKET_SESSIONS.items():
        if times["start"] <= hour < times["end"]:
            return session
    return "Sakin"
 
def is_weekend():
    """Haftasonu kontrolü"""
    return now_tr().weekday() >= 5  # 5=Cumartesi, 6=Pazar
 
def is_holiday():
    """Tatil günü kontrolü"""
    today = now_tr()
    return (today.month, today.day) in HOLIDAYS
 
def is_high_impact_news_time():
    """Yüksek etkili haber saati mi?"""
    return now_tr().hour in HIGH_IMPACT_HOURS
 
def is_friday_close():
    """Cuma kapanışı mı? (22:00+)"""
    now = now_tr()
    return now.weekday() == 4 and now.hour >= 20
 
def get_market_warnings():
    """Piyasa uyarılarını topla"""
    warnings = []
    
    if is_weekend():
        warnings.append("🚫 HAFTASONU — Düşük hacim, sahte hareketler olabilir!")
    
    if is_holiday():
        warnings.append("🚫 TATİL GÜNÜ — Piyasalar hacimsiz!")
    
    if is_friday_close():
        warnings.append("⚠️ CUMA KAPANIŞI — Haftasonu pozisyon taşıma riski!")
    
    if is_high_impact_news_time():
        warnings.append("📰 MAKRO HABER SAATİ — Fed/NFP/CPI açıklanabilir, dikkat!")
    
    session = get_market_session()
    if session == "Sakin":
        warnings.append("😴 SAKİN SEANS — Düşük hacim, acele etme!")
    
    return warnings, session
 
def get_data(symbol, interval, period):
    """yfinance ile veri çek"""
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period=period, interval=interval)
        if df is not None and len(df) > 20:
            df.columns = [c.lower() for c in df.columns]
            print(f"✅ {symbol} veri alındı ({len(df)} mum)")
            return df
        print(f"⚠️ {symbol} yeterli veri yok")
        return None
    except Exception as e:
        print(f"❌ {symbol} veri hata: {e}")
        return None
 
def calculate_rsi(closes, period=14):
    delta = closes.diff()
    gain = delta.where(delta > 0, 0).rolling(window=period).mean()
    loss = -delta.where(delta < 0, 0).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))
 
def calculate_ote(swing_low, swing_high):
    diff = swing_high - swing_low
    return {
        "upper": swing_high - diff * 0.62,
        "ideal": swing_high - diff * 0.705,
        "lower": swing_high - diff * 0.79,
    }
 
def analyze_volume(df):
    """Hacim analizi"""
    if 'volume' not in df.columns or df['volume'].sum() == 0:
        return None, "Hacim verisi yok"
    
    avg_volume = df['volume'].tail(20).mean()
    current_volume = df['volume'].iloc[-1]
    volume_ratio = current_volume / avg_volume if avg_volume > 0 else 0
    
    if volume_ratio > 1.5:
        return volume_ratio, "🔥 Yüksek hacim — Güçlü hareket!"
    elif volume_ratio > 1.0:
        return volume_ratio, "✅ Normal hacim"
    else:
        return volume_ratio, "⚠️ Düşük hacim — Dikkatli ol!"
 
def get_entry_decision(rsi_4h, rsi_1h, rsi_15m, in_ote, volume_comment, warnings):
    """Gir/Bekle/Kovalama kararı"""
    
    # Kovalama kontrolü
    if rsi_4h > 70 and rsi_1h > 70:
        return "🚫 KOVALAMA — RSI aşırı alımda, giriş yapma!"
    
    # Haftasonu/tatil kontrolü
    if is_weekend() or is_holiday():
        return "🚫 BEKLE — Haftasonu/tatil, piyasa hacimsiz!"
    
    # Cuma kapanış kontrolü
    if is_friday_close():
        return "⚠️ BEKLE — Cuma kapanışı, haftasonu riski var!"
    
    # Haber saati kontrolü
    if is_high_impact_news_time():
        return "⚠️ DİKKAT — Makro haber saati, volatilite yüksek!"
    
    # OTE içinde mi?
    if not in_ote:
        return "⏳ BEKLE — Fiyat OTE zonunda değil, bekliyoruz!"
    
    # Düşük hacim kontrolü
    if "Düşük hacim" in str(volume_comment):
        return "⚠️ DİKKAT — OTE'de ama hacim düşük, onay bekle!"
    
    # RSI uygun mu?
    if rsi_4h < 65 and rsi_1h < 60:
        session = get_market_session()
        if session in ["Avrupa", "ABD"]:
            return "✅ GİR — OTE zonu + RSI uygun + Aktif seans!"
        else:
            return "🟡 GİREBİLİRSİN — OTE uygun ama seans sakin, dikkatli ol!"
    
    return "⏳ BEKLE — Koşullar henüz tam değil"
 
def analyze_symbol(name, yf_symbol):
    try:
        df_4h = get_data(yf_symbol, "1h", "30d")
        df_1h = get_data(yf_symbol, "1h", "7d")
        df_15m = get_data(yf_symbol, "15m", "2d")
 
        if df_4h is None or df_1h is None or df_15m is None:
            return None
 
        rsi_4h = calculate_rsi(df_4h['close']).iloc[-1]
        rsi_1h = calculate_rsi(df_1h['close']).iloc[-1]
        rsi_15m = calculate_rsi(df_15m['close']).iloc[-1]
        current_price = df_15m['close'].iloc[-1]
 
        # Swing noktaları
        swing_high = df_4h['high'].tail(60).max()
        swing_low = df_4h['low'].tail(60).min()
        ote = calculate_ote(swing_low, swing_high)
 
        # OTE içinde mi?
        in_ote = ote['lower'] <= current_price <= ote['upper']
 
        # Hacim analizi
        volume_ratio, volume_comment = analyze_volume(df_15m)
 
        # Piyasa uyarıları
        warnings, session = get_market_warnings()
 
        # Karar
        decision = get_entry_decision(
            rsi_4h, rsi_1h, rsi_15m,
            in_ote, volume_comment, warnings
        )
 
        trade_signal = None
 
        # Sadece GİR kararında sinyal oluştur
        if "✅ GİR" in decision or "🟡 GİREBİLİRSİN" in decision:
            sl = swing_low * 0.994
            tp1 = swing_high * 1.01
            tp2 = swing_high * 1.025
            risk = abs(current_price - sl)
            reward = abs(tp1 - current_price)
            rr = reward / risk if risk > 0 else 0
 
            if rr >= MIN_RR:
                trade_signal = {
                    "entry": round(current_price, 2),
                    "sl": round(sl, 2),
                    "tp1": round(tp1, 2),
                    "tp2": round(tp2, 2),
                    "rr": round(rr, 2),
                }
 
        return {
            "symbol": name,
            "price": round(current_price, 2),
            "rsi_4h": round(rsi_4h, 1),
            "rsi_1h": round(rsi_1h, 1),
            "rsi_15m": round(rsi_15m, 1),
            "in_ote": in_ote,
            "ote_zone": f"{round(ote['lower'],2)} - {round(ote['upper'],2)}",
            "swing_low": round(swing_low, 2),
            "swing_high": round(swing_high, 2),
            "volume_comment": volume_comment,
            "volume_ratio": round(volume_ratio, 2) if volume_ratio else 0,
            "warnings": warnings,
            "session": session,
            "decision": decision,
            "trade_signal": trade_signal,
        }
 
    except Exception as e:
        print(f"❌ {name} analiz hata: {e}")
        return None
 
def format_signal_message(s):
    emoji_map = {"BTC": "₿", "XAG": "🥈", "XAU": "🥇"}
    emoji = emoji_map.get(s['symbol'], "📊")
    t = s['trade_signal']
    now = now_tr().strftime('%d.%m.%Y %H:%M')
 
    warnings_text = ""
    if s['warnings']:
        warnings_text = "\n" + "\n".join(s['warnings']) + "\n"
 
    return f"""🚨 <b>GİRİŞ FIRSATI!</b> 🚨
 
{emoji} <b>{s['symbol']}/USDT</b>
⏰ {now} | 📍 {s['session']} Seansi
 
💰 <b>Fiyat:</b> {s['price']}
🎯 <b>Karar:</b> {s['decision']}
 
📍 <b>Entry:</b> {t['entry']}
🛑 <b>Stop Loss:</b> {t['sl']}
✅ <b>TP1:</b> {t['tp1']}
✅ <b>TP2:</b> {t['tp2']}
📊 <b>R:R:</b> {t['rr']}:1
 
📈 <b>RSI:</b> 4H:{s['rsi_4h']} | 1H:{s['rsi_1h']} | 15D:{s['rsi_15m']}
📦 <b>Hacim:</b> {s['volume_comment']}
🎯 <b>OTE Zonu:</b> {s['ote_zone']}
💼 <b>İşlem:</b> 10.000 USDT (2x){warnings_text}
 
⚡ Binance'e git, emri koy!"""
 
def format_warning_message(s):
    """Sadece uyarı mesajı"""
    emoji_map = {"BTC": "₿", "XAG": "🥈", "XAU": "🥇"}
    emoji = emoji_map.get(s['symbol'], "📊")
    now = now_tr().strftime('%d.%m.%Y %H:%M')
    warnings_text = "\n".join(s['warnings'])
 
    return f"""⚠️ <b>{emoji} {s['symbol']} UYARI</b>
⏰ {now}
 
💰 Fiyat: {s['price']}
📊 RSI: 4H:{s['rsi_4h']} | 1H:{s['rsi_1h']}
🎯 Karar: {s['decision']}
 
{warnings_text}"""
 
def should_notify(symbol, key, cooldown=14400):
    k = f"{symbol}_{key}"
    now = time.time()
    if k in last_signals and now - last_signals[k] < cooldown:
        return False
    last_signals[k] = now
    return True
 
def run_analysis():
    print(f"\n🔍 Analiz: {now_tr().strftime('%H:%M:%S')} | Seans: {get_market_session()}")
 
    for name, yf_symbol in SYMBOLS.items():
        result = analyze_symbol(name, yf_symbol)
        if result is None:
            continue
 
        print(f"  {name}: ${result['price']} | RSI 4H:{result['rsi_4h']} 1H:{result['rsi_1h']} | {result['decision'][:30]}")
 
        # Giriş sinyali
        if result['trade_signal']:
            if should_notify(name, "SIGNAL"):
                send_telegram(format_signal_message(result))
                print(f"  📱 {name} sinyal gönderildi!")
 
        # Önemli uyarı varsa bildir (haber saati, haftasonu vs)
        elif result['warnings'] and result['in_ote']:
            if should_notify(name, "WARNING", cooldown=7200):
                send_telegram(format_warning_message(result))
 
        time.sleep(2)
 
def send_morning_summary():
    """Sabah özeti"""
    warnings, session = get_market_warnings()
    warning_text = "\n".join(warnings) if warnings else "✅ Normal piyasa koşulları"
    
    send_telegram(f"""🌅 <b>Günaydın! Günlük Özet</b>
⏰ {now_tr().strftime('%d.%m.%Y %H:%M')}
📍 Aktif Seans: {session}
 
{warning_text}
 
₿ BTC | 🥈 XAG | 🥇 XAU izleniyor
🔍 Analiz her 15 dakikada devam ediyor!""")
 
def main():
    print("🚀 Trader Bot v2.0 başlatılıyor...")
    
    send_telegram(f"""🤖 <b>Trader Asistanı v2.0 Aktif!</b>
 
₿ BTC | 🥈 XAG | 🥇 XAU izleniyor
📊 ICT / OTE / SMC metodolojisi
⏰ Her 15 dakikada analiz
 
✅ Yeni özellikler:
• Türkiye saati
• Haftasonu/tatil uyarısı
• Piyasa seans takibi
• Hacim analizi
• Makro haber saati uyarısı
• Gir/Bekle/Kovalama kararı
 
Hayırlı kazançlar! 🎯
{now_tr().strftime('%d.%m.%Y %H:%M')}""")
 
    last_status_day = -1
 
    while True:
        try:
            now = now_tr()
 
            # Sabah 08:00 özeti
            if now.hour == 8 and now.minute < 15 and now.day != last_status_day:
                send_morning_summary()
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
 
