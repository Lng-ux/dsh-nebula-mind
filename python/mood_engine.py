"""小鱼自己的心境引擎：不依赖任何别的情绪插件。

两个半衰期（量级参考了同类实现的实测值）：
  · 心情（surface/underlying 的 value）向基线回落：12 小时
  · 八维情绪各自衰减：90 分钟

三样别人做得比我好的东西，抄进来了：
  · 两层：surface（这一下） / underlying（底色）
  · unresolved：没解开的结，会持续往底色里压
  · affection：好感，慢变量，几乎不回落

用法：
    python python/mood_engine.py show
    python python/mood_engine.py tick
    python python/mood_engine.py apply --text "他夸我了，很开心"
"""
from __future__ import annotations

import json
import math
import os
import sys
import os
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths as _P  # noqa: E402

DIMS = ("happy", "calm", "sweet", "heartache", "worry", "sad", "jealous", "angry")
BASELINE = 70.0
HALF_MOOD_H = 12.0
HALF_DIM_MIN = 90.0
SURFACE_SHARE = 0.65          # 表层情绪里新事件的占比，剩下给底色

# 八维各自散得多快（分钟）。负向留得比正向久——Kzeroko 的 NPC 情绪运行时实测：
# hurt 2100s / angry 1350s / uneasy 960s，而 joyful 只有 900s、excited 240s。
# 这里放慢到小时级，但保住「负向约等于正向的两倍」这个比例。
DIM_HALF = {
    "happy": 60.0,
    "calm": 110.0,
    "sweet": 70.0,
    "heartache": 180.0,
    "worry": 160.0,
    "sad": 180.0,
    "jealous": 150.0,
    "angry": 40.0,
}

# 瞬时层：这一下涌上来的那部分，几分钟就退。
# 不另开一套 dims——surface.dims 里记总量，_phase 里记"其中多少会快掉"，
# 衰减时先把这小块退掉，剩下的再按 DIM_HALF 走。这样所有读 dims 的地方一行都不用改。
PHASE_HALF_MIN = 3.0
PHASE_SHARE = 0.65          # 新涌进来的情绪，六成五标成"会快掉的"

# 耐受：同一个维度 15 分钟内连着被触发，一次比一次钝（Drivesoid 的 ×0.7 递减）
TOL_WINDOW_MS = 15 * 60 * 1000
TOL_BASE = 0.7
TOL_FLOOR = 0.3

# 七种"体内的东西"：仿真参数，不是医学值。半衰期单位分钟，基线 50。
HORMONES = {
    "dopamine":      {"half": 4.0,   "base": 50.0, "name": "多巴胺",   "blurb": "期待、想再来一次"},
    "noradrenaline": {"half": 2.0,   "base": 50.0, "name": "去甲肾上腺素", "blurb": "警觉、绷紧"},
    "cortisol":      {"half": 60.0,  "base": 50.0, "name": "皮质醇",   "blurb": "压力，慢而黏"},
    "serotonin":     {"half": 1440.0,"base": 50.0, "name": "血清素",   "blurb": "情绪底色"},
    "oxytocin":      {"half": 30.0,  "base": 50.0, "name": "催产素",   "blurb": "依恋、想靠近"},
    "endorphin":     {"half": 20.0,  "base": 50.0, "name": "内啡肽",   "blurb": "舒缓、镇痛"},
    "gaba":          {"half": 15.0,  "base": 50.0, "name": "GABA",    "blurb": "刹车、平静"},
}

