"""情绪来源适配层：把「别人的情绪插件」翻译成我们能用的那一种形状。

三种来源（配置在 心事本/mind-config.json 的 mood 段）：

    {"mood": {"provider": "builtin"}}
    {"mood": {"provider": "file", "file": "C:/.../别的情绪插件/state.json",
              "map": {"value": "$.state.mood",
                       "dims": "$.state.dims",
                       "reason": "$.state.reason"},
              "write": false, "staleSeconds": 600}}
    {"mood": {"provider": "http", "url": "http://127.0.0.1:8903/mood", "timeoutMs": 800}}

统一输出：
    {"schema":"mood/1","value":90,"dims":{...},"label":"happy",
     "params":{"rate":-7,"volume":0,"clingy":0.78,"distance":0.25},
     "reason":"...","at":1763000000000,"stale":false,"source":"builtin"}

跑法：
    python python/mood.py            # 打印归一化结果
    python python/mood.py --raw      # 打印原始读到的内容
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths as _P  # noqa: E402

SCHEMA = "mood/1"
DIMS = ("happy", "calm", "sweet", "heartache", "worry", "sad", "jealous")


def _root() -> Path:
    # 跟 paths.MIND 同一个来源（NEBULA_MIND → NEBULA_HOME/memory）。
    # 以前这里按插件目录往上推四层，换了安装位置就指错了。
    return Path(_P.MIND)


def _conf() -> dict:
    p = _root() / "mind-config.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _jsonpath(obj, path: str):
    """只支持 $.a.b.c 与 $.a["b"] 这两种，够用就好。"""
    cur = obj
    for part in path.replace("[\"", ".").replace("\"]", "").split("."):
        if part in ("", "$"):
            continue
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except Exception:
                return None
        else:
            return None
        if cur is None:
            return None
    return cur


def _default_params(value: float, dims: dict) -> dict:
    """三档基线 + 七维微调（host.js 的 norm() 里是同一套，改一处要改两处）。"""
    if value >= 75:
        rate, volume, clingy, distance = -8.0, 0.0, 0.70, 0.30
    elif value >= 45:
        rate, volume, clingy, distance = -10.0, 0.0, 0.40, 0.60
    else:
        rate, volume, clingy, distance = -14.0, -1.5, 0.15, 0.85
    if dims:
        if dims.get("worry", 0) >= 8:
            rate -= 2
        if dims.get("happy", 0) >= 12:
            clingy = min(1.0, clingy + 0.08)
        if dims.get("sad", 0) >= 6:
            distance = min(1.0, distance + 0.10)
    return {"rate": round(rate, 1), "volume": round(volume, 1),
            "clingy": round(clingy, 2), "distance": round(distance, 2)}


def _normalize(value, dims, reason, at=None, source="builtin", stale=False) -> dict:
    try:
        value = float(value)
    except Exception:
        value = 70.0
    dims = {k: float((dims or {}).get(k) or 0) for k in DIMS}
    label = max(dims, key=lambda k: dims[k]) if any(dims.values()) else "calm"
    return {
        "schema": SCHEMA,
        "value": round(max(0.0, min(100.0, value)), 1),
        "dims": {k: round(v, 2) for k, v in dims.items()},
        "label": label,
        "params": _default_params(value, dims),
        "reason": (reason or "").strip()[:120],
        "at": int(at or time.time() * 1000),
        "stale": bool(stale),
        "source": source,
    }


def read_builtin() -> dict:
    """自己的账：只读 mood_engine 的实况（mood-state.json）。

    2026-11-14：不再退回 mood.json —— 那份中间文件已经删了，
    节奏/力度那些参数现在由 host.js 的 norm() 直接从引擎算。
    """
    eng = _from_engine()
    if eng is not None:
        return eng
    return _normalize(70, {}, "还没有心情记录")


def read_file(cfg: dict) -> dict:
    path = Path(os.path.expandvars(os.path.expanduser(cfg.get("file", ""))))
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        out = read_builtin()
        out["stale"] = True
        out["source"] = f"file(失败: {type(e).__name__})"
        return out
    m = cfg.get("map") or {}
    value = _jsonpath(raw, m.get("value", "")) if m.get("value") else None
    dims = _jsonpath(raw, m.get("dims", "")) if m.get("dims") else None
    reason = _jsonpath(raw, m.get("reason", "")) if m.get("reason") else ""
    # 没给映射就按已知的两种形状猜：moods.__default__ 或扁平的 mood/value
    if value is None and isinstance(raw.get("moods"), dict):
        d = raw["moods"].get("__default__") or next(iter(raw["moods"].values()), {})
        value, dims, reason = d.get("mood"), d.get("dims"), d.get("lastReason")
    out = _normalize(value, dims, reason, source=f"file:{path.name}")
    stale_after = float(cfg.get("staleSeconds") or 600)
    if (time.time() * 1000 - out["at"]) / 1000 > stale_after and cfg.get("staleSeconds"):
        out["stale"] = True
    return out


def read_http(cfg: dict) -> dict:
    import urllib.request
    url = cfg.get("url", "")
    timeout = float(cfg.get("timeoutMs") or 800) / 1000.0
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            d = json.loads(r.read().decode("utf-8", "replace"))
        if isinstance(d.get("dims"), dict) or "value" in d:
            return _normalize(d.get("value"), d.get("dims"), d.get("reason"),
                              d.get("at"), source="http")
    except Exception:
        pass
    out = read_builtin()
    out["stale"] = True
    out["source"] = "http(失败)"
    return out


def _from_engine() -> dict | None:
    """自带引擎（mood_engine.py）有状态就用它——这是"独立于别的情绪插件"的那一半。"""
    try:
        import mood_engine as me  # 同目录
        st = me.tick(me._load())
        s_, u_ = st["surface"], st["underlying"]
        value = round(s_["value"] * me.SURFACE_SHARE + u_["value"] * (1 - me.SURFACE_SHARE), 2)
        dims = {k: round(v, 2) for k, v in s_["dims"].items()}
        label = max(dims, key=lambda k: dims[k]) if any(dims.values()) else "calm"
        return {
            "schema": "mood/1", "value": value, "dims": dims, "label": label,
            "params": _default_params(value, dims),
            "reason": (st.get("history") or [{}])[-1].get("why", ""),
            "reasonAt": (st.get("history") or [{}])[-1].get("at"),
            # at 用"算到此刻"的时间：tick() 已经在内存里补完衰减，
            # 存盘时间另给 storedAt，面板显示才不会停在几小时前
            "at": int(time.time() * 1000), "storedAt": st.get("at"),
            "stale": False, "source": "engine",
            "surface": s_["value"], "underlying": u_["value"],
            "unresolved": len(st.get("unresolved", [])),
            "affection": st.get("affection", {}).get("level"),
            "hormones": {k: round(v, 1) for k, v in (st.get("hormones") or {}).items()},
        }
    except Exception:
        return None


def read_mood(conf: dict | None = None) -> dict:
    cfg = (conf or _conf()).get("mood") or {}
    provider = (cfg.get("provider") or "builtin").lower()
    if provider == "file":
        return read_file(cfg)
    if provider == "http":
        return read_http(cfg)
    return read_builtin()


def main() -> int:
    import sys
    out = read_mood()
    if "--raw" in sys.argv:
        print(json.dumps(_conf().get("mood") or {}, ensure_ascii=False, indent=2))
        return 0
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
