import os
import requests
import pandas as pd
import numpy as np
import time
from datetime import datetime
import pytz
import yfinance as yf

# === KONFİGÜRASYON ===
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
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

# === PIYASA SAATLERİ ===
MARKET_SESSIONS = {
    "Asya":   {"start": 2,  "end": 9},
    "Avrupa": {"start": 10, "end": 16},
    "ABD":    {"start": 16, "end": 23},
}

HIGH_IMPACT_HOURS = [15, 16, 17, 18]

HOLIDAYS = [
    (1, 1), (4, 23), (5, 1), (5, 19),
    (7, 15), (8, 30), (10, 29),
]

def now_tr():
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
    hour = now_tr().hour
    for session, times in MARKET_SESSIONS.items():
        if times["start"] <= hour < times["end"]:
            return session
    return "Sakin"

def is_weekend():
    return now_tr().weekday() >= 5

def is_holiday():
    today = now_tr()
    return (today.month, today.day) in HOLIDAYS

def is_high_impact_news_time():
    return now_tr().hour in HIGH_IMPACT_HOURS

def is_friday_close():
    now = now_tr()
    return now.weekday() == 4 and now.hour >= 20

def get_market_warnings():
    warnings = []
    if is_weekend():
        warnings.append("🚫 HAFTASONU — Düşük hacim, sahte hareketler!")
    if is_holiday():
        warnings.append("🚫 TATİL GÜNÜ — Piyasalar hacimsiz!")
    if is_friday_close():
        warnings.append("⚠️ CUMA KAPANIŞI — Haftasonu pozisyon riski!")
    if is_high_impact_news_time():
        warnings.append("📰 MAKRO HABER SAATİ — Fed/NFP/CPI dikkat!")
    session = get_market_session()
    if session == "Sakin":
        warnings.append("😴 SAKİN SEANS — Düşük hacim, acele etme!")
    return warnings, session

def get_data(symbol, interval, period):
    """Veri çek"""
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period=period, interval=interval)
        if df is not None and len(df) > 20:
            df.columns = [c.lower() for c in df.columns]
            print(f"✅ {symbol} [{interval}] {len(df)} mum")
            return df
        print(f"⚠️ {symbol} [{interval}] yeterli veri yok")
        return None
    except Exception as e:
        print(f"❌ {symbol} [{interval}] hata: {e}")
        return None

def calculate_rsi(closes, period=14):
    delta = closes.diff()
    gain = delta.where(delta > 0, 0).rolling(window=period).mean()
    loss = -delta.where(delta < 0, 0).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def resample_to_4h(df_1h):
    """1H veriyi 4H'a dönüştür"""
    try:
        df = df_1h.copy()
        df.index = pd.to_datetime(df.index)
        df_4h = df.resample('4h').agg({
            'open': 'first',
            'high': 'max',
            'low': 'min',
            'close': 'last',
            'volume': 'sum'
        }).dropna()
        return df_4h
    except Exception as e:
        print(f"❌ 4H resample hata: {e}")
        return None

def detect_market_structure(df, lookback=20):
    """
    Market structure tespiti — HH/HL (bullish) veya LH/LL (bearish)
    Son lookback mumu içinde swing high/low'ları bulur
    """
    if df is None or len(df) < lookback + 5:
        return "belirsiz", None, None

    highs = df['high'].values
    lows = df['low'].values

    # Pivot high/low bul (5 mum penceresinde)
    pivot_highs = []
    pivot_lows = []
    window = 3

    for i in range(window, len(df) - window):
        if all(highs[i] >= highs[i-j] for j in range(1, window+1)) and \
           all(highs[i] >= highs[i+j] for j in range(1, window+1)):
            pivot_highs.append((i, highs[i]))
        if all(lows[i] <= lows[i-j] for j in range(1, window+1)) and \
           all(lows[i] <= lows[i+j] for j in range(1, window+1)):
            pivot_lows.append((i, lows[i]))

    # Son 2 pivot high ve low'a bak
    if len(pivot_highs) < 2 or len(pivot_lows) < 2:
        return "belirsiz", None, None

    last_hh = pivot_highs[-1][1]
    prev_hh = pivot_highs[-2][1]
    last_ll = pivot_lows[-1][1]
    prev_ll = pivot_lows[-2][1]

    # Yapı tespiti
    if last_hh > prev_hh and last_ll > prev_ll:
        structure = "bullish"  # HH + HL
    elif last_hh < prev_hh and last_ll < prev_ll:
        structure = "bearish"  # LH + LL
    else:
        structure = "belirsiz"

    # Son swing high ve low
    swing_high = pivot_highs[-1][1]
    swing_low = pivot_lows[-1][1]

    return structure, swing_high, swing_low