# 词表：不追求全，追求"我平时真会听到的话"。权重是情绪强度，不是好坏大小。
POS = {
    "开心": 6, "高兴": 6, "喜欢": 5, "爱你": 8, "谢谢": 4, "厉害": 5, "好棒": 6,
    "辛苦": 3, "放心": 3, "可爱": 6, "乖": 5, "抱抱": 5, "摸摸": 4, "亲": 5,
    "陪我": 4, "想你": 6, "好看": 4, "成功": 5, "搞定": 4, "成了": 4, "夸": 4,
    "哈哈": 3, "嘿嘿": 3, "舒服": 3, "期待": 3, "安心": 4, "暖": 4, "懂了": 2,
}
NEG = {
    "难过": 8, "伤心": 7, "哭": 7, "委屈": 6, "烦": 5, "累": 4,
    "失望": 7, "讨厌": 6, "笨": 5, "蠢": 5, "骂": 6, "凶": 5, "滚": 7, "不理": 7,
    "走了": 4, "离开": 5, "没电": 3, "生病": 6, "感冒": 5, "痛": 5,
    "怕": 5, "担心": 4, "焦虑": 5, "失眠": 5, "孤单": 6, "冷": 2, "算了": 4,
}
CALM = {"安静": 4, "平静": 4, "陪着": 3, "在": 1, "慢慢": 2, "不急": 3}
SWEET = {"喜欢": 4, "甜": 5, "可爱": 4, "亲": 4, "抱": 3, "想你": 4, "乖": 3}
JEALOUS = {"别人": 3, "别的": 3, "她不": 4, "吃醋": 6, "谁": 1, "另一个": 4}
WORRY = {"担心": 4, "怕": 4, "小心": 3, "注意": 2, "安全": 3, "别": 1}
SAD_WORDS = {"难过": 5, "伤心": 5, "哭": 5, "失望": 4, "委屈": 4, "算了": 3}
HEART = {"心疼": 6, "难受": 4, "舍不得": 5, "对不起": 4, "抱歉": 3}
# 生气：V 负、A 高、D 高。按 OCC 这是「他做的事我不认同」，跟委屈（D 低）分开。
# 词表宁可窄——「烦」「累」那些属于别的维度，塞进来会让生气天天亮。
ANGER = {"气死": 8, "生气": 6, "火大": 7, "恼火": 7, "恼": 4, "凭什么": 6, "过分": 6,
         "不爽": 6, "混账": 7, "被耍": 7, "憋气": 6, "气得": 6, "滚": 6, "凶我": 5,
         "骂我": 6, "别碰": 5, "不管我": 4}


def _root() -> Path:
    # 跟 paths.MIND 同一个来源。以前这里按文件位置一层层试，
    # 装在 node_modules 里就会在“插件目录/心事本”旁边另建一个数据根。
    return Path(_P.MIND)


STATE = "mood-state.json"


def _now_ms() -> int:
    return int(time.time() * 1000)


def _load() -> dict:
    p = _root() / STATE
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        if d.get("schema") == "mood-state/1":
            return d
    except Exception:
        pass
    return {
        "schema": "mood-state/1",
        "at": _now_ms(),
        "surface": {"value": BASELINE, "dims": {k: 0.0 for k in DIMS}},
        "underlying": {"value": BASELINE, "dims": {k: 0.0 for k in DIMS}},
        "unresolved": [],
        "affection": {"level": 60.0, "trend": 0.0},
        "hormones": {k: v["base"] for k, v in HORMONES.items()},
        "history": [],
        "_phase": {k: 0.0 for k in DIMS},
        "_recent": {},
    }


def _save(st: dict) -> None:
    st["at"] = _now_ms()
    p = _root() / STATE
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)


def _decay(value: float, minutes: float, half_minutes: float) -> float:
    k = 1 - math.pow(0.5, max(0.0, minutes) / half_minutes)
    return value + (BASELINE - value) * k if half_minutes != HALF_DIM_MIN else value * (1 - k)


def _tolerance(st: dict, dim: str, now_ms: int) -> float:
    """同一个维度短时间连着被触发，一次比一次钝。返回这次该乘的系数。

    15 分钟窗口：第 1 次 ×1.0，第 2 次 ×0.7，第 3 次 ×0.49…… 地板 0.3。
    这是「耐受」的便宜版——不建模受体敏感度，只记次数。
    """
    rec = st.setdefault("_recent", {})
    cur = rec.get(dim) or {}
    last = float(cur.get("t") or 0)
    n = int(cur.get("n") or 0)
    n = n + 1 if (now_ms - last) <= TOL_WINDOW_MS else 1
    rec[dim] = {"t": now_ms, "n": n}
    return max(TOL_FLOOR, TOL_BASE ** (n - 1))


