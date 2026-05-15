#!/usr/bin/env python3
"""TG Bot V7 - with Post-Trade Review Archiver"""
import requests, time, json, os, sys
from datetime import datetime

TG_TOKEN = "8640664409:AAHPZIZd1YGa6jCwXziM01qoJ0RJwCfG-LM"
TG_CHAT_ID = "8410098965"
BAILIAN_KEY = "sk-b2f5c0f817514e5bbf7ac7c4622f52e5"
BAILIAN_URL = "https://api.deepseek.com/chat/completions"
ZHIPU_KEY = "14fb054920d248bfb4b0f793445db366.nvwsxzMLghC3rXQs"
ZHIPU_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
SIGNAL_CSV = "/root/eth_monitor/v8_signals.csv"
TRADING_LOG = "/root/trading_log.md"
CHECK_INTERVAL = 60
ANALYSIS_LOG = "/root/analysis_log.csv"
_last_update_id = 0
_known = set()
_last_ctx = {"price":0,"liq":{},"ai_reply":"","time":"","cmd":""}
_last_memory = {}
_CR_STATS = {"total":0,"approved":0,"blocked":0,"pending":0}
_CR_LOG = "/root/cr_log.csv"

CLN1 = "你是旺财,一个全职ETH交易员的私人分析助手。"
CLN2 = "自信点！你是一个有10年经验的全职交易员。有数据就说结论,别说废话,别自我审查。"
CLN3 = "交易话题：结论+关键数据+建议。非交易话题：正常聊天。"
SYSTEM_PROMPT = chr(10).join([CLN1, CLN2, CLN3])

def init_cr_log():
    if not os.path.exists(_CR_LOG):
        with open(_CR_LOG, "a") as f:
            f.write("time,status,reason,regime" + chr(10))

def log_cr(status, reason, regime):
    init_cr_log()
    _CR_STATS["total"] += 1
    if status == "ALIGNED": _CR_STATS["approved"] += 1
    elif status in ("CONFLICT","REJECT"): _CR_STATS["blocked"] += 1
    else: _CR_STATS["pending"] += 1
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    with open(_CR_LOG, "a") as f:
        f.write(now + "," + status + "," + (reason[:30] if reason else "") + "," + regime + chr(10))


def get_cr_stats():
    t = _CR_STATS
    if t["total"] == 0: return "CR zan wu shu ju"
    block_rate = t["blocked"] / t["total"] * 100
    return "CR: zong%ddan tongguo%d zuduan%d bi%d zuduan lv%.1f%%" % (t["total"], t["approved"], t["blocked"], t["pending"], block_rate)


def log(m):
    print("[%s] %s" % (time.strftime('%H:%M:%S'), m), flush=True)

def send_tg(text):
    for a in range(3):
        try:
            r = requests.post("https://api.telegram.org/bot%s/sendMessage" % TG_TOKEN,
                json={"chat_id": TG_CHAT_ID, "text": text}, timeout=10)
            if r.status_code == 200: return True
        except:
            if a < 2: time.sleep(1)
    return False

def save_review():
    ctx = _last_ctx
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = []
    lines.append("\n## 交易复盘时间：%s" % now)
    lines.append("")
    if ctx["price"]:
        liq = ctx.get("liq", {})
        tl = liq.get("total_long", 0)
        ts = liq.get("total_short", 0)
        ratio = "N/A"
        if tl and ts:
            r = max(tl, ts) / min(tl, ts)
            bias = "空头偏多" if ts > tl else "多头偏多"
            ratio = "1:%.1f (%s)" % (r, bias)
        lines.append("- 盘面快照：Price: $%.2f | Liq Ratio: %s" % (ctx["price"], ratio))
        lines.append("- 触发指令：%s" % ctx.get("cmd", "未知"))
    lines.append("")
    lines.append("- AI 核心博弈判断：")
    if ctx["ai_reply"]:
        lines.append("  " + ctx["ai_reply"].replace("\n", "\n  "))
    else:
        lines.append("  （无AI分析记录）")
    lines.append("")
    lines.append("- 结果验证点：")
    lines.append("  ")
    
    text = "\n".join(lines)
    try:
        with open(TRADING_LOG, "a", encoding="utf-8") as f:
            f.write(text + "\n")
        log("review saved")
        return "✅ 复盘已存档：%s" % now
    except Exception as e:
        log("save err: %s" % e)
        return "❌ 存档失败: %s" % e

