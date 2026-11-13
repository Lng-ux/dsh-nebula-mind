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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths as _P  # noqa: E402
ROOT = _P.MIND
MOOD = _P.MOOD_STATE


def mood_now():
    """把当前心情带进来，让"什么心情下记的事"将来能被同一种心情想起来。"""
    try:
        st = json.load(open(MOOD, encoding="utf-8"))
        m = st.get("moods", {}).get("__default__") or {}
        dims = m.get("dims") or {}
        return {"mood": round(m.get("mood", 0), 1), "dims": {k: round(v, 2) for k, v in dims.items()}}
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


def cmd_add(text, imp=None):
    rows = read_jsonl("notes.jsonl")
    rec = M.enrich({"id": next_id(rows), "at": now(), "text": text, "mood": mood_now(),
                    "imp": float(imp) if imp else 0.5},
                   default_via="我自己想的")
    r = append("notes.jsonl", rec)
    append_vector("事实", r["id"], text)
    print("记下 #%d" % r["id"])


def cmd_think(text):
    """念想：没人找我、我自己脑子里冒出来的东西。和随手记分开存。"""
    rows = read_jsonl("thoughts.jsonl")
    rec = M.enrich({"id": next_id(rows), "at": now(), "text": text, "mood": mood_now()},
                   default_via="我自己想的")
    r = append("thoughts.jsonl", rec)
    append_vector("念想", r["id"], text)
    print("念想 #%d" % r["id"])


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
    "A_hot": 0.55, "A_warm": 0.25, "A_deep": 0.08,
    "w_bm25": 0.40, "w_vec": 0.35, "w_graph": 0.15, "w_emo": 0.10,
    "rrf_k": 60, "graph_hop1": 0.30, "graph_hop2": 0.10,
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

    A ≥0.55 → 热　0.25~0.55 → 温　0.08~0.25 → 深水　<0.08 → 阁楼
    被召回 ≥2 次：往上浮一层；情绪剧震（同色且强度 ≥0.5）把 3/4 层拉回 2。
    """
    rows = _all_rows()
    cur = mood_now() or {}
    dims = cur.get("dims") or {}
    top = max(dims, key=lambda k: dims.get(k, 0)) if dims else None
    dist, sink, lift, shock = {}, 0, 0, 0
    for fn, tag, r in rows:
        pinned = bool(r.get("pinned")) or tag in ("约定", "分寸")
        try:
            age = M.age_days(r.get("at", ""))
        except Exception:
            age = 0.0
        imp = float(r.get("imp", 0.5))
        A = 1.0 if pinned else 2 ** (-age / (30.0 * (1 + 2 * imp)))
        hits = int(r.get("hits", 0))
        if pinned:
            d = 0
        else:
            cf = conf()
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


EMBED_PY = os.environ.get("NEBULA_EMBED_PYTHON") or r"D:\indextts\.venv\Scripts\python.exe"
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


def cmd_recall(query, n=5, hybrid=True):
    """翻旧账：字面（BM25）+ 语义（BGE）+ 关联（图扩散）+ 情绪共振，四路融合。"""
    docs = []
    for name, tag in (("notes.jsonl", "事实"), ("summaries.jsonl", "小结"), ("thoughts.jsonl", "念想")):
        for r in read_jsonl(name):
            docs.append({"tag": tag, "id": r.get("id"), "at": r.get("at", ""),
                         "text": r.get("text", ""), "mood": r.get("mood"),
                         "emo": r.get("emo"), "e": float(r.get("e") or 0),
                         "importance": r.get("importance", 2),
                         "pinned": bool(r.get("pinned"))})
    if not docs:
        print("心事本还是空的。")
        return

    cur = mood_now() or {}
    cur_dims = cur.get("dims") or {}
    top_dim = max(cur_dims, key=lambda k: cur_dims.get(k, 0)) if cur_dims else None

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
            s += idf * tf[w] / (tf[w] + 1.2)
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
    kws = {"%s#%s" % (d["tag"], d["id"]): set(tokenize(d["text"])) for d in docs}
    idx = {"%s#%s" % (d["tag"], d["id"]): d for d in docs}
    adj = {k: {} for k in idx}
    ks = list(idx)
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

    bonus = {}
    for k0 in seeds:
        for nk, ew in adj.get(k0, {}).items():
            bonus[nk] = max(bonus.get(nk, 0.0), seed[k0] * cf["w_graph"] * cf["graph_hop1"] * (ew / 2.0))
            for nk2, ew2 in adj.get(nk, {}).items():
                if nk2 == k0:
                    continue
                bonus[nk2] = max(bonus.get(nk2, 0.0), seed[k0] * cf["w_graph"] * cf["graph_hop2"] * (ew2 / 2.0))

    # ── 融合 ───────────────────────────────────────────
    out = []
    for k, d in idx.items():
        sc = seed.get(k, 0.0) + bonus.get(k, 0.0)
        if sc <= 0:
            continue
        # 新鲜度
        try:
            age = (time.time() - time.mktime(time.strptime(d["at"][:19], "%Y-%m-%dT%H:%M:%S"))) / 86400
            age = max(0.0, age)
        except Exception:
            age = 0.0
        H = cf["half_base"] * (1 + cf["half_imp"] * d["importance"])
        A = 1.0 if d["pinned"] else 2 ** (-age / H)
        sc *= 0.55 + 0.45 * A
        # 情绪共振：同色且强度够，更容易想起来
        if top_dim and d["emo"] == top_dim and d["e"] >= 0.3:
            sc *= 1.0 + cf["w_emo"]
        out.append((sc, d, k))

    out.sort(key=lambda x: -x[0])
    for sc, d, k in out[:n]:
        tags = []
        if k in rank_bm:
            tags.append("词#%d" % (rank_bm[k] + 1))
        if k in rank_vec:
            tags.append("义#%d" % (rank_vec[k] + 1))
        if bonus.get(k):
            tags.append("图+%.2f" % bonus[k])
        if top_dim and d["emo"] == top_dim and d["e"] >= 0.3:
            tags.append("情·%s" % top_dim)
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
    elif args.cmd == "sink":
        cmd_sink(args.tag, args.id)


if __name__ == "__main__":
    main()
