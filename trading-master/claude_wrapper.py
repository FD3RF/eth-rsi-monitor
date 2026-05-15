#!/usr/bin/env python3
"""API 调用器 - DeepSeek官方"""
import requests, json, os, time

ENDPOINTS = [
    {"name": "DeepSeek", "key": "sk-eevlkxrnmfgtoxyijmftmcexvqrdkjkokmhszpiebjfwhgvm",
     "url": "https://api.siliconflow.cn/v1/chat/completions",
     "model": "deepseek-ai/DeepSeek-V4-Flash"},
]

SWITCH_FILE = "/opt/trading-master/active_key.txt"

def get_ep():
    return ENDPOINTS[0], 0

def ask(messages, max_tokens=512, timeout=60):
    ep, idx = get_ep()
    data = {"model": ep["model"], "messages": messages, "max_tokens": max_tokens}
    for attempt in range(3):
        try:
            r = requests.post(ep["url"], json=data,
                headers={"Authorization": "Bearer "+ep["key"], "Content-Type": "application/json"},
                timeout=timeout)
            if r.status_code == 200:
                return r.json()["choices"][0]["message"]["content"]
            print(f"[API] {ep['name']} {r.status_code}", flush=True)
        except Exception as e:
            print(f"[API] {ep['name']} ERR: {e}", flush=True)
        time.sleep(1)
    return ""
