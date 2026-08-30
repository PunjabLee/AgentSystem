#!/usr/bin/env python3
"""P0-6 环节④：答案与引用的一致性

用 M3（AutoDL Qwen3.5-397B-A17B）基于检索结果生成答案，
再核对答案中的关键事实是否都能在引用 chunk 中找到。
"""
import json, re, subprocess, pathlib

def env(k):
    for line in open(".env"):
        m = re.match(rf"^{k}=(.*)$", line.strip())
        if m: return m.group(1).strip()

KEY = env("AUTODL_ART_API_KEY")
RES = json.loads(pathlib.Path("artifacts/p0-6/retrieval.json").read_text(encoding="utf-8"))
OUT = pathlib.Path("artifacts/p0-6"); OUT.mkdir(parents=True, exist_ok=True)

SYS = """你是企业知识库问答助手。严格依据【检索到的资料】作答。

规则：
1. 只使用资料中明确写出的内容，不得补充资料外的知识
2. 每条关键事实（数值、阈值、条款）后必须标注来源编号，如 [1]
3. 资料中找不到答案时，明确说明「所提供资料中未涵盖该内容」，不得编造
4. 涉及事业部特有数值时，必须写明适用于哪个事业部"""

def ask(q, chunks):
    ctx = "\n\n".join(f"[{i}] {c}" for i, c in enumerate(chunks, 1))
    r = subprocess.run(["curl","-sS","--max-time","120",
        "-H",f"Authorization: Bearer {KEY}","-H","Content-Type: application/json",
        "-d", json.dumps({
            "model":"Qwen3.5-397B-A17B",
            "enable_thinking": False,
            "messages":[{"role":"system","content":SYS},
                        {"role":"user","content":f"【检索到的资料】\n{ctx}\n\n【问题】{q}"}],
            "max_tokens": 600, "temperature": 0.1,
        }, ensure_ascii=False),
        "https://www.autodl.art/api/v1/chat/completions"], capture_output=True, text=True)
    d = json.loads(r.stdout)
    if "error" in d: return f"[ERROR] {d['error']}"
    return d["choices"][0]["message"]["content"].strip()

report = []
for item in RES:
    qid, q = item["qid"], item["query"]
    recs = (item["response"].get("records") or [])
    chunks = [(r.get("segment") or {}).get("content","") for r in recs]
    segids = [((r.get("segment") or {}).get("id") or "")[:8] for r in recs]
    print("=" * 76); print(f"【{qid}】{q}"); print("=" * 76)
    if not chunks:
        print("  无召回，跳过"); continue
    ans = ask(q, chunks)
    print(f"  引用可用 chunk: {len(chunks)} 个  seg={segids}")
    print(f"\n  ── 答案 ──\n{chr(10).join('  '+l for l in ans.splitlines())}\n")
    report.append({"qid":qid,"query":q,"segment_ids":segids,"chunks":chunks,"answer":ans})

(OUT/"answers.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print("=" * 76); print(f"已落盘 {OUT}/answers.json —— 下一步人工核对(c)层一致性"); print("=" * 76)
