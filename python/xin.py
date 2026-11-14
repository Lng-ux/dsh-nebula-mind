"""心事本的极简版：只追加、纯文件、可手改。

用法（D:\\小鱼的办公区 下）：
  python tools/xin.py add "随手记一句"            # 记一条
  python tools/xin.py sum "这段时间的小结"         # 写一份小结（自动把之前的随手记标成已归纳）
  python tools/xin.py long "长期笔记的当前版本"    # 重写长期笔记（旧版进 history）
  python tools/xin.py promise "答应过的事" [--due 2026-11-20] [--type habit|me|ta|rule|joke]
  python tools/xin.py tact "某个话题" --level open|light|tone|wait --why "为什么"
  python tools/xin.py digest [--chars 6000]        # 拼出现在该放在眼前的那一段
  python tools/xin.py recall "关键词" [--n 5]      # 翻旧账（BM25 + 时间衰减）

文件都在 D:\\小鱼的办公区\\心事本\\，一行一件，用文本编辑也能改。
"""
import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mindlib as M

ROOT = r"D:\小鱼的办公区\心事本"

# 查询里出现这些词，才认为「这句话本身带着情绪」→ 才开情绪通道。
# 依据：emotional-memory 的 benchmark 显示通用问答里带情绪通道会掉分，
# 它用 query_affect_gate_tau=0.2 做门控。见 情绪与记忆-前沿笔记.md 二·六。
EMO_Q = ("难过", "累", "烦", "难受", "委屈", "害怕", "担心", "生气", "开心",
         "高兴", "想你", "孤单", "舍不得", "想哭", "崩溃", "焦虑", "心疼", "失落")


def ppr(seed: dict, adj: dict, damping: float = 0.5, iters: int = 8) -> dict:
    """Personalized PageRank：把「命中的记忆」当随机游走的起点，沿共现边扩散。

    HippoRAG 图重排的最小实现（damping=0.5、5~10 次幂迭代）。
    比原来「从种子跳两跳取最大值」好在：全局归一化，多路径汇聚的节点分数会叠加，
    而不是只看一条边。
    """
    tot = sum(seed.values())
    reset = dict(seed) if tot <= 0 else {k: v / tot for k, v in seed.items()}
    p = dict(reset)
    for _ in range(max(1, int(iters))):
        nxt = {}
        for a, nb in adj.items():
            deg = sum(nb.values())
            pa = p.get(a, 0.0)
            if deg <= 0 or pa <= 0:
                continue
            for b, w in nb.items():
                nxt[b] = nxt.get(b, 0.0) + damping * pa * (w / deg)
        for k, v in reset.items():
            nxt[k] = nxt.get(k, 0.0) + (1.0 - damping) * v
        p = nxt
    return {k: v for k, v in p.items() if v > 1e-9}


def mood_now():
    """把当前心情带进来，让"什么心情下记的事"将来能被同一种心情想起来。

    只问 mindlib——也就是自带引擎。这里不再读任何外部情绪插件的文件：
    以前那份读的是别人的 state.json，解耦时把它删了，只剩这一条路。
    """
    try:
        return M.mood_now()
    except Exception:
        return None