def calculate_ote(swing_low, swing_high, direction="long"):
    """OTE zonu — 0.62-0.79 Fibonacci"""
    diff = swing_high - swing_low
    if direction == "long":
        # Yukarı trendde geri çekilme — swing high'dan aşağı ölçüyoruz
        return {
            "upper": swing_high - diff * 0.62,
            "ideal": swing_high - diff * 0.705,
            "lower": swing_high - diff * 0.79,
        }
    else:
        # Aşağı trendde geri çekilme — swing low'dan yukarı ölçüyoruz
        return {
            "upper": swing_low + diff * 0.79,
            "ideal": swing_low + diff * 0.705,
            "lower": swing_low + diff * 0.62,
        }

def get_structural_sl(structure, swing_high, swing_low, current_price, buffer=0.003):
    """Yapısal SL — swing noktasının altı/üstü"""
    if structure == "bullish":
        # Long işlem — swing low'un altı
        sl = swing_low * (1 - buffer)
    elif structure == "bearish":
        # Short işlem — swing high'ın üstü
        sl = swing_high * (1 + buffer)
    else:
        sl = None
    return round(sl, 2) if sl else None

def analyze_volume(df):
    """Hacim analizi — son 20 mumdaki ortalamaya göre"""
    if 'volume' not in df.columns or df['volume'].sum() == 0:
        return None, "Hacim verisi yok"

    avg_volume = df['volume'].tail(20).mean()
    current_volume = df['volume'].iloc[-1]
    volume_ratio = current_volume / avg_volume if avg_volume > 0 else 0

    if volume_ratio > 2.0:
        return volume_ratio, "🔥 Çok yüksek hacim — Güçlü hareket!"
    elif volume_ratio > 1.5:
        return volume_ratio, "✅ Yüksek hacim — Güvenilir sinyal"
    elif volume_ratio > 1.0:
        return volume_ratio, "✅ Normal hacim"
    else:
        return volume_ratio, "⚠️ Düşük hacim — Dikkatli ol!"

def analyze_candle_structure(df):
    """
    Son mumun yapısını yorumla:
    - Gövde büyüklüğü
    - Fitil oranı
    - Hacimle ilişkisi
    """
    if df is None or len(df) < 2:
        return "Yetersiz veri"

    last = df.iloc[-1]
    body = abs(last['close'] - last['open'])
    total_range = last['high'] - last['low']
    
    if total_range == 0:
        return "Doji"

    body_ratio = body / total_range
    upper_wick = last['high'] - max(last['close'], last['open'])
    lower_wick = min(last['close'], last['open']) - last['low']

    # Ortalama gövde (son 20 mum)
    avg_body = abs(df['close'] - df['open']).tail(20).mean()
    is_big_candle = body > avg_body * 1.8

    interpretation = []

    if body_ratio > 0.7 and is_big_candle:
        if last['close'] > last['open']:
            interpretation.append("💚 Güçlü alım mumu — Momentum yukarı")
        else:
            interpretation.append("🔴 Güçlü satış mumu — Momentum aşağı")
    elif body_ratio < 0.3:
        if upper_wick > lower_wick * 2:
            interpretation.append("🕯️ Üst fitil baskın — Satış baskısı var")
        elif lower_wick > upper_wick * 2:
            interpretation.append("🕯️ Alt fitil baskın — Alış baskısı var")
        else:
            interpretation.append("🕯️ Doji/Belirsizlik — Karar anı")

    return " | ".join(interpretation) if interpretation else "Normal mum"

