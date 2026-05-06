#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ETH RSI监控 V7 - 工业级进化版
三大升级: 双数据源 + 健康监控 + 形态识别
不动云端 V6.3，纯本地运行测试
"""
import requests, time, threading, json, os, sys
from datetime import datetime

# ========== 文件日志 (直接写文件, 绕开 Windows GBK stdout) ==========
LOG_FILE = r"C:\Users\Administrator\WorkBuddy\Claw\v7_output.log"
_LOG_FD = None
def _init_log():
    global _LOG_FD
    try:
        _LOG_FD = open(LOG_FILE, 'a', encoding='utf-8', buffering=1)
    except:
        pass

def log(msg, *args, **kwargs):
    """纯文件日志, 不碰stdout. 接受print遗留的flush等参数"""
    ts = datetime.now().strftime('%H:%M:%S')
    line = "[%s] %s\n" % (ts, msg)
    try:
        if _LOG_FD:
            _LOG_FD.write(line)
            _LOG_FD.flush()
        else:
            with open(LOG_FILE, 'a', encoding='utf-8') as f:
                f.write(line)
                f.flush()
    except:
        pass

_init_log()

# ========== 配置 ==========
GATEIO_API = "https://api.gateio.ws/api/v4"
COINGECKO_API = "https://api.coingecko.com/api/v3"
CONTRACT = "ETH_USDT"
# ====== 推送配置 ======
# 本地测试: Telegram (云端 V6.3 仍用 Bark, 互不干扰)
TG_TOKEN = "8640664409:AAHPZIZd1YGa6jCwXziM01qoJ0RJwCfG-LM"
TG_CHAT_ID = "8410098965"
TG_API = "https://api.telegram.org/bot%s/sendMessage" % TG_TOKEN
RSI_PERIODS = [6, 14, 21]
LOW_THRESHOLD = 30; HIGH_THRESHOLD = 70
INTERVALS = {"15m": "15分钟", "1h": "1小时", "4h": "4小时"}
CHECK_INTERVAL = 60; REQUEST_TIMEOUT = 8
VOL_LOOKBACK = 20; VOL_SURGE = 1.5; VOL_SHRINK = 0.5; PRICE_CHG_MIN = 0.5
BOLL_PERIOD = 20; BOLL_STD = 2
EMA_SHORT = 144; EMA_LONG = 169
POSITION_LOOKBACK = 500
COINGECKO_DAYS = {"15m": 1, "1h": 7, "4h": 30}  # CoinGecko OHLC days参数

# 胜率查表
WINRATE = {
    "rsi_low":  {"45min":"59%","90min":"60%","180min":"62%"},
    "rsi_high": {"45min":"55%","90min":"56%","180min":"76%"},
    "boll_up":  {"45min":"56%","90min":"53%","180min":"70%"},
    "boll_low": {"45min":"48%","90min":"53%","180min":"54%"},
    "vol_drop": {"45min":"66%","90min":"66%","180min":"66%"},
}

# ========== 冷却去重 ==========
class CooldownSet:
    def __init__(self, cd=1800):
        self._d={}; self._l=threading.Lock(); self._cd=cd
    def can_push(self, k):
        n=time.time()
        with self._l:
            if n-self._d.get(k,0)<self._cd: return False
            self._d[k]=n
            ex=[x for x,t in self._d.items() if t<n-self._cd*2]
            for x in ex: del self._d[x]
            return True
ac = CooldownSet(1800)

# ========== 健康监控 ==========
class HealthMonitor:
    def __init__(self):
        self.last_heartbeat = 0
        self.consec_fails = 0
        self.total_fails = 0
        self.data_source = "Gate.io"
        self.lock = threading.Lock()
    
    def record_success(self, source):
        with self.lock:
            self.consec_fails = 0
            self.data_source = source
    
    def record_fail(self):
        with self.lock:
            self.consec_fails += 1
            self.total_fails += 1
    
    def check_heartbeat(self):
        """每4小时发一次心跳"""
        now = time.time()
        if now - self.last_heartbeat >= 14400:  # 4小时
            self.last_heartbeat = now
            return True
        return False
    
    def is_critical(self):
        """连续10次失败触发报警"""
        return self.consec_fails >= 10
    
    def status_report(self):
        with self.lock:
            return {
                "source": self.data_source,
                "consec_fails": self.consec_fails,
                "total_fails": self.total_fails,
                "last_hb": datetime.fromtimestamp(self.last_heartbeat).strftime('%H:%M') if self.last_heartbeat else "never"
            }

hm = HealthMonitor()

# ========== 指标计算 ==========
def calc_rsi(p, period):
    if not p or len(p)<period+1: return None
    g,l=[],[]
    for i in range(1,len(p)):
        c=p[i]-p[i-1]
        if c>0: g.append(c); l.append(0.0)
        else: g.append(0.0); l.append(abs(c))
    ag=sum(g[:period])/period; al=sum(l[:period])/period
    for i in range(period,len(g)):
        ag=(ag*(period-1)+g[i])/period; al=(al*(period-1)+l[i])/period
    return 100.0-100.0/(1.0+ag/al) if al>1e-10 else 100.0

def calc_ema(p, period):
    if len(p)<period: return None
    m=2/(period+1); r=sum(p[:period])/period
    for x in p[period:]: r=(x-r)*m+r
    return r

def calc_boll(p, period=BOLL_PERIOD, std=BOLL_STD):
    if len(p)<period: return None,None,None
    s=sum(p[-period:])/period; v=sum((x-s)**2 for x in p[-period:])/period; sd=v**0.5
    return s, s+std*sd, s-std*sd

def detect_position(pr, c):
    lookback = min(POSITION_LOOKBACK, len(c))
    if lookback < 20: return ""
    recent = c[-lookback:]
    lo, hi = min(recent), max(recent)
    rng = hi - lo
    if rng < 1: return ""
    pct = (pr - lo) / rng
    if pct > 0.75: return "高位区"
    elif pct < 0.25: return "低位区"
    else: return "中位区"

# ========== 双数据源 ==========
def get_data_gateio(iv):
    """主数据源: Gate.io"""
    interval_map = {"15m":"15m","1h":"1h","4h":"4h"}
    for a in range(3):
        try:
            r=requests.get("%s/futures/usdt/candlesticks"%GATEIO_API,
                params={"contract":CONTRACT,"interval":interval_map.get(iv,iv),"limit":100},timeout=REQUEST_TIMEOUT)
            r.raise_for_status(); d=r.json()
            if isinstance(d,list) and len(d)>=30:
                closes = [float(c['c']) for c in d]
                volumes = [float(c['v']) for c in d]
                return closes, volumes, d  # 返回ohlc全量用于形态识别
        except:
            if a<2: time.sleep(2**a)
    return None,None,None

def get_data_coingecko(iv):
    """备用数据源: CoinGecko (免费公开API)"""
    days = COINGECKO_DAYS.get(iv, 1)
    for a in range(3):
        try:
            r=requests.get("%s/coins/ethereum/ohlc"%COINGECKO_API,
                params={"vs_currency":"usd","days":str(days)},timeout=REQUEST_TIMEOUT)
            r.raise_for_status(); d=r.json()
            # CoinGecko返回: [[ts, o, h, l, c], ...]
            if isinstance(d,list) and len(d)>=30:
                closes = [float(c[4]) for c in d]
                volumes = [float(c[1])*float(c[2]) for c in d]  # 估算成交量 ≈ 开盘*最高
                # 转换成统一格式用于形态识别
                ohlc_list = [{"o":str(c[1]),"h":str(c[2]),"l":str(c[3]),"c":str(c[4]),"t":c[0]} for c in d]
                return closes, volumes, ohlc_list
        except:
            if a<2: time.sleep(2**a)
    return None,None,None

def get_data(iv):
    """智能双数据源: Gate.io优先, 失败自动切CoinGecko"""
    c, v, raw = get_data_gateio(iv)
    if c:
        hm.record_success("Gate.io")
        return c, v, raw, "Gate.io"
    
    log("[%s] Gate.io失败, 切换到CoinGecko" % datetime.now())
    c, v, raw = get_data_coingecko(iv)
    if c:
        hm.record_success("CoinGecko")
        return c, v, raw, "CoinGecko"
    
    hm.record_fail()
    return None, None, None, None

# ========== K线形态识别 ==========
def detect_candle_pattern(raw_candles):
    """
    基于最后3根K线的形态识别
    返回: [(形态名, 方向, 置信度), ...]
    """
    if not raw_candles or len(raw_candles) < 3:
        return []
    
    patterns = []
    # 取最后3根
    c3 = raw_candles[-3:]
    
    # 统一提取o/h/l/c
    def get_ohlc(c):
        if isinstance(c, dict):
            return float(c['o']), float(c['h']), float(c['l']), float(c['c'])
        return float(c[1]), float(c[2]), float(c[3]), float(c[4])
    
    try:
        o0, h0, l0, c0 = get_ohlc(c3[0])  # 第1根
        o1, h1, l1, c1 = get_ohlc(c3[1])  # 第2根
        o2, h2, l2, c2 = get_ohlc(c3[2])  # 第3根(最新)
    except:
        return []
    
    body0 = abs(c0 - o0); body1 = abs(c1 - o1); body2 = abs(c2 - o2)
    upper0 = h0 - max(o0, c0); lower0 = min(o0, c0) - l0
    upper1 = h1 - max(o1, c1); lower1 = min(o1, c1) - l1
    upper2 = h2 - max(o2, c2); lower2 = min(o2, c2) - l2
    total0 = h0 - l0 if h0 - l0 > 0.01 else 0.01
    total1 = h1 - l1 if h1 - l1 > 0.01 else 0.01
    total2 = h2 - l2 if h2 - l2 > 0.01 else 0.01
    
    # --- 早晨之星 (看多) ---
    # 第1根大阴线, 第2根小实体(十字星/纺锤), 第3根大阳线越过第1根中点
    is_bear1 = c0 < o0 and body0/total0 > 0.5
    is_small2 = body1/total1 < 0.3
    is_bull3 = c2 > o2 and body2/total2 > 0.5
    gap_up = o2 > (o0 + c0)/2  # 第3根开盘越过第1根中点
    if is_bear1 and is_small2 and is_bull3 and gap_up:
        patterns.append(("早晨之星", "bullish", 0.7))
    
    # --- 黄昏之星 (看空) ---
    # 第1根大阳线, 第2根小实体, 第3根大阴线跌破第1根中点
    is_bull1 = c0 > o0 and body0/total0 > 0.5
    is_small2 = body1/total1 < 0.3
    is_bear3 = c2 < o2 and body2/total2 > 0.5
    gap_down = c2 < (o0 + c0)/2  # 第3根收盘跌破第1根中点
    if is_bull1 and is_small2 and is_bear3 and gap_down:
        patterns.append(("黄昏之星", "bearish", 0.7))
    
    # --- 锤子线 (看多) ---
    # 下影线长(>实体2倍), 上影线短, 实体在顶部
    lower_shadow = lower2; upper_shadow = upper2
    if lower_shadow > body2 * 2 and upper_shadow < body2 * 0.5 and body2 > 0:
        patterns.append(("锤子线", "bullish", 0.6))
    
    # --- 射击之星 (看空) ---
    # 上影线长(>实体2倍), 下影线短, 实体在底部
    if upper_shadow > body2 * 2 and lower_shadow < body2 * 0.5 and body2 > 0:
        patterns.append(("射击之星", "bearish", 0.6))
    
    # --- 看多吞没 ---
    # 第1根阴线, 第2根阳线完全吞噬第1根
    if c1 < o1 and c2 > o2 and o2 < c1 and c2 > o1:
        patterns.append(("看多吞没", "bullish", 0.65))
    
    # --- 看空吞没 ---
    # 第1根阳线, 第2根阴线完全吞噬第1根
    if c1 > o1 and c2 < o2 and o2 > c1 and c2 < o1:
        patterns.append(("看空吞没", "bearish", 0.65))
    
    # --- 十字星 (方向不确定) ---
    if body2/total2 < 0.1 and total2 > 0.5:  # 实体很小但波动够大
        patterns.append(("十字星", "neutral", 0.3))
    
    return patterns


# ========== 推送 ==========
def push(title, body):
    """本地测试 → Telegram (云端 V6.3 仍用 Bark, 互不干扰)"""
    # 移除特殊字符防止GBK编码问题
    import re
    safe_title = re.sub(r'[^\x00-\x7F\u4e00-\u9fff]', '', title)
    safe_body = re.sub(r'[^\x00-\x7F\u4e00-\u9fff]', '', body)
    full_msg = "%s\n\n%s" % (safe_title, safe_body)
    for a in range(2):  # 只重试2次, 防卡死
        try:
            r=requests.post(TG_API, json={
                "chat_id":TG_CHAT_ID,"text":full_msg,
                "parse_mode":"HTML","disable_web_page_preview":True}, timeout=5)  # 5s超时
            r.raise_for_status()
            if r.json().get("ok"): log("tg ok: %s" % safe_title[:20]); return True
        except:
            if a<1: time.sleep(1)
    log("tg fail: %s" % safe_title[:20])
    return False

# ========== 逻辑主循环 ==========
def run_period(iv, name):
    """对每个时间框架执行一次检查"""
    log(">>> %s start" % name)
    c, v, raw_ohlc, source = get_data(iv)
    if not c or len(c) < 22:
        return
    
    # --- RSI计算 ---
    r = {}
    for p in RSI_PERIODS:
        x = calc_rsi(c, p)
        if x is not None: r[p] = x
    if len(r) < 3: return
    
    pr = c[-1]; pc = 0
    ema_s = calc_ema(c, EMA_SHORT)
    ema_l = calc_ema(c, EMA_LONG) if len(c) >= EMA_LONG else None
    bm, bu, bl = calc_boll(c)
    avg_v = (sum(v[-VOL_LOOKBACK-1:-1])/VOL_LOOKBACK) if len(v) >= VOL_LOOKBACK+2 else 1
    vr = v[-1]/avg_v if avg_v > 0 else 1
    pch = (c[-1]-c[-2])/c[-2]*100 if len(c) >= 2 else 0
    pos = detect_position(pr, c)
    
    now_str = "%dh:%dm" % (datetime.now().hour, datetime.now().minute)
    hr = "[%s] $%.0f" % (name, pr)
    
    # --- 形态识别 ---
    patterns = detect_candle_pattern(raw_ohlc) if raw_ohlc else []
    
    # ===== 原有7种信号 (与V6.3一致) =====
    
    # 信号1: BOLL上轨突破
    if bu and pr > bu:
        k = "boll_up_%s" % iv
        if ac.can_push(k):
            wr = WINRATE["boll_up"].get("180min","")
            wr_s = " 近3h下跌概率%s" % wr if wr else ""
            # 附加形态信息
            pattern_info = ""
            for pat, dir, conf in patterns:
                if dir == "bearish":
                    pattern_info = " 形态确认: %s(%.0f%%)" % (pat, conf*100)
            title = "⚠️🔴 涨太快了别追·等待做空"
            body = ("%s\n现价$%.0f 冲出布林带上轨 %s%s%s\n"
                    "👉 等回踩$%.0f缩量企稳再考虑，追高9成站岗。\n%s") % (hr, pr, pos, wr_s, pattern_info, bm, now_str)
            if push(title, body): pc += 1
    
    # 信号2: BOLL下轨突破
    if bl and pr < bl:
        k = "boll_low_%s" % iv
        if ac.can_push(k):
            wr = WINRATE["boll_low"].get("180min","")
            wr_s = " 近3h反弹概率%s" % wr if wr else ""
            pattern_info = ""
            for pat, dir, conf in patterns:
                if dir == "bullish":
                    pattern_info = " 形态确认: %s(%.0f%%)" % (pat, conf*100)
            title = "⚠️🟢 跌太狠了别慌·等待做多"
            body = ("%s\n现价$%.0f 跌破布林带下轨 %s%s%s\n"
                    "👉 别割肉，等回到$%.0f+出阳线止跌再说。\n%s") % (hr, pr, pos, wr_s, pattern_info, bm, now_str)
            if push(title, body): pc += 1
    
    # 信号3: RSI低位
    for p in RSI_PERIODS:
        if p in r and r[p] < LOW_THRESHOLD:
            k = "low_%s_%d" % (iv, p)
            if ac.can_push(k):
                extra = ""
                if pr < bl: extra += " 跌出布林带下轨"
                elif ema_s and ema_l and pr < min(ema_s, ema_l): extra += " 大方向空头"
                if vr > VOL_SURGE and pch < 0: extra += " 放量砸盘中"
                wr = WINRATE["rsi_low"].get("180min","")
                wr_s = " 近3h反弹概率%s" % wr if wr else ""
                pattern_info = ""
                for pat, dir, conf in patterns:
                    if dir == "bullish":
                        pattern_info = " 形态确认: %s(%.0f%%)" % (pat, conf*100)
                title = "⚠️🟢 短线跌过头了·关注做多"
                body = ("%s\nRSI=%.0f 超卖 %s%s%s%s\n"
                        "👉 RSI回到35以上+阳线再考虑多。\n%s") % (hr, r[p], pos, wr_s, extra, pattern_info, now_str)
                if push(title, body): pc += 1
    
    # 信号4: RSI高位
    for p in RSI_PERIODS:
        if p in r and r[p] > HIGH_THRESHOLD:
            k = "high_%s_%d" % (iv, p)
            if ac.can_push(k):
                extra = ""
                if pr > bu: extra += " 冲出布林带上轨"
                elif ema_s and ema_l and pr > max(ema_s, ema_l): extra += " 大方向多头"
                if vr < VOL_SHRINK: extra += " 缩量拉涨小心假突破"
                wr = WINRATE["rsi_high"].get("180min","")
                wr_s = " 近3h下跌概率%s" % wr if wr else ""
                pattern_info = ""
                for pat, dir, conf in patterns:
                    if dir == "bearish":
                        pattern_info = " 形态确认: %s(%.0f%%)" % (pat, conf*100)
                title = "⚠️🔴 短线涨过头了·关注做空"
                body = ("%s\nRSI=%.0f 超买 %s%s%s%s\n"
                        "👉 RSI回到60以下+缩量回调再考虑空。\n%s") % (hr, r[p], pos, wr_s, extra, pattern_info, now_str)
                if push(title, body): pc += 1
    
    # 信号5: 放量下跌
    if v and len(v) >= 22:
        if vr > VOL_SURGE and pch < -PRICE_CHG_MIN:
            k = "vdrop_%s" % iv
            if ac.can_push(k):
                extra2 = ""
                if r[6] < 30: extra2 = " RSI超卖但放量砸盘别急着抄"
                wr = WINRATE["vol_drop"].get("180min","")
                wr_s = " 近3h反弹概率%s" % wr if wr else ""
                title = "🔴 有人在砸盘·别接飞刀"
                body = ("%s\n放量暴跌%.2f%% 量能%.1f倍 %s%s\n"
                        "👉 缩量企稳+阳线反包再考虑。\n%s") % (hr, pch, vr, pos, wr_s, now_str)
                if push(title, body): pc += 1
        
        # 信号6: 缩量上涨背离
        if vr < VOL_SHRINK and pch > PRICE_CHG_MIN*0.6 and r[6] > 65:
            k = "vbear_%s" % iv
            if ac.can_push(k):
                title = "🐻🔴 涨不动了·关注做空"
                body = ("%s\n涨了但量仅%.2f倍 %s 典型量价背离\n"
                        "👉 RSI拐头向下+突然放量=做空信号。\n%s") % (hr, vr, pos, now_str)
                if push(title, body): pc += 1
        
        # 信号7: 放量不涨
        if vr > VOL_SURGE and abs(pch) < 0.2:
            k = "vtop_%s" % iv
            if ac.can_push(k):
                more = " RSI高位" if r[6] > 65 else " 方向不明"
                title = "⚖️ 多空在打架·先观望"
                body = ("%s%s %s 放量滞涨\n"
                        "👉 谁赢跟谁走，别冲进去当炮灰。\n%s") % (hr, more, pos, now_str)
                if push(title, body): pc += 1
    
    # ===== 新增: 纯形态信号 (无RSI/BOLL触发时才发, 避免重复) =====
    # 只有当上面的7种信号都没触发, 且形态置信度>=0.65才发
    if pc == 0 and patterns:
        for pat, direction, conf in patterns:
            if conf < 0.65:
                continue
            is_bearish = (direction == "bearish")
            is_bullish = (direction == "bullish")
            # 避免与已有RSI信号打架: RSI没超买/超卖时才发纯形态信号
            rsi_ok = all(p not in r or (LOW_THRESHOLD <= r[p] <= HIGH_THRESHOLD) for p in RSI_PERIODS)
            if not rsi_ok:
                continue
            k = "pat_%s_%s" % (iv, pat)
            if ac.can_push(k):
                emoji = "🟢" if is_bullish else "🔴"
                action = "关注做多" if is_bullish else "关注做空"
                title = "%s %s出现·%s" % (emoji, pat, action)
                body = ("%s\n检测到%s %s\n"
                        "👉 配合RSI/量价确认后操作。\n%s") % (hr, pat, pos, now_str)
                if push(title, body):
                    pc += 1
                    break  # 每种信源只发一条
    
    # 打印日志
    pl = "  ".join("RSI(%d)=%.2f" % (p, r.get(p, 0)) for p in RSI_PERIODS)
    src = "  |  src:%s" % source
    ex = "  |  p:%d" % pc if pc else ""
    pat_str = "  |  pattern:%s" % ",".join(p[0] for p in patterns) if patterns else ""
    log("[%s] %s  |  %s%s%s%s" % (datetime.now(), name, pl, src, ex, pat_str), flush=True)


# ========== 主循环 ==========
def main():
    log("=" * 65)
    log("  ETH RSI 监控 V7 - 工业级进化版")
    log("  时间: %s" % datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    log("  合约: %s" % CONTRACT)
    log("  RSI: %s  <%d|>%d" % (RSI_PERIODS, LOW_THRESHOLD, HIGH_THRESHOLD))
    log("  BOLL(%d,%.0f)  EMA(%d,%d)" % (BOLL_PERIOD, BOLL_STD, EMA_SHORT, EMA_LONG))
    log("  升级: 双数据源 | 健康监控 | 形态识别")
    log("  推送: Telegram (测试通道)")
    log("  ** 云端 V6.3 不受影响 **")
    log("=" * 65)
    
    while True:
        try:
            for iv, nm in INTERVALS.items():
                try:
                    run_period(iv, nm)
                except Exception as e:
                    log("[%s] %s e: %s" % (datetime.now(), nm, e))
            
            # --- 健康监控: 心跳 ---
            if hm.check_heartbeat():
                status = hm.status_report()
                push("💚 系统心跳 - 运行正常",
                     "数据源: %s\n连续失败: %d次\n累计失败: %d次\n上次心跳: %s\n时间: %s" % (
                         status["source"], status["consec_fails"],
                         status["total_fails"], status["last_hb"],
                         datetime.now().strftime('%Y-%m-%d %H:%M')))
                log("[%s] 心跳推送" % datetime.now(), flush=True)
            
            # --- 健康监控: 故障报警 ---
            if hm.is_critical():
                push("🚨 故障警报 - 数据源持续异常",
                     "连续%d次拉取失败!\n当前主源: %s\n请检查网络或API状态。\n时间: %s" % (
                         hm.consec_fails, hm.data_source,
                         datetime.now().strftime('%Y-%m-%d %H:%M')))
                log("[%s] 故障报警! 连续%d次失败" % (datetime.now(), hm.consec_fails), flush=True)
                # 重置计数器, 避免重复报警
                with hm.lock:
                    hm.consec_fails = 0
            
            time.sleep(CHECK_INTERVAL)
        except KeyboardInterrupt:
            log("\n[%s] 用户中断, 退出..." % datetime.now())
            break
        except Exception as e:
            log("[%s] main e: %s" % (datetime.now(), e))
            time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
