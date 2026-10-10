import unittest
from copy import deepcopy
from mcmeal.planner import InputError, apply_official_total, plan, split_cents
from mcmeal.live import business_data, nutrition_map
from mcmeal.mcp import Client, MCPError


def offer(oid="chicken",price=1000,**kw):
    return {"id":oid,"name":oid,"price_cents":price,"available":True,"kcal":300,
            "categories":["main","drink"],"tags":[],"known_tags":["beef","sugary-drink"],**kw}


def request(n=2,budget=4000):
    return {"budget_cents":budget,"participants":[{"id":str(i),"name":str(i)} for i in range(n)]}


class PlannerTests(unittest.TestCase):
    def test_remainder_is_exact(self):
        self.assertEqual(split_cents(1001,["a","b","c"]),{"a":334,"b":334,"c":333})

    def test_person_not_dropped_when_budget_insufficient(self):
        result=plan(request(budget=1999),{"offers":[offer()]})
        self.assertEqual(result["status"],"infeasible")
        self.assertEqual(result["shortfall_at_least_cents"],1)

    def test_beef_exclusion_and_missing_information(self):
        req=request(1);req["participants"][0]["exclude_tags"]=["beef"]
        menu={"offers":[offer("beef",1,tags=["beef"]),offer("unknown",2,known_tags=[]),offer()]}
        self.assertEqual(plan(req,menu)["allocations"][0]["offer_id"],"chicken")

    def test_purchase_required_price_is_not_selected(self):
        menu={"offers":[offer("card",1,requires_purchase=True),offer()]}
        self.assertEqual(plan(request(1),menu)["total_cents"],1000)

    def test_coupon_limit_shared_across_offers(self):
        coupon={"id":"coupon","owner":"payer","max_uses":1}
        menu={"offers":[offer("a",100,coupon=coupon),offer("b",200,coupon=coupon),offer()]}
        req={**request(),"payer_id":"payer"}
        self.assertEqual(plan(req,menu,"economy")["total_cents"],1100)

    def test_coupon_from_another_account_excluded(self):
        menu={"offers":[offer("coupon",1,coupon={"id":"x","owner":"other","max_uses":1}),offer()]}
        self.assertEqual(plan({**request(1),"payer_id":"me"},menu)["total_cents"],1000)

    def test_quantity_cap_enforced(self):
        menu={"offers":[offer("limited",1,max_quantity=1),offer()]}
        self.assertEqual(plan(request(),menu,"economy")["total_cents"],1001)

    def test_cents_are_integers(self):
        for value in [1.5,True,-1,"100"]:
            with self.assertRaises(InputError):
                plan(request(),{"offers":[offer(price=value)]})

    def test_unknown_calories_cannot_pass_hard_cap(self):
        req=request(1);req["participants"][0]["max_kcal"]=500
        self.assertEqual(plan(req,{"offers":[offer(kcal=None)]})["status"],"infeasible")

    def test_shared_calories_are_counted_in_cap(self):
        req=request();req["participants"][0]["max_kcal"]=350
        req["shared"]=[{"offer_id":"snack","participant_ids":["0","1"]}]
        menu={"offers":[offer(),offer("snack",100,categories=["snack"],kcal=101)]}
        self.assertEqual(plan(req,menu)["status"],"infeasible")

    def test_unknown_shared_calories_stop_cap_check(self):
        req=request();req["participants"][0]["max_kcal"]=500
        req["shared"]=[{"offer_id":"snack","participant_ids":["0","1"]}]
        self.assertEqual(plan(req,{"offers":[offer(),offer("snack",100,categories=["snack"],kcal=None)]})["status"],"infeasible")

    def test_shared_consumer_preferences_enforced(self):
        req=request();req["participants"][0]["exclude_tags"]=["beef"]
        req["shared"]=[{"offer_id":"snack","participant_ids":["0","1"]}]
        self.assertEqual(plan(req,{"offers":[offer(),offer("snack",100,tags=["beef"],categories=["snack"])]})["status"],"infeasible")

    def test_fee_and_shared_remainders_conserve_total(self):
        req=request(3);req["shared"]=[{"offer_id":"snack","participant_ids":["0","2"]}]
        result=plan(req,{"fee_cents":101,"offers":[offer(),offer("snack",101,categories=["snack"])]})
        self.assertEqual(sum(a["total_cents"] for a in result["allocations"]),3202)
        self.assertEqual(result["allocations"][1]["shared_cents"],0)

    def test_quote_can_fail_budget_even_after_planning(self):
        planned=plan(request(budget=2000),{"offers":[offer()]})
        quoted=apply_official_total(planned,2001,2000)
        self.assertEqual(quoted["status"],"quote_over_budget")
        self.assertEqual(quoted["acceptance"][1]["status"],"FAIL")
        self.assertEqual(sum(a["total_cents"] for a in quoted["allocations"]),2001)

    def test_quote_discount_split_is_exact(self):
        planned=plan(request(),{"offers":[offer()]})
        self.assertEqual(sum(a["total_cents"] for a in apply_official_total(planned,1999,4000)["allocations"]),1999)

    def test_search_limit_is_not_proof_of_infeasibility(self):
        result=plan(request(),{"offers":[offer()]},node_limit=1)
        self.assertEqual(result["status"],"search_limit")
        self.assertFalse(result["search_complete"])

    def test_duplicate_people_rejected(self):
        req=request();req["participants"][1]["id"]="0"
        with self.assertRaises(InputError):
            plan(req,{"offers":[offer()]})

    def test_preference_and_economy_have_different_objectives(self):
        req=request(1);req["participants"][0]["prefer_tags"]=["grilled"]
        menu={"offers":[offer(),offer("grilled",2000,tags=["grilled"])]}
        self.assertEqual(plan(req,menu,"preference")["total_cents"],2000)
        self.assertEqual(plan(req,menu,"economy")["total_cents"],1000)

    def test_inputs_are_not_mutated(self):
        req=request();menu={"offers":[offer()]};before=deepcopy((req,menu))
        plan(req,menu)
        self.assertEqual((req,menu),before)

    def test_eight_people_search_finishes_with_duplicate_resources(self):
        req=request(8,100000)
        menu={'offers':[offer(str(i),1000+i*50,tags=['grilled'] if i%2 else []) for i in range(17)]}
        for person in req['participants']:
            person['prefer_tags']=['grilled']
        result=plan(req,menu)
        self.assertTrue(result['search_complete'])
        self.assertEqual(result['total_cents'],8400)

    def test_resource_memo_does_not_reuse_exhausted_coupon(self):
        req={**request(3,10000),'payer_id':'payer'}
        menu={'offers':[offer('limited',1,coupon={'id':'x','owner':'payer','max_uses':1}),offer('normal',100)]}
        self.assertEqual(plan(req,menu,'economy')['total_cents'],201)


class IntegrationContractTests(unittest.TestCase):
    def test_http_success_without_business_success_is_rejected(self):
        with self.assertRaises(MCPError):
            business_data({"structuredContent":{"code":200,"success":False}})

    def test_nutrition_parse_and_schema_drift(self):
        self.assertEqual(nutrition_map('[1]{fields}:\n  测试餐,null,1000,239,1,2,3,4,5'),{"测试餐":239})
        with self.assertRaises(MCPError):
            nutrition_map('[1]{fields}:\n  测试餐,1,2')

    def test_order_creation_is_blocked_before_network(self):
        client=Client(token="test-only-placeholder")
        with self.assertRaises(MCPError):
            client.call("create-order",{})


if __name__=="__main__":
    unittest.main()