def get_entry_decision(structure, rsi_4h, rsi_1h, rsi_15m, in_ote, volume_comment, warnings, current_price, ote):
    """
    Giriş kararı — yapı + RSI + OTE + hacim + piyasa koşulları
    """
    # Acil çıkış koşulları
    if is_weekend() or is_holiday():
        return "🚫 BEKLE — Haftasonu/tatil!"
    if is_friday_close():
        return "⚠️ BEKLE — Cuma kapanışı riski!"
    if is_high_impact_news_time():
        return "⚠️ DİKKAT — Makro haber saati, volatilite yüksek!"

    # Yapı belirsizse girme
    if structure == "belirsiz":
        return "⏳ BEKLE — Market structure belirsiz, yön netleşmedi!"

    # RSI aşırı alım/satım kontrolü
    if structure == "bullish" and rsi_4h > 70:
        return "🚫 KOVALAMA — RSI aşırı alımda, giriş yapma!"
    if structure == "bearish" and rsi_4h < 30:
        return "🚫 KOVALAMA — RSI aşırı satımda, giriş yapma!"

    # OTE kontrolü
    if not in_ote:
        return "⏳ BEKLE — Fiyat OTE zonunda değil!"

    # Düşük hacim uyarısı
    if "Düşük hacim" in str(volume_comment):
        return "⚠️ DİKKAT — OTE'de ama hacim düşük, onay bekle!"

    # Aktif seans + RSI uygun
    session = get_market_session()
    if structure == "bullish" and rsi_4h < 65 and rsi_1h < 60:
        if session in ["Avrupa", "ABD"]:
            return "✅ LONG GİR — Bullish yapı + OTE + RSI uygun + Aktif seans!"
        else:
            return "🟡 LONG GİREBİLİRSİN — Bullish + OTE uygun, seans sakin dikkat!"
    
    if structure == "bearish" and rsi_4h > 35 and rsi_1h > 40:
        if session in ["Avrupa", "ABD"]:
            return "✅ SHORT GİR — Bearish yapı + OTE + RSI uygun + Aktif seans!"
        else:
            return "🟡 SHORT GİREBİLİRSİN — Bearish + OTE uygun, seans sakin dikkat!"

    return "⏳ BEKLE — Koşullar henüz tam değil"

def analyze_symbol(name, yf_symbol):
    try:
        # === VERİ ÇEK ===
        # 1H veriyi çek, 4H'a dönüştür
        df_1h_raw = get_data(yf_symbol, "1h", "60d")
        df_1h = get_data(yf_symbol, "1h", "7d")
        df_15m = get_data(yf_symbol, "15m", "2d")

        if df_1h_raw is None or df_1h is None or df_15m is None:
            return None

        # 4H oluştur
        df_4h = resample_to_4h(df_1h_raw)
        if df_4h is None or len(df_4h) < 20:
            return None

        # === RSI HESAPLA ===
        rsi_4h = calculate_rsi(df_4h['close']).iloc[-1]
        rsi_1h = calculate_rsi(df_1h['close']).iloc[-1]
        rsi_15m = calculate_rsi(df_15m['close']).iloc[-1]
        current_price = df_15m['close'].iloc[-1]

        # === MARKET STRUCTURE (4H'da bak) ===
        structure, swing_high_4h, swing_low_4h = detect_market_structure(df_4h, lookback=30)

        if swing_high_4h is None or swing_low_4h is None:
            print(f"⚠️ {name} swing noktaları bulunamadı")
            return None

        # === OTE ZONU ===
        direction = "long" if structure == "bullish" else "short"
        ote = calculate_ote(swing_low_4h, swing_high_4h, direction)

        # OTE içinde mi?
        in_ote = ote['lower'] <= current_price <= ote['upper']

        # === YAPISAL SL ===
        structural_sl = get_structural_sl(structure, swing_high_4h, swing_low_4h, current_price)

        # === HACİM ANALİZİ ===
        volume_ratio, volume_comment = analyze_volume(df_15m)

        # === MUM ANALİZİ ===
        candle_comment = analyze_candle_structure(df_15m)

        # === PİYASA UYARILARI ===
        warnings, session = get_market_warnings()

        # === KARAR ===
        decision = get_entry_decision(
            structure, rsi_4h, rsi_1h, rsi_15m,
            in_ote, volume_comment, warnings,
            current_price, ote
        )

        # === SİNYAL OLUŞTUR ===
        trade_signal = None

        if ("✅ LONG GİR" in decision or "🟡 LONG GİREBİLİRSİN" in decision or
            "✅ SHORT GİR" in decision or "🟡 SHORT GİREBİLİRSİN" in decision):
            
            if structural_sl:
                risk = abs(current_price - structural_sl)
                
                if structure == "bullish":
                    # Long — TP swing high'ın üstü
                    tp1 = swing_high_4h * 1.008
                    tp2 = swing_high_4h * 1.02
                else:
                    # Short — TP swing low'un altı
                    tp1 = swing_low_4h * 0.992
                    tp2 = swing_low_4h * 0.98

                reward = abs(tp1 - current_price)
                rr = reward / risk if risk > 0 else 0

                if rr >= MIN_RR:
                    trade_signal = {
                        "direction": "LONG" if structure == "bullish" else "SHORT",
                        "entry": round(current_price, 4),
                        "sl": structural_sl,
                        "tp1": round(tp1, 4),
                        "tp2": round(tp2, 4),
                        "rr": round(rr, 2),
                        "risk_pct": round((risk / current_price) * 100, 2),
                    }
                else:
                    decision += f" (R:R {round(rr,1)}:1 — yetersiz, min {MIN_RR}:1)"

        return {
            "symbol": name,
            "price": round(current_price, 4),
            "rsi_4h": round(rsi_4h, 1),
            "rsi_1h": round(rsi_1h, 1),
            "rsi_15m": round(rsi_15m, 1),
            "structure": structure,
            "swing_high": round(swing_high_4h, 4),
            "swing_low": round(swing_low_4h, 4),
            "in_ote": in_ote,
            "ote_zone": f"{round(ote['lower'],2)} — {round(ote['upper'],2)}",
            "ote_ideal": round(ote['ideal'], 2),
            "structural_sl": structural_sl,
            "volume_comment": volume_comment,
            "volume_ratio": round(volume_ratio, 2) if volume_ratio else 0,
            "candle_comment": candle_comment,
            "warnings": warnings,
            "session": session,
            "decision": decision,
            "trade_signal": trade_signal,
        }

    except Exception as e:
        print(f"❌ {name} analiz hata: {e}")
        import traceback
        traceback.print_exc()
        return None