def scan_liquidation():
    try:
        url = "https://www.okx.com/api/v5/public/liquidation-orders"
        params = {"instType":"SWAP","instFamily":"ETH-USDT","state":"filled","limit":"50"}
        r = requests.get(url, params=params, timeout=15, headers={"User-Agent":"Mozilla/5.0"})
        if r.status_code != 200:
            return {"error": "OKX API %d" % r.status_code}
        data = r.json().get("data", [])
        if not data:
            return {"error": "无清算数据"}
        longs, shorts = {}, {}
        tl, ts = 0, 0
        for item in data:
            for d in item.get("details", []):
                px = round(float(d["bkPx"]) / 5) * 5
                usd = float(d["sz"]) * px
                side = d["posSide"]
                if side == "long":
                    longs[px] = longs.get(px, 0) + usd; tl += usd
                else:
                    shorts[px] = shorts.get(px, 0) + usd; ts += usd
        top_l = sorted(longs.items(), key=lambda x: -x[1])[:3]
        top_s = sorted(shorts.items(), key=lambda x: -x[1])[:3]
        return {"total_long":round(tl),"total_short":round(ts),
                "long_clusters":[{"price":p,"amount":round(a)} for p,a in top_l],
                "short_clusters":[{"price":p,"amount":round(a)} for p,a in top_s]}
    except Exception as e:
        return {"error": str(e)}

def format_liquidation(liq):
    if "error" in liq:
        return "⚠️ 清算数据获取失败: " + liq["error"]
    tl = liq["total_long"]; ts = liq["total_short"]
    ratio = max(tl, ts) / min(tl, ts) if min(tl, ts) > 0 else 99
    bias = "空头" if ts > tl else "多头"
    lines = ["💀 清算地图 (24h OKX)"]
    lines.append("多头清算: $%.1fM  |  空头清算: $%.1fM" % (tl/1e6, ts/1e6))
    lines.append("多空清算比: 1:%.1f (%s偏多)" % (ratio, bias))
    if ratio >= 3:
        lines.append("")
        lines.append("⚠️ 极端偏见预警：%s清算量超另一方%d倍！" % (bias, round(ratio)))
        if bias == "空头":
            lines.append("👉 解读：空头大爆仓，价格在涨/高位，爆空动能可能衰竭")
            lines.append("👉 博弈：追多谨慎，警惕主力反手砸盘，对照4H压力位")
        else:
            lines.append("👉 解读：多头大爆仓，价格在跌/低位，爆多动能可能衰竭")
            lines.append("👉 博弈：追空谨慎，警惕主力反手拉盘，对照4H支撑位")
    if liq["long_clusters"]:
        lines.append("")
        lines.append("🔴 多头密集清算区 (做多止损位):")
        for c in liq["long_clusters"]:
            lines.append("  $%d: %.0f万美金" % (c["price"], c["amount"]/10000))
    if liq["short_clusters"]:
        lines.append("")
        lines.append("🟢 空头密集清算区 (做空止损位):")
        for c in liq["short_clusters"]:
            lines.append("  $%d: %.0f万美金" % (c["price"], c["amount"]/10000))
    return "\n".join(lines)


import re

def fetch_url_content(url):
    """抓取网页内容，供AI分析"""
    try:
        r = requests.get(url, timeout=15, headers={"User-Agent":"Mozilla/5.0"})
        if r.status_code == 200:
            text = r.text
            # 提取纯文本（简单去html标签）
            text = re.sub(r"<[^>]+>", "", text)
            text = re.sub(r"\s+", " ", text).strip()
            return text[:3000]
        return None
    except:
        return None


def rule_mapper(rsi, ema20_up, ema60_up, vol_ratio, liq_short):
    """纯确定性规则引擎, 替代Qwen7B分类器"""
    score = 50
    # RSI
    if rsi < 30: score += 20
    elif rsi > 70: score -= 20
    # EMA
    if ema20_up: score += 10
    if ema60_up: score += 10
    # 成交量
    if vol_ratio >= 2: score += 10
    # 清算
    if liq_short > 1000: score -= 10
    # 归一化
    if score >= 65: return "buy"
    if score <= 35: return "sell"
    return "idle"

