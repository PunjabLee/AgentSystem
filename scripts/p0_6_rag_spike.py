#!/usr/bin/env python3
"""P0-6 · RAG 引用可溯源验证

按 docs/P0-6-RAG-SPIKE-PLAN.md 的冻结判据执行。
判据在执行前已 commit，本脚本不得修改判据，只负责取证。
"""
import json, os, re, secrets, subprocess, sys, time, pathlib

BASE = "http://127.0.0.1/v1"
DOC  = pathlib.Path("corpus/06-色差与等级判定标准.md")
OUT  = pathlib.Path("artifacts/p0-6"); OUT.mkdir(parents=True, exist_ok=True)

def env(k):
    for line in open(".env"):
        m = re.match(rf"^{k}=(.*)$", line.strip())
        if m: return m.group(1).strip()
    return None

EMB_PROVIDER = "langgenius/ollama/ollama"
EMB_MODEL    = "qwen3-embedding:0.6b"
KEY = env("DIFY_DATASET_API_KEY")
if not KEY:
    sys.exit("✗ .env 中缺 DIFY_DATASET_API_KEY —— 需先在 Dify 界面创建知识库 API 密钥")

def api(method, path, payload=None, files=None, timeout=180):
    cmd = ["curl","-sS","--max-time",str(timeout),"-X",method,
           "-H",f"Authorization: Bearer {KEY}"]
    if files:
        for k,v in files.items(): cmd += ["-F", f"{k}={v}"]
    elif payload is not None:
        cmd += ["-H","Content-Type: application/json","-d",json.dumps(payload,ensure_ascii=False)]
    r = subprocess.run(cmd+[f"{BASE}{path}"], capture_output=True, text=True)
    try: return json.loads(r.stdout)
    except Exception: return {"_raw": r.stdout[:600], "_err": r.stderr[:300]}

# ── 1. 建知识库（父子分段 + 经济型混合检索）────────────────────────
print("=" * 76); print("① 创建知识库"); print("=" * 76)
ds = api("POST", "/datasets", {
    "name": f"P0-6-色差判定-{time.strftime('%m%d-%H%M%S')}-{secrets.token_hex(2)}",
    "description": "P0-6 引用可溯源验证。三 BU 混编，刻意不拆文件以取得基线。",
    "indexing_technique": "high_quality",
    "permission": "only_me",
})
did = ds.get("id")
if not did: sys.exit(f"✗ 建库失败: {json.dumps(ds, ensure_ascii=False)[:400]}")
print(f"  ✅ dataset_id = {did}")

# ── 2. 上传文档，父子分段 ─────────────────────────────────────────
print("\n" + "=" * 76); print("② 上传文档（父子分段：父块按 H2/H3 小节）"); print("=" * 76)
rule = {
    "indexing_technique": "high_quality",
    "doc_form": "hierarchical_model",          # 父子分段
    "process_rule": {
        "mode": "hierarchical",
        "rules": {
            "pre_processing_rules": [
                {"id": "remove_extra_spaces", "enabled": True},
                {"id": "remove_urls_emails",  "enabled": False},
            ],
            "segmentation":        {"separator": "\n## ", "max_tokens": 2000},
            "parent_mode":         "paragraph",
            "subchunk_segmentation": {"separator": "\n", "max_tokens": 300, "chunk_overlap": 50},
        },
    },
}
up = api("POST", f"/datasets/{did}/document/create-by-file",
         files={"data": json.dumps(rule, ensure_ascii=False), "file": f"@{DOC}"})
doc = (up.get("document") or {})
docid, batch = doc.get("id"), up.get("batch")
if not docid: sys.exit(f"✗ 上传失败: {json.dumps(up, ensure_ascii=False)[:500]}")
print(f"  ✅ document_id = {docid}")

print("\n  等待索引完成…")
for i in range(90):
    st = api("GET", f"/datasets/{did}/documents/{batch}/indexing-status")
    d = (st.get("data") or [{}])[0]
    s = d.get("indexing_status")
    if s == "completed":
        print(f"  ✅ 索引完成（{i*2}s）  段落数={d.get('completed_segments')}/{d.get('total_segments')}")
        break
    if s == "error":
        sys.exit(f"✗ 索引失败: {d.get('error')}")
    time.sleep(2)
else:
    sys.exit("✗ 索引超时")

