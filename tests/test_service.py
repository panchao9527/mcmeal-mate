import json
import time
import unittest
import urllib.request
from copy import deepcopy
from threading import Event,Thread
from urllib.error import HTTPError

from mcmeal.planner import InputError,apply_official_total,plan
from mcmeal.service import LocalService,WebError,validate_web_request
from mcmeal.web import make_server
from mcmeal.live import Session
from test_planner import offer,request


MENU={'offers':[offer()],'fee_cents':0,'source':{'endpoint':'https://mcp.mcd.cn','store_code':'123','store_name':'测试门店','order_type':1,'be_type':1,'fetched_at':'2026-10-10 12:00:00'}}


class FakeSession:
    def __init__(self,progress,owner):
        self.progress,self.owner=progress,owner

    def stores(self,city,keyword):
        self.owner['searches']+=1
        self.progress('query-nearby-stores',0)
        return [{'storeCode':'123','storeName':'测试门店','businessStatus':True,'address':'公开门店地址'}]

    def refresh(self,city,keyword,code):
        self.owner['refreshes']+=1
        self.progress('query-meals',1)
        if self.owner.get('gate'):
            self.owner['gate'].wait(2)
        return deepcopy(MENU)

    def quote(self,req,menu,strategy):
        self.owner['quotes']+=1
        self.progress('calculate-price',2)
        result=plan(req,menu,strategy)
        if result['status']=='planned':
            result=apply_official_total(result,result['total_cents']+self.owner.get('delta',0),req['budget_cents'])
            result['quoted_at']='2026-10-10 12:01:00'
        return result

    def evidence(self):
        return {'mutating_tools_called':[]}


class LiveServiceTests(unittest.TestCase):
    def setUp(self):
        self.owner={'searches':0,'refreshes':0,'quotes':0}
        self.now=[0]
        self.service=LocalService(MENU,lambda progress:FakeSession(progress,self.owner),True,clock=lambda:self.now[0])

    def tearDown(self):
        if self.owner.get('gate'):
            self.owner['gate'].set()
        self.service.close()

    def finish(self,job):
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            status=self.service.job(job['job_id'])
            if status['status']!='running':
                return status
            time.sleep(.005)
        self.fail('job did not complete')

    def selection(self):
        result=self.finish(self.service.start_stores({'city':'测试市','keyword':'公开地标'}))
        self.assertEqual(result['status'],'complete')
        return {'search_id':result['result']['search_id'],'store_code':'123'}

    def test_unconfigured_service_does_not_call_remote(self):
        self.service.configured=False
        with self.assertRaises(WebError) as captured:
            self.service.start_stores({'city':'测试市','keyword':'地标'})
        self.assertEqual(captured.exception.status,409)
        self.assertEqual(self.owner['searches'],0)

    def test_forged_store_rejected_before_remote_work(self):
        selection=self.selection();selection['store_code']='999'
        with self.assertRaises(InputError):
            self.service.start_live({**selection,'request':request(1)})
        self.assertEqual(self.owner['refreshes'],0)

    def test_selection_expires(self):
        selection=self.selection();self.now[0]=1801
        with self.assertRaises(InputError):
            self.service.start_live({**selection,'request':request(1)})

    def test_async_result_contains_current_quote(self):
        selected=self.selection()
        result=self.finish(self.service.start_live({**selected,'request':request(1)}))
        self.assertEqual(result['status'],'complete')
        self.assertEqual(result['result']['status'],'quoted')
        self.assertEqual(result['result']['source']['kind'],'official-mcp-live')

    def test_cached_menu_still_gets_fresh_quote(self):
        selected=self.selection()
        first=self.finish(self.service.start_live({**selected,'request':request(1)}))
        self.owner['delta']=1
        second=self.finish(self.service.start_live({**selected,'request':request(1)}))
        self.assertEqual(self.owner['refreshes'],1)
        self.assertEqual(self.owner['quotes'],2)
        self.assertTrue(second['result']['menu_cache_used'])
        self.assertEqual(second['result']['total_cents'],first['result']['total_cents']+1)

    def test_menu_cache_expires(self):
        selected=self.selection()
        self.finish(self.service.start_live({**selected,'request':request(1)}))
        self.now[0]=301
        self.finish(self.service.start_live({**selected,'request':request(1)}))
        self.assertEqual(self.owner['refreshes'],2)

    def test_live_budget_failure_remains_failed(self):
        selected=self.selection();self.owner['delta']=1
        result=self.finish(self.service.start_live({**selected,'request':request(1,1000)}))
        self.assertEqual(result['result']['status'],'quote_over_budget')
        self.assertEqual(result['result']['acceptance'][1]['status'],'FAIL')

    def test_unknown_exceptions_do_not_reflect_credentials(self):
        def broken(progress):
            raise RuntimeError('secret-test-credential')
        self.service.session_factory=broken
        result=self.finish(self.service.start_stores({'city':'测试市','keyword':'地标'}))
        self.assertEqual(result['status'],'failed')
        self.assertNotIn('secret-test-credential',json.dumps(result))

    def test_concurrent_live_request_is_bounded(self):
        selected=self.selection();self.owner['gate']=Event()
        running=self.service.start_live({**selected,'request':request(1)})
        with self.assertRaises(WebError) as captured:
            self.service.start_live({**selected,'request':request(1)})
        self.assertEqual(captured.exception.status,429)
        self.owner['gate'].set();self.finish(running)

    def test_malformed_request_rejected(self):
        for invalid in [None,[],{'budget_cents':1000,'participants':[None]}]:
            with self.assertRaises(InputError):
                validate_web_request(invalid,MENU)

    def test_shared_consumer_removed_from_request_rejected(self):
        req=request(1);req['shared']=[{'offer_id':'shared-fries','participant_ids':['0','removed']}]
        with self.assertRaises(InputError):
            validate_web_request(req,MENU)


class HttpBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.service=LocalService(MENU,configured=False)
        cls.server=make_server(0,cls.service)
        cls.thread=Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
        cls.url=f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.service.close()

    def fetch(self,path,body=None,headers=None):
        req=urllib.request.Request(self.url+path,data=json.dumps(body).encode() if body is not None else None,
            headers={'Content-Type':'application/json',**(headers or {})})
        try:
            with urllib.request.urlopen(req,timeout=3) as response:
                return response.status,json.load(response)
        except HTTPError as error:
            return error.code,json.load(error)

    def test_foreign_origin_blocked(self):
        status,_=self.fetch('/api/stores',{'city':'上海市','keyword':'地标'},{'Origin':'https://untrusted.example'})
        self.assertEqual(status,403)

    def test_rebinding_host_blocked_on_get(self):
        status,_=self.fetch('/api/data',headers={'Host':'untrusted.example'})
        self.assertEqual(status,403)

    def test_public_state_has_only_boolean_configuration(self):
        status,data=self.fetch('/api/data')
        self.assertEqual(status,200)
        self.assertIs(data['live_configured'],False)
        self.assertNotIn('Authorization',json.dumps(data))

    def test_custom_person_is_used_by_plan_endpoint(self):
        req=request(1,10000);req['participants'][0]['name']='自定义成员'
        status,data=self.fetch('/api/plan',{'request':req})
        self.assertEqual(status,200)
        self.assertEqual([a['name'] for a in data['allocations']],['自定义成员'])

    def test_malformed_json_shape_is_400(self):
        status,_=self.fetch('/api/plan',[])
        self.assertEqual(status,400)

    def test_missing_token_does_not_create_job(self):
        status,_=self.fetch('/api/stores',{'city':'上海市','keyword':'地标'})
        self.assertEqual(status,409)


class BreakfastAvailabilityTests(unittest.TestCase):
    def setUp(self):
        self.session=object.__new__(Session)

    def test_missing_shared_fries_reports_supply_conflict(self):
        req=request(1);req['shared']=[{'offer_id':'shared-fries','participant_ids':['0']}]
        result=self.session.quote(req,MENU)
        self.assertEqual(result['status'],'infeasible')
        self.assertIn('共享中薯条',result['reason'])

    def test_required_fries_not_replaced_by_breakfast_item(self):
        req=request(1);req['participants'][0]['required_categories']=['main','fries']
        result=self.session.quote(req,MENU)
        self.assertEqual(result['status'],'infeasible')
        self.assertIn('必有薯条',result['reason'])


if __name__=='__main__':
    unittest.main()