def calc_conflict_score(rsi, ema20_up, ema60_up, vol_ratio):
    s = {"trend_rsi": 0, "liquidity": 0, "structure": 0, "volatility": 0}
    if rsi < 30 and ema60_up: s["trend_rsi"] = 25
    elif rsi > 70 and not ema60_up: s["trend_rsi"] = 25
    elif rsi < 30 or rsi > 70: s["trend_rsi"] = 15
    if vol_ratio >= 3: s["liquidity"] = 20
    elif vol_ratio >= 2: s["liquidity"] = 10
    if ema20_up != ema60_up: s["structure"] = 30
    if rsi < 25 or rsi > 75: s["volatility"] = 15
    elif rsi < 30 or rsi > 70: s["volatility"] = 10
    return {"total": sum(s.values()), "breakdown": s}

def get_regime_tag(rsi, ema20_up, ema60_up, vol_ratio):
    if vol_ratio >= 3 and (rsi < 30 or rsi > 70): return "HIGH_VOL"
    if ema20_up and ema60_up: return "TREND_UP"
    if not ema20_up and not ema60_up: return "TREND_DOWN"
    if ema20_up != ema60_up: return "RANGE_BOUND"
    if vol_ratio >= 2: return "BREAKOUT_PREP"
    return "RANGE_BOUND"


# === CONFLICT RESOLVER (bu kerrao guo ceng) ===
# Position: called AFTER signal generation, BEFORE any output
# Authority: risk > xinhao > memory

def conflict_resolver(signal_dir, signal_conf, market_regime, last_memory, analysis_log):
    """Juedui caijue ceng. shuchu ALIGNED / CONFLICT / PENDING."""
    result = {"status": "ALIGNED", "reasons": [], "blocks": []}
    # 1. Fangxiang yizhixing jiancha
    if signal_dir == "buy" and market_regime == "TREND_DOWN":
        result["status"] = "CONFLICT"
        result["reasons"].append("fangxiang chongtu: xinhao mai duo dan shichang xiadie qushi")
    elif signal_dir == "sell" and market_regime == "TREND_UP":
        result["status"] = "CONFLICT"
        result["reasons"].append("fangxiang chongtu: xinhao mai kong dan shichang shangzhang qushi")
    elif signal_dir in ("buy","sell") and market_regime == "RANGE_BOUND" and signal_conf < 60:
        result["status"] = "PENDING"
        result["reasons"].append("zhendang qi: xinhao quexin buzhu, guanwang")
    
    # 2. Memory yizhixing: ru guo shang ci fenxi jieguo yu ben ci chongtu
    if last_memory and last_memory.get("direction") and last_memory["direction"] != signal_dir:
        if last_memory["direction"] != "idle" and signal_dir != "idle":
            result["status"] = "CONFLICT"
            result["reasons"].append("memory chongtu: shang ci %s, ben ci %s" % (last_memory["direction"], signal_dir))
            result["blocks"].append("dengdai memory yi zhi, zan bu zhixing")
    
    # 3. Fengxian youxianji: ru guo zhi xinhao di, jishi fangxiang dui ye bu zhixing
    if signal_conf < 30 and signal_dir != "idle":
        result["status"] = "CONFLICT"
        result["reasons"].append("fengxian youxian: xinhao zhixin du diyu 30")
        result["blocks"].append("zi dong ju dan")
    
    return result


def get_klines():
    try:
        url = "https://api.gateio.ws/api/v4/futures/usdt/candlesticks?contract=ETH_USDT&interval=15m&limit=30"
        r = requests.get(url, timeout=10)
        if r.status_code != 200: return {"error":"gate api %d" % r.status_code}
        data = r.json()
        closes = [float(x["c"]) for x in data]
        highs = [float(x["h"]) for x in data]
        lows = [float(x["l"]) for x in data]
        vols = [float(x["v"]) for x in data]
        close = closes[-1]
        ema20 = sum(closes[-20:])/20
        ema60 = sum(closes[-60:])/60 if len(closes)>=60 else ema20
        gains = losses = 0
        for i in range(1,15):
            d = closes[-i] - closes[-i-1]
            if d>0: gains+=d
            else: losses-=d
        rsi = 50
        if losses>0: rsi = 100-100/(1+(gains/14)/(losses/14))
        vol_sma = sum(vols[-20:])/20
        vol_ratio = round(vols[-1]/vol_sma,1) if vol_sma>0 else 0
        return {"price":close,"ema20":round(ema20,2),"ema60":round(ema60,2),"rsi":round(rsi,1),"vol_ratio":vol_ratio,"high_15m":max(highs),"low_15m":min(lows)}
    except Exception as e:
        return {"error":str(e)}


