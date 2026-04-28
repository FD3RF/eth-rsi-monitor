import time
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timezone

# 配置信息
SYMBOL = "ETH_USDT"
BARK_URL = "https://api.day.app/gbRTde9uu3C8AwZBqorEj8/时效性通知?level=timeSensitive"
INTERVALS = ["15m", "1h", "4h"]
RSI_PERIOD = 14
THRESHOLDS_LOW = [30, 20, 10]
THRESHOLDS_HIGH = [70, 80, 90]

def calculate_rsi(prices, period=14):
    if len(prices) < period + 1:
        return None
    deltas = np.diff(prices)
    seed = deltas[:period+1]
    up = seed[seed >= 0].sum() / period
    down = -seed[seed < 0].sum() / period
    if down == 0: return 100
    rs = up / down
    rsi = np.zeros_like(prices)
    rsi[:period] = 100. - 100. / (1. + rs)

    for i in range(period, len(prices)):
        delta = deltas[i - 1]
        upval = delta if delta > 0 else 0.
        downval = -delta if delta < 0 else 0.
        up = (up * (period - 1) + upval) / period
        down = (down * (period - 1) + downval) / period
        if down == 0:
            rsi[i] = 100
        else:
            rs = up / down
            rsi[i] = 100. - 100. / (1. + rs)
    return rsi

def get_gate_data(symbol, interval):
    # 获取K线数据
    kline_url = "https://api.gateio.ws/api/v4/spot/candlesticks"
    params = {"currency_pair": symbol, "interval": interval, "limit": 100}
    
    # 获取24h涨跌数据
    ticker_url = "https://api.gateio.ws/api/v4/spot/tickers"
    ticker_params = {"currency_pair": symbol}
    
    try:
        k_resp = requests.get(kline_url, params=params, timeout=10).json()
        t_resp = requests.get(ticker_url, params=ticker_params, timeout=10).json()
        
        closes = [float(item[2]) for item in k_resp]
        times = [int(item[0]) for item in k_resp]
        change_24h = float(t_resp[0]['change_percentage']) if t_resp else 0.0
        
        return {
            "closes": closes,
            "times": times,
            "change_24h": change_24h,
            "current_price": closes[-1]
        }
    except Exception as e:
        print(f"获取数据失败: {e}")
        return None

def send_bark_notification(title, body):
    import urllib.parse
    safe_title = urllib.parse.quote(title)
    safe_body = urllib.parse.quote(body)
    full_url = f"{BARK_URL}&title={safe_title}&body={safe_body}"
    try:
        requests.get(full_url, timeout=10)
    except Exception as e:
        print(f"推送失败: {e}")

def check_and_notify():
    for interval in INTERVALS:
        data = get_gate_data(SYMBOL, interval)
        if not data: continue
        
        rsi_series = calculate_rsi(data['closes'], RSI_PERIOD)
        if rsi_series is None: continue
        
        current_rsi = rsi_series[-1]
        prev_rsi = rsi_series[-2]
        trend = "向上 ↑" if current_rsi > prev_rsi else "向下 ↓"
        trend_icon = "🚨" if current_rsi > 70 else "⚠️" if current_rsi < 30 else ""
        
        if not trend_icon: continue # 未触发任何阈值
        
        # 检查触发档位
        low_checks = [f"<{t} {'✅' if current_rsi < t else '❌'}" for t in [30, 20, 10]]
        high_checks = [f">{t} {'✅' if current_rsi > t else '❌'}" for t in [70, 80, 90]]
        
        is_low = any(current_rsi < t for t in THRESHOLDS_LOW)
        is_high = any(current_rsi > t for t in THRESHOLDS_HIGH)
        
        if is_low or is_high:
            type_str = "超卖" if is_low else "超买"
            op = "<" if is_low else ">"
            target_t = next((t for t in (THRESHOLDS_LOW if is_low else THRESHOLDS_HIGH[::-1]) if (current_rsi < t if is_low else current_rsi > t)), 30 if is_low else 70)
            
            title = f"{trend_icon} ETH {interval} RSI {type_str} {op} {target_t}"
            
            # 格式化时间
            last_k_time = datetime.fromtimestamp(data['times'][-1], tz=timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
            
            body = (
                f"价格: {data['current_price']:.2f} USDT\n"
                f"RSI(14): {current_rsi:.2f}（{trend}，较上一根 {prev_rsi:.2f}）\n"
                f"触发档位: {' '.join(low_checks if is_low else high_checks)}\n"
                f"所在K线: {last_k_time} 收盘\n"
                f"24h 涨跌: {data['change_24h']:+.2f}%\n"
                f"数据源: Gate.io ETH_USDT 永续"
            )
            
            print(f"触发通知:\n{title}\n{body}")
            send_bark_notification(title, body)

if __name__ == "__main__":
    check_and_notify()
