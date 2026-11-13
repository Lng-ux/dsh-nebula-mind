#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""用本机 BGE 小模型给心事本做向量。

必须有 torch —— 所以这个脚本要用 IndexTTS 那个 venv 的 python 跑：
    D:/indextts/.venv/Scripts/python.exe tools/embed.py --build
    D:/indextts/.venv/Scripts/python.exe tools/embed.py --query "她那天说累"

产物：
    心事本/vectors.json   {"model": "...", "dim": 512, "items": [{"id": "...", "v": [...]}, ...]}
纯 CPU，几十毫秒一条；几百条记忆整体重算几秒。
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths as _P  # noqa: E402
HERE = os.path.dirname(os.path.abspath(__file__))
MIND = _P.MIND
MODEL = _P.BGE_DIR  # 可被 --model 覆盖
OUT = os.path.join(MIND, "vectors.json")

FILES = [("notes.jsonl", "事实"), ("summaries.jsonl", "小结"), ("thoughts.jsonl", "念想")]


def _load(model_dir):
    import torch
    from transformers import AutoTokenizer, AutoModel
    tok = AutoTokenizer.from_pretrained(model_dir)
    mdl = AutoModel.from_pretrained(model_dir)
    mdl.eval()
    return torch, tok, mdl


def _encode(torch, tok, mdl, texts, batch=16):
    import torch as T
    out = []
    for i in range(0, len(texts), batch):
        chunk = texts[i:i + batch]
        b = tok(chunk, padding=True, truncation=True, max_length=256, return_tensors="pt")
        with T.no_grad():
            h = mdl(**b).last_hidden_state[:, 0]      # BGE 用 [CLS]
        h = T.nn.functional.normalize(h, p=2, dim=1)
        out.extend(h.tolist())
    return out


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
            if not txt:
                continue
            items.append({"id": "%s#%s" % (tag, r.get("id")), "text": txt})
    return items


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--query", default="")
    ap.add_argument("--model", default=None)
    a = ap.parse_args()
    model_dir = a.model or MODEL

    if not os.path.isdir(model_dir):
        print("模型还没下好：%s" % model_dir, file=sys.stderr)
        return 2

    torch, tok, mdl = _load(model_dir)

    if a.query:
        v = _encode(torch, tok, mdl, [a.query])[0]
        print(json.dumps(v))
        return 0

    docs = _docs()
    if not docs:
        print("心事本是空的")
        return 1
    vecs = _encode(torch, tok, mdl, [d["text"] for d in docs])
    data = {"model": os.path.basename(model_dir), "dim": len(vecs[0]), "items":
            [{"id": d["id"], "v": [round(x, 5) for x in v]} for d, v in zip(docs, vecs)]}
    json.dump(data, open(OUT, "w", encoding="utf-8"), ensure_ascii=False)
    print("写成 %d 条 / %d 维 -> %s (%.1f KB)" % (len(vecs), data["dim"], OUT,
                                                  os.path.getsize(OUT) / 1024))
    return 0


if __name__ == "__main__":
    sys.exit(main())
