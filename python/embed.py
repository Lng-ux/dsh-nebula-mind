#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""给心事本做语义向量 —— 不需要 torch，有 numpy 就行。

模型和词表随插件一起分发（assets/bert-small-zh-fp16.npz，48MB；assets/vocab.txt）。
推理是 python/bert_np.py 里手写的 numpy 前向，和 transformers 的输出逐位一致。

    python python/embed.py --build
    python python/embed.py --query "她那天说累"

产物：
    心事本/vectors.json   {"model": "...", "dim": 512, "items": [{"id": "...", "v": [...]}, ...]}
纯 CPU，一条十几到几十毫秒；几百条记忆整体重算一两秒。
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths as _P  # noqa: E402
HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(os.path.dirname(HERE), "assets")
MIND = _P.MIND
MODEL = os.path.join(ASSETS, "bert-small-zh-fp16.npz")
VOCAB = os.path.join(ASSETS, "vocab.txt")
OUT = os.path.join(MIND, "vectors.json")

FILES = [("notes.jsonl", "事实"), ("summaries.jsonl", "小结"), ("thoughts.jsonl", "念想"),
         ("promises.jsonl", "约定"), ("tacts.jsonl", "分寸"), ("profile.jsonl", "画像")]


def _load():
    import bert_np
    if not os.path.exists(MODEL):
        raise FileNotFoundError("模型文件不在：%s" % MODEL)
    return bert_np.Bert(MODEL, VOCAB)


def _encode(mdl, texts, batch=16):
    return mdl.encode(texts)


def _docs():
    items = []
    for name, tag in FILES:
        p = os.path.join(MIND, name)
        if not os.path.exists(p):
            continue
        for line in open(p, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            txt = r.get("text") or r.get("content") or ""
            if not txt and r.get("topic"):
                txt = "%s：%s" % (r.get("topic"), r.get("why") or "")
            if not txt:
                continue
            items.append({"id": "%s#%s" % (tag, r.get("id")), "text": txt})
    # 文档（手记/规矩/守则/声音/发布指南/插件说明）也进向量，语义路才召回得到
    try:
        import hashlib
        if HERE not in sys.path:
            sys.path.insert(0, HERE)
        import xin
        for tag, _head, txt, _at in xin.doc_chunks():
            items.append({"id": "文档·%s#%s" % (tag, hashlib.md5(txt.encode("utf-8")).hexdigest()[:10]),
                          "text": txt})
    except Exception as e:
        print("文档向量跳过：%s" % e, file=sys.stderr)
    return items


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--query", default="")
    a = ap.parse_args()

    try:
        mdl = _load()
    except Exception as e:
        print("语义模型不可用：%s" % e, file=sys.stderr)
        return 2

    if a.query:
        v = _encode(mdl, [a.query])[0]
        print(json.dumps([round(float(x), 6) for x in v]))
        return 0

    docs = _docs()
    if not docs:
        print("心事本是空的")
        return 1
    vecs = _encode(mdl, [d["text"] for d in docs])
    data = {"model": "bge-small-zh-fp16", "dim": len(vecs[0]), "items":
            [{"id": d["id"], "v": [round(float(x), 5) for x in v]} for d, v in zip(docs, vecs)]}
    json.dump(data, open(OUT, "w", encoding="utf-8"), ensure_ascii=False)
    print("写成 %d 条 / %d 维 -> %s (%.1f KB)" % (len(vecs), data["dim"], OUT,
                                                  os.path.getsize(OUT) / 1024))
    return 0


if __name__ == "__main__":
    sys.exit(main())