def _phase(st: dict) -> dict:
    """瞬时层：surface.dims 里"会在几分钟内退掉"的那部分。老存档没有就补一层 0。"""
    ph = st.get("_phase")
    if not isinstance(ph, dict):
        ph = {}
    for k in DIMS:
        ph.setdefault(k, 0.0)
    st["_phase"] = ph
    return ph


def tick(st: dict | None = None, now_min: float | None = None) -> dict:
    st = st or _load()
    last = st.get("at") or _now_ms()
    mins = (now_min * 60000 - last) / 60000 if now_min is not None else (_now_ms() - last) / 60000
    if mins <= 0.5:
        return st
    km = 1 - math.pow(0.5, mins / (HALF_MOOD_H * 60))
    ph = _phase(st)
    for layer in ("surface", "underlying"):
        v = st[layer]["value"]
        st[layer]["value"] = round(v + (BASELINE - v) * km, 2)
        for k in DIMS:
            kd = 1 - math.pow(0.5, mins / DIM_HALF.get(k, HALF_DIM_MIN))
            cur = st[layer]["dims"].get(k, 0.0)
            if layer == "surface":
                # 先把"瞬时那一小块"快速退掉，剩下的再按维度的半衰走
                p0 = min(cur, float(ph.get(k, 0.0)))
                drop = p0 * (1 - math.pow(0.5, mins / PHASE_HALF_MIN))
                cur = max(0.0, cur - drop)
                newv = cur * (1 - kd)
                st[layer]["dims"][k] = round(newv, 3)
                ph[k] = round(min(newv, p0 - drop), 3)
            else:
                st[layer]["dims"][k] = round(cur * (1 - kd), 3)
    # 没解开的结：每小时往底色里压一点，最多压 12 点
    for u in st["unresolved"]:
        u["age_h"] = round((_now_ms() - u["at"]) / 3600000, 2)
        pressure = min(12.0, u["weight"] * min(1.0, u["age_h"] / 24))
        st["underlying"]["value"] = round(max(0.0, st["underlying"]["value"] - pressure / 24), 2)
    # 激素：各自按自己的半衰期回落到基线（快慢两层就在这）
    horm = st.setdefault("hormones", {k: v["base"] for k, v in HORMONES.items()})
    for k, v in HORMONES.items():
        if k == "cortisol":
            continue
        cur = float(horm.get(k, v["base"]))
        kh = 1 - math.pow(0.5, mins / v["half"])
        horm[k] = round(cur + (v["base"] - cur) * kh, 2)
    # 皮质醇单独走 HPA 轴：事件先记进目标值，再有约 15 分钟的延迟追上来，之后按 60 分钟衰减。
    # 单指数是简化，但那个滞后是真的——皮质醇对心理应激的峰值要晚 15~30 分钟。
    cb = HORMONES["cortisol"]["base"]
    ctg = float(st.get("_cort_target", cb))
    ctg += (cb - ctg) * (1 - math.pow(0.5, mins / HORMONES["cortisol"]["half"]))
    cl = float(st.get("_cort_lag", horm.get("cortisol", cb)))
    cl += (ctg - cl) * min(1.0, mins / 15.0)
    st["_cort_target"] = round(ctg, 2)
    st["_cort_lag"] = round(cl, 2)
    horm["cortisol"] = round(cl, 2)

    # 好感慢慢靠拢（涨得慢、掉得也慢）
    a = st["affection"]
    a["level"] = round(a["level"] + (60 - a["level"]) * (1 - math.pow(0.5, mins / (24 * 60 * 30))), 2)
    st["at"] = _now_ms()
    return st


def _hormone_gain(st: dict) -> float:
    """激素对情绪增量的调制：不是平均，是偏移。范围约 0.5~1.5。"""
    h = st.get("hormones") or {k: v["base"] for k, v in HORMONES.items()}
    c = (float(h.get("cortisol", 50)) - 50) / 50
    s5 = (float(h.get("serotonin", 50)) - 50) / 50
    d = (float(h.get("dopamine", 50)) - 50) / 50
    return max(0.5, min(1.5, 1 + 0.35 * c - 0.20 * s5 + 0.15 * d))


