#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自检：告诉用户这台机器上，哪些能力开了、哪些还差什么。

用法：
    python python/doctor.py
    NEBULA_PYTHON=... python python/doctor.py

输出三项：✅ 可用 / ⚠️ 降级可用 / ❌ 不可用（并给一句下一步）。
"""
import importlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths as P  # noqa: E402

OK, WARN, BAD = "✅", "⚠️", "❌"
rows = []


def add(level, name, detail, hint=""):
    rows.append((level, name, detail, hint))


def check_python():
    v = sys.version_info
    if v >= (3, 9):
        add(OK, "Python", f"{v.major}.{v.minor}.{v.micro}")
    else:
        add(BAD, "Python", f"{v.major}.{v.minor}（需要 3.9+）", "装一个新的 Python 再跑")


def check_data_dir():
    try:
        P.MIND_DIR = Path(P.MIND)
        P.MIND_DIR.mkdir(parents=True, exist_ok=True)
        t = P.MIND_DIR / ".doctor-probe"
        t.write_text("ok", encoding="utf-8")
        t.unlink()
        n = len(list(P.MIND_DIR.glob("*.jsonl")))
        add(OK, "数据目录可写", f"{P.MIND}（{n} 本账）")
    except Exception as e:
        add(BAD, "数据目录不可写", f"{P.MIND}: {e}", "检查权限，或用 NEBULA_HOME 换一个目录")


def check_accounts():
    books = {
        "notes.jsonl": "事实",
        "profile.jsonl": "画像",
        "promises.jsonl": "约定",
        "thoughts.jsonl": "念想",
    }
    have = {name: 0 for name in books}
    for f in books:
        p = Path(P.MIND) / f
        if p.exists():
            have[f] = sum(1 for line in p.read_text(encoding="utf-8").splitlines() if line.strip())
    total = sum(have.values())
    if total == 0:
        add(WARN, "记忆", "还是空的", '先记一条：python python/xin.py add "一句话"')
    else:
        detail = " / ".join(f"{books[k]} {v}" for k, v in have.items() if v)
        add(OK, "记忆", detail)


def _vec_service():
    """探常驻向量服务（tools/embed_server.py，跑在 .venv 里）。

    系统 python 没有 torch 不代表没有语义检索——服务一起，recall 就走它。
    返回 (model, dim) 或 None。
    """
    import urllib.request
    for url in ("http://127.0.0.1:8902/health", "http://127.0.0.1:8902/"):
        try:
            with urllib.request.urlopen(url, timeout=1.5) as r:
                d = json.loads(r.read().decode("utf-8"))
            if isinstance(d, dict) and d.get("ok"):
                return d.get("model"), d.get("dim")
        except Exception:
            continue
    return None


def check_vector():
    svc = _vec_service()
    if svc:
        model, dim = svc
        n = 0
        vec = Path(P.MIND) / "vectors.json"
        if vec.exists():
            try:
                n = len(json.loads(vec.read_text(encoding="utf-8")).get("items", []))
            except Exception:
                n = -1
        tail = " / 索引 %d 条" % n if n > 0 else (" / 索引坏了" if n < 0 else "")
        add(OK, "语义检索", "服务在线（%s %s 维）%s" % (model or "?", dim or "?", tail))
        return
    try:
        importlib.import_module("torch")
        importlib.import_module("transformers")
    except Exception as e:
        add(WARN, "语义检索", f"服务没起，本地也没有 torch/transformers（{type(e).__name__}）",
            "缺它也能用：关键词 / 图扩散 / 情绪共振照常；要开语义检索先起 tools/embed_server.py")
        return
    model = Path(P.BGE_DIR)
    if not model.exists():
        add(WARN, "语义检索", f"模型没下（{model}）", "见 README「向量检索」的下载步骤，或设 NEBULA_BGE_DIR")
        return
    vec = Path(P.MIND) / "vectors.json"
    if vec.exists():
        try:
            n = len(json.loads(vec.read_text(encoding="utf-8")).get("items", []))
            add(OK, "语义检索", f"模型在 / 索引 {n} 条")
        except Exception:
            add(WARN, "语义检索", "索引坏了", "重建：python python/embed.py --build")
    else:
        add(WARN, "语义检索", "模型在，还没建索引", "python python/embed.py --build")


def check_mood():
    p = Path(P.MOOD_STATE)
    if p.exists():
        add(OK, "心情联动", f"{p}")
    else:
        add(WARN, "心情联动", "还没有心情记录（第一次写记忆后会生成）", "不影响别的，心情按中性算")


def check_starmap():
    try:
        out = Path(tempfile.gettempdir()) / "nebula-doctor.html"
        r = subprocess.run([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "star_map.py"),
                            "--out", str(out)], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=60)
        if r.returncode == 0 and out.exists():
            add(OK, "星图生成", f"{out.name}（{out.stat().st_size // 1024} KB）")
        else:
            add(BAD, "星图生成失败", (r.stderr or r.stdout or "").strip()[:120])
    except Exception as e:
        add(BAD, "星图生成失败", str(e)[:120])


def main():
    check_python()
    check_data_dir()
    check_accounts()
    check_vector()
    check_mood()
    check_starmap()

    print("记忆星图 · 自检\n" + "=" * 46)
    for level, name, detail, hint in rows:
        print(f"{level} {name:<12} {detail}")
        if hint:
            print(f"     ↳ {hint}")
    bad = sum(1 for r in rows if r[0] == BAD)
    warn = sum(1 for r in rows if r[0] == WARN)
    print("=" * 46)
    print(f"结论：{len(rows) - bad - warn} 项可用，{warn} 项降级，{bad} 项不可用")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
