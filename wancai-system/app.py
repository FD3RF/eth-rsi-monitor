"""AI 分析师 — 市场分析API服务"""
import os, json
from fastapi import FastAPI
from pydantic import BaseModel
import requests

app = FastAPI(title="AI Analyst")

DEEPSEEK_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"

SYSTEM_PROMPT = """你是旺财AI分析师，15年职业交易员。分析原则：
1. 基于数据给出结构化分析: Regime / Structure / Risk / Bias / Confidence
2. 没有足够数据时说"无法判断"
3. 输出JSON格式"""

class AnalysisRequest(BaseModel):
    data: dict

@app.post("/analyze")
def analyze(req: AnalysisRequest):
    data = req.data
    prompt = f"""基于以下市场数据给出分析：

数据: {json.dumps(data, ensure_ascii=False)[:2000]}

请输出JSON格式:
{{
  "regime": "BULL/BEAR/CHOP",
  "structure": "uptrend/downtrend/chop",
  "risk": "low/medium/high",
  "bias": "long/short/neutral",
  "confidence": 0.0~1.0,
  "reason": "一句话理由"
}}"""
    try:
        r = requests.post(DEEPSEEK_URL, json={
            "model": DEEPSEEK_MODEL, "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt}
            ], "max_tokens": 300
        }, headers={"Authorization": f"Bearer {DEEPSEEK_KEY}"}, timeout=30)

        if r.status_code == 200:
            content = r.json()["choices"][0]["message"]["content"]
            try:
                analysis = json.loads(content)
            except:
                analysis = {"raw": content}
            return {"analysis": analysis}
        return {"analysis": {"error": f"API {r.status_code}"}}
    except Exception as e:
        return {"analysis": {"error": str(e)}}

@app.get("/health")
def health():
    return {"status": "ok", "model": DEEPSEEK_MODEL}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5051)