def run_cmd(cmd):
    import subprocess
    blocked = ["rm -rf", "mkfs", "reboot", "shutdown", "dd if=", "> /dev/", ":(){ :|:& };:"]
    for b in blocked:
        if b in cmd: return {"error":"blocked: " + b}
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
        return {"stdout": r.stdout[-1500:], "stderr": r.stderr[-500:], "code": r.returncode}
    except subprocess.TimeoutExpired:
        return {"error":"timeout"}
    except Exception as e:
        return {"error":str(e)}

def read_file(path):
    try:
        with open(path, "r") as f:
            return {"content": f.read()[-2000:]}
    except Exception as e:
        return {"error":str(e)}

def write_file(path, content):
    blocked_paths = ["/etc/", "/boot/", "/usr/", "/bin/", "/sbin/"]
    for b in blocked_paths:
        if path.startswith(b): return {"error":"blocked path: " + b}
    try:
        with open(path, "w") as f:
            f.write(content)
        return {"ok": True}
    except Exception as e:
        return {"error":str(e)}

def ask_agent(prompt):
    """智能体：自动调用工具获取数据后分析"""
    tools = [
        {"type":"function","function":{"name":"get_price","description":"获取ETH实时价格和24h最高最低","parameters":{"type":"object","properties":{}}}},
        {"type":"function","function":{"name":"get_liq","description":"获取ETH清算地图数据(多空密集区)","parameters":{"type":"object","properties":{}}}},
        {"type":"function","function":{"name":"get_v8","description":"获取V8系统最新扫描状态","parameters":{"type":"object","properties":{}}}},
        {"type":"function","function":{"name":"get_stats","description":"获取V8信号历史胜率统计","parameters":{"type":"object","properties":{}}}},
        {"type":"function","function":{"name":"fetch_url","description":"抓取网页内容供分析","parameters":{"type":"object","properties":{"url":{"type":"string","description":"网页URL"}}}}},{"type":"function","function":{"name":"get_klines","description":"获取15M K线数据(EMA20/60,RSI,成交量)","parameters":{"type":"object","properties":{}}}},{"type":"function","function":{"name":"run_cmd","description":"执行Shell命令","parameters":{"type":"object","properties":{"cmd":{"type":"string"}}}}},{"type":"function","function":{"name":"read_file","description":"读取文件内容","parameters":{"type":"object","properties":{"path":{"type":"string"}}}}},{"type":"function","function":{"name":"write_file","description":"写入文件(禁止系统目录)","parameters":{"type":"object","properties":{"path":{"type":"string"},"content":{"type":"string"}}}}},
    ]
    tool_map = {"get_price": get_eth_price_lite, "get_liq": get_liq_lite, "get_v8": get_v8_status, "get_stats": get_v8_stats, "fetch_url": fetch_url_wrapper, "get_klines": get_klines, "run_cmd": run_cmd, "read_file": read_file, "write_file": write_file}
    
    messages = [{"role":"system","content":SYSTEM_PROMPT},{"role":"user","content":prompt}]
    
    backends = [
        (("SiliconFlow", BAILIAN_URL, BAILIAN_KEY, "deepseek-v4-flash")),
    ]
    
    for name, url, key, model in backends:
        for a in range(3):
            try:
                data = {"model":model,"messages":messages,"tools":tools,"tool_choice":"auto","max_tokens":512}
                if "dashscope" in url: data["enable_thinking"]=False
                r = requests.post(url, json=data, headers={"Authorization":"Bearer %s"%key,"Content-Type":"application/json"}, timeout=60)
                if r.status_code != 200:
                    log("%s fail %d" % (name, r.status_code))
                    continue
                j = r.json()
                msg = j["choices"][0]["message"]
                # 如果有工具调用，执行并回传结果
                if msg.get("tool_calls"):
                    messages.append(msg)
                    for tc in msg["tool_calls"]:
                        fn = tc["function"]["name"]
                        args = json.loads(tc["function"]["arguments"] or "{}")
                        result = tool_map.get(fn, lambda: "unknown")(**args)
                        messages.append({"role":"tool","tool_call_id":tc["id"],"content":json.dumps(result,ensure_ascii=False)})
                        log("tool: %s" % fn)
                    # 第二次请求得到最终回答
                    data2 = {"model":model,"messages":messages,"max_tokens":512}
                    if "dashscope" in url: data2["enable_thinking"]=False
                    r2 = requests.post(url, json=data2, headers={"Authorization":"Bearer %s"%key,"Content-Type":"application/json"}, timeout=60)
                    if r2.status_code == 200:
                        j2 = r2.json()
                        # Critic: only if trading-related
                        trading_kw2 = ["eth","btc","多","空"]
                        is_trading2 = any(kw in prompt.lower() for kw in trading_kw2)
                        plain_answer = j2["choices"][0]["message"]["content"]
                        if not is_trading2:
                            log("skip tool critic (chat)")
                            return plain_answer
                        return plain_answer
                        log("%s agent ok" % name)
                        return plain_answer
                else:
                    # Critic: only if trading-related
                    trading_kw = ["eth","btc","多","空","行情","价格","清算","波动","入场","损止","止盈","分析","交易","k线","ema","rsi","高量","金叉","死叉","倍量","背离","超卖","超买"]
                    is_trading = any(kw in prompt.lower() for kw in trading_kw)
                    plain_answer = msg["content"]
                    if not is_trading:
                        log("skip critic (chat)")
                        return plain_answer
                    return plain_answer
                    log("%s ok" % name)
                    return plain_answer
            except Exception as e:
                log("%s err: %s" % (name, e))
                if a < 1: time.sleep(1)
        log("%s down" % name)
    return "请求失败"

