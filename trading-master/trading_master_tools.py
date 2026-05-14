#!/usr/bin/env python3
"""交易大师工具包 — 市场数据 + 搜索 + 记忆"""
import requests, json, time, os, html

# ── 配置 ──
GATEIO = "https://api.gateio.ws/api/v4/futures/usdt"
SYMBOL = "ETH_USDT"
CACHE_DIR = "/opt/trading-master/cache"
os.makedirs(CACHE_DIR, exist_ok=True)

# ══════════════════════════════════════════════════════════════
# 工具1: 市场数据
# ══════════════════════════════════════════════════════════════

def get_price() -> dict:
    """获取当前ETH价格 + 24h统计"""
    try:
        r = requests.get(f"{GATEIO}/tickers?contract={SYMBOL}", timeout=8)
        if r.status_code == 200:
            d = r.json()[0]
            return {
                "price": float(d["last"]),
                "high_24h": float(d["high_24h"]),
                "low_24h": float(d["low_24h"]),
                "volume_24h": float(d["volume_24h"]),
                "change_24h": float(d["change_percentage"]),
            }
    except: pass
    return {"error": "获取失败"}

def get_klines(interval="15m", limit=100) -> list:
    """获取K线数据"""
    try:
        r = requests.get(f"{GATEIO}/candlesticks",
                         params={"contract": SYMBOL, "interval": interval, "limit": limit},
                         timeout=8)
        if r.status_code == 200:
            d = r.json(); d.reverse()
            return [{"c":float(x["c"]),"h":float(x["h"]),"l":float(x["l"]),"v":float(x["v"])} for x in d]
    except: pass
    return []

def get_orderbook() -> dict:
    """获取深度数据"""
    try:
        r = requests.get(f"{GATEIO}/order_book?contract={SYMBOL}&limit=10", timeout=8)
        if r.status_code == 200:
            d = r.json()
            bs = sum(float(b[1])*float(b[0]) for b in d.get("bids",[]))
            as_ = sum(float(a[1])*float(a[0]) for a in d.get("asks",[]))
            return {"bid_depth": round(bs/1e6,2), "ask_depth": round(as_/1e6,2),
                    "spread": round(float(d["asks"][0][0])-float(d["bids"][0][0]),1) if d.get("asks") and d.get("bids") else 0}
    except: pass
    return {}

def get_liquidation() -> dict:
    """获取清算数据（OKX）"""
    try:
        r = requests.get("https://www.okx.com/api/v5/public/liquidation-orders",
            params={"instType":"SWAP","instFamily":"ETH-USDT","state":"filled","limit":"50"},
            timeout=15, headers={"User-Agent":"Mozilla/5.0"})
        if r.status_code == 200:
            data = r.json().get("data",[]); lo={}; so={}; tl=0; ts=0
            for item in data:
                for d in item.get("details",[]):
                    px=round(float(d["bkPx"])/5)*5; usd=float(d["sz"])*px
                    if d["posSide"]=="long": lo[px]=lo.get(px,0)+usd; tl+=usd
                    else: so[px]=so.get(px,0)+usd; ts+=usd
            tl_s=f"${tl/1e6:.1f}M" if tl else "0"; ts_s=f"${ts/1e6:.1f}M" if ts else "0"
            return {"long_liq": tl_s, "short_liq": ts_s,
                    "top_long": [{"p":p,"a":round(a)} for p,a in sorted(lo.items(),key=lambda x:-x[1])[:3]],
                    "top_short": [{"p":p,"a":round(a)} for p,a in sorted(so.items(),key=lambda x:-x[1])[:3]]}
    except: pass
    return {}

# ══════════════════════════════════════════════════════════════
# 工具2: 技术指标
# ══════════════════════════════════════════════════════════════

def calc_ema(prices, period):
    if len(prices) < period: return None
    k=2/(period+1); r=sum(prices[:period])/period
    for p in prices[period:]: r=p*k+r*(1-k)
    return r

def calc_rsi(closes, period=14):
    if len(closes) < period+1: return 50
    g=l=0
    for i in range(1, period+1):
        z=closes[i]-closes[i-1]
        if z>0: g+=z
        else: l-=z
    return 100-100/(1+(g/period)/(l/period)) if l>0 else 100