# ── 3. 检查切块实况（顺带定下 §8.1 的空白）──────────────────────
print("\n" + "=" * 76); print("③ 切块实况（设计文档 §8.1 至今为空，本步定下）"); print("=" * 76)
segs = api("GET", f"/datasets/{did}/documents/{docid}/segments")
data = segs.get("data") or []
print(f"  段落总数: {len(data)}")
for s in data[:3]:
    c = (s.get("content") or "").replace("\n", " ")
    print(f"    [{s.get('position')}] {len(c)} 字 | {c[:80]}…")
    if s.get("child_chunks"): print(f"         子块数: {len(s['child_chunks'])}")
(OUT / "segments.json").write_text(json.dumps(segs, ensure_ascii=False, indent=2), encoding="utf-8")

# ── 4. 五个问题（判据见 docs/P0-6-RAG-SPIKE-PLAN.md §3，此处只取证）──
QUESTIONS = [
    ("Q1", "印染事业部优等品的 ΔE 阈值是多少？",          "单跳事实"),
    ("Q2", "优等品怎么判定？",                            "跨 BU 歧义（三选一）"),
    ("Q3", "印染优等品的色差阈值有没有不适用的情况？",      "例外条款"),
    ("Q4", "建陶一等品的平整度偏差上限是多少？",           "表格取值"),
    ("Q5", "产品包装破损应该怎么索赔？",                   "无答案拒答"),
]
print("\n" + "=" * 76); print("④ 五题检索取证"); print("=" * 76)
results = []
for qid, q, kind in QUESTIONS:
    r = api("POST", f"/datasets/{did}/retrieve", {
        "query": q,
        "retrieval_model": {
            "search_method": "hybrid_search",
            "reranking_enable": False,
            "reranking_mode": "weighted_score",
            "weights": {
                "vector_setting": {
                    "vector_weight": 0.7,
                    "embedding_provider_name": EMB_PROVIDER,
                    "embedding_model_name": EMB_MODEL,
                },
                "keyword_setting": {"keyword_weight": 0.3},
            },
            "top_k": 5,
            "score_threshold_enabled": False,
        },
    })
    recs = (r.get("records") or [])
    print(f"\n  【{qid}】{kind}")
    print(f"    Q: {q}")
    if not recs:
        print(f"    ⚠️ 无召回 | {json.dumps(r, ensure_ascii=False)[:200]}")
    for j, rec in enumerate(recs, 1):
        seg = rec.get("segment") or {}
        doc_ = seg.get("document") or {}
        c = (seg.get("content") or "").replace("\n", " ")
        print(f"    {j}. score={rec.get('score'):.4f} seg={seg.get('id','')[:8]}… "
              f"doc={doc_.get('name','?')[:26]}")
        print(f"       {c[:150]}…")
    results.append({"qid": qid, "kind": kind, "query": q, "response": r})

(OUT / "retrieval.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

# ── 5. 元数据过滤实测（兑现此前只查源码未实跑的驳回）───────────────
print("\n" + "=" * 76); print("⑤ 元数据过滤实测"); print("=" * 76)
print("  此前依据源码 RetrievalModel.metadata_filtering_conditions 驳回了")
print("  「Dify /retrieve 不支持元数据过滤」的评审结论，但只查源码未实跑。")
mf = api("POST", f"/datasets/{did}/retrieve", {
    "query": "优等品怎么判定？",
    "retrieval_model": {
        "search_method": "hybrid_search", "reranking_enable": False,
        "reranking_mode": "weighted_score",
        "weights": {
            "vector_setting": {"vector_weight": 0.7,
                               "embedding_provider_name": EMB_PROVIDER,
                               "embedding_model_name": EMB_MODEL},
            "keyword_setting": {"keyword_weight": 0.3},
        },
        "top_k": 5, "score_threshold_enabled": False,
        "metadata_filtering_conditions": {
            "logical_operator": "and",
            "conditions": [{"name": "bu_code", "comparison_operator": "is", "value": "BU-A"}],
        },
    },
})
if "records" in mf:
    print(f"  ✅ 接口接受 metadata_filtering_conditions，返回 {len(mf['records'])} 条")
    print("     （文档未打元数据时应为全量或空，关键是接口不报错）")
else:
    print(f"  🔴 被拒绝 —— 此前的驳回不成立: {json.dumps(mf, ensure_ascii=False)[:300]}")
(OUT / "metadata_filter.json").write_text(json.dumps(mf, ensure_ascii=False, indent=2), encoding="utf-8")

print("\n" + "=" * 76)
print(f"取证完成。原始响应已落盘 {OUT}/")
print("下一步：对照 docs/P0-6-RAG-SPIKE-PLAN.md §3 的三层判据人工核对。")
print("=" * 76)