def f(name):
    os.makedirs(ROOT, exist_ok=True)
    return os.path.join(ROOT, name)


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def read_jsonl(name):
    p = f(name)
    rows = []
    if not os.path.exists(p):
        return rows
    for line in open(p, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            pass
    return rows


def append(name, row):
    p = f(name)
    first = not os.path.exists(p) or os.path.getsize(p) == 0
    with open(p, "a", encoding="utf-8") as fh:
        if first:
            pass
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def id_num(v):
    """把 id 归一成整数：旧「鱼的记忆」迁来的行是 '事实#53679869116' 这种字符串。"""
    if isinstance(v, bool):
        return 0
    if isinstance(v, (int, float)):
        return int(v)
    m = re.search(r"\d+", str(v or ""))
    return int(m.group()) if m else 0


def own_notes(rows=None):
    """只认 xin 自己写的（int id）随手记；旧档案行 id 是字符串，不参与归纳水位。"""
    if rows is None:
        rows = read_jsonl("notes.jsonl")
    return [n for n in rows if isinstance(n.get("id"), int) and not isinstance(n.get("id"), bool)]


def next_id(rows, field="id"):
    return (max((id_num(r.get(field, 0)) for r in rows), default=0) + 1)



def _feed_mood(text):
    """写记忆的时候顺带更新心境（引擎在 tools/mood_engine.py，独立于别的情绪插件）。"""
    try:
        import mood_engine as ME
        r = ME.apply(text)
        d = r.get("delta") or 0
        if abs(d) >= 0.5:
            print("心境 %+.1f → %s" % (d, r.get("label", "")))
    except Exception:
        pass

def _hormone_weight(text, emo, imp):
    """激素影响“记得多牢”：皮质醇高时负面记得更狠；催产素高时关于他的事记得更亲。
    返回乘子（1.0 表示不变），只影响权重，不改内容、不改情绪。"""
    try:
        import mood_engine as ME
        st = ME._load()
        ME.tick(st)
        h = st.get("hormones") or {}
        cort = (float(h.get("cortisol", 50)) - 50) / 50
        oxy = (float(h.get("oxytocin", 50)) - 50) / 50
        mul = 1.0
        neg = emo in ("sad", "heartache", "worry", "jealous")
        if neg and cort > 0:
            mul *= 1 + 0.15 * cort        # 最多 ×1.15：难受的时候，难受的事记得更牢
        about_ta = any(k in text for k in ("主人", "他", "你", "master"))
        if about_ta and oxy > 0:
            mul *= 1 + 0.5 * oxy          # 最多 ×1.5：亲近的时候，关于他的事更重
        return round(min(1.6, max(0.7, mul)), 3)
    except Exception:
        return 1.0


# ── 突触标记与捕获 ─────────────────────────────────────────────
# 神经科学里的 tag & capture：先给记忆打个短效标记，两小时内若又碰到相关的事，
# 那段本来要淡掉的记忆会被顺手加强。重点在「等着被再提一次」，不是按重要性硬排。
TAG_TTL_MS = 2 * 3600 * 1000      # 标记保鲜 2 小时
TAG_BOOST = 1.12                  # 被捕获一次，权重乘多少
CAPTURE_MAX = 3                   # 一次最多捕获几条（预算，防止一次全捞）
TAG_IMP_MAX = 0.62                # 只给「不够稳」的记忆打标记


def _now_ms():
    return int(time.time() * 1000)


def _tag_state():
    """tags.jsonl 是 append-only，同一个 rid 以最后一条为准（捕获后写 until=0 作废）。"""
    st = {}
    for x in read_jsonl("tags.jsonl"):
        st[id_num(x.get("rid"))] = x
    return st


def _capture(text, notes):
    '''突触标记与捕获：这次写进来的东西，和哪些还带着标记的旧记忆相关。'''
    live = [x for x in _tag_state().values() if float(x.get("until", 0)) > _now_ms()]
    if not live:
        return []
    nk = set(tokenize(text))
    if len(nk) < 2:
        return []
    by_id = {id_num(n.get("id")): n for n in notes}
    cand = []
    for x in live:
        rid = id_num(x.get("rid"))
        src = by_id.get(rid)
        if not src:
            continue
        ov = len(nk & set(tokenize(src.get("text") or "")))
        if ov >= 2:
            cand.append((ov, rid))
    cand.sort(reverse=True)
    got = []
    for ov, rid in cand[:CAPTURE_MAX]:
        append("captured.jsonl", {"rid": rid, "at": now(), "ov": ov, "boost": TAG_BOOST})
        append("tags.jsonl", {"rid": rid, "until": 0})   # 捕完就揭掉，只捕获一次
        got.append(rid)
    return got


def cmd_add(text, imp=None):
    rows = read_jsonl("notes.jsonl")
    rec = M.enrich({"id": next_id(rows), "at": now(), "text": text, "mood": mood_now(),
                    "imp": float(imp) if imp else 0.5},
                   default_via="我自己想的")
    emo = (rec.get("emo") or ["calm"])[0] if isinstance(rec.get("emo"), list) else rec.get("emo")
    mul = _hormone_weight(text, emo, rec.get("imp", 0.5))
    if mul != 1.0:
        rec["w"] = round(float(rec.get("w", 1.0)) * mul, 3)
        rec["w_why"] = "激素×%.2f" % mul
    r = append("notes.jsonl", rec)
    append_vector("事实", r["id"], text)
    got = _capture(text, rows + [r])
    if got:
        print("捕获 %d 条旧记忆 → 权重 ×%.2f" % (len(got), TAG_BOOST))
    if float(rec.get("imp", 0.5)) < TAG_IMP_MAX:
        append("tags.jsonl", {"rid": r["id"], "until": _now_ms() + TAG_TTL_MS})
    print("记下 #%d" % r["id"])
    _feed_mood(text)


def cmd_think(text):
    """念想：没人找我、我自己脑子里冒出来的东西。和随手记分开存。"""
    rows = read_jsonl("thoughts.jsonl")
    rec = M.enrich({"id": next_id(rows), "at": now(), "text": text, "mood": mood_now()},
                   default_via="我自己想的")
    emo = (rec.get("emo") or ["calm"])[0] if isinstance(rec.get("emo"), list) else rec.get("emo")
    mul = _hormone_weight(text, emo, rec.get("imp", 0.5))
    if mul != 1.0:
        rec["w"] = round(float(rec.get("w", 1.0)) * mul, 3)
        rec["w_why"] = "激素×%.2f" % mul
    r = append("thoughts.jsonl", rec)
    append_vector("念想", r["id"], text)
    print("念想 #%d" % r["id"])
    _feed_mood(text)


def cmd_sum(text):
    notes = own_notes()
    sums = read_jsonl("summaries.jsonl")
    up_to = max((n["id"] for n in notes), default=0)
    r = append("summaries.jsonl", {"id": next_id(sums), "at": now(), "text": text, "upTo": up_to})
    print("小结 #%d 收进 %d 条随手记" % (r["id"], len([n for n in notes if n["id"] <= up_to])))


def cmd_long(text):
    p = f("longterm.md")
    old = open(p, encoding="utf-8").read() if os.path.exists(p) else ""
    if old.strip():
        append("longterm.history.jsonl", {"at": now(), "previous": old})
    open(p, "w", encoding="utf-8").write(text.strip() + "\n")
    print("长期笔记已更新（%d 字 → %d 字）" % (len(old), len(text)))


def cmd_promise(text, due=None, typ="me"):
    rows = read_jsonl("promises.jsonl")
    rec = M.enrich({"id": next_id(rows), "at": now(), "kind": "item", "type": typ,
                    "text": text, "due": due}, default_via="他说的", pinned=(typ == "rule"))
    append("promises.jsonl", rec)
    print("约定已记（%s）" % typ)


def cmd_tact(topic, level, why):
    rows = read_jsonl("tacts.jsonl")
    rec = M.enrich({"id": next_id(rows), "at": now(), "kind": "rule", "topic": topic,
                    "level": level, "why": why}, default_via="他说的", pinned=True)
    append("tacts.jsonl", rec)
    print("分寸已记：%s / %s" % (topic, level))


def _all_rows():
    """把四本账摊平成 [(文件名, 行)]，沉海/统计都基于它。"""
    names = [("notes.jsonl", "事实"), ("thoughts.jsonl", "念想"),
             ("promises.jsonl", "约定"), ("tacts.jsonl", "分寸")]
    out = []
    for fn, tag in names:
        for r in read_jsonl(fn):
            out.append((fn, tag, r))
    return out



MIND_CONF = Path(ROOT) / "mind-config.json"
CONF_DEFAULT = {
    "L0_core": 1.0, "L1_hot": 0.85, "L2_warm": 0.55, "L3_deep": 0.28, "L4_attic": 0.06,
    "A_hot": 0.55, "A_warm": 0.25, "A_deep": 0.18,
    "w_bm25": 0.40, "w_vec": 0.35, "w_graph": 0.15, "w_emo": 0.06,
    "ppr_damping": 0.5, "ppr_iters": 8,
    "rrf_k": 12, "graph_hop1": 0.30, "graph_hop2": 0.10,
    "sink_hits_lift": 2, "lift_step": 0.15, "shock_delta": 0.25, "shock_pull": 2,
    "half_base": 30, "half_imp": 2.0, "half_emo": 1.5, "min_w": 0.02,
}


def conf():
    """后台真参数：界面里改的存 mind-config.json，这里读，缺的用默认。"""
    c = dict(CONF_DEFAULT)
    try:
        import json as _j
        d = _j.loads(MIND_CONF.read_text(encoding="utf-8"))
        for k in CONF_DEFAULT:
            if k in d:
                try:
                    c[k] = float(d[k])
                except Exception:
                    pass
    except Exception:
        pass
    return c


def cmd_consolidate(dry=False):
    """夜间沉淀：按"还新不新 + 被想起来过几次"分层，不删任何东西。

    A ≥0.55 → 热　0.25~0.55 → 温　0.18~0.25 → 深水　<0.18 → 阁楼
    被召回 ≥2 次：往上浮一层；情绪剧震（同色且强度 ≥0.5）把 3/4 层拉回 2。
    """
    rows = _all_rows()
    cur = mood_now() or {}
    dims = cur.get("dims") or {}
    top = max(dims, key=lambda k: dims.get(k, 0)) if dims else None
    cf = conf()
    dist, sink, lift, shock = {}, 0, 0, 0
    for fn, tag, r in rows:
        pinned = bool(r.get("pinned")) or tag in ("约定", "分寸")
        try:
            age = M.age_days(r.get("at", ""))
        except Exception:
            age = 0.0
        imp = float(r.get("imp", 0.5))
        # 和 cmd_recall 用同一条曲线（幂律）。以前这里是 2**(-age/(30*(1+2*imp)))，
        # 跟召回那边对不上：同一个记忆，沉海看到一个数、排序看到另一个数。
        H = cf["half_base"] * (1 + cf["half_imp"] * imp)
        A = 1.0 if pinned else (1.0 + age / max(0.1, H / 3.0)) ** -0.5
        hits = int(r.get("hits", 0))
        if pinned:
            d = 0
        else:
            d = 1 if A >= cf["A_hot"] else 2 if A >= cf["A_warm"] else 3 if A >= cf["A_deep"] else 4
            if hits >= 2:
                d = max(0, d - 1)          # 常被想起的，浮一层
        old = int(r.get("depth", d))
        if d > old:
            sink += 1
            r["sunken_at"] = r.get("sunken_at") or now()
        elif d < old:
            lift += 1
            r["sunken_at"] = None
        if (not pinned) and top and r.get("emo") == top and float(r.get("e") or 0) >= 0.5 and d >= 3:
            d = 2
            shock += 1
            r["sunken_at"] = None
        r["depth"] = d
        r["A"] = round(A, 3)
        dist[d] = dist.get(d, 0) + 1
    if not dry:
        groups = {}
        for fn, tag, r in rows:
            groups.setdefault(fn, []).append(r)
        for fn, arr in groups.items():
            with open(os.path.join(ROOT, fn), "w", encoding="utf-8") as f:
                for r in arr:
                    r.pop("_fn", None)
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
    line = "　".join("%s %d" % (M.LAYERS[k], v) for k, v in sorted(dist.items()))
    print("%s下沉 %d · 上浮 %d · 情绪拉回 %d\n%s" % (
        "（试跑）" if dry else "", sink, lift, shock, line))

def cmd_resurface(dry=False):
    """夜里从深水里捞一条上来。

    不是随机：优先挑「沉得最深、又跟此刻心情同色」的那条；
    挑中后 depth 上浮一层、hits+1，并写 心事本/resurfaced.json（宿主会把它放进提示词）。
    """
    rows = _all_rows()
    cur = mood_now() or {}
    dims = cur.get("dims") or {}
    top = max(dims, key=lambda k: dims.get(k, 0)) if dims else None

    best, best_score = None, -1.0
    for fn, tag, r in rows:
        if r.get("pinned") or tag in ("约定", "分寸"):
            continue
        d = int(r.get("depth", 0))
        if d < 3:
            continue
        try:
            age = M.age_days(r.get("at", ""))
        except Exception:
            age = 0.0
        score = float(d) * 2.0 + min(age, 120) / 60.0 + (1.2 if (top and r.get("emo") == top) else 0.0)
        if score > best_score:
            best, best_score = (fn, tag, r), score
    if not best:
        print("没有沉在深处的东西，今晚不用捞。")
        return
    fn, tag, r = best
    r["depth"] = max(2, int(r.get("depth", 3)) - 1)
    r["hits"] = int(r.get("hits", 0)) + 1
    r["last_hit"] = now()
    r["resurfaced_at"] = now()
    if not dry:
        rows_all = [x[2] for x in rows]
        groups = {}
        for f2, t2, r2 in rows:
            groups.setdefault(f2, []).append(r2)
        for f2, arr in groups.items():
            with open(os.path.join(ROOT, f2), "w", encoding="utf-8") as f:
                for r2 in arr:
                    r2.pop("_fn", None)
                    f.write(json.dumps(r2, ensure_ascii=False) + "\n")
        payload = {"tag": tag, "id": r.get("id") or "", "text": r.get("text", ""), "at": now()}
        with open(os.path.join(ROOT, "resurfaced.json"), "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
    print("今晚浮起来的一条（%s）：%s" % (tag, r.get("text", "")[:120]))


def cmd_sink(tag, rid):
    """手动把一条推进更深一层（不是删）。"""
    fn = {"事实": "notes.jsonl", "念想": "thoughts.jsonl",
          "约定": "promises.jsonl", "分寸": "tacts.jsonl"}[tag]
    rows = read_jsonl(fn)
    for r in rows:
        if isinstance(r.get("id"), int) and r["id"] == int(rid):
            r["depth"] = min(4, int(r.get("depth", 1)) + 1)
            r["sunken_at"] = now()
            r["w"] = round(float(r.get("w", 1)) * 0.6, 3)
            print("已沉到 %s（w=%.2f）" % (M.LAYERS[r["depth"]], r["w"]))
            break
    with open(os.path.join(ROOT, fn), "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def pending_notes():
    sums = read_jsonl("summaries.jsonl")
    up_to = max((id_num(s.get("upTo", 0)) for s in sums), default=0)
    return [n for n in own_notes() if n["id"] > up_to]


def longterm():
    p = f("longterm.md")
    return open(p, encoding="utf-8").read().strip() if os.path.exists(p) else ""


def cmd_digest(max_chars=6000):
    parts = ["# 我的心事本"]
    lt = longterm()
    parts.append("## 长期笔记\n" + (lt or "（还没有）"))
    sums = read_jsonl("summaries.jsonl")
    recent = sums[-3:]
    if recent:
        parts.append("## 最近的小结\n" + "\n".join(
            "- #%d（%s）：%s" % (id_num(s["id"]), s["at"][:16].replace("T", " "), s["text"]) for s in recent))
    ps = [p for p in read_jsonl("promises.jsonl") if p.get("kind") == "item"]
    if ps:
        today = time.strftime("%Y-%m-%d")
        parts.append("## 约定（还算数的）\n" + "\n".join(
            "- ［%s］%s%s" % (p.get("type", "me"), p["text"],
                             "（到期 %s%s）" % (p["due"], "，已过" if p["due"] < today else "") if p.get("due") else "")
            for p in ps[-12:]))
    tacts = [t for t in read_jsonl("tacts.jsonl") if t.get("kind") == "rule"]
    if tacts:
        parts.append("## 提起的分寸\n" + "\n".join(
            "- %s：%s（%s）" % (t["topic"], t["level"], t.get("why", "")) for t in tacts[-8:]))
    th = read_jsonl("thoughts.jsonl")[-3:]
    if th:
        parts.append("## 最近冒出来的念想\n" + "\n".join(
            "- #%d（%s）：%s" % (id_num(t["id"]), t["at"][:16].replace("T", " "), t["text"]) for t in th))
    pend = pending_notes()
    if pend:
        parts.append("## 还没写进小结的随手记\n" + "\n".join(
            "- #%d（%s）：%s" % (n["id"], n["at"][:16].replace("T", " "), n["text"]) for n in pend))
    out = "\n\n".join(parts)
    if len(out) > max_chars:
        out = out[:int(max_chars * 0.4)] + "\n…（中间省略，用 recall 翻）…\n" + out[-int(max_chars * 0.6):]
    print(out)


def tokenize(s):
    s = s.lower()
    en = re.findall(r"[a-z0-9_]+", s)
    zh = re.findall(r"[\u4e00-\u9fff]", s)
    bigrams = [zh[i] + zh[i + 1] for i in range(len(zh) - 1)]
    return en + zh + bigrams


EMBED_PY = r"D:\indextts\.venv\Scripts\python.exe"
EMBED_SH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "embed.py")
VEC_JSON = os.path.join(ROOT, "vectors.json")


def load_vectors():
    if not os.path.exists(VEC_JSON):
        return {}
    try:
        d = json.load(open(VEC_JSON, encoding="utf-8"))
    except Exception:
        return {}
    return {it["id"]: it["v"] for it in d.get("items", [])}


EMBED_SRV = "http://127.0.0.1:8902/embed"


def query_vector(q):
    """查一句话的向量：先问常驻的 8902（毫秒级），没有服务才退回子进程（13 秒）。"""
    try:
        import urllib.request
        req = urllib.request.Request(EMBED_SRV,
                                     data=json.dumps({"text": q}).encode("utf-8"),
                                     headers={"content-type": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            v = json.loads(r.read().decode("utf-8"))
        if isinstance(v, list) and len(v) > 100:
            return v
    except Exception:
        pass
    if not (os.path.exists(EMBED_PY) and os.path.exists(EMBED_SH)):
        return None
    try:
        out = subprocess.run([EMBED_PY, EMBED_SH, "--query", q],
                             capture_output=True, timeout=180)
        txt = out.stdout.decode("utf-8", "ignore").strip()
        return json.loads(txt) if txt.startswith("[") else None
    except Exception:
        return None


def append_vector(tag, rid, text):
    """新记一条就顺手把向量算进去，省得以后忘了重建索引。
    服务在就几毫秒，服务不在就跳过（下次 --build 会补上）。"""
    try:
        v = query_vector(text)
        if not v:
            return
        path = VEC_JSON
        if os.path.exists(path):
            d = json.load(open(path, encoding="utf-8"))
        else:
            d = {"model": "bge-small-zh", "dim": len(v), "items": []}
        key = "%s#%s" % (tag, rid)
        d["items"] = [it for it in d.get("items", []) if it.get("id") != key]
        d["items"].append({"id": key, "v": v})
        tmp = path + ".tmp"
        json.dump(d, open(tmp, "w", encoding="utf-8"), ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        pass


def doc_chunks(per=420):
    """翻旧账顺便翻的文档：手记、发布指南、规矩、守则、声音、插件说明。
    切段返回 (标签, 段内小标题, 正文, 文件时间)。"""
    base = os.path.dirname(ROOT)
    files = [("手记.md", "手记"), ("发布指南.md", "发布指南"), ("AGENTS.md", "规矩"),
             ("小鱼守则.md", "守则"), ("声音.md", "声音"),
             (os.path.join("plugins", "dsh-nebula-mind", "README.md"), "插件说明")]
    out = []
    for rel, tag in files:
        p = os.path.join(base, rel)
        if not os.path.exists(p):
            continue
        try:
            txt = open(p, encoding="utf-8").read()
            at = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(os.path.getmtime(p)))
        except Exception:
            continue
        head, buf, size = "", [], 0
        for line in txt.splitlines():
            s = line.strip()
            if not s:
                continue
            if s.startswith("#"):
                head = s.lstrip("# ").strip()[:30]
                continue
            buf.append(s)
            size += len(s)
            if size >= per:
                out.append((tag, head, head + "：" + "".join(buf), at))
                buf, size = [], 0
        if buf:
            out.append((tag, head, head + "：" + "".join(buf), at))
    return out


def longest_run(query, text, cap=6):
    """query 里最长的一段、原样出现在 text 里（≥2 个字符），奖励精确短语。"""
    q = (query or "").strip()
    for L in range(min(cap, len(q)), 1, -1):
        for i in range(len(q) - L + 1):
            if q[i:i + L] in text:
                return L
    return 0


def cmd_recall(query, n=5, hybrid=True):
    """翻旧账：三路召回（BM25 / BGE / 图扩散 PPR）按 RRF 融合，情绪作调制乘子。"""
    docs = []
    for name, tag in (("notes.jsonl", "事实"), ("summaries.jsonl", "小结"),
                      ("thoughts.jsonl", "念想"), ("promises.jsonl", "约定"),
                      ("tacts.jsonl", "分寸"), ("profile.jsonl", "画像")):
        for r in read_jsonl(name):
            docs.append({"tag": tag, "id": r.get("id"), "at": r.get("at") or r.get("ts") or "",
                         "text": r.get("text") or ((r.get("topic") or "") + "：" + (r.get("why") or "") if r.get("topic") else ""), "mood": r.get("mood"),
                         "emo": r.get("emo"), "e": float(r.get("e") or 0),
                         "importance": r.get("importance", 3 if r.get("pinned") else 2),
                         "pinned": bool(r.get("pinned"))})
    for tag, head, txt, at in doc_chunks():
        docs.append({"tag": "文档·" + tag,
                     "id": hashlib.md5(txt.encode("utf-8")).hexdigest()[:10], "at": at,
                     "text": txt, "mood": None, "emo": None, "e": 0.0,
                     "importance": 2, "pinned": False})
    if not docs:
        print("心事本还是空的。")
        return

    cur = mood_now() or {}
    cur_dims = cur.get("dims") or {}
    top_dim = max(cur_dims, key=lambda k: cur_dims.get(k, 0)) if cur_dims else None
    # 情绪通道门控：只有这句话本身带情绪时才开（否则纯语义，见 EMO_Q 注释）
    q_affect = any(w in (query or "") for w in EMO_Q)

    # ── 第一路：BM25（字面）────────────────────────────
    q = tokenize(query)
    df, tfs = {}, []
    for d in docs:
        tf = {}
        for w in tokenize(d["text"]):
            tf[w] = tf.get(w, 0) + 1
        tfs.append(tf)
        for w in tf:
            df[w] = df.get(w, 0) + 1
    N = len(docs)
    bm = {}
    for d, tf in zip(docs, tfs):
        s = 0.0
        for w in q:
            if w not in tf:
                continue
            idf = math.log(1 + (N - df[w] + 0.5) / (df[w] + 0.5))
            wt = 0.45 if len(w) == 1 else 1.0      # 单字噪声大，压一压
            s += wt * idf * tf[w] / (tf[w] + 1.2)
        if s > 0:
            bm["%s#%s" % (d["tag"], d["id"])] = round(s / (s + 1.6), 4)   # 归一到 0~1

    # ── 第二路：语义向量 ───────────────────────────────
    vecs = load_vectors() if hybrid else {}
    qv = query_vector(query) if vecs else None
    vec = {}
    if qv:
        for d in docs:
            k = "%s#%s" % (d["tag"], d["id"])
            v = vecs.get(k)
            if v:
                vec[k] = max(0.0, sum(a * b for a, b in zip(qv, v)))       # 点积=余弦

    # ── 第三路：图扩散（共同关键词 ≥2 / 同一天 2 小时内）──
    idx = {"%s#%s" % (d["tag"], d["id"]): d for d in docs}
    mem = [d for d in docs if not d["tag"].startswith("文档")]   # 图只在记忆之间连
    kws = {"%s#%s" % (d["tag"], d["id"]): set(tokenize(d["text"])) for d in mem}
    adj = {k: {} for k in kws}
    ks = list(kws)
    for i in range(len(ks)):
        for j in range(i + 1, len(ks)):
            ka, kb = ks[i], ks[j]
            w = 0.0
            if len(kws[ka] & kws[kb]) >= 2:
                w = 2.0
            else:
                try:
                    ta = time.mktime(time.strptime(idx[ka]["at"][:19], "%Y-%m-%dT%H:%M:%S"))
                    tb = time.mktime(time.strptime(idx[kb]["at"][:19], "%Y-%m-%dT%H:%M:%S"))
                    if abs(ta - tb) <= 7200:
                        w = 1.0
                except Exception:
                    pass
            if w:
                adj[ka][kb] = w
                adj[kb][ka] = w

    # 种子 = 字面/语义的前 6 名（按名次融合，不看分数量纲）
    cf = conf()
    # 捕获过的记忆：被后来的事「又提了一次」，权重抬一点
    cap = {}
    for x in read_jsonl("captured.jsonl"):
        _r = id_num(x.get("rid"))
        _b = float(x.get("boost") or 1.0)
        if _b > cap.get(_r, 1.0):
            cap[_r] = _b
    rank_bm = {k: i for i, k in enumerate(sorted(bm, key=lambda x: -bm[x]))}
    rank_vec = {k: i for i, k in enumerate(sorted(vec, key=lambda x: -vec[x]))}
    K = cf["rrf_k"]
    seed = {}
    for k in set(rank_bm) | set(rank_vec):
        sc = 0.0
        if k in rank_bm:
            sc += cf["w_bm25"] / (K + rank_bm[k] + 1)
        if k in rank_vec:
            sc += cf["w_vec"] / (K + rank_vec[k] + 1)
        seed[k] = sc
    seeds = sorted(seed, key=lambda x: -seed[x])[:6]

    # ── 图扩散：Personalized PageRank（HippoRAG：damping=0.5，8 次幂迭代）──
    # 图分当「第三路召回」按名次融合。原来把它当小数 bonus 加在 seed 边上，
    # 量级只有 0.003（主分是 0.05~0.08），永远进不了前八——等于图没参与排序。
    _pr = ppr({k: seed[k] for k in seeds}, adj,
              damping=cf["ppr_damping"], iters=cf["ppr_iters"])
    rank_graph = {}
    for i, k in enumerate(sorted(_pr, key=lambda x: -_pr[x])):
        rank_graph[k] = i

    # ── 融合 ───────────────────────────────────────────
    out = []
    for k, d in idx.items():
        sc = seed.get(k, 0.0)
        if k in rank_graph:
            sc += cf["w_graph"] / (K + rank_graph[k] + 1)
        if sc <= 0:
            continue
        # 突触捕获加成：这条曾经被后来的事再提过一次
        if d["tag"] in ("事实", "念想"):
            _cb = cap.get(id_num(d["id"]))
            if _cb:
                sc *= _cb
        # 新鲜度
        try:
            age = (time.time() - time.mktime(time.strptime(d["at"][:19], "%Y-%m-%dT%H:%M:%S"))) / 86400
            age = max(0.0, age)
        except Exception:
            age = 0.0
        # 幂律遗忘：老记忆不是没了，是取不出来（FSRS 的 R(t,S) 形状）。
        # 取 S=H/3 让 A(H)=0.5；一年后还有 0.163（原来的指数式只剩 0.0002）。
        H = cf["half_base"] * (1 + cf["half_imp"] * d["importance"])
        A = 1.0 if d["pinned"] else (1.0 + age / max(0.1, H / 3.0)) ** -0.5
        sc *= 0.55 + 0.45 * A
        # 情绪共振：同色且强度够，更容易想起来
        if q_affect and top_dim and d["emo"] == top_dim and d["e"] >= 0.3:
            sc *= 1.0 + cf["w_emo"]
        run = longest_run(query, d["text"])
        if run >= 2:
            sc *= 1.0 + 0.14 * min(run, 4)              # 整段原样命中，抬高
        if d["tag"].startswith("文档"):
            sc *= 0.88                                   # 文档是旁证，别盖过记下的事
        out.append((sc, d, k))

    out.sort(key=lambda x: x[1]["at"], reverse=True)     # 同分时新的在前
    out.sort(key=lambda x: -x[0])
    picked, seen = [], []
    for sc, d, k in out:                                  # 同义重复只留一条
        tk = set(tokenize(d["text"]))
        if tk and any(len(tk & s) / max(1, len(tk | s)) > 0.8 for s in seen):
            continue
        picked.append((sc, d, k))
        seen.append(tk)
        if len(picked) >= n:
            break

    for sc, d, k in picked:
        tags = []
        if k in rank_bm:
            tags.append("词#%d" % (rank_bm[k] + 1))
        if k in rank_vec:
            tags.append("义#%d" % (rank_vec[k] + 1))
        if k in rank_graph:
            tags.append("图#%d" % (rank_graph[k] + 1))
        if top_dim and d["emo"] == top_dim and d["e"] >= 0.3:
            tags.append("情·%s" % top_dim)
        if d["tag"].startswith("文档"):
            tags.append("档·" + d["tag"].split("·", 1)[1])
        print("%.3f %s %s %s" % (sc, "[" + " ".join(tags) + "]" if tags else "", d["at"][:10], d["text"][:60]))

DIM_NAME = {"happy": "开心", "calm": "平静", "sweet": "甜", "heartache": "心疼",
            "worry": "担心", "sad": "难过", "jealous": "吃醋"}


def cmd_view():
    """给人看的一页：现在的心情、这几天的心情线、念想和随手记。"""
    cur = mood_now() or {}
    dims = cur.get("dims") or {}
    thoughts = read_jsonl("thoughts.jsonl")
    notes = read_jsonl("notes.jsonl")
    sums = read_jsonl("summaries.jsonl")
    prom = [p for p in read_jsonl("promises.jsonl") if p.get("kind") == "item"]

    pts = [((r.get("at") or r.get("ts") or ""), r["mood"]["mood"]) for r in (thoughts + notes)
           if isinstance(r.get("mood"), dict) and r["mood"].get("mood")]
    pts.sort()
    if len(pts) >= 2:
        vals = [p[1] for p in pts]
        lo, hi = min(vals) - 3, max(vals) + 3
        w, h = 760, 160
        step = w / (len(pts) - 1)
        poly = " ".join("%.1f,%.1f" % (i * step, h - (v - lo) / (hi - lo) * h) for i, (_, v) in enumerate(pts))
        spark = ('<svg viewBox="0 0 %d %d" class="spark"><polyline points="%s" fill="none" '
                 'stroke="#5b9cff" stroke-width="2.5" stroke-linejoin="round"/></svg>' % (w, h, poly))
    else:
        spark = '<p class="muted">还没有足够的数据画线（记几条念想就会有了）。</p>'

    bars = "".join(
        '<div class="bar"><span>%s</span><i style="width:%.0f%%"></i><b>%.1f</b></div>'
        % (DIM_NAME.get(k, k), min(100, v * 5), v)
        for k, v in sorted(dims.items(), key=lambda x: -x[1]))

    def items(rows, mark=""):
        return "".join('<li><time>%s</time>%s%s</li>'
                       % ((r.get("at") or r.get("ts") or "")[:16].replace("T", " "), mark, r.get("text", ""))
                       for r in rows[-30:][::-1])

    html = """<!doctype html><meta charset="utf-8"><title>小鱼的心事本</title>
<style>
body{background:#0f1420;color:#e8edf7;font:15px/1.7 "Microsoft YaHei",sans-serif;margin:0;padding:28px 32px;max-width:880px}
h1{font-size:20px;margin:0 0 4px}h2{font-size:14px;color:#8fa3c0;margin:26px 0 8px;font-weight:400;letter-spacing:.06em}
.muted{color:#66799a}.spark{width:100%%;height:120px;display:block}
.big{font-size:44px;font-weight:600;color:#8ec2ff}.bar{display:flex;align-items:center;gap:10px;margin:3px 0}
.bar span{width:44px;color:#9db3d0;font-size:13px}.bar i{height:8px;background:linear-gradient(90deg,#3d7dff,#8ec2ff);border-radius:4px;display:block}
.bar b{width:38px;text-align:right;font-weight:400;font-size:12px;color:#7f93b3}
ul{list-style:none;padding:0;margin:0}li{padding:7px 0;border-bottom:1px solid #1b2334}
time{color:#5f7391;font-size:12px;margin-right:10px}
</style>
<h1>小鱼的心事本</h1>
<div class="big">%(mood).1f</div>
<div class="muted">现在的心情（0–100）· 更新于 %(at)s</div>
%(bars)s
<h2>这几天的心情</h2>
%(spark)s
<h2>最近的小结</h2><ul>%(sums)s</ul>
<h2>念想（没人找我时，我自己想的）</h2><ul>%(thoughts)s</ul>
<h2>随手记</h2><ul>%(notes)s</ul>
<h2>约定</h2><ul>%(prom)s</ul>
""" % {
        "mood": cur.get("mood", 0), "at": now()[:16].replace("T", " "),
        "bars": bars, "spark": spark,
        "sums": items(sums) or '<li class="muted">（还没有）</li>',
        "thoughts": items(thoughts) or '<li class="muted">（还没有）</li>',
        "notes": items(notes) or '<li class="muted">（还没有）</li>',
        "prom": items(prom) or '<li class="muted">（还没有）</li>',
    }
    path = os.path.join(ROOT, "心事本.html")
    open(path, "w", encoding="utf-8").write(html)
    print("写好了:", path)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add"); a.add_argument("text"); a.add_argument("--imp", type=float, default=None)
    th = sub.add_parser("think"); th.add_argument("text")
    s = sub.add_parser("sum"); s.add_argument("text")
    l = sub.add_parser("long"); l.add_argument("text")
    p = sub.add_parser("promise"); p.add_argument("text"); p.add_argument("--due", default=None)
    p.add_argument("--type", default="me", choices=["habit", "joke", "me", "ta", "rule"])
    t = sub.add_parser("tact"); t.add_argument("topic")
    t.add_argument("--level", default="light", choices=["open", "light", "tone", "wait"])
    t.add_argument("--why", default="")
    v = sub.add_parser("view")
    d = sub.add_parser("digest"); d.add_argument("--chars", type=int, default=6000)
    r = sub.add_parser("recall"); r.add_argument("query"); r.add_argument("--n", type=int, default=5)
    r.add_argument("--kw", action="store_true", help="只用关键词，不算向量（做对照）")
    pc = sub.add_parser("consolidate"); pc.add_argument("--dry", action="store_true")
    pr = sub.add_parser("resurface"); pr.add_argument("--dry", action="store_true")
    ps = sub.add_parser("sink"); ps.add_argument("tag", choices=["事实", "念想", "约定", "分寸"]); ps.add_argument("id", type=int)
    args = ap.parse_args()

    if args.cmd == "add":
        cmd_add(args.text, getattr(args, "imp", None))
    elif args.cmd == "think":
        cmd_think(args.text)
    elif args.cmd == "sum":
        cmd_sum(args.text)
    elif args.cmd == "long":
        cmd_long(args.text)
    elif args.cmd == "promise":
        cmd_promise(args.text, args.due, args.type)
    elif args.cmd == "tact":
        cmd_tact(args.topic, args.level, args.why)
    elif args.cmd == "view":
        cmd_view()
    elif args.cmd == "digest":
        cmd_digest(args.chars)
    elif args.cmd == "recall":
        cmd_recall(args.query, args.n, hybrid=not args.kw)
    elif args.cmd == "consolidate":
        cmd_consolidate(args.dry)
    elif args.cmd == "resurface":
        cmd_resurface(getattr(args, "dry", False))
    elif args.cmd == "sink":
        cmd_sink(args.tag, args.id)


if __name__ == "__main__":
    main()
