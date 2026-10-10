"""Loopback web UI, with local asynchronous jobs for official MCP reads."""
import json
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from .planner import InputError,plan
from .service import LocalService,WebError,validate_web_request

ROOT=Path(__file__).resolve().parent.parent


def make_server(port=8765,service=None):
    snapshot=json.loads((ROOT/'examples/live-menu-snapshot.json').read_text(encoding='utf-8'))
    service=service or LocalService(snapshot)

    class Handler(BaseHTTPRequestHandler):
        def allowed(self):
            hosts={f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'}
            return self.headers.get('Host') in hosts and self.headers.get('Origin') in {None,*[f'http://{host}' for host in hosts]}

        def send(self,status,data,content_type='application/json; charset=utf-8'):
            if not isinstance(data,bytes):
                data=json.dumps(data,ensure_ascii=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type',content_type)
            self.send_header('Content-Length',str(len(data)))
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Cache-Control','no-store')
            self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'")
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError,ConnectionResetError):
                pass

        def do_GET(self):
            if not self.allowed():
                return self.send(403,{'error':'仅允许本机页面访问。'})
            routes={'/':('index.html','text/html'),'/app.js':('app.js','application/javascript'),'/style.css':('style.css','text/css'),'/custom.css':('custom.css','text/css')}
            if self.path in routes:
                filename,mime=routes[self.path]
                self.send(200,(ROOT/'demo'/filename).read_bytes(),mime+'; charset=utf-8')
            elif self.path=='/api/data':
                self.send(200,{'request':json.loads((ROOT/'examples/request.json').read_text(encoding='utf-8')),
                               'source':snapshot['source'],'live_configured':service.configured,'version':'0.2.0'})
            elif self.path.startswith('/api/jobs/'):
                try:
                    self.send(200,service.job(self.path[len('/api/jobs/'):]))
                except WebError as error:
                    self.send(error.status,{'error':str(error)})
            else:
                self.send(404,{'error':'页面不存在。'})

        def do_POST(self):
            if not self.allowed():
                return self.send(403,{'error':'仅允许本机页面调用。'})
            if self.headers.get('Content-Type','').split(';')[0].strip()!='application/json':
                return self.send(415,{'error':'请使用JSON格式。'})
            try:
                length=int(self.headers.get('Content-Length',0))
                if not 0<length<=32000:
                    raise InputError('请求大小不符合要求。')
                payload=json.loads(self.rfile.read(length))
                if self.path=='/api/plan':
                    validate_web_request(payload['request'],snapshot)
                    self.send(200,plan(payload['request'],snapshot,payload.get('strategy','preference')))
                elif self.path=='/api/stores':
                    self.send(202,service.start_stores(payload))
                elif self.path=='/api/live':
                    self.send(202,service.start_live(payload))
                else:
                    self.send(404,{'error':'接口不存在。'})
            except WebError as error:
                self.send(error.status,{'error':str(error)})
            except InputError as error:
                self.send(400,{'error':str(error)})
            except (ValueError,KeyError,TypeError,AttributeError):
                self.send(400,{'error':'输入不符合要求，请检查成员、预算、共享小食或门店选择。'})

        def log_message(self,*args):
            pass

    server=ThreadingHTTPServer(('127.0.0.1',port),Handler)
    server.daemon_threads=True
    server.local_service=service
    return server


def serve(port=8765):
    server=make_server(port)
    print(f'麦麦搭子：http://127.0.0.1:{port}，按 Ctrl+C 结束。',flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.local_service.close()
        server.server_close()
