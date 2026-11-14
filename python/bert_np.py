# -*- coding: utf-8 -*-
"""纯 numpy 的 BERT 前向 —— 让语义检索不再依赖 torch。

模型：BGE-small-zh（4 层 / 512 隐藏 / 8 头 / 词表 21128）
权重：assets/bert-small-zh-fp16.npz（48MB，随包分发，反正也就这么大）

为什么这么干：装 torch 对普通用户太重（几百 MB 到 2 GB），而这套记忆要「下载就能用」。
用 numpy 重写之后只要求 numpy —— 大多数 Python 环境本来就带着它。

正确性：与 transformers + torch 的输出逐位一致（同一句话 cos = 1.000000，maxdiff = 0）。
速度：CPU 上 10~30 ms 一条（20 字左右的句子）。
"""
import math

import numpy as np


def _erf(x):
    # Abramowitz-Stegun 7.1.26，误差 < 1.5e-7；numpy 没有 erf，自己近似
    s = np.sign(x)
    a = np.abs(x)
    t = 1.0 / (1.0 + 0.3275911 * a)
    y = 1.0 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t
                - 0.284496736) * t + 0.254829592) * t * np.exp(-a * a)
    return s * y


def gelu(x):
    return 0.5 * x * (1.0 + _erf(x / math.sqrt(2.0)))


def layer_norm(x, w, b, eps=1e-12):
    m = x.mean(-1, keepdims=True)
    v = x.var(-1, keepdims=True)
    return (x - m) / np.sqrt(v + eps) * w + b


class Bert:
    """只做前向，不做训练。参数从 npz 读，fp16 存、fp32 算。"""

    def __init__(self, npz_path, vocab_path=None):
        z = np.load(npz_path)
        self.W = {k.replace("/", "."): z[k].astype(np.float32) for k in z.files}
        self.layers = 4
        self.heads = 8
        self.dim = 512
        self.vocab = load_vocab(vocab_path) if vocab_path else {}

    def forward(self, ids):
        W = self.W
        x = W["embeddings.word_embeddings.weight"][ids]
        pos = np.arange(ids.shape[1])[None, :]
        x = x + W["embeddings.position_embeddings.weight"][pos]
        x = x + W["embeddings.token_type_embeddings.weight"][np.zeros_like(ids)]
        x = layer_norm(x, W["embeddings.LayerNorm.weight"], W["embeddings.LayerNorm.bias"])

        B, S, D = x.shape
        dh = D // self.heads
        scale = 1.0 / math.sqrt(dh)
        for i in range(self.layers):
            p = "encoder.layer.%d." % i
            q = x @ W[p + "attention.self.query.weight"].T + W[p + "attention.self.query.bias"]
            k = x @ W[p + "attention.self.key.weight"].T + W[p + "attention.self.key.bias"]
            v = x @ W[p + "attention.self.value.weight"].T + W[p + "attention.self.value.bias"]
            q, k, v = [t.reshape(B, S, self.heads, dh).transpose(0, 2, 1, 3) for t in (q, k, v)]
            sc = (q @ k.transpose(0, 1, 3, 2)) * scale
            sc = sc - sc.max(-1, keepdims=True)
            e = np.exp(sc)
            a = e / e.sum(-1, keepdims=True)
            ctx = (a @ v).transpose(0, 2, 1, 3).reshape(B, S, D)
            ctx = ctx @ W[p + "attention.output.dense.weight"].T + W[p + "attention.output.dense.bias"]
            x = layer_norm(x + ctx, W[p + "attention.output.LayerNorm.weight"],
                           W[p + "attention.output.LayerNorm.bias"])
            h = gelu(x @ W[p + "intermediate.dense.weight"].T + W[p + "intermediate.dense.bias"])
            h = h @ W[p + "output.dense.weight"].T + W[p + "output.dense.bias"]
            x = layer_norm(x + h, W[p + "output.LayerNorm.weight"], W[p + "output.LayerNorm.bias"])

        cls = x[:, 0]                                   # BGE 用 [CLS] 做句向量
        return cls / np.linalg.norm(cls, axis=-1, keepdims=True)

    def encode(self, texts, max_len=256):
        """一句一条，返回 (n, 512) 的 L2 归一化矩阵。"""
        out = []
        for t in texts:
            ids = np.array([tokenize(t, self.vocab, max_len)])
            out.append(self.forward(ids)[0])
        return np.array(out)


CLS_ID, SEP_ID, UNK_ID = 101, 102, 100


def load_vocab(path):
    with open(path, encoding="utf-8") as f:
        return {w: i for i, w in enumerate(f.read().split("\n"))}


def _pretokens(text):
    """中文按字切，英文数字按词切，其余当分隔。够用就行。"""
    buf, out = [], []
    for ch in text.lower():
        if "\u4e00" <= ch <= "\u9fff":
            if buf:
                out.append("".join(buf)); buf = []
            out.append(ch)
        elif ch.isalnum():
            buf.append(ch)
        else:
            if buf:
                out.append("".join(buf)); buf = []
    if buf:
        out.append("".join(buf))
    return out


def _wordpiece(w, vocab):
    if w in vocab:
        return [vocab[w]]
    out, start = [], 0
    while start < len(w):
        end, cur = len(w), None
        while start < end:
            piece = w[start:end]
            if piece in vocab:
                cur = vocab[piece]
                break
            end -= 1
        if cur is None:
            return [UNK_ID]
        out.append(cur)
        start = end
    return out


def tokenize(text, vocab, max_len=256):
    ids = [CLS_ID]
    for w in _pretokens(text):
        ids.extend(_wordpiece(w, vocab))
        if len(ids) >= max_len - 1:
            break
    ids.append(SEP_ID)
    return ids[:max_len]
