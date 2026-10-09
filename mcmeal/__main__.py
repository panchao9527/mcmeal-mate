from __future__ import annotations

import argparse
import json
from pathlib import Path
from .planner import plan
from .live import Session, save


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main():
    parser=argparse.ArgumentParser(description="麦麦搭子：多人配餐与点餐验收")
    commands=parser.add_subparsers(dest="command",required=True)
    local=commands.add_parser("plan")
    local.add_argument("--request",default="examples/request.json")
    local.add_argument("--menu",default="examples/live-menu-snapshot.json")
    local.add_argument("--strategy",choices=["preference","economy"],default="preference")
    local.add_argument("--out")
    refresh=commands.add_parser("refresh")
    refresh.add_argument("--city",default="上海市")
    refresh.add_argument("--keyword",default="人民广场")
    refresh.add_argument("--store-code")
    refresh.add_argument("--out",default="local-runs/menu.json")
    refresh.add_argument("--evidence",default="local-runs/evidence.json")
    quote=commands.add_parser("quote")
    quote.add_argument("--request",default="examples/request.json")
    quote.add_argument("--menu",default="local-runs/menu.json")
    quote.add_argument("--strategy",choices=["preference","economy"],default="preference")
    quote.add_argument("--out",default="local-runs/quoted-plan.json")
    web=commands.add_parser("serve")
    web.add_argument("--port",type=int,default=8765)
    args=parser.parse_args()
    if args.command=="serve":
        from .web import serve
        serve(args.port)
        return
    if args.command=="plan":
        result=plan(read(args.request),read(args.menu),args.strategy)
    elif args.command=="refresh":
        session=Session()
        result=session.refresh(args.city,args.keyword,args.store_code)
        save(args.evidence,session.evidence())
    else:
        session=Session()
        result=session.quote(read(args.request),read(args.menu),args.strategy)
    if args.out:
        save(args.out,result)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=="__main__":
    main()
