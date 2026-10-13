import json
from copy import deepcopy
from datetime import datetime,timedelta,timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mcmeal.planner import InputError
from mcmeal.purchase import start,revise,record_quote,preview,confirm,record_order,query_request,record_status,reconcile,bill,repeat_request,write_state,load_state,locked

ROOT=Path(__file__).resolve().parent.parent


class PurchaseFlowTests(unittest.TestCase):
    def setUp(self):
        self.session=json.loads((ROOT/'examples/skill-demo/session.json').read_text(encoding='utf-8'))
        self.state=start(self.session)
        self.ways=[{'code':'demo-takeout','title':'演示外带'}]
        record_quote(self.state,'economy',self.quote_receipt())

    def quote_receipt(self,total=None):
        entry=self.state['draft']['plans']['economy'];call=entry['quote_request']
        return {'plan_fingerprint':call['plan_fingerprint'],'arguments':deepcopy(call['arguments']),
                'response':{'success':True,'datetime':'DEMO','data':{'price':entry['result']['total_cents'] if total is None else total,'takeWayList':deepcopy(self.ways)}}}

    def preview(self):
        return preview(self.state,'economy','demo-takeout')

    def recheck(self,p,total=None):
        return {'confirmation_id':p['confirmation_id'],'arguments':deepcopy(p['recheck_request']['arguments']),
                'response':{'success':True,'datetime':'DEMO','data':{'price':6900 if total is None else total,'takeWayList':deepcopy(self.ways)}}}

    def dispatch(self):
        p=self.preview();return confirm(self.state,p['confirmation_id'],'确认下单',self.recheck(p))

    def creation_receipt(self):
        p=self.state['purchase']
        return {'mode':'demo','confirmation_id':p['confirmation_id'],'attempt_id':p['attempt_id'],'arguments':deepcopy(p['create_arguments']),
                'response':{'success':True,'datetime':'DEMO','data':{'orderId':'DEMO-ORDER-1','payH5Url':'https://example.invalid/demo-pay',
                'orderDetail':{'orderId':'DEMO-ORDER-1','orderStatus':'1','realTotalAmount':'69.00','pickupCode':'DO-NOT-SHOW-BEFORE-PAID','storeName':'虚构门店'}}}}

    def query_receipt(self,status='2',amount='69.00'):
        query=query_request(self.state)
        return {'mode':'demo','query_id':query['query_id'],'arguments':query['tool_request']['arguments'],
                'response':{'success':True,'datetime':'DEMO','data':{'orderId':'DEMO-ORDER-1','orderStatus':status,'realTotalAmount':amount,'pickupCode':'DEMO-A01'}}}

    def test_complete_buying_demo_without_real_write_request(self):
        with patch('socket.socket',side_effect=AssertionError('Network must not be used')):
            dispatched=self.dispatch()
            self.assertEqual(dispatched['status'],'simulation_ready')
            self.assertNotIn('tool_request',dispatched)
            pending=record_order(self.state,self.creation_receipt())
            self.assertEqual(pending['status'],'awaiting_payment')
            self.assertNotIn('pickup_code',pending)
            paid=record_status(self.state,self.query_receipt())
            self.assertTrue(paid['paid_verified']);self.assertEqual(paid['pickup_code'],'DEMO-A01')
            self.assertIn('演示账单',bill(self.state))
            self.assertIn('示例共享薯条',bill(self.state))

    def test_generic_good_or_continue_is_not_purchase_approval(self):
        p=self.preview()
        for message in ['好','继续','先别下单','可以考虑','取消']:
            with self.assertRaises(InputError):confirm(self.state,p['confirmation_id'],message,self.recheck(p))
        self.assertEqual(self.state['status'],'confirmation_required')

    def test_invalid_or_changed_confirmation_id_rejected(self):
        p=self.preview()
        with self.assertRaises(InputError):confirm(self.state,'wrong','确认下单',self.recheck(p))
        self.state['purchase']['create_arguments']['items'][0]['quantity']+=1
        with self.assertRaises(InputError):confirm(self.state,p['confirmation_id'],'确认下单',self.recheck(p))

    def test_revised_budget_invalidates_previous_preview(self):
        old=self.preview();session=deepcopy(self.session);session['request']['budget_cents']=8000
        changed=revise(self.state,session)
        with self.assertRaises(InputError):confirm(changed,old['confirmation_id'],'确认下单',self.recheck(old))
        self.assertFalse(changed['quotes'])

    def test_unknown_take_way_not_guessed(self):
        with self.assertRaises(InputError):preview(self.state,'economy','guessed-locker')

    def test_price_change_requires_new_preview(self):
        p=self.preview();changed=confirm(self.state,p['confirmation_id'],'确认下单',self.recheck(p,total=7000))
        self.assertEqual(changed['status'],'price_changed')
        self.assertNotIn('tool_request',changed);self.assertIsNone(self.state['purchase'])

    def test_failed_price_recheck_does_not_export_create(self):
        p=self.preview();receipt=self.recheck(p);receipt['response']['success']=False
        with self.assertRaises(InputError):confirm(self.state,p['confirmation_id'],'确认下单',receipt)
        self.assertEqual(self.state['status'],'confirmation_required')

    def test_different_store_recheck_does_not_export_create(self):
        p=self.preview();receipt=self.recheck(p);receipt['arguments']['storeCode']='another'
        with self.assertRaises(InputError):confirm(self.state,p['confirmation_id'],'确认下单',receipt)

    def test_removed_take_way_invalidates_confirmation(self):
        p=self.preview();receipt=self.recheck(p);receipt['response']['data']['takeWayList']=[]
        with self.assertRaises(InputError):confirm(self.state,p['confirmation_id'],'确认下单',receipt)

    def test_take_way_description_change_requires_fresh_confirmation(self):
        p=self.preview();receipt=self.recheck(p);receipt['response']['data']['takeWayList'][0]['title']='演示堂食'
        changed=confirm(self.state,p['confirmation_id'],'确认下单',receipt)
        self.assertEqual(changed['status'],'options_changed');self.assertIsNone(self.state['purchase'])
        self.assertNotIn('tool_request',changed)

    def test_purchase_summary_shows_context_and_quantities(self):
        session=deepcopy(self.session)
        session['context'].update({'needTableware':False,'reservationDate':'2026-10-13 12:30'})
        for candidate in session['candidates']:
            candidate['quote']['arguments'].update(session['context'])
        self.state=start(session);record_quote(self.state,'economy',self.quote_receipt())
        shown=self.preview()['summary']
        self.assertIn('2026-10-13 12:30',shown);self.assertIn('餐具：不需要',shown)
        self.assertIn('× 1份',shown);self.assertIn('到店自取',shown)

    def test_create_export_is_single_use(self):
        p=self.preview();receipt=self.recheck(p)
        confirm(self.state,p['confirmation_id'],'确认下单',receipt)
        with self.assertRaises(InputError):confirm(self.state,p['confirmation_id'],'确认下单',receipt)
        with self.assertRaises(InputError):self.preview()

    def test_unknown_creation_outcome_blocks_retry(self):
        self.dispatch();receipt=self.creation_receipt();receipt['response']={'isError':True}
        result=record_order(self.state,receipt)
        self.assertEqual(result['status'],'outcome_unknown')
        with self.assertRaises(InputError):self.preview()
        with self.assertRaises(InputError):revise(self.state,self.session)
        with self.assertRaises(InputError):repeat_request(self.state)

    def test_repeat_reuses_needs_but_not_old_time_or_order(self):
        self.dispatch();record_order(self.state,self.creation_receipt());record_status(self.state,self.query_receipt())
        self.state['session']['context']['reservationDate']='2026-10-13 12:30'
        repeated=repeat_request(self.state)
        self.assertEqual(repeated['status'],'needs_current_menu');self.assertEqual(repeated['request'],self.session['request'])
        self.assertNotIn('reservationDate',repeated['previous_context']);self.assertNotIn('tool_request',repeated)
        repeated['request']['budget_cents']=1
        self.assertEqual(self.state['session']['request']['budget_cents'],9000)

    def test_creation_receipt_for_another_attempt_rejected(self):
        self.dispatch();receipt=self.creation_receipt();receipt['attempt_id']='other'
        with self.assertRaises(InputError):record_order(self.state,receipt)

    def test_order_exists_even_when_returned_amount_changed(self):
        self.dispatch();receipt=self.creation_receipt();receipt['response']['data']['orderDetail']['realTotalAmount']='70.00'
        result=record_order(self.state,receipt)
        self.assertEqual(result['status'],'order_created_review_needed')
        self.assertEqual(self.state['order']['order_id'],'DEMO-ORDER-1')
        with self.assertRaises(InputError):self.preview()

    def test_user_paid_claim_does_not_override_unpaid_status(self):
        self.dispatch();record_order(self.state,self.creation_receipt())
        result=record_status(self.state,self.query_receipt(status='1'))
        self.assertFalse(result['paid_verified']);self.assertNotIn('pickup_code',result)
        self.assertIn('尚未核实付款',bill(self.state))

    def test_unrelated_order_cannot_confirm_payment(self):
        self.dispatch();record_order(self.state,self.creation_receipt());receipt=self.query_receipt()
        receipt['response']['data']['orderId']='DEMO-OTHER'
        with self.assertRaises(InputError):record_status(self.state,receipt)

    def test_unknown_status_not_labeled_paid(self):
        self.dispatch();record_order(self.state,self.creation_receipt())
        result=record_status(self.state,self.query_receipt(status='99'))
        self.assertFalse(result['paid_verified']);self.assertNotIn('pickup_code',result)

    def test_stale_live_query_does_not_prove_paid(self):
        self.dispatch();record_order(self.state,self.creation_receipt())
        receipt=self.query_receipt();self.state['mode']='live';receipt['mode']='live'
        receipt['response']['datetime']=(datetime.now(timezone.utc)-timedelta(hours=1)).isoformat()
        with self.assertRaises(InputError):record_status(self.state,receipt)
        self.assertFalse(self.state['order']['paid_verified']);self.assertIsNotNone(self.state['last_query'])

    def test_actual_paid_amount_reconciles_bill_exactly(self):
        self.dispatch();record_order(self.state,self.creation_receipt())
        record_status(self.state,self.query_receipt(amount='69.01'))
        result=self.state['quotes']['economy']
        self.assertEqual(sum(p['total_cents'] for p in result['allocations']),6901)
        self.assertIn('合计：¥69.01',bill(self.state))

    def test_missing_actual_amount_is_labeled_estimate(self):
        self.dispatch();record_order(self.state,self.creation_receipt());receipt=self.query_receipt();receipt['response']['data'].pop('realTotalAmount')
        record_status(self.state,receipt)
        self.assertIn('暂按最近报价估算',bill(self.state))

    def test_cancelled_status_does_not_show_pickup_code(self):
        self.dispatch();record_order(self.state,self.creation_receipt())
        result=record_status(self.state,self.query_receipt(status='7'))
        self.assertEqual(result['status'],'cancelled');self.assertNotIn('pickup_code',result)

    def test_expired_unpaid_order_is_not_recreated(self):
        self.dispatch();record_order(self.state,self.creation_receipt());receipt=self.query_receipt(status='1')
        receipt['response']['data']['expirePayTime']=(datetime.now(timezone.utc)-timedelta(minutes=1)).isoformat()
        result=record_status(self.state,receipt)
        self.assertEqual(result['status'],'payment_expired');self.assertNotIn('pickup_code',result)
        with self.assertRaises(InputError):self.preview()

    def history_receipt(self):
        query=query_request(self.state);p=self.state['purchase'];args=p['create_arguments']
        row={'orderId':'DEMO-FOUND','storeCode':args['storeCode'],'orderType':'1','beType':'1',
             'createTime':p['submitted_at'],'realTotalAmount':'69.00',
             'orderProductList':[{'productCode':i['productCode'],'quantity':i['quantity']} for i in args['items']]}
        return {'mode':'demo','query_id':query['query_id'],'arguments':{},'response':{'success':True,'data':{'list':[row]}}}

    def test_unknown_outcome_can_be_reconciled_without_second_order(self):
        self.dispatch();receipt=self.history_receipt()
        with self.assertRaises(InputError):reconcile(self.state,receipt,'DEMO-FOUND','好')
        found=reconcile(self.state,receipt,'DEMO-FOUND','确认认领订单')
        self.assertEqual(found['status'],'order_found')
        self.assertEqual(query_request(self.state)['tool_request']['tool'],'query-order')
        with self.assertRaises(InputError):self.preview()

    def test_old_or_different_business_order_is_not_claimed(self):
        self.dispatch();receipt=self.history_receipt();row=receipt['response']['data']['list'][0]
        row['beType']='2'
        with self.assertRaises(InputError):reconcile(self.state,receipt,'DEMO-FOUND','确认认领订单')
        row['beType']='1';row['createTime']=(datetime.now(timezone.utc)-timedelta(hours=1)).isoformat()
        with self.assertRaises(InputError):reconcile(self.state,receipt,'DEMO-FOUND','确认认领订单')
        self.assertEqual(self.state['status'],'submission_pending');self.assertIsNone(self.state['order'])

    def test_pending_payment_link_has_to_be_https(self):
        self.dispatch();receipt=self.creation_receipt();receipt['response']['data']['payH5Url']='javascript:alert(1)'
        result=record_order(self.state,receipt)
        self.assertIsNone(result['pay_url']);self.assertIn('未返回可用支付链接',result['message'])

    def test_live_confirm_exports_bound_real_tool_only_after_recheck(self):
        session=deepcopy(self.session);session['mode']='live';session['context']['storeCode']='12345'
        for c in session['candidates']:
            c['quote']['arguments']['storeCode']='12345';c['quote']['response']['datetime']=datetime.now(timezone.utc).isoformat()
        state=start(session);entry=state['draft']['plans']['economy'];call=entry['quote_request']
        quote={'plan_fingerprint':call['plan_fingerprint'],'arguments':call['arguments'],'response':{'success':True,'datetime':datetime.now(timezone.utc).isoformat(),'data':{'price':6900,'takeWayList':self.ways}}}
        record_quote(state,'economy',quote);p=preview(state,'economy','demo-takeout')
        check={'confirmation_id':p['confirmation_id'],'arguments':call['arguments'],'response':deepcopy(quote['response'])}
        check['response']['datetime']=datetime.now(timezone.utc).isoformat()
        result=confirm(state,p['confirmation_id'],'确认下单',check)
        self.assertEqual(result['tool_request']['tool'],'create-order')
        self.assertEqual(result['tool_request']['arguments']['storeCode'],'12345')
        self.assertEqual(state['status'],'submission_pending')

    def test_state_integrity_and_exclusive_lock(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'local-runs') as directory:
            path=Path(directory)/'meal.json';write_state(path,self.state)
            self.assertEqual(load_state(path)['status'],'choices_ready')
            bad=json.loads(path.read_text(encoding='utf-8'));bad['session']['request']['budget_cents']=999999
            path.write_text(json.dumps(bad),encoding='utf-8')
            with self.assertRaises(InputError):load_state(path)
            with locked(path):
                with self.assertRaises(InputError):
                    with locked(path):pass


if __name__=='__main__':unittest.main()
