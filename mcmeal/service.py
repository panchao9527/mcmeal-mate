"""Bounded local live-query jobs. Credentials never enter browser responses."""
from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import RLock
from uuid import uuid4

from .live import Session
from .mcp import MCPError
from .planner import InputError, integer, validate


class WebError(ValueError):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


PHASES = {
    'query-nearby-stores':'正在查询营业门店',
    'query-meals':'正在查询所选门店菜单',
    'list-nutrition-foods':'正在读取官方营养信息',
    'query-meal-detail':'正在核对餐品和套餐组成',
    'calculate-price':'正在进行官方价格试算',
}


def validate_web_request(request, snapshot):
    validate(request, snapshot)
    if request['budget_cents'] > 1000000:
        raise InputError('单次预算最高10000元。')
    ids = [p['id'] for p in request['participants']]
    for person in request['participants']:
        if len(person['id']) > 40:
            raise InputError('成员标识过长。')
        if set(person.get('exclude_tags',[])) - {'beef','spicy','sugary-drink'}:
            raise InputError('页面支持主食肉类、辣味和饮料偏好筛选。')
        if set(person.get('prefer_tags',[])) - {'burger','classic','grilled','spicy','fries'}:
            raise InputError('不支持此口味偏好。')
        if set(person.get('required_categories',['main'])) - {'main','drink','fries'}:
            raise InputError('不支持此必选餐品类别。')
        if 'main' not in person.get('required_categories',['main']):
            raise InputError('每个人都需要安排一份主食。')
        if person.get('max_kcal') is not None and person['max_kcal'] > 10000:
            raise InputError('能量上限参数过大。')
    shared = request.get('shared', [])
    if not isinstance(shared,list) or len(shared)>1:
        raise InputError('页面支持一组共享中薯小食。')
    for row in shared:
        if not isinstance(row,dict) or row.get('offer_id') != 'shared-fries':
            raise InputError('共享小食选项无效。')
        quantity = integer(row.get('quantity',1),'共享份数',1)
        consumers = row.get('participant_ids',[])
        if quantity > 4 or not isinstance(consumers,list) or not consumers or any(not isinstance(pid,str) for pid in consumers):
            raise InputError('共享中薯为1至4份，且必须选择食用成员。')
        if len(set(consumers))!=len(consumers) or set(consumers)-set(ids):
            raise InputError('共享小食的成员无效或重复。')