def get_eth_price_lite():
    p,v,l,h = get_eth_price()
    return {"price":p,"volume_24h":v,"low_24h":l,"high_24h":h}

def get_liq_lite():
    liq = scan_liquidation()
    if "error" in liq: return liq
    return {"total_long_M":round(liq["total_long"]/1e6,1),"total_short_M":round(liq["total_short"]/1e6,1),
            "long_clusters":liq["long_clusters"],"short_clusters":liq["short_clusters"]}

def fetch_url_wrapper(url):
    return fetch_url_content(url) or {"error":"抓取失败"}

def get_eth_price():
    try:
        r = requests.get("https://api.gateio.ws/api/v4/futures/usdt/tickers?contract=ETH_USDT", timeout=8)
        if r.status_code == 200:
            d = r.json()
            return float(d[0]["last"]), float(d[0]["volume_24h"]), float(d[0]["low_24h"]), float(d[0]["high_24h"])
    except:
        pass
    return 0,0,0,0

def get_v8_status():
    try:
        lf = "/root/eth_monitor/v8_output.log"
        if not os.path.exists(lf): return "无日志"
        with open(lf) as f:
            lines = f.readlines()
        for line in reversed(lines):
            if "SCAN:" in line or "TRADE" in line or "数据" in line:
                return line.strip()
        return "无数据"
    except:
        return "读取失败"

def get_v8_stats():
    try:
        if not os.path.exists(SIGNAL_CSV): return "暂无信号"
        with open(SIGNAL_CSV) as f:
            lines = f.readlines()
        total = len(lines)-1
        if total == 0: return "暂无信号"
        wins = sum(1 for l in lines[1:] if len(l.split(","))>13 and l.split(",")[13].strip()=="W")
        losses = sum(1 for l in lines[1:] if len(l.split(","))>13 and l.split(",")[13].strip()=="L")
        wr = wins/(wins+losses)*100 if wins+losses>0 else 0
        return "信号:%d单  2h胜率:%.1f%%  胜:%d  负:%d" % (total, wr, wins, losses)
    except:
        return "读取失败"

def check_v8_signals():
    if not os.path.exists(SIGNAL_CSV): return
    try:
        with open(SIGNAL_CSV) as f:
            lines = f.readlines()
        if len(lines) <= 1: return
        for line in lines[1:]:
            parts = line.strip().split(",")
            if not parts or not parts[0].isdigit(): continue
            sid = int(parts[0])
            if sid not in _known and len(parts) > 8:
                d = "做多" if parts[2]=="LONG" else "做空"
                send_tg("📊 V8信号 #%d\n方向: %s\n价格: $%s\n理由: %s" % (sid, d, parts[3], parts[8]))
                _known.add(sid); log("sig #%d" % sid)
            if len(parts) > 14 and parts[13] in ("W","L"):
                k = "s_%d" % sid
                if k not in _known:
                    em = "✅" if parts[13]=="W" else "❌"
                    d = "做多" if parts[2]=="LONG" else "做空"
                    send_tg("%s V8结算 #%d\n方向: %s\n2h: %s" % (em, sid, d, "盈利" if parts[13]=="W" else "亏损"))
                    _known.add(k); _known.add(sid); log("sig #%d settled" % sid)
    except:
        pass