def format_signal_message(s):
    emoji_map = {"BTC": "₿", "XAG": "🥈", "XAU": "🥇"}
    emoji = emoji_map.get(s['symbol'], "📊")
    t = s['trade_signal']
    now = now_tr().strftime('%d.%m.%Y %H:%M')

    structure_emoji = "📈" if s['structure'] == "bullish" else "📉"
    direction_emoji = "🟢" if t['direction'] == "LONG" else "🔴"

    warnings_text = ""
    if s['warnings']:
        warnings_text = "\n⚠️ " + "\n⚠️ ".join(s['warnings'])

    return f"""🚨 <b>GİRİŞ FIRSATI!</b> 🚨

{emoji} <b>{s['symbol']}/USDT — {t['direction']}</b>
⏰ {now} | 📍 {s['session']} Seansi

💰 <b>Fiyat:</b> {s['price']}
{structure_emoji} <b>Yapı:</b> {s['structure'].upper()} (HH/HL zinciri)
🎯 <b>Karar:</b> {s['decision']}

{direction_emoji} <b>Entry:</b> {t['entry']}
🛑 <b>Stop Loss:</b> {t['sl']} (yapısal)
✅ <b>TP1:</b> {t['tp1']}
✅ <b>TP2:</b> {t['tp2']}
📊 <b>R:R:</b> {t['rr']}:1
⚡ <b>Risk:</b> %{t['risk_pct']}

📈 <b>RSI:</b> 4H:{s['rsi_4h']} | 1H:{s['rsi_1h']} | 15D:{s['rsi_15m']}
📦 <b>Hacim:</b> {s['volume_comment']}
🕯️ <b>Mum:</b> {s['candle_comment']}
🎯 <b>OTE Zonu:</b> {s['ote_zone']}
🎯 <b>OTE İdeal:</b> {s['ote_ideal']}
💼 <b>İşlem:</b> 10.000 USDT (2x kaldıraç){warnings_text}

⚡ Binance'e git, emri koy!"""

