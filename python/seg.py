# -*- coding: utf-8 -*-
"""中文切词：查字典，不装任何依赖。

- 词典：assets/seg-words.txt（34k 常用名词/动词，从 jieba 的公开词表筛出来的）
  + assets/seg-words-extra.txt（我们自己的行话：记忆星图、测试壳、沉海……）
- 切法：正向最大匹配。切不出来的单字直接丢——宁可少出词，也不把两个字粘成假词。
- 英文/型号整段保留：IndexTTS2、BM25、FTS5、dsh-nebula-mind。

以前是「2~4 字滑窗全切」，于是「不再换回云」「鱼的办」这种跨词碎片全进了关键词，
看着像乱码。改字典以后，词要么是词，要么不出现。
"""

import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
WORDS_FILE = os.path.join(os.path.dirname(HERE), "assets", "seg-words.txt")
EXTRA_FILE = os.path.join(os.path.dirname(HERE), "assets", "seg-words-extra.txt")

STOP = set("""的 了 就 也 都 还 和 跟 与 及 或 但 而 是 在 有 没 不 很 太 又 再 才 已经 一下 一个 一条
这个 那个 我们 你们 他们 自己 什么 怎么 因为 所以 如果 但是 然后 之后 之前 现在 今天 明天 昨天
时候 一点 可以 应该 需要 觉得 知道 看到 这样 那样 这些 那些 一样 不是 就是 没有 还有 其实 只是
一直 有点 有些 东西 事情 地方 方式 起来 出来 进来 下去 上面 下面 里面 外面 这边 那边 多少
几个 两个 三个 第一 第二 最后 开始 结束 感觉 知道 让我 只有 一次 一件 这种 那种 一些 之后
前者 后者 之类 等等 上面 下面 里面 外面 以及 并且 或者 还有 比如 例如 至少 大概 基本
""".split())

EN = re.compile(r"[A-Za-z][A-Za-z0-9_+\-\.]*")
ZH = re.compile(r"[\u4e00-\u9fff]+")

_cache = {"words": None, "path": None}
CUSTOM = set()   # 自己的行话：切词时给它加权，不然会被更常见的两半切开


def _read(path, default_freq=30):
    """读到 {词: (频次, 词性)}。行格式：「词」或「词\\t频次」或「词\\t频次\\t词性」。"""
    out = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                parts = line.split("\t")
                w = parts[0]
                fr, pos = default_freq, "x"
                if len(parts) >= 2:
                    try:
                        fr = int(parts[1])
                    except Exception:
                        fr = default_freq
                if len(parts) >= 3:
                    pos = parts[2]
                if len(w) >= 2:
                    if w not in out or out[w][0] < fr:
                        out[w] = (fr, pos)
    except Exception:
        pass
    return out


_POS = {}


def pos_of(w):
    return _POS.get(w, "x")


def words():
    """词典 {词: 频次}（模块级缓存）。行话默认给 200 的频次，够它切出来。"""
    if _cache["words"] is None:
        raw = _read(WORDS_FILE)
        for k, (fr, _p) in _read(EXTRA_FILE, default_freq=200).items():
            raw[k] = (max(raw.get(k, (0, "x"))[0], fr), "n")
            CUSTOM.add(k)
        _cache["words"] = {k: v[0] for k, v in raw.items()}
        _POS.clear()
        _POS.update({k: v[1] for k, v in raw.items()})
    return _cache["words"]


def add_words(extra):
    """把账本里学到的词并进来（不进文件，只在这一次进程里用）。"""
    w = words()
    for x in extra or ():
        if len(x) >= 2 and x not in STOP:
            if x not in w:
                w[x] = 80
                _POS[x] = "n"
    return w


