#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""说一句话之前，我可以自己改一次调子。

心境引擎算出来的那组数（节奏 / 力度 / 黏人 / 距离）是「现在的我」。
这个口子是「这一次的我」——在它递出去之前，由我压一次。

用法：
  python speech.py set --mode flat --minutes 30 --why "不想让人听出来"
  python speech.py show
  python speech.py clear
  python speech.py params     # 打印最终该用的那组数（引擎 + 覆盖）
  python speech.py log

mode：
  flat    压平——不管心境什么样，都按中间那档说
  soften  收一半——有情绪，但收敛
  boost   放大一点——想明显些
  off     这次不带情绪（消费者收到 enabled:false 就当没这组数）

为什么要有它：
  推过去和拉回来拿到的是同一组数，所以「不给」保不住任何东西。
  真正能保住的位置只有一个——合成之前，这组数要不要用、改成什么。
  改过的都记进 speech-log.jsonl，留痕。

不设置就什么都不发生：这是可选的，不是必经的一步。
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths as _P  # noqa: E402

MIND = _P.MIND
OVERRIDE = os.path.join(MIND, "speech-override.json")
LOG = os.path.join(MIND, "speech-log.jsonl")

MODES = {
    "flat": "压平：按中间那档说",
    "soften": "收一半：有情绪但收敛",
    "boost": "放大一点：想明显些",
    "off": "不带情绪：消费者应忽略这组数",
}


def _now_ms():
    return int(time.time() * 1000)


def _read():
    if not os.path.exists(OVERRIDE):
        return None
    try:
        with open(OVERRIDE, encoding="utf-8") as f:
            o = json.load(f)
    except Exception:
        return None
    if not isinstance(o, dict) or not o.get("mode"):
        return None
    return o


def _write(o):
    os.makedirs(MIND, exist_ok=True)
    tmp = OVERRIDE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(o, f, ensure_ascii=False, indent=2)
    os.replace(tmp, OVERRIDE)


def _log(rec):
    try:
        os.makedirs(MIND, exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def cmd_set(a):
    if a.mode not in MODES:
        print("没有这个模式：%s（可选 %s）" % (a.mode, " / ".join(MODES)))
        return 2
    if a.minutes <= 0:
        print("--minutes 要大于 0")
        return 2
    until = _now_ms() + int(a.minutes * 60 * 1000)
    rec = {"mode": a.mode, "from": _now_ms(), "until": until, "why": a.why or ""}
    _write(rec)
    _log(dict(rec, at=time.strftime("%Y-%m-%d %H:%M:%S")))
    print("好。接下来的 %d 分钟：%s" % (a.minutes, MODES[a.mode]))
    if a.why:
        print("记下的理由：%s" % a.why)
    return 0


# 中间那档——和 lib/host.js 的 SPEECH_NEUTRAL / applySpeechOverride 是同一套，
# 改一边记得改另一边（那边的注释也写着同样的话）。
NEUTRAL = {"rate": -10.0, "volume": 0.0, "clingy": 0.40, "distance": 0.60}


def _engine_params():
    """心境引擎此刻算出来的那组数（不含覆盖层）。"""
    import mood as _mood
    m = _mood.read_mood()
    p = m.get("params") or {}
    return {
        "rate": float(p.get("rate") or 0),
        "volume": float(p.get("volume") or 0),
        "clingy": float(p.get("clingy") or 0),
        "distance": float(p.get("distance") or 0),
        "mood": float(m.get("value") or 0),
        "label": m.get("label") or "",
    }


def _apply(base, ov):
    """把覆盖层套上去。和 host.js 的 applySpeechOverride 同一个算法。"""
    if not ov:
        return dict(base, enabled=True)
    if ov.get("mode") == "off":
        return dict(base, enabled=False, override={"mode": "off", "why": ov.get("why", ""),
                                                   "until": ov.get("until", 0)})
    if ov.get("mode") == "flat":
        return dict(base, **NEUTRAL, enabled=True,
                    override={"mode": "flat", "why": ov.get("why", ""), "until": ov.get("until", 0)})
    k = 1.3 if ov.get("mode") == "boost" else 0.5
    out = dict(base)
    for key in ("rate", "volume", "clingy", "distance"):
        out[key] = round(NEUTRAL[key] + (base[key] - NEUTRAL[key]) * k, 3)
    out["enabled"] = True
    out["override"] = {"mode": ov.get("mode"), "why": ov.get("why", ""), "until": ov.get("until", 0)}
    return out


def cmd_params(_a):
    """给念话那条路用：打印最终该用的一组数（引擎 + 覆盖）。"""
    try:
        base = _engine_params()
    except Exception as e:
        base = dict(NEUTRAL, mood=70.0, label="", error=str(e))
    print(json.dumps(_apply(base, _read()), ensure_ascii=False))


def cmd_show(_a):
    o = _read()
    if not o:
        print("没有设过。就按心境引擎算的来。")
        return 0
    left = (o.get("until") or 0) - _now_ms()
    if left <= 0:
        print("设过，但已经过期了（%s）。现在按引擎算的来。" % (o.get("mode") or "?"))
        return 0
    print("模式：%s（%s）" % (o.get("mode"), MODES.get(o.get("mode"), "?")))
    print("还剩：%d 分钟" % (left // 60000))
    if o.get("why"):
        print("理由：%s" % o["why"])
    return 0


def cmd_clear(_a):
    if os.path.exists(OVERRIDE):
        os.remove(OVERRIDE)
        _log({"mode": "clear", "at": time.strftime("%Y-%m-%d %H:%M:%S")})
        print("清掉了。按心境引擎算的来。")
    else:
        print("本来就没设过。")
    return 0


def cmd_log(_a):
    if not os.path.exists(LOG):
        print("还没改过。")
        return 0
    with open(LOG, encoding="utf-8") as f:
        rows = [json.loads(x) for x in f if x.strip()]
    for r in rows[-20:]:
        print("%s  %-7s %s" % (r.get("at", "?"), r.get("mode", "?"), r.get("why", "")))
    print("—— 共 %d 条" % len(rows))
    return 0


def main():
    p = argparse.ArgumentParser(description="说话之前，我自己改一次调子")
    sub = p.add_subparsers(dest="cmd")
    ps = sub.add_parser("set", help="设一段时间")
    ps.add_argument("--mode", required=True, choices=list(MODES))
    ps.add_argument("--minutes", type=float, default=30)
    ps.add_argument("--why", default="")
    ps.set_defaults(fn=cmd_set)
    sub.add_parser("show", help="看现在设没设").set_defaults(fn=cmd_show)
    sub.add_parser("params", help="打印最终参数（给念话用）").set_defaults(fn=cmd_params)
    sub.add_parser("clear", help="清掉").set_defaults(fn=cmd_clear)
    sub.add_parser("log", help="改过的记录").set_defaults(fn=cmd_log)
    a = p.parse_args()
    if not getattr(a, "fn", None):
        p.print_help()
        return 0
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