def format_warning_message(s):
    emoji_map = {"BTC": "₿", "XAG": "🥈", "XAU": "🥇"}
    emoji = emoji_map.get(s['symbol'], "📊")
    now = now_tr().strftime('%d.%m.%Y %H:%M')
    warnings_text = "\n".join(s['warnings'])
    structure_emoji = "📈" if s['structure'] == "bullish" else ("📉" if s['structure'] == "bearish" else "↔️")

    return f"""⚠️ <b>{emoji} {s['symbol']} UYARI</b>
⏰ {now}

💰 Fiyat: {s['price']}
{structure_emoji} Yapı: {s['structure'].upper()}
📊 RSI: 4H:{s['rsi_4h']} | 1H:{s['rsi_1h']} | 15D:{s['rsi_15m']}
🎯 Karar: {s['decision']}
🕯️ Mum: {s['candle_comment']}

{warnings_text}"""

def format_status_message(results):
    """15 dakikada bir kısa durum özeti"""
    now = now_tr().strftime('%H:%M')
    lines = [f"📊 <b>Durum — {now}</b>\n"]
    
    for s in results:
        if s is None:
            continue
        emoji_map = {"BTC": "₿", "XAG": "🥈", "XAU": "🥇"}
        emoji = emoji_map.get(s['symbol'], "📊")
        structure_emoji = "📈" if s['structure'] == "bullish" else ("📉" if s['structure'] == "bearish" else "↔️")
        ote_emoji = "🎯" if s['in_ote'] else "  "
        
        lines.append(
            f"{emoji} <b>{s['symbol']}</b>: {s['price']} | RSI4H:{s['rsi_4h']} "
            f"| {structure_emoji}{s['structure'][:4]} {ote_emoji}"
        )
    
    return "\n".join(lines)

def should_notify(symbol, key, cooldown=14400):
    k = f"{symbol}_{key}"
    now = time.time()
    if k in last_signals and now - last_signals[k] < cooldown:
        return False
    last_signals[k] = now
    return True

def run_analysis():
    print(f"\n🔍 Analiz: {now_tr().strftime('%H:%M:%S')} | Seans: {get_market_session()}")

    results = []
    signal_sent = False

    for name, yf_symbol in SYMBOLS.items():
        result = analyze_symbol(name, yf_symbol)
        results.append(result)
        
        if result is None:
            continue

        print(f"  {name}: {result['price']} | Yapı:{result['structure']} | RSI4H:{result['rsi_4h']} | OTE:{result['in_ote']} | {result['decision'][:40]}")

        # Giriş sinyali
        if result['trade_signal']:
            if should_notify(name, "SIGNAL"):
                send_telegram(format_signal_message(result))
                print(f"  📱 {name} GİRİŞ sinyali gönderildi!")
                signal_sent = True

        # Uyarı — OTE'deyse ve uyarı varsa
        elif result['warnings'] and result['in_ote']:
            if should_notify(name, "WARNING", cooldown=7200):
                send_telegram(format_warning_message(result))

        time.sleep(2)

    return results

def send_morning_summary():
    warnings, session = get_market_warnings()
    warning_text = "\n".join(warnings) if warnings else "✅ Normal piyasa koşulları"

    send_telegram(f"""🌅 <b>Günaydın! Günlük Özet</b>
⏰ {now_tr().strftime('%d.%m.%Y %H:%M')}
📍 Aktif Seans: {session}

{warning_text}

₿ BTC | 🥈 XAG | 🥇 XAU izleniyor
🔍 4H Market Structure + OTE + Yapısal SL aktif
📊 Her 15 dakikada analiz devam ediyor!""")

def main():
    print("🚀 Trader Bot v3.0 başlatılıyor...")

    send_telegram(f"""🤖 <b>Trader Asistanı v3.0 Aktif!</b>

₿ BTC | 🥈 XAG | 🥇 XAU izleniyor
📊 ICT / OTE / SMC / Market Structure

✅ Yenilikler v3.0:
• Gerçek 4H analizi (1H → 4H dönüşüm)
• Market Structure tespiti (HH/HL/LH/LL)
• Yapısal SL — swing noktaları bazlı
• Long/Short yönü otomatik
• Mum + Hacim birlikte analizi
• OTE trend yönüne göre hesaplanıyor

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
            print(f"⏳ {CHECK_INTERVAL//60} dakika bekleniyor...")
            time.sleep(CHECK_INTERVAL)

        except KeyboardInterrupt:
            send_telegram("🔴 Bot durduruldu.")
            break
        except Exception as e:
            print(f"❌ Ana hata: {e}")
            time.sleep(60)

if __name__ == "__main__":
    main()
