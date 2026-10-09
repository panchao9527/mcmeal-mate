"""Loopback-only, offline review demo. No credentials or live calls in the UI."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from .planner import InputError, plan

ROOT = Path(__file__).resolve().parent.parent


def serve(port=8765):
    class Handler(BaseHTTPRequestHandler):
        def send(self,status,data,content_type="application/json; charset=utf-8"):
            if not isinstance(data,bytes):
                data=json.dumps(data,ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type",content_type)
            self.send_header("Content-Length",str(len(data)))
            self.send_header("X-Content-Type-Options","nosniff")
            self.send_header("Cache-Control","no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            routes={"/":("index.html","text/html"),"/app.js":("app.js","application/javascript"),"/style.css":("style.css","text/css")}
            if self.path in routes:
                filename,mime=routes[self.path]
                self.send(200,(ROOT/"demo"/filename).read_bytes(),mime+"; charset=utf-8")
            elif self.path=="/api/data":
                self.send(200,{"request":json.loads((ROOT/"examples/request.json").read_text(encoding="utf-8")),
                               "source":json.loads((ROOT/"examples/live-menu-snapshot.json").read_text(encoding="utf-8"))["source"]})
            else:
                self.send(404,{"error":"页面不存在"})

        def do_POST(self):
            if self.path!="/api/plan":
                return self.send(404,{"error":"接口不存在"})
            allowed={f"127.0.0.1:{port}",f"localhost:{port}"}
            if self.headers.get("Host") not in allowed or self.headers.get("Origin") not in {None,*[f"http://{host}" for host in allowed]}:
                return self.send(403,{"error":"仅允许本机演示页面调用"})
            try:
                length=int(self.headers.get("Content-Length",0))
                if not 0 < length <= 32000:
                    raise InputError("请求大小不符合要求")
                payload=json.loads(self.rfile.read(length))
                menu=json.loads((ROOT/"examples/live-menu-snapshot.json").read_text(encoding="utf-8"))
                result=plan(payload["request"],menu,payload.get("strategy","preference"))
                self.send(200,result)
            except (InputError,ValueError,KeyError,TypeError,AttributeError) as error:
                self.send(400,{"error":str(error)})

        def log_message(self,*args):
            pass

    server=ThreadingHTTPServer(("127.0.0.1",port),Handler)
    print(f"麦麦搭子演示：http://127.0.0.1:{port}，按 Ctrl+C 结束。",flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