def get_indicators() -> dict:
    """计算多周期技术指标"""
    m15 = get_klines("15m", 200)
    h1  = get_klines("1h", 200)
    h4  = get_klines("4h", 200)
    if not m15 or not h1 or not h4: return {}

    m15c = [b["c"] for b in m15]
    h1c  = [b["c"] for b in h1]
    h4c  = [b["c"] for b in h4]

    # RSI
    rsi_15m = round(calc_rsi(m15c), 1)
    rsi_1h  = round(calc_rsi(h1c), 1)
    rsi_4h  = round(calc_rsi(h4c), 1)

    # EMA
    ema144_1h = calc_ema(h1c, 144)
    ema169_1h = calc_ema(h1c, 169)
    ema144_4h = calc_ema(h4c, 144)
    ema169_4h = calc_ema(h4c, 169)

    regime = "?"
    if ema144_4h and ema169_4h:
        regime = "LONG" if ema144_4h > ema169_4h else "SHORT"

    return {
        "price": m15[-1]["c"],
        "regime_4h": regime,
        "rsi": {"15m": rsi_15m, "1h": rsi_1h, "4h": rsi_4h},
        "ema_4h": {"ema144": round(ema144_4h,1) if ema144_4h else None,
                   "ema169": round(ema169_4h,1) if ema169_4h else None},
    }

# ══════════════════════════════════════════════════════════════
# 工具3: 市场上下文（整合所有数据给Claude）
# ══════════════════════════════════════════════════════════════

def get_market_context() -> str:
    """获取完整的市场上下文文本（给Claude的prompt用）"""
    price = get_price()
    indicators = get_indicators()
    ob = get_orderbook()
    liq = get_liquidation()

    lines = ["=== 当前市场数据 ==="]
    if "price" in price:
        lines.append(f"ETH: ${price['price']:.1f}")
        lines.append(f"24h: ${price['low_24h']:.0f} ~ ${price['high_24h']:.0f}")
        lines.append(f"24h量: ${price.get('volume_24h',0)/1e6:.1f}M")
        lines.append(f"24h涨跌: {price.get('change_24h',0):+.2f}%")
    if indicators:
        lines.append(f"4H Regime: {indicators.get('regime_4h','?')}")
        rsi = indicators.get('rsi',{})
        lines.append(f"RSI: 15m={rsi.get('15m','?')} 1h={rsi.get('1h','?')} 4h={rsi.get('4h','?')}")
    if ob:
        lines.append(f"深度: 买${ob.get('bid_depth','?')}M / 卖${ob.get('ask_depth','?')}M")
    if liq:
        lines.append(f"清算: 多{liq.get('long_liq','?')} / 空{liq.get('short_liq','?')}")
    return "\n".join(lines)

# ══════════════════════════════════════════════════════════════
# 工具4: 保存/读取分析报告
# ══════════════════════════════════════════════════════════════

def save_analysis(question: str, answer: str):
    """保存一次分析记录"""
    path = os.path.join(CACHE_DIR, f"analysis_{time.strftime('%Y%m%d')}.md")
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"\n## {time.strftime('%H:%M')} 问: {question}\n\n{answer}\n---\n")

def get_recent_analyses(n=5) -> str:
    """获取最近N条分析记录"""
    path = os.path.join(CACHE_DIR, f"analysis_{time.strftime('%Y%m%d')}.md")
    if not os.path.exists(path): return "无历史记录"
    with open(path) as f:
        lines = f.readlines()
    return "".join(lines[-n*20:])

if __name__ == "__main__":
    # 测试
    print(get_market_context())

# ══════════════════════════════════════════════════════════════
# 工具5: 联网搜索（DuckDuckGo，免费无需Key）
# ══════════════════════════════════════════════════════════════

def search_web(query: str, max_results: int = 5) -> str:
    """
    通过 DuckDuckGo 搜索最新资讯
    无需API Key，免费使用
    """
    try:
        url = "https://html.duckduckgo.com/html/"
        data = {"q": query, "kl": "wt-wt"}
        r = requests.post(url, data=data, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }, timeout=15)

        if r.status_code != 200:
            return f"搜索失败: HTTP {r.status_code}"

        # 解析HTML结果
        results = []
        lines = r.text.split('<a rel="nofollow" href="')
        for line in lines[1:max_results+1]:
            try:
                url_end = line.index('"')
                url = line[:url_end]
                # 提取标题
                title_start = line.index('class="result__a">') + 18
                title_end = line.index('</a>', title_start)
                title = html.unescape(line[title_start:title_end])
                # 提取摘要
                snippet = ""
                if 'class="result__snippet"' in line:
                    sn_start = line.index('class="result__snippet"') + 24
                    sn_start = line.index('>', sn_start) + 1
                    sn_end = line.index('</a>', sn_start)
                    snippet = html.unescape(line[sn_start:sn_end]).strip()
                results.append(f"- {title}\n  {url}\n  {snippet[:150]}")
            except:
                continue

        if not results:
            return "未找到相关结果"
        return "搜索结果:\n" + "\n".join(results[:max_results])

    except Exception as e:
        return f"搜索异常: {str(e)[:50]}"


# ══════════════════════════════════════════════════════════════
# 工具6: 对话记忆（增强版）
# ══════════════════════════════════════════════════════════════

MEMORY_FILE = "/opt/trading-master/memory.json"
MAX_HISTORY = 20  # 最多记住20轮对话

