"""心事本的记忆模型（M0）——所有写入者共用，别各写一套。

字段（都是可加可省的，老记录读的时候用默认值补）：
    w          权重（写入时算一次，之后由召回强化/沉海调整）
    imp        重要度 0~1
    e          情绪强度 0~1
    emo        情绪色：某一维的名字（happy/calm/sweet/heartache/worry/sad/jealous）
    depth      沉海层：0=核心 1=热 2=温 3=深水 4=阁楼
    pinned     恒星，永不沉
    last_hit   最后一次被想起来（年龄从这里算；不再有刻痕计数）
    sunken_at  什么时候沉下去的
    valid_to   被新记忆取代的时间（null 表示还算数）
    via        来源：他说的 / 我自己想的 / 工具 / 网页
"""
import sys

import json
import os
from datetime import datetime

# 记录格式版本：2026-11-25 加。老记录没有这个字段，读的时候按 1 处理。
SCHEMA_VERSION = 1

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths as _P  # noqa: E402
ROOT = os.path.dirname(os.path.abspath(__file__))
MIND = _P.MIND

DIMS = ["happy", "calm", "sweet", "heartache", "worry", "sad", "jealous"]

# 来源权重：他说的话天然比别处重
W_BASE = {"他说的": 4.0, "我自己想的": 2.0, "工具": 1.5, "网页": 1.2}

LAYERS = ["核心", "热", "温", "深水", "阁楼"]


def now():
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def mood_now():
    """此刻的心情：优先用自带引擎（mood-state.json）。

    返回 {"mood": 0~100, "dims": {八维}}，形状跟老版一致，调用点不用改。
    只读自带引擎；读不到就退回心事本里的 mood-state.json；再读不到
    返回 {}。不碰任何外部情绪插件的文件。
    """
    try:
        import mood_engine as ME
        st = ME._load()          # 只读；tick 只在内存里补衰减，不写盘
        ME.tick(st)
        sf = st.get("surface") or {}
        dims = sf.get("dims") or {}
        if dims:
            return {"mood": sf.get("value"), "dims": dims}
    except Exception:
        pass
    try:
        with open(os.path.join(MIND, "mood-state.json"), encoding="utf-8") as f:
            d = json.load(f)
        sf = d.get("surface") or {}
        if isinstance(sf, dict) and sf.get("dims"):
            return {"mood": sf.get("value"), "dims": sf["dims"]}
    except Exception:
        pass
    return {}


def dominant_emo(mood=None):
    """取最强的一维当情绪色；顺带给出强度 0~1。"""
    m = mood if mood is not None else mood_now()
    dims = (m or {}).get("dims") or {}
    if not dims:
        return None, 0.0
    k = max(dims, key=lambda x: dims.get(x, 0) or 0)
    v = float(dims.get(k) or 0)
    # 维度经验上 0~40，30 以上算满
    return k, min(1.0, v / 30.0)


def weight(imp=0.5, e=0.0, via="我自己想的", age_days=0.0):
    """写入权重：来源 × (1 + 重要度 × 情绪强度) —— 再乘一次新鲜的加成。"""
    base = W_BASE.get(via, 1.5)
    w = base * (1.0 + imp) * (1.0 + 0.6 * e)
    w *= 1.0 + 0.25 * max(0.0, 1.0 - age_days / 30.0)      # 刚记下的更重
    return round(w, 3)


def depth_for(age_days, pinned=False):
    if pinned:
        return 0
    if age_days <= 7:
        return 1
    if age_days <= 30:
        return 2
    if age_days <= 120:
        return 3
    return 4


_AT_FMTS = ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d")


def age_days(at):
    """离现在多少天。容忍没有秒的时间戳——解析失败会静默返回 0，
    等于把「三个月前」当成「刚刚」（和 epoch() 那个 bug 同一类）。"""
    s0 = str(at or "").strip()
    for fmt in _AT_FMTS:
        n = 19 if "%S" in fmt else 16
        try:
            t = datetime.strptime(s0[:n], fmt)
            return max(0.0, (datetime.now() - t).total_seconds() / 86400.0)
        except Exception:
            continue
    return 0.0


def enrich(rec, default_via="我自己想的", pinned=False):
    """给一条记忆补上新字段；已经有值的保留。返回新 dict（不改原对象）。"""
    r = dict(rec)
    r.setdefault("schema", SCHEMA_VERSION)
    at = r.get("at") or now()
    r.setdefault("at", at)
    r["imp"] = float(r.get("imp", 1.0 if pinned else 0.5))
    r["pinned"] = bool(r.get("pinned", pinned))

    # 情绪色：没写过就取写入那一刻的心情
    if not r.get("emo"):
        k, v = dominant_emo(r.get("mood"))
        r["emo"] = k
        r.setdefault("e", round(v, 3))
    r.setdefault("e", 0.0)

    r.setdefault("via", default_via)
    r.pop("hits", None)
    r.setdefault("last_hit", None)
    r.setdefault("sunken_at", None)
    r.setdefault("valid_to", None)

    d = age_days(at)
    r.setdefault("w", weight(r["imp"], r["e"], r["via"], d))
    r.setdefault("depth", depth_for(d, r["pinned"]))
    return r


def layer_name(depth):
    try:
        return LAYERS[int(depth)]
    except Exception:
        return "热"