def handle_messages():
    global _last_update_id
    try:
        r = requests.get("https://api.telegram.org/bot%s/getUpdates" % TG_TOKEN,
            params={"offset": _last_update_id+1, "timeout": 10}, timeout=15)
        if r.status_code != 200: return
        for upd in r.json().get("result", []):
            uid = upd["update_id"]; _last_update_id = max(_last_update_id, uid)
            msg = upd.get("message", {}); cid = msg.get("chat",{}).get("id"); txt = msg.get("text","").strip()
            if not txt or str(cid) != TG_CHAT_ID: continue
            log("recv: %s" % txt[:50])

            if txt in ("/save", "记录复盘", "保存复盘"):
                send_tg(save_review())
                continue

            if txt == "/start":
                send_tg("你好！我是旺财\n\n/eth - ETH行情\n/liq - 清算地图\n/signal - V8最新\n/stats - 信号统计\n/status - 系统状态\n/save - 记录复盘\n/help - 帮助\n\n直接聊天：AI分析盘面")
            elif txt == "/eth":
                p,v,l,h = get_eth_price()
                if p: send_tg("📊 ETH永续\n当前: $%.2f\n24h高: $%.2f\n24h低: $%.2f\n24h量: %.0f张" % (p,h,l,v)); log("/eth: $%.2f" % p)
                else: send_tg("获取行情失败")
            elif txt == "/liq":
                liq = scan_liquidation()
                msg = format_liquidation(liq)
                p,v,l,h = get_eth_price()
                _last_ctx["price"] = p
                _last_ctx["liq"] = liq
                _last_ctx["time"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                _last_ctx["cmd"] = "/liq"
                send_tg(msg); log("/liq ok")
            elif txt == "/signal":
                s = get_v8_status(); send_tg("📡 V8扫描\n%s" % s); log("/signal: %s" % s[:40])
            elif txt == "/stats":
                s = get_v8_stats(); send_tg("📈 V8成绩\n%s" % s); log("/stats: %s" % s[:40])
            elif txt == "/status":
                p,v,l,h = get_eth_price(); pstr = "$%.0f" % p if p else "?"; st = get_v8_stats()
                send_tg("🔍 ETH系统\n价格: %s\n%s" % (pstr, st)); log("/status: %s %s" % (pstr, st[:30]))
            elif txt == "/cr":
                send_tg("⚖️ " + get_cr_stats()); log("/cr")
            elif txt == "/help":
                send_tg("🤖 旺财命令:\n/eth - ETH行情\n/liq - 清算地图\n/signal - V8最新\n/stats - 胜率统计\n/status - 系统状态\n/save - 记录复盘\n\n直接发消息：AI六层分析")
            else:
                p,v,l,h = get_eth_price()
                # 检测URL，自动抓取内容
                urls = re.findall(r"https?://[^ ]+", txt)
                fetch_text = ""
                if urls:
                    for url in urls[:2]:
                        fetched = fetch_url_content(url)
                        if fetched:
                            fetch_text += " [网页内容] " + fetched[:1000]
                final_prompt = txt + fetch_text if fetch_text else txt
                send_tg("⏳ 正在分析...")
                reply = ask_agent(final_prompt)
                send_tg("🤖 " + reply)
                _last_ctx["price"] = p
                _last_ctx["ai_reply"] = reply
                _last_ctx["time"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                _last_ctx["cmd"] = "AI分析：" + txt[:30]
                log("replied: %s" % reply[:50])
    except Exception as e:
        log("err: %s" % e)

def main():
    log("TG Bot starting...")
    if not os.path.exists(TRADING_LOG):
        with open(TRADING_LOG, "w", encoding="utf-8") as f:
            f.write("# 交易复盘日志\n\n")
        log("created trading_log.md")
    while True:
        try: settle_analysis()
        except: pass
        try: check_v8_signals()
        except: pass
        try: handle_messages()
        except: pass
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