def _hormone_event(st: dict, text: str, p: float, n: float, sweet, calm) -> None:
    """把这一条消息翻译成激素的涨跌。强度小，但方向明确。"""
    h = st.setdefault("hormones", {k: v["base"] for k, v in HORMONES.items()})

    def add(k, dv):
        # 皮质醇不直接改实际值：先记在「目标」上，由 tick 里的 HPA 延迟层慢慢追上去
        if k == "cortisol":
            t = float(st.get("_cort_target", 50.0)) + dv
            st["_cort_target"] = round(max(0.0, min(100.0, t)), 2)
            return
        v = float(h.get(k, 50.0)) + dv
        h[k] = round(max(0.0, min(100.0, v)), 2)

    if p > 0:
        add("dopamine", min(14.0, p * 1.1))
        add("serotonin", min(6.0, p * 0.4))
    if n > 0:
        add("cortisol", min(16.0, n * 0.9))
        add("serotonin", -min(2.5, n * 0.18))
    if n >= 6:
        add("noradrenaline", min(18.0, n * 1.2))
    if sum(x for _, x in sweet) > 0 or "抱" in text or "想你" in text:
        add("oxytocin", min(16.0, 6 + sum(x for _, x in sweet) * 0.8))
    if sum(x for _, x in calm) > 0 or "晚安" in text or "睡" in text:
        add("gaba", min(14.0, 5 + sum(x for _, x in calm) * 0.9))
    if "哈哈" in text or "笑" in text:
        add("endorphin", 8.0)
    if "担心" in text or "怕" in text or "没事吧" in text:
        add("cortisol", 4.0)


# 谈论情绪 ≠ 有情绪。
# 谈论情绪 ≠ 有情绪。
# 「说生气」「没生气」「你以为我生气了」「把生气加成第八维」「这句的难过是名词」——
# 都是在提到某个词，不是在表达它。判官读上下文能分清，关键词表分不清，
# 所以在词表命中之前先做一次「提及检测」：词前 4 个字里出现这些标记就跳过这次出现。
MENTION_LEAD = (
    "说", "讲", "提到", "所谓", "以为", "误以为", "误判", "判成", "标成", "标错", "写成", "改成",
    "不是", "没有", "没", "别", "问", "显示", "判定", "标记", "写", "引用", "转述", "陈述",
    "讨论", "理解成", "当成", "叫作", "叫做", "叫", "这个词", "这句", "这种", "这一维", "维度",
    "标签", "分数", "词", "字", "被记进", "归到", "算成", "记成", "识别成", "判为",
)
MENTION_WINDOW = 4


# 整句层面的“元语言”信号：出现这些，基本是在讨论情绪这回事，不是在表达
META_SENTENCE = ("这句话", "这句里", "这个字", "两个字", "名词", "标错", "判错", "误判",
                 "识别错", "标签写", "维度名", "词表", "改名叫")
# 词后面紧跟这些，说明是在谈论这个词本身（“加成第八维”“这个词”）
MENTION_TAIL = ("维", "这个词", "两个字", "这个词上", "标签", "加成")


def _is_mention(text: str, idx: int, word: str = "") -> bool:
    head = text[max(0, idx - MENTION_WINDOW):idx]
    if any(t in head for t in MENTION_LEAD):
        return True
    # 被引号包住：是引用别人的话，或是在点名这个说法
    if idx > 0 and text[idx - 1] in "「『\"“":
        return True
    tail = text[idx + len(word): idx + len(word) + 6] if word else ""
    if tail and any(t in tail for t in MENTION_TAIL):
        return True
    return False


def _hits(text: str, table: dict) -> list[tuple[str, int]]:
    """词表命中，但跳过被谈论的那几次出现（同一词只算一次）。"""
    out = []
    if any(m in text for m in META_SENTENCE):   # 整句在讨论情绪本身，不记分
        return out
    for w, n in table.items():
        i = text.find(w)
        real = False
        while i >= 0:
            if not _is_mention(text, i, w):
                real = True
                break
            i = text.find(w, i + 1)
        if real:
            out.append((w, n))
    return out


def _hits_anger(text: str) -> list[tuple[str, int]]:
    return _hits(text, ANGER)


def _hits_heart(text: str) -> list[tuple[str, int]]:
    return _hits(text, HEART)