def save_memory(question: str, answer: str):
    """保存对话到长期记忆"""
    history = load_memory()
    history.append({
        "time": time.strftime("%Y-%m-%d %H:%M"),
        "q": question[:100],
        "a": answer[:200],
    })
    if len(history) > MAX_HISTORY:
        history = history[-MAX_HISTORY:]
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)
    # 同时保存详细分析到每日文件
    save_analysis(question, answer)

def load_memory() -> list:
    """加载历史对话"""
    try:
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return []

def get_memory_context(n: int = 5) -> str:
    """获取最近N轮对话作为上下文"""
    history = load_memory()
    if not history:
        return ""
    recent = history[-n:]
    lines = ["最近对话记录:"]
    for h in recent:
        lines.append(f"  [{h['time']}] 问: {h['q'][:80]}")
        lines.append(f"           答: {h['a'][:100]}")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════
# 工具7: 沙箱代码执行（安全受限）
# ══════════════════════════════════════════════════════════════

WORKSPACE = "/opt/trading-master/workspace"
os.makedirs(WORKSPACE, exist_ok=True)

# 禁止修改的系统文件列表
FORBIDDEN_PATHS = ["/root/wangcai.py", "/opt/wangcai-backup/", "/opt/trading-master/trading_master.py",
                   "/opt/trading-master/claude_wrapper.py", "/etc/systemd/", "/root/.claude/"]

def write_script(code: str, filename: str = "analysis.py") -> str:
    """
    在沙箱工作区写入Python脚本
    可用于：计算指标、画图、数据分析
    不能用于：修改系统文件
    """
    # 安全检查
    for forbidden in FORBIDDEN_PATHS:
        if forbidden in filename or "../" in filename or filename.startswith("/"):
            return f"❌ 禁止写入系统文件路径: {filename}"

    filepath = os.path.join(WORKSPACE, filename)
    # 安全检查：禁止覆盖关键文件
    if os.path.exists(filepath) and filename in ("trading_master.py", "claude_wrapper.py", "trading_master_tools.py"):
        return "❌ 禁止覆盖系统文件"

    try:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(code)
        return f"✅ 脚本已保存: {filepath} ({len(code)}字符)"
    except Exception as e:
        return f"❌ 写入失败: {e}"


def run_script(filename: str = "analysis.py", timeout: int = 15) -> str:
    """
    在沙箱中运行Python脚本
    返回执行结果（stdout）
    超时自动终止
    """
    filepath = os.path.join(WORKSPACE, filename)
    if not os.path.exists(filepath):
        return f"❌ 文件不存在: {filepath}"

    import subprocess
    try:
        result = subprocess.run(
            ["python3", filepath],
            capture_output=True, text=True, timeout=timeout,
            cwd=WORKSPACE
        )
        output = result.stdout.strip() or result.stderr.strip()
        return output[:1000] if output else "脚本执行完毕（无输出）"
    except subprocess.TimeoutExpired:
        return "⏰ 脚本执行超时"
    except Exception as e:
        return f"❌ 执行失败: {e}"


def list_scripts() -> str:
    """列出工作区所有脚本"""
    try:
        files = [f for f in os.listdir(WORKSPACE) if f.endswith(".py")]
        if not files:
            return "工作区为空"
        lines = ["工作区脚本:"]
        for f in sorted(files):
            size = os.path.getsize(os.path.join(WORKSPACE, f))
            lines.append(f"  - {f} ({size}字节)")
        return "\n".join(lines)
    except Exception as e:
        return f"❌ 读取失败: {e}"


# ══════════════════════════════════════════════════════════════
# 工具8: 生成图表（matplotlib）
# ══════════════════════════════════════════════════════════════

def generate_chart(data_points: list, title: str = "ETH分析图", filename: str = "chart.png") -> str:
    """
    生成简单图表并保存
    data_points: [(label, value), ...] 或 [value, ...]
    返回文件路径
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        if not data_points:
            return "❌ 无数据"

        fig, ax = plt.subplots(figsize=(10, 5))

        # 判断数据类型
        if isinstance(data_points[0], (list, tuple)):
            labels = [str(d[0]) for d in data_points]
            values = [float(d[1]) for d in data_points]
            ax.bar(range(len(values)), values, color="#2196F3", alpha=0.7)
            ax.set_xticks(range(len(labels)))
            ax.set_xticklabels(labels, rotation=45, fontsize=8)
        else:
            values = [float(d) for d in data_points]
            ax.plot(values, color="#2196F3", linewidth=1.5)
            ax.fill_between(range(len(values)), values, alpha=0.1)

        ax.set_title(title, fontsize=14)
        ax.grid(alpha=0.3)
        plt.tight_layout()

        filepath = os.path.join(WORKSPACE, filename)
        plt.savefig(filepath, dpi=120)
        plt.close()
        return f"✅ 图表已保存: {filepath}"
    except ImportError:
        return "❌ 需要安装matplotlib才能生成图表"
    except Exception as e:
        return f"❌ 图表生成失败: {e}"
