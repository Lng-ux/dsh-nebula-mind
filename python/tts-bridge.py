#!/usr/bin/env python3
"""接住 dsh-nebula-mind 推过来的「节奏 / 力度 / 黏人度 / 距离感」。

这不是插件的一部分，是给「用调接口那种方式」的人准备的一个最小接收端：
它只做一件事——把收到的 JSON 存成同目录下的 tts-latest.json，并留一个
读回来的口子。你的 TTS 怎么念、什么时候念，仍然是你自己的事。

用法：
    python tts-bridge.py            # 默认 127.0.0.1:8765
    python tts-bridge.py 9000       # 换端口

然后到插件设置里选「调接口」，地址填 http://127.0.0.1:8765

只用标准库，不用装任何东西。想改成直接调你的语音服务，看 handle() 那几行。
"""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "tts-latest.json")


def save(payload):
    """存下来，再交给你的 TTS。这里默认只存，你可以在下面加一行调用。"""
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, OUT)
    # 例：subprocess.run(["你的TTS", "--rate", str(payload.get("rate"))])
    return OUT


class H(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("content-type", "application/json; charset=utf-8")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/ping"):
            return self._send(200, {"ok": True, "out": OUT})
        if self.path.startswith("/latest"):
            try:
                with open(OUT, encoding="utf-8") as f:
                    return self._send(200, json.load(f))
            except Exception as e:
                return self._send(404, {"ok": False, "err": str(e)})
        return self._send(404, {"ok": False, "err": "只有 /ping 和 /latest"})

    def do_POST(self):
        try:
            n = int(self.headers.get("content-length") or 0)
            if n > 64 * 1024:                 # 这组数就几十字节，超过 64KB 一律不收
                return self._send(413, {"ok": False, "err": "太大了，这组数不该超过 64KB"})
            payload = json.loads(self.rfile.read(n) or b"{}")
            if not isinstance(payload, dict):
                raise ValueError("要一个 JSON 对象")
            path = save(payload)
            print("[bridge] 收到 " + json.dumps(payload, ensure_ascii=False))
            return self._send(200, {"ok": True, "saved": path})
        except Exception as e:
            return self._send(400, {"ok": False, "err": str(e)})

    def log_message(self, *a):
        pass          # 安静点，别刷屏


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    print("[bridge] 听 127.0.0.1:%d，收到就写 %s" % (port, OUT))
    print("[bridge] 到插件设置里选「调接口」，地址填 http://127.0.0.1:%d" % port)
    HTTPServer(("127.0.0.1", port), H).serve_forever()
