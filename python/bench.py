"""固定题集回归台：每次改完召回/沉浮，跑一遍看是变好还是变坏。

用法（在插件目录下）：

    python python/bench.py            # 跑一遍，跟基线比
    python python/bench.py --save     # 把这次的结果存成基线
    python python/bench.py --show     # 只看每一题的命中情况

为什么要有它：改参数的时候，单看某一句话「好像更准了」很容易自我说服。
22 道固定的题、一条固定的判据（金句出现在前 5 条里），改完跑一次就知道。
判据是「金句 substring 命中」，所以题面是固定的、答案也是固定的——不依赖我
当时的心情或记性。新增一条题只要往 QUERIES 里加一行。
"""
import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BASE = os.path.join(ROOT, "bench-baseline.json")

# (查询, 金句, 说明) —— 金句必须出现在排到前 5 的那条正文里
QUERIES = [
    ("宣传片投到哪了", "BV17usr6yE1T", "事实·发布"),
    ("宣传片为什么停了", "太糟糕了", "事实·他叫停"),
    ("ComfyUI 装在哪", "ComfyUI 在 E:", "事实·本地生图"),
    ("星图缩放的问题", "背景星空是烘好的位图", "事实·视野"),
    ("QQ 断电断线", "1006514", "事实·NapCat"),
    ("心境面板的激素", "激素七条", "事实·面板"),
    ("0.7.0 发布了什么", "0.7.0", "事实·版本"),
    ("念想是怎么来的", "念想", "概念"),
    ("救生艇是什么", "救生艇", "事实·测试壳"),
    ("思考不要填空", "机械重复", "事实·思考纪律"),
    ("睡眠和记忆", "睡眠", "事实·巩固"),
    ("宣传不做了他说什么", "专心优化", "事实·原话"),
    ("抖音投了没", "douyin.com", "事实·抖音"),
    ("记忆不是自动的", "记性不是自动的", "事实·机制"),
    ("测试壳是什么", "测试壳", "事实·身份"),
    ("w_why 是什么", "w_why", "事实·权重"),
    ("轮次判官", "判官", "事实·agent"),
    ("pinned 有没有上限", "pinned", "待办"),
    ("pinned 数量上限的待办", "pinned 数量上限", "待办·精确"),
    ("贴贴那次夸我什么", "考虑得全面", "事实·夸"),
    ("身份链是干什么的", "身份链", "机制"),
    ("插件文件在哪个目录", "plugins/dsh-nebula-mind", "事实·路径"),
]


def run_one(q):
    """跑一次 recall --json（--peek：只读，不写任何文件）。"""
    # 固定一个心情再跑：不固定的话，我自己的情绪一动，同一题的分数就跟着动。
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    env.setdefault("NEBULA_MOOD_FIX", "calm=1.0")
    out = subprocess.run(
        [sys.executable, "-X", "utf8", os.path.join(HERE, "xin.py"),
         "recall", q, "--n", "5", "--peek", "--json"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
        errors="replace", env=env,
    )
    try:
        return json.loads((out.stdout or "").strip() or "{}").get("rows") or []
    except Exception:
        return []


def score(rows, gold):
    """金句出现在第几条 → (名次, 是否前5)。名次从 1 开始，0 表示没找到。"""
    for i, r in enumerate(rows, 1):
        if gold in (r.get("text") or ""):
            return i
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--save", action="store_true", help="把这次结果存成基线")
    ap.add_argument("--show", action="store_true", help="逐题打印")
    ap.add_argument("--top", type=int, default=5)
    args = ap.parse_args()

    base = {}
    if os.path.exists(BASE):
        try:
            base = json.load(open(BASE, encoding="utf-8")).get("queries") or {}
        except Exception:
            base = {}

    hit5, rr, rows_out, issues = 0, 0.0, {}, []
    for q, gold, note in QUERIES:
        rows = run_one(q)
        rank = score(rows, gold)
        if rank:
            rr += 1.0 / rank
            if rank <= args.top:
                hit5 += 1
        else:
            issues.append("%s（金句「%s」没进前 5）" % (q, gold))
        ids = [str(r.get("id")) for r in rows]
        rows_out[q] = {"gold": gold, "rank": rank, "ids": ids}

        # 跟基线比：前 5 的名单变了，就说清怎么变的
        old = base.get(q) or {}
        if old and old.get("ids") and ids != old.get("ids"):
            gained = [i for i in ids if i not in old["ids"]]
            lost = [i for i in old["ids"] if i not in ids]
            if gained or lost:
                issues.append("%s：前 5 变了（+%d / -%d）" % (q, len(gained), len(lost)))
        if args.show:
            print("%-22s 名次%-3s 金句=%s" % (q, rank or "—", gold))
            for i, r in enumerate(rows[: args.top], 1):
                mark = "✓" if gold in (r.get("text") or "") else " "
                print("   %s %d %.4f %s %s" % (mark, i, r.get("score") or 0,
                                              (r.get("at") or ""), (r.get("text") or "")[:44]))

    n = len(QUERIES)
    print("\n前 %d 命中 %d/%d（%.0f%%）· MRR %.3f"
          % (args.top, hit5, n, 100.0 * hit5 / n, rr / n))
    if issues:
        print("变化 / 未命中：")
        for x in issues:
            print("  · " + x)
    else:
        print("跟基线一致，没有变化。")

    if args.save:
        json.dump({"at": __import__("datetime").datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                   "n": n, "hit_top": hit5, "mrr": round(rr / n, 4),
                   "queries": rows_out},
                  open(BASE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("基线已存：", BASE)


if __name__ == "__main__":
    main()