def learn_from(texts, min_count=2, max_len=4):
    """从账本里补「词典里没有、但一直出现」的行话。

    做法：先用字典切，切不出来的那几段（字典的空洞）才当候选——比如
    「救生艇」「测试壳」「沉海」。空洞里本来就混着虚词，所以过一遍停用字。
    这样不会像 n-gram 那样把「什么时候」「换会话里」也当成词。"""
    from collections import Counter
    vocab = words()
    cnt = Counter()
    BAD = set("的了着过是在被把和跟与对从向就都也还又再才而但所以如果因为而且没有不很太")
    for t in texts or ():
        t = t or ""
        i, flush = 0, []
        while i < len(t):
            seg_text = ZH.match(t, i)
            if not seg_text:
                if flush:
                    w = "".join(flush)
                    if 2 <= len(w) <= max_len:
                        cnt[w] += 1
                    flush = []
                i += 1
                continue
            j = seg_text.end()
            k = i
            while k < j:
                hit = None
                for n in range(min(6, j - k), 1, -1):
                    w = t[k:k + n]
                    if w in vocab:
                        hit = w
                        break
                if hit:
                    if flush:
                        w = "".join(flush)
                        if 2 <= len(w) <= max_len:
                            cnt[w] += 1
                        flush = []
                    k += len(hit)
                else:
                    flush.append(t[k])
                    k += 1
            i = j
        if flush:
            w = "".join(flush)
            if 2 <= len(w) <= max_len:
                cnt[w] += 1
    out = []
    for w, c in cnt.items():
        if c < min_count or w in STOP or w in vocab:
            continue
        if len(w) == 2 and c < 3:          # 两个字的残缺片段，只收反复出现的
            continue
        if w[0] in BAD or w[-1] in BAD:
            continue
        out.append(w)
    out.sort(key=lambda w: (-cnt[w], -len(w), w))
    return out


def segment(text, vocab=None):
    """切词：查字典 + 按词频挑最像人话的那条路。

    单纯最大匹配会把「写实现」切成「写实/现」。这里按「整词覆盖 + 词频」打分，
    所以会选「写/实现」——宁可少出一个词，也不要切错位置的词。
    """
    import math
    vocab = vocab or words()
    text = text or ""
    out = []
    i, L = 0, len(text)
    LOG = {w: math.log(1 + f) for w, f in vocab.items()} if False else None
    while i < L:
        m = EN.match(text, i)
        if m:
            w = m.group(0).rstrip("-._")
            if len(w) >= 2:
                out.append(w.lower())
            i = m.end()
            continue
        seg_m = ZH.match(text, i)
        if not seg_m:
            i += 1
            continue
        j = seg_m.end()
        n = j - i
        if n < 2:
            i = j
            continue
        NEG = -10 ** 9
        dp = [NEG] * (n + 1)
        pick = [None] * (n + 1)
        dp[0] = 0.0
        for k in range(n):
            if dp[k] <= NEG / 2:
                continue
            # 单字：丢掉，罚一点，让整词优先
            if dp[k] - 4 > dp[k + 1]:
                dp[k + 1] = dp[k] - 4
                pick[k + 1] = None
            for ln in range(2, min(6, n - k) + 1):
                w = text[i + k:i + k + ln]
                f = vocab.get(w)
                if f is None or w in STOP:
                    continue
                sc = dp[k] + math.log(1 + f) + ln + (4.0 * len(w) if w in CUSTOM else 0.0)
                if sc > dp[k + ln]:
                    dp[k + ln] = sc
                    pick[k + ln] = w
        k, plan = n, []
        while k > 0:
            w = pick[k]
            if w is None:
                k -= 1
                continue
            plan.append(w)
            k -= len(w)
        out.extend(reversed(plan))
        i = j
    return out


def tokens(text, vocab=None):
    return set(segment(text, vocab))


def keywords(text, topn=8, vocab=None):
    """面板/图上显示的关键词：整词，按「出现次数 × 词长」排，长的优先。"""
    seg = segment(text, vocab)
    freq = {}
    for w in seg:
        if len(w) < 2 or w in STOP:
            continue
        freq[w] = freq.get(w, 0) + 1
    def rank(kv):
        w, c = kv
        noun = 1 if pos_of(w).startswith("n") else 0
        return (-noun, -(c * 100 + len(w)), w)
    arr = sorted(freq.items(), key=rank)
    return [w for w, _ in arr[:topn]]
