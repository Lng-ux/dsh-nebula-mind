"""把语义模型挂在 8902 端口上，别每次查一句话都重新加载（加载一次要几秒，太贵）。

POST /embed  {"text": "..."}  ->  [0.01, -0.02, ...]  (512 维，已归一化)
GET  /health -> {"ok": true, "model": "...", "dim": 512}

不需要 torch —— 推理是 bert_np.py 里手写的 numpy 前向，权重在 assets/ 里随插件走。
所以任何有 numpy 的 Python 都能起：
  python python/embed_server.py

可选口令：set NEBULA_EMBED_TOKEN=xxx 之后，每个请求要带 Authorization: Bearer xxx。
"""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(os.path.dirname(HERE), "assets")
MODEL = os.path.join(ASSETS, "bert-small-zh-fp16.npz")
VOCAB = os.path.join(ASSETS, "vocab.txt")
PORT = int(os.environ.get("NEBULA_BGE_PORT") or os.environ.get("YU_BGE_PORT") or "8902")
# 可选口令：设了 NEBULA_EMBED_TOKEN 就要求每个请求带 Authorization: Bearer <token>。
# 不设就不校验（默认，兼容老用法）。本机单用户场景下这层意义有限，
# 但多用户 / 远程开机器的场合能挡住"随便一个进程无限调用把 CPU 吃满"。
TOKEN = os.environ.get("NEBULA_EMBED_TOKEN") or ""

_T = {}


def _load():
    if "model" in _T:
        return _T["model"]
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    import bert_np
    _T["model"] = bert_np.Bert(MODEL, VOCAB)
    return _T["model"]


def embed(text):
    return [round(float(x), 6) for x in _load().encode([text])[0]]


class H(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("content-type", "application/json; charset=utf-8")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _auth_ok(self):
        if not TOKEN:
            return True
        got = (self.headers.get("authorization") or "")
        return got == "Bearer " + TOKEN

    def do_GET(self):
        if not self._auth_ok():
            return self._send(401, {"ok": False, "error": "unauthorized"})
        if self.path.startswith("/health"):
            _load()
            self._send(200, {"ok": True, "model": "bge-small-zh-fp16", "dim": 512})
        else:
            self._send(404, {"ok": False})

    def do_POST(self):
        if not self._auth_ok():
            return self._send(401, {"ok": False, "error": "unauthorized"})
        if not self.path.startswith("/embed"):
            return self._send(404, {"ok": False})
        n = int(self.headers.get("content-length") or 0)
        try:
            req = json.loads(self.rfile.read(n).decode("utf-8"))
            self._send(200, embed(req.get("text") or ""))
        except Exception as e:
            self._send(500, {"ok": False, "error": "%s: %s" % (type(e).__name__, e)})

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    import threading
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)          # 先占端口，别等模型加载完
    threading.Thread(target=_load, daemon=True).start()        # 后台预热，第一句话就不用等
    print("embed_server on 127.0.0.1:%d (numpy, no torch)" % PORT, flush=True)
    srv.serve_forever()