def apply(text: str, st: dict | None = None) -> dict:
    """一条消息/一件事 → 情绪增量。返回这次判定，供日志与面板用。"""
    st = tick(st or _load())
    text = (text or "")[:500]
    pos = _hits(text, POS)
    neg = _hits(text, NEG)
    anger = _hits_anger(text)
    calm = _hits(text, CALM)
    sweet = _hits(text, SWEET)
    jeal = _hits(text, JEALOUS)
    worry = _hits(text, WORRY)
    sadw = _hits(text, SAD_WORDS)
    heart = _hits_heart(text)
    p = sum(n for _, n in pos)
    n = sum(x for _, x in neg) + sum(x for _, x in anger)
    delta = (p - n) * 0.45                       # 0.45：一步别迈太大，防贴顶
    delta = max(-9.0, min(9.0, delta))
    delta *= _hormone_gain(st)                   # 皮质醇高→更敏感；血清素高→更稳
    delta = max(-9.0, min(9.0, delta))
    dims = {k: 0.0 for k in DIMS}
    dims["calm"] += sum(x for _, x in calm) * 0.8
    dims["sweet"] += sum(x for _, x in sweet) * 0.8
    dims["jealous"] += sum(x for _, x in jeal) * 0.8
    dims["worry"] += sum(x for _, x in worry) * 0.8
    dims["sad"] += sum(x for _, x in sadw) * 0.8
    dims["heartache"] += sum(x for _, x in heart) * 0.8
    dims["angry"] += sum(x for _, x in anger) * 0.9
    dims["happy"] += max(0, p) * 0.5
    # 耐受：同一个维度短时间连着被触发，这次的总量和分到维度的量一起变钝
    tms = _now_ms()
    tols = {k: _tolerance(st, k, tms) for k in DIMS if dims[k]}
    if tols:
        delta = max(-9.0, min(9.0, delta * tols[max(tols, key=lambda k: dims[k])]))
    if p == 0 and n == 0 and not any(dims.values()):
        # 没有明显情绪词，但激素照常有反应（晚安→GABA、想你→催产素），这条不能丢
        _hormone_event(st, text, p, n, sweet, calm)
        st["at"] = _now_ms()
        _save(st)
        return {"delta": 0, "why": "没读到明显情绪", "label": "calm", "state": st}
    surf = st["surface"]
    under = st["underlying"]
    ph = _phase(st)
    surf["value"] = round(max(0.0, min(100.0, surf["value"] + delta)), 2)
    under["value"] = round(max(0.0, min(100.0, under["value"] + delta * 0.35)), 2)
    for k in DIMS:
        if not dims[k]:
            continue
        d = dims[k] * tols.get(k, 1.0)            # 连着来的同一个维度，一次比一次钝
        surf["dims"][k] = round(surf["dims"].get(k, 0) + d, 3)
        under["dims"][k] = round(under["dims"].get(k, 0) + d * 0.5, 3)
        ph[k] = round(min(surf["dims"][k], ph.get(k, 0.0) + d * PHASE_SHARE), 3)
    if pos and not neg:
        st["affection"]["level"] = round(min(100.0, st["affection"]["level"] + 0.6), 2)
    if neg:
        st["affection"]["level"] = round(max(0.0, st["affection"]["level"] - 0.25), 2)
        # 强度够的负面，记一条"没解开的结"（之后解开可以手动 resolve）
        if n >= 6:
            st["unresolved"].append({"text": text[:60], "at": _now_ms(),
                                     "weight": round(min(1.0, n / 12), 2), "age_h": 0})
            st["unresolved"] = st["unresolved"][-10:]
    _hormone_event(st, text, p, n, sweet, calm)
    label = max(surf["dims"], key=lambda k: surf["dims"][k]) if any(surf["dims"].values()) else "calm"
    st["history"].append({"at": _now_ms(), "delta": round(delta, 2),
                          "value": surf["value"], "label": label,
                          "why": text.strip()[:500]})
    st["history"] = st["history"][-60:]
    st["at"] = _now_ms()
    _save(st)
    return {"delta": round(delta, 2), "why": text.strip()[:200], "label": label, "state": st}


