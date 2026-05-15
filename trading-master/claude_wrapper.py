#!/usr/bin/env python3
"""API 调用器 - 双Key自动切换（免费→付费）"""
import requests, json, os, time

ENDPOINTS = [
    # Key-0: 硅基流动（免费2000万token）
    {"name": "硅基流动(免费)", "key": "sk-eevlkxrnmfgtoxyijmftmcexvqrdkjkokmhszpiebjfwhgvm",
     "url": "https://api.siliconflow.cn/v1/chat/completions",
     "model": "deepseek-ai/DeepSeek-V4-Flash"},
    # Key-1: DeepSeek官方（付费备用）
    {"name": "DeepSeek(付费)", "key": "sk-b2f5c0f817514e5bbf7ac7c4622f52e5",
     "url": "https://api.deepseek.com/chat/completions",
     "model": "deepseek-v4-flash"},
]

SWITCH_FILE = "/opt/trading-master/active_key.txt"

def get_ep():
    try:
        with open(SWITCH_FILE) as f:
            idx = int(f.read().strip())
            if idx < len(ENDPOINTS): return ENDPOINTS[idx], idx
    except: pass
    return ENDPOINTS[0], 0

def ask(messages, max_tokens=512, timeout=60):
    for attempt in range(len(ENDPOINTS)):  # 先试当前，失败后逐个切换
        ep, idx = get_ep()
        data = {"model": ep["model"], "messages": messages, "max_tokens": max_tokens}
        try:
            r = requests.post(ep["url"], json=data,
                headers={"Authorization": "Bearer "+ep["key"], "Content-Type": "application/json"},
                timeout=timeout)
            if r.status_code == 200:
                return r.json()["choices"][0]["message"]["content"]
            # 配额耗尽/余额不足 -> 自动切到下一个Key
            if r.status_code in (402, 429) or "insufficient" in r.text.lower() or "quota" in r.text.lower():
                next_idx = (idx + 1) % len(ENDPOINTS)
                with open(SWITCH_FILE, "w") as f: f.write(str(next_idx))
                print(f"[API] {ep['name']} 额度用完，自动切到 {ENDPOINTS[next_idx]['name']}", flush=True)
                continue
            print(f"[API] {ep['name']} {r.status_code}", flush=True)
        except Exception as e:
            print(f"[API] {ep['name']} ERR: {e}", flush=True)
        time.sleep(1)
    return ""
