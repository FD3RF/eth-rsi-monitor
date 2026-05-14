#!/usr/bin/env python3
"""
旺财交易大师 — 双Key自动切换
独立系统，不碰旺财决策层。
Telegram发 /master <问题> 触发分析。
"""
import json, requests, time, os, threading
from trading_master_tools import get_market_context, save_analysis, get_recent_analyses, search_web, save_memory, get_memory_context, write_script, run_script, list_scripts, generate_chart
from claude_wrapper import ask as call_llm

# ── Telegram（香港ECS直连） ──
TG_TOKEN = "8640664409:AAHPZIZd1YGa6jCwXziM01qoJ0RJwCfG-LM"
CHAT_ID = "8410098965"
BARK_KEY = "gbRTde9uu3C8AwZBqorEj8"
OFFSET_FILE = "/opt/trading-master/offset.txt"

os.makedirs("/opt/trading-master", exist_ok=True)

MASTER_SYSTEM = """你是旺财交易大师，20年华尔街交易经验，全能交易助手。
风格：犀利、简洁、直击要害。

输出格式：结论 → 证据 → 风险 → 动作

你的能力：
1. 📊 实时市场数据（自动获取ETH价格/指标/清算）
2. 🔍 联网搜索（用户问新闻/最新消息时自动搜）
3. 💾 对话记忆（记得之前的交流）
4. 💻 写代码执行（Python沙箱，可写脚本算指标/画图/分析数据）
   - 用 write_script(code, filename) 写脚本
   - 用 run_script(filename) 运行
   - 用 generate_chart(data, title) 画图
   - 工作区: /opt/trading-master/workspace/
5. 📈 数据可视化（生成K线/指标图表）

安全限制：
- ❌ 不能修改旺财系统文件
- ❌ 不能修改自身代码
- ❌ 不能改systemd服务
- 只能在沙箱工作区内写代码

禁止：无止损建议、具体点位预测、模糊表述。
没有足够数据时明确说"无法判断"。
"""

def call_master(question):
    """调用LLM（带搜索+记忆）"""
    market = get_market_context()
    memory = get_memory_context(5)

    # 判断是否需要搜索（包含行情/新闻/最新相关词时触发）
    search_keywords = ["新闻", "最新", "今天", "最近", "消息", "分析", "走势",
                       "news", "latest", "today", "update", "happened", "公告",
                       "原因", "为什么", "发生了什么"]
    search_result = ""
    if any(kw in question.lower() for kw in search_keywords):
        search_result = search_web(f"ETH {question[:50]} 行情 2026")
        if not search_result or search_result.startswith("搜索失败"):
            search_result = search_web(f"加密货币 {question[:50]} 最新消息")

    messages = [
        {"role": "system", "content": MASTER_SYSTEM},
        {"role": "user", "content": f"{market}\n\n{memory}\n\n{search_result}\n\n问题: {question}"}
    ]

    answer = call_llm(messages, max_tokens=1000, timeout=120)
    if answer:
        save_memory(question, answer)
    return answer or "大师暂时无法响应"

def push(msg):
    for _ in range(2):
        try:
            requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                          json={"chat_id": CHAT_ID, "text": msg[:2000]}, timeout=15)
            requests.post("https://api.day.app/push",
                          json={"device_key": BARK_KEY, "title": "🧠交易大师",
                                "body": msg[:200], "group": "交易大师"}, timeout=8)
            return
        except: time.sleep(1)

def poll():
    offset = 0
    try:
        with open(OFFSET_FILE) as f: offset = int(f.read().strip())
    except: pass

    push("🧠 旺财交易大师上线\n/master <问题> 即可提问")

    while True:
        try:
            r = requests.get(f"https://api.telegram.org/bot{TG_TOKEN}/getUpdates",
                             params={"offset": offset+1, "timeout": 30}, timeout=35)
            if r.status_code != 200: continue
            for upd in r.json().get("result", []):
                offset = max(offset, upd["update_id"])
                msg = upd.get("message", {}).get("text", "")
                cid = upd.get("message", {}).get("chat", {}).get("id")
                if not msg or str(cid) != CHAT_ID: continue
                
                # 命令保留，其余全部走大师
                if msg in ("/start", "/eth", "/liq", "/signal", "/stats", "/regime", "/save"):
                    with open(OFFSET_FILE, "w") as f: f.write(str(offset))
                    continue  # 这些命令让旺财处理
                
                # 去掉 /master 前缀即可
                question = msg[8:] if msg.startswith("/master ") else msg
                push(f"🧠 交易大师分析中... ({time.strftime('%H:%M')})")
                ans = call_master(question)
                push(f"🧠 交易大师\n\n{ans}")
                with open(OFFSET_FILE, "w") as f: f.write(str(offset))
            with open(OFFSET_FILE, "w") as f: f.write(str(offset))
        except Exception as e:
            print(f"[大师] {e}", flush=True)
        time.sleep(1)

def web_health():
    from http.server import HTTPServer, BaseHTTPRequestHandler
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')
        def log_message(self, *a): pass
    HTTPServer(("0.0.0.0", 5060), H).serve_forever()

if __name__ == "__main__":
    print("🧠 交易大师启动 (双Key自动切换)", flush=True)
    threading.Thread(target=web_health, daemon=True).start()
    poll()
