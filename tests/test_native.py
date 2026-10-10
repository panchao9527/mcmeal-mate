import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest
from unittest.mock import patch

from mcmeal.native import business, finalize, normalize, prepare, share
from mcmeal.planner import InputError

ROOT=Path(__file__).resolve().parent.parent


class NativeSkillTests(unittest.TestCase):
    def setUp(self):
        self.session=json.loads((ROOT/'examples/skill-demo/session.json').read_text(encoding='utf-8'))

    def draft(self,live=False):
        data=deepcopy(self.session)
        if live:
            data['mode']='live'
            for candidate in data['candidates']:
                candidate['quote']['response']['datetime']=datetime.now(timezone.utc).isoformat()
        return prepare(normalize(data))

    def receipt(self,draft,strategy='preference',total=None):
        entry=draft['plans'][strategy]
        call=entry['quote_request']
        return {'plan_fingerprint':call['plan_fingerprint'],'arguments':deepcopy(call['arguments']),
                'response':{'success':True,'datetime':datetime.now(timezone.utc).isoformat(),
                            'data':{'price':entry['result']['total_cents'] if total is None else total}}}

    def test_complete_demo_without_network_or_credentials(self):
        with patch('socket.socket',side_effect=AssertionError('Unexpected network')):
            draft=self.draft()
            for strategy in ('preference','economy'):
                result=finalize(draft,self.receipt(draft,strategy),strategy)
                self.assertEqual(result['status'],'demo_quoted')
                text=share(result)
                self.assertIn('虚构餐品和金额',text)
                self.assertIn('示例共享薯条 × 1',text)
                self.assertNotIn('grilled',text)
                self.assertNotIn('classic',text)
                self.assertNotIn('官方',json.dumps(result['acceptance'],ensure_ascii=False))
                for name in ['小林','小周','小陈']:
                    self.assertIn(name,text)

    def test_live_wrapper_and_total_reconciled_to_the_cent(self):
        draft=self.draft(live=True)
        receipt=self.receipt(draft,total=8501)
        receipt['response']={'content':[{'type':'text','text':json.dumps(receipt['response'])}],'isError':False}
        result=finalize(draft,receipt)
        self.assertEqual(result['status'],'quoted')
        self.assertEqual(sum(row['total_cents'] for row in result['allocations']),8501)

    def test_candidate_wrong_store_quote_rejected(self):
        self.session['candidates'][0]['quote']['arguments']['storeCode']='different-store'
        with self.assertRaises(InputError):normalize(self.session)

    def test_unknown_attributes_cannot_be_claimed_known(self):
        self.session['candidates'][0]['attribute_evidence'].pop('beef')
        with self.assertRaises(InputError):normalize(self.session)

    def test_missing_nutrition_evidence_rejected(self):
        self.session['candidates'][0].pop('nutrition_evidence')
        with self.assertRaises(InputError):normalize(self.session)

    def test_new_product_codes_and_four_rounds_supported(self):
        item=self.session['candidates'][0]['items'][0]
        item['productCode']='unseen-product-2099'
        item['roundList']=[{'round':str(i),'comboItemList':[{'code':f'choice-{i}','quantity':1}]} for i in range(4)]
        self.session['candidates'][0]['quote']['arguments']['items']=deepcopy(self.session['candidates'][0]['items'])
        normalized=normalize(self.session)
        draft=prepare(normalized)
        exported=draft['plans']['economy']['quote_request']['arguments']['items']
        found=next(i for i in exported if i['productCode']=='unseen-product-2099')
        self.assertEqual(len(found['roundList']),4)
        self.assertEqual(found['quantity'],3)

    def test_different_round_choices_are_not_merged(self):
        for candidate in self.session['candidates'][:3]:
            candidate['items']=[{'productCode':'same-set','quantity':1,'roundList':[{'round':'drink','comboItemList':[{'code':candidate['id'],'quantity':1}]}]}]
            candidate['quote']['arguments']['items']=deepcopy(candidate['items'])
        draft=prepare(normalize(self.session))
        items=draft['plans']['preference']['quote_request']['arguments']['items']
        sets=[i for i in items if i['productCode']=='same-set']
        self.assertEqual(len(sets),2)

    def test_budget_edit_invalidates_old_receipt(self):
        old=self.draft();receipt=self.receipt(old)
        self.session['request']['budget_cents']=9100
        updated=self.draft()
        with self.assertRaises(InputError):finalize(updated,receipt)

    def test_tampering_with_prepared_request_rejected(self):
        draft=self.draft();receipt=self.receipt(draft)
        draft['normalized']['request']['budget_cents']=9200
        with self.assertRaises(InputError):finalize(draft,receipt)

    def test_quote_for_different_item_quantity_rejected(self):
        draft=self.draft();receipt=self.receipt(draft)
        receipt['arguments']['items'][0]['quantity']+=1
        with self.assertRaises(InputError):finalize(draft,receipt)

    def test_new_prepare_batch_invalidates_previous_fingerprint(self):
        old=self.draft();new=self.draft()
        with self.assertRaises(InputError):finalize(new,self.receipt(old))

    def test_transport_or_business_failure_not_accepted(self):
        draft=self.draft()
        for response in [{'isError':True,'structuredContent':{'success':True,'data':{'price':100}}},
                         {'success':False,'data':{'price':100}}]:
            receipt=self.receipt(draft);receipt['response']=response
            with self.assertRaises(InputError):finalize(draft,receipt)

    def test_non_integer_or_negative_official_price_rejected(self):
        draft=self.draft()
        for amount in [True,'8500',8500.0,-1,None]:
            receipt=self.receipt(draft);receipt['response']['data']['price']=amount
            with self.assertRaises(InputError):finalize(draft,receipt)

    def test_old_or_missing_live_timestamp_rejected(self):
        now=datetime.now(timezone.utc)
        for stamp in [None,(now-timedelta(hours=1)).isoformat(),(now+timedelta(hours=1)).isoformat()]:
            with self.assertRaises(InputError):
                business({'success':True,'datetime':stamp,'data':{'price':100}},'live',now=now)

    def test_receipt_predating_live_plan_rejected(self):
        draft=self.draft(live=True);receipt=self.receipt(draft)
        receipt['response']['datetime']=(datetime.now(timezone.utc)-timedelta(minutes=1)).isoformat()
        with self.assertRaises(InputError):finalize(draft,receipt)

    def test_final_budget_failure_is_clear_in_share_text(self):
        draft=self.draft(live=True);result=finalize(draft,self.receipt(draft,total=9500))
        self.assertEqual(result['status'],'quote_over_budget')
        self.assertIn('本方案超出预算',share(result))

    def test_unquoted_plan_cannot_be_shared_as_final(self):
        with self.assertRaises(InputError):share(self.draft()['plans']['preference']['result'])

    def test_unclear_coupon_ownership_is_rejected(self):
        candidate=self.session['candidates'][0]
        candidate['items'][0]['couponId']='unknown-owner-coupon'
        candidate['quote']['arguments']['items']=deepcopy(candidate['items'])
        with self.assertRaises(InputError):normalize(self.session)


if __name__=='__main__':
    unittest.main()
