#!/usr/bin/env python3
"""旺财早报 - 每日8:00自动推送"""
import requests, time, sys
sys.path.insert(0, "/root")
from tg_bot import get_eth_price, scan_liquidation, get_v8_stats, ask_agent

TG_TOKEN = "8640664409:AAHPZIZd1YGa6jCwXziM01qoJ0RJwCfG-LM"
TG_CHAT_ID = "8410098965"

def tg(t):
    for _ in range(3):
        try:
            r = requests.post("https://api.telegram.org/bot%s/sendMessage" % TG_TOKEN,
                json={"chat_id": TG_CHAT_ID, "text": t[:2000]}, timeout=10)
            if r.status_code == 200: return
        except:
            pass

def morning_brief():
    p,v,l,h = get_eth_price()
    liq = scan_liquidation()
    stats = get_v8_stats()
    
    change_24h = ((p - h) / h * 100) if h > 0 else 0
    change_dir = "涨" if p > l + (h-l)/2 else "跌"
    
    # Build compact prompt for DeepSeek
    prompt = "生成今日早报(8行内)。数据:ETH当前$%.2f,24h高$%.2f低$%.2f,偏%s。" % (p, h, l, change_dir)
    prompt += "清算:多$%.1fM空$%.1fM。" % (liq.get("total_long",0)/1e6, liq.get("total_short",0)/1e6)
    prompt += "V8:%s。" % stats
    prompt += "格式:今日概况(1行)+关键位(1行)+清算博弈(1行)+操作建议(1行)。结尾叫主人。"
    
    analysis = ask_agent(prompt)
    tg("--- 旺财早报 ---" + chr(10) + analysis)
    print("brief pushed")

if __name__ == "__main__":
    morning_brief()