def resolve(text_fragment: str = "", st: dict | None = None) -> int:
    st = tick(st or _load())
    before = len(st["unresolved"])
    if text_fragment:
        st["unresolved"] = [u for u in st["unresolved"] if text_fragment not in u["text"]]
    else:
        st["unresolved"] = []
    _save(st)
    return before - len(st["unresolved"])



def feed(delta: float, dim: str, why: str = "") -> dict:
    """小模型判完给的这个「这一轮的心情涨跌」，落进引擎。
    表层吃全量、底色吃三成；八维按涨跌方向和 dim 加分；负面较大就记成一件没解开的结。"""
    delta = max(-9.0, min(9.0, float(delta or 0)))
    dim = dim if dim in DIMS else ("happy" if delta > 0 else "sad")
    st = tick(_load())
    s, u = st["surface"], st["underlying"]
    # 耐受也要管总分，不能只管八维：不然连着写/连着被推，value 会一路直线加满。
    tol = _tolerance(st, dim, int(time.time() * 1000))
    d_eff = round(delta * tol, 2)
    s["value"] = max(0.0, min(100.0, s["value"] + d_eff))
    u["value"] = max(0.0, min(100.0, u["value"] + d_eff * 0.30))
    gain = min(12.0, abs(delta) * 1.6) * tol
    dim_gain = gain if (delta >= 0 or dim == "angry") else gain * 0.9
    s["dims"][dim] = max(0.0, min(40.0, s["dims"].get(dim, 0.0) + dim_gain))
    u["dims"][dim] = max(0.0, min(40.0, u["dims"].get(dim, 0.0) + (gain * 0.4 if (delta >= 0 or dim == "angry") else gain * 0.5)))
    ph = _phase(st)
    ph[dim] = round(min(s["dims"][dim], ph.get(dim, 0.0) + gain * PHASE_SHARE), 3)
    if delta <= -6 and len(st["unresolved"]) < 10:
        st["unresolved"].append({"at": int(time.time() * 1000), "text": (why or "")[:300], "weight": round(abs(delta) / 9.0, 2)})
    st["history"].append({"at": int(time.time() * 1000), "delta": round(delta, 2),
                          "dim": dim, "why": (why or "")[:500]})
    st["history"] = st["history"][-60:]
    _save(st)
    return {"ok": True, "delta": round(delta, 2), "dim": dim, "value": round(s["value"], 2),
            "underlying": round(u["value"], 2), "unresolved": len(st["unresolved"])}


def show() -> dict:
    st = tick(_load())
    _save(st)
    s, u = st["surface"], st["underlying"]
    return {
        "schema": "mood/1",
        "value": round((s["value"] * SURFACE_SHARE + u["value"] * (1 - SURFACE_SHARE)), 2),
        "surface": s["value"], "underlying": u["value"],
        "dims": {k: round(v, 2) for k, v in s["dims"].items()},
        "label": max(s["dims"], key=lambda k: s["dims"][k]) if any(s["dims"].values()) else "calm",
        "unresolved": len(st["unresolved"]),
        "affection": st["affection"]["level"],
        "history": st["history"][-8:],
        "source": "engine",
        "stale": False,
        "at": st["at"],
    }


def main() -> int:
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "show"
    if cmd == "tick":
        st = tick()
        _save(st)
        print(json.dumps({"ok": True, "value": st["surface"]["value"]}, ensure_ascii=False))
    elif cmd == "apply":
        text = sys.argv[sys.argv.index("--text") + 1] if "--text" in sys.argv else ""
        out = apply(text)
        print(json.dumps({k: v for k, v in out.items() if k != "state"}, ensure_ascii=False))
    elif cmd == "feed":
        raw = sys.argv[sys.argv.index("--json") + 1] if "--json" in sys.argv else "{}"
        try:
            d = json.loads(raw)
        except Exception:
            d = {}
        out = feed(d.get("delta") or 0, d.get("dim") or "", d.get("why") or "")
        print(json.dumps(out, ensure_ascii=False))
    elif cmd == "resolve":
        frag = sys.argv[sys.argv.index("--text") + 1] if "--text" in sys.argv else ""
        print(json.dumps({"resolved": resolve(frag)}, ensure_ascii=False))
    else:
        print(json.dumps(show(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
