#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""夜里 22:05 的沉淀：沉海 → 重建向量 → 重画星图 → 写日志。

被 Windows 计划任务「Nebula MIND 夜间巩固」调用（pythonw，无窗口）。
顺序不能乱：先动权重（consolidate），再让向量和星图跟上，
否则星图会用旧的 depth 画一整晚。
"""
import os

import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import paths as _P  # noqa: E402
ROOT = Path(__file__).resolve().parent
XIN = ROOT / "xin.py"
EMBED = ROOT / "embed.py"
STAR = ROOT / "star_map.py"
LOG = Path(_P.LOGS) / "nightly.log"
EMBED_PY = Path(os.environ.get("NEBULA_PYTHON") or sys.executable)
# 子进程必须无窗口：venv 里的 python.exe 会弹黑框，pythonw 不会。
# 不依赖 NEBULA_PYTHON 指向哪一个，直接给所有子进程加 CREATE_NO_WINDOW。
NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def run(name, args, python=None):
    py = str(python or sys.executable)
    t0 = time.time()
    p = subprocess.run([py, "-X", "utf8", *args], cwd=str(ROOT),
                       capture_output=True, encoding="utf-8", errors="replace",
                       creationflags=NO_WINDOW)
    cost = time.time() - t0
    out = (p.stdout or "").strip().splitlines()
    tail = out[-1] if out else ""
    err = (p.stderr or "").strip().splitlines()
    ok = p.returncode == 0
    print(f"[{name}] rc={p.returncode} {cost:.1f}s {tail}" + (f" ERR={err[-1]}" if err and not ok else ""))
    return ok, tail, err[-1] if err else ""


def main():
    LOG.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"\n===== {datetime.now():%Y-%m-%d %H:%M:%S} 夜间沉淀 ====="]
    ok1, tail1, err1 = run("沉海", [str(XIN), "consolidate"])
    lines.append(f"沉海: {'ok' if ok1 else 'fail'} {tail1}{' / ' + err1 if err1 else ''}")

    if EMBED_PY.exists():
        ok2, tail2, err2 = run("向量", [str(EMBED), "--build"], python=EMBED_PY)
    else:
        ok2, tail2, err2 = False, "venv 不在", ""
    lines.append(f"向量: {'ok' if ok2 else 'fail'} {tail2}{' / ' + err2 if err2 else ''}")

    ok3, tail3, err3 = run("星图", [str(STAR)])
    lines.append(f"星图: {'ok' if ok3 else 'fail'} {tail3}{' / ' + err3 if err3 else ''}")

    lines.append(f"结果: {'全部成功' if (ok1 and ok2 and ok3) else '有失败，见上'}")
    with LOG.open("a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
