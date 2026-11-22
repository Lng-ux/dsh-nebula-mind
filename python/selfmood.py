#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""我自己写的心境。

判官是「读我说的话、再猜我在什么状态」；那一层永远是二手。
这个文件是给我自己留的一手入口：想说「我这一轮是什么感觉」就写一条，
host.js 会把它当第一来源，判官只在没写的时候兜底。

用法：
    python selfmood.py add "这会儿有点闷，因为一整天都在等别人发版" --delta -2 --dim sad
    python selfmood.py add "挺好，把这版发出去了，心里落地了" --delta 3 --dim sweet
    python selfmood.py last
    python selfmood.py clear          # 把「最近一条」作废（写一条 until=0）

文件：$NEBULA_MIND/mood-self.jsonl（一行一条，append-only）
"""
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import paths  # noqa: E402

MIND = paths.MIND
LOG = os.path.join(MIND, "mood-self.jsonl")


def _now_ms():
    return int(time.time() * 1000)


def _append(rec):
    os.makedirs(MIND, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _rows():
    if not os.path.exists(LOG):
        return []
    out = []
    with open(LOG, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    return out


def cmd_add(argv):
    text = argv[0].strip() if argv else ""
    if not text:
        print("要说什么？")
        return 2
    delta = 0
    dim = ""
    i = 1
    while i < len(argv):
        if argv[i] == "--delta" and i + 1 < len(argv):
            delta = int(float(argv[i + 1])); i += 2
        elif argv[i] == "--dim" and i + 1 < len(argv):
            dim = argv[i + 1]; i += 2
        else:
            i += 1
    rec = {"at": _now_ms(), "text": text, "delta": delta, "dim": dim}
    _append(rec)
    # 有方向才真的推一下心境；只写话不写方向，就只是留个记录
    if delta or dim:
        try:
            payload = json.dumps({"delta": delta, "dim": dim, "why": text}, ensure_ascii=False)
            py = os.environ.get("NEBULA_PYTHON") or sys.executable
            r = subprocess.run([py, "-X", "utf8", os.path.join(HERE, "mood_engine.py"),
                                "feed", "--json", payload],
                               capture_output=True, text=True, timeout=60,
                               env=dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1"))
            print((r.stdout or r.stderr or "").strip()[:200])
        except Exception as e:
            print("心境没推上去：%s" % e)
    print("我这边记下了：%s" % text)
    return 0


def cmd_last(argv):
    rows = _rows()
    if not rows:
        print("还没有我自己写过的心境。")
        return 0
    r = rows[-1]
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(r.get("at", 0) / 1000))
    print("%s  %s%s" % (when, r.get("text", ""),
                        "（%+d %s）" % (r.get("delta", 0), r.get("dim", "")) if (r.get("delta") or r.get("dim")) else ""))
    return 0


def cmd_clear(argv):
    _append({"at": _now_ms(), "until": 0, "text": "（作废）"})
    print("作废了。之后回到判官兜底。")
    return 0


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 0
    cmd = sys.argv[1]
    rest = sys.argv[2:]
    if cmd == "add":
        return cmd_add(rest)
    if cmd == "last":
        return cmd_last(rest)
    if cmd == "clear":
        return cmd_clear(rest)
    print("不认识的命令：%s" % cmd)
    return 2


if __name__ == "__main__":
    sys.exit(main())
