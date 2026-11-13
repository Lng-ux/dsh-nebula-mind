"""把 BGE 小模型挂在 8902 端口上，别每次查一句话都重新加载（一次 13 秒，太贵）。

POST /embed  {"text": "..."}  ->  [0.01, -0.02, ...]  (512 维，已归一化)
GET  /health -> {"ok": true, "model": "...", "dim": 512}

用 IndexTTS 那个 venv 跑（里面有 torch + transformers）：
  D:\\indextts\\.venv\\Scripts\\pythonw.exe tools\\embed_server.py
"""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL = os.environ.get("YU_BGE_DIR", r"D:\cache\modelscope\bge-small-zh")
PORT = int(os.environ.get("YU_BGE_PORT", "8902"))

_T = {}


def _load():
    if "model" in _T:
        return _T
    import torch
    from transformers import AutoModel, AutoTokenizer
    torch.set_num_threads(max(2, (os.cpu_count() or 4) // 2))
    _T["torch"] = torch
    _T["tok"] = AutoTokenizer.from_pretrained(MODEL)
    m = AutoModel.from_pretrained(MODEL).eval()
    _T["model"] = m
    return _T


def embed(text):
    t = _load()
    torch, tok, model = t["torch"], t["tok"], t["model"]
    with torch.no_grad():
        b = tok([text], padding=True, truncation=True, max_length=256, return_tensors="pt")
        out = model(**b).last_hidden_state[:, 0]          # BGE 用 CLS
        v = torch.nn.functional.normalize(out, dim=-1)[0].tolist()
    return v


class H(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("content-type", "application/json; charset=utf-8")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/health"):
            t = _load()
            self._send(200, {"ok": True, "model": os.path.basename(MODEL), "dim": 512})
        else:
            self._send(404, {"ok": False})

    def do_POST(self):
        if not self.path.startswith("/embed"):
            return self._send(404, {"ok": False})
        n = int(self.headers.get("content-length") or 0)
        try:
            req = json.loads(self.rfile.read(n).decode("utf-8"))
            v = embed(req.get("text") or "")
            self._send(200, v)
        except Exception as e:
            self._send(500, {"ok": False, "error": "%s: %s" % (type(e).__name__, e)})

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    import threading
    print("embed_server on 127.0.0.1:%d, model=%s" % (PORT, MODEL), flush=True)
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)          # 先占端口，别等模型加载完
    threading.Thread(target=_load, daemon=True).start()        # 后台预热，第一句话就不用等
    srv.serve_forever()
