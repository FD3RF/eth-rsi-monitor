#!/usr/bin/env python3
"""在服务器上直接运行，测试 Bark 推送"""
import urllib.request
import urllib.parse

title = urllib.parse.quote("RSI监控测试推送")
body = urllib.parse.quote("部署成功！ETH RSI监控已上线\n阈值: 超卖≤10/20/30  超买≥70/80/90\n周期: 15m/1h/4h")
url = f"https://api.day.app/gbRTde9uu3C8AwZBqorEj8/{title}/{body}?level=timeSensitive"
print(f"URL: {url[:100]}...")
req = urllib.request.Request(url)
try:
    resp = urllib.request.urlopen(req, timeout=8)
    print(f"HTTP状态码: {resp.status}")
    print(f"响应: {resp.read().decode()}")
    print("✅ 推送成功！请检查手机")
except Exception as e:
    print(f"❌ 推送失败: {e}")