class LocalService:
    def __init__(self, snapshot, session_factory=None, configured=None, clock=time.monotonic):
        self.snapshot = deepcopy(snapshot)
        self.configured = bool(os.environ.get('MCD_MCP_TOKEN')) if configured is None else configured
        self.session_factory = session_factory or (lambda progress: Session(progress=progress))
        self.clock = clock
        self.lock = RLock()
        self.pool = ThreadPoolExecutor(max_workers=1,thread_name_prefix='mcmeal-live')
        self.jobs = {}
        self.searches = {}
        self.menus = {}
        self.active = None
        self.closed = False

    def _update(self, jid, **fields):
        with self.lock:
            self.jobs[jid].update(fields)

    def _start(self, work):
        if not self.configured:
            raise WebError(409,'实时查询尚未配置。请在启动服务的终端设置 MCD_MCP_TOKEN。')
        with self.lock:
            if self.closed:
                raise WebError(503,'本机服务正在关闭。')
            if self.active:
                raise WebError(429,'上一条实时查询正在进行，请等待完成。')
            now=self.clock()
            self.jobs={jid:job for jid,job in self.jobs.items() if now-job['created']<900}
            self.searches={sid:s for sid,s in self.searches.items() if now-s['created']<1800}
            while len(self.jobs)>=24:
                del self.jobs[next(iter(self.jobs))]
            jid=uuid4().hex
            self.jobs[jid]={'id':jid,'status':'running','phase':'正在连接麦当劳官方服务','calls_completed':0,'created':now}
            self.active=jid
        self.pool.submit(self._run,jid,work)
        return {'job_id':jid}

    def _run(self, jid, work):
        def progress(name,count):
            if self.closed:
                raise MCPError('本机服务已关闭，查询停止。')
            self._update(jid,phase=PHASES.get(name,'正在查询'),calls_completed=count)
        try:
            session=self.session_factory(progress)
            result=work(session,jid)
            terminal={'status':'complete','phase':'查询完成','result':result}
        except (MCPError,InputError,WebError) as error:
            terminal={'status':'failed','phase':'查询未完成','error':str(error)}
        except Exception:
            terminal={'status':'failed','phase':'查询未完成','error':'查询发生异常，请稍后重新尝试。'}
        with self.lock:
            self.jobs[jid].update(terminal)
            self.active=None

    def start_stores(self, payload):
        if not isinstance(payload,dict):
            raise InputError('查询条件必须为对象。')
        city, keyword = payload.get('city'),payload.get('keyword')
        if not isinstance(city,str) or not 1<=len(city.strip())<=40 or not isinstance(keyword,str) or not 1<=len(keyword.strip())<=80:
            raise InputError('请填写城市和门店附近的地标关键词。')
        city,keyword=city.strip(),keyword.strip()
        def work(session,jid):
            stores=session.stores(city,keyword)
            choices=[{k:s.get(k) for k in ('storeCode','storeName','address','distance')} for s in stores]
            sid=uuid4().hex
            with self.lock:
                self.searches[sid]={'city':city,'keyword':keyword,'codes':{s['storeCode'] for s in stores},'created':self.clock()}
                while len(self.searches)>24:
                    del self.searches[next(iter(self.searches))]
            return {'search_id':sid,'stores':choices}
        return self._start(work)

    def start_live(self,payload):
        if not isinstance(payload,dict):
            raise InputError('配餐条件必须为对象。')
        request=payload.get('request')
        validate_web_request(request,self.snapshot)
        strategy=payload.get('strategy','preference')
        if strategy not in {'preference','economy'}:
            raise InputError('配餐策略无效。')
        sid,code=payload.get('search_id'),payload.get('store_code')
        if not isinstance(sid,str) or not isinstance(code,str):
            raise InputError('请先查询并选择门店。')
        with self.lock:
            search=deepcopy(self.searches.get(sid))
        if not search or self.clock()-search['created']>=1800 or code not in search['codes']:
            raise InputError('门店选择已失效，请重新查询门店。')
        request=deepcopy(request)
        def work(session,jid):
            cached=self.menus.get(code)
            cache_used=bool(cached and self.clock()-cached['created']<300)
            if cache_used:
                menu=deepcopy(cached['menu'])
                self._update(jid,phase='已复用近期菜单，正在重新核对整单价格')
            else:
                menu=session.refresh(search['city'],search['keyword'],code)
                self.menus[code]={'menu':deepcopy(menu),'created':self.clock()}
                while len(self.menus)>8:
                    del self.menus[next(iter(self.menus))]
            menu['source']['kind']='official-mcp-live'
            menu['source']['notice']='菜单最多缓存5分钟；每个新方案重新进行官方整单核价。'
            self._update(jid,phase='正在计算符合个人要求的饭局方案')
            result=session.quote(request,menu,strategy)
            result['source']=deepcopy(menu['source'])
            result['menu_cache_used']=cache_used
            result['candidate_count']=len(menu['offers'])
            result['evidence']=session.evidence()
            return result
        return self._start(work)

    def job(self,jid):
        with self.lock:
            job=self.jobs.get(jid)
            if not job or self.clock()-job['created']>=900:
                raise WebError(404,'查询任务已过期，请重新生成。')
            return {k:deepcopy(v) for k,v in job.items() if k!='created'}

    def close(self):
        self.closed=True
        self.pool.shutdown(wait=False,cancel_futures=True)
