"""Build public, bounded candidate sets from verified official tool results."""
from __future__ import annotations

import csv
import io
import json
from collections import Counter
from copy import deepcopy
from pathlib import Path

from .mcp import Client, MCPError, unwrap
from .planner import InputError, apply_official_total, plan


def business_data(response):
    payload = unwrap(response)
    if not isinstance(payload, dict) or payload.get("success") is not True:
        raise MCPError("官方工具未返回业务成功；不会继续生成已验证报价。")
    return payload


def nutrition_map(data):
    if not isinstance(data, str) or not data.startswith("[") or "\n" not in data:
        raise MCPError("营养数据格式已变化；请更新解析器。")
    result = {}
    for row in csv.reader(io.StringIO(data.split("\n", 1)[1]), skipinitialspace=True):
        if len(row) != 9:
            raise MCPError("营养数据列数已变化。")
        result[row[0].strip()] = int(row[3])
    return result


class Session:
    def __init__(self, client=None):
        self.client = client or Client()
        info = self.client.initialize()
        self.records = []
        self.server_info = info.get("serverInfo", {})

    def call(self, name, arguments):
        payload = business_data(self.client.call(name, arguments))
        # No account IDs, coupons, addresses, raw headers or traces in public evidence.
        self.records.append({"tool": name, "datetime": payload.get("datetime"),
                             "business_success": True, "code": payload.get("code")})
        return payload["data"]

    def refresh(self, city="上海市", keyword="人民广场", store_code=None):
        stores = self.call("query-nearby-stores", {"beType":1,"searchType":2,"city":city,"keyword":keyword})
        store = next((s for s in stores if s.get("businessStatus") is True and
                      (not store_code or s.get("storeCode") == store_code)), None)
        if not store:
            raise MCPError("没有找到指定且营业中的门店。")
        context = {"storeCode":store["storeCode"],"orderType":1,"beType":1}
        menu = self.call("query-meals", context)
        nutrition = nutrition_map(self.call("list-nutrition-foods", {}))
        details = {}
        offers = []

        def detail(code):
            if code not in details:
                details[code] = self.call("query-meal-detail", {**context,"code":code})
            return details[code]

        def single(code, category):
            product = detail(code)
            if product.get("rounds"):
                raise MCPError("单品结构变化；不猜测套餐默认选择。")
            return {"name":product["name"], "category":category,
                    "kcal":nutrition.get(product["name"]), "request":{"productCode":code,"quantity":1}}

        def append_offer(oid, components, meat, tags):
            items = [c["request"] for c in components]
            quote = self.call("calculate-price", {**context,"items":items})
            if type(quote.get("price")) is not int or quote["price"] < 0:
                raise MCPError("官方报价不是预期的整数分。")
            kcals = [c["kcal"] for c in components]
            # Beef tag concerns the main's meat classification, not trace ingredients.
            offers.append({"id":oid, "name":" + ".join(c["name"] for c in components),
                "price_cents":quote["price"], "kcal":sum(kcals) if all(k is not None for k in kcals) else None,
                "available":True, "categories":sorted({c["category"] for c in components}),
                "tags":sorted(set(tags) | ({"beef"} if meat=="beef" else set())),
                "known_tags":["beef","sugary-drink"], "items":items,
                "components":[{k:v for k,v in c.items() if k!='request'} for c in components],
                "classification_note":"主食肉类及饮料偏好分类；不代表配料或过敏原核查"})

        water = single("3757", "drink") if "3757" in menu["meals"] else None
        fries = single("4810", "fries") if "4810" in menu["meals"] else None
        for code, meat, extra in [("1450","chicken",[]),("1406","chicken",["grilled"]),
                                  ("1440","chicken",["spicy"]),("1100","beef",["classic"])]:
            if code not in menu["meals"] or not water or menu["meals"][code].get("canWithOrder"):
                continue
            main = single(code, "main")
            append_offer(code+"-water", [main,water], meat, ["burger",*extra])
        for code, meat, extra in [("9900005453","chicken",[]),("9900005462","chicken",["grilled"]),
                                  ("9900005456","chicken",["spicy"]),("9900005449","beef",["classic"])]:
            if code not in menu["meals"] or menu["meals"][code].get("canWithOrder"):
                continue
            product = detail(code)
            chosen, rounds = [], []
            for index, round_info in enumerate(product.get("rounds", [])):
                # This adapter deliberately handles only a main + snack + drink set.
                if len(product["rounds"]) != 3 or round_info.get("quantity") != 1:
                    raise MCPError("套餐结构变化；请由 Skill 检查选择后再构建候选。")
                default = next((v for v in round_info["choices"] if v.get("isDefault")==1), None)
                if index == 2:
                    default = next((v for v in round_info["choices"] if "无糖可口可乐" in v.get("name", "")), None)
                if default is None:
                    raise MCPError("找不到套餐默认项或无糖饮料；不推测可用替换。")
                category = ["main","fries","drink"][index]
                if index == 1 and "薯条" not in default["name"]:
                    raise MCPError("套餐小食默认项变化；请重新分类。")
                chosen.append({"name":default["name"],"category":category,"kcal":nutrition.get(default["name"])})
                rounds.append({"round":str(round_info["id"]),"comboItemList":[{"code":default["code"],"quantity":1}]})
            item = {"productCode":code,"quantity":1,"roundList":rounds}
            # One orderable set; retain selected components for nutrition, not separate charges.
            quote = self.call("calculate-price", {**context,"items":[item]})
            kcals = [c["kcal"] for c in chosen]
            if type(quote.get("price")) is not int or quote["price"] < 0:
                raise MCPError("套餐报价格式变化。")
            offers.append({"id":code+"-zero", "name":product["name"]+"（无糖可乐）",
                "price_cents":quote["price"],"kcal":sum(kcals) if all(k is not None for k in kcals) else None,
                "available":True,"categories":["main","fries","drink"],
                "tags":["burger","fries",*extra,*(["beef"] if meat=="beef" else [])],
                "known_tags":["beef","sugary-drink"],"items":[item],"components":chosen,
                "classification_note":"主食肉类及饮料偏好分类；不代表配料或过敏原核查"})
        if fries:
            append_offer("shared-fries",[fries],"none",["fries"])
        return {"source":{"kind":"official-mcp-snapshot","endpoint":"https://mcp.mcd.cn",
                          "fetched_at":self.records[-1]["datetime"],"store_code":store["storeCode"],
                          "store_name":store["storeName"],"order_type":1,"be_type":1,
                          "notice":"历史现场快照；价格、供应和营养需在使用时重新查询。"},
                "fee_cents":0,"offers":offers}

    def quote(self, request, menu, strategy="preference"):
        source = menu.get("source", {})
        if source.get("endpoint") != "https://mcp.mcd.cn" or not source.get("store_code"):
            raise InputError("只能对包含官方门店上下文的候选核价。")
        result = plan(request, menu, strategy)
        if result["status"] != "planned":
            return result
        by_id = {o["id"]:o for o in menu["offers"]}
        items = []
        for allocation in result["allocations"]:
            items.extend(deepcopy(by_id[allocation["offer_id"]]["items"]))
        for shared in request.get("shared", []):
            for item in deepcopy(by_id[shared["offer_id"]]["items"]):
                item["quantity"] *= shared.get("quantity",1)
                items.append(item)
        # Merge identical configurable sets; do not merge different round selections.
        aggregate = {}
        for item in items:
            signature = json.dumps({k:v for k,v in item.items() if k!='quantity'},sort_keys=True)
            if signature not in aggregate:
                aggregate[signature] = deepcopy(item)
                aggregate[signature]["quantity"] = 0
            aggregate[signature]["quantity"] += item["quantity"]
        quote = self.call("calculate-price", {"storeCode":source["store_code"],"orderType":source["order_type"],
                          "beType":source["be_type"],"items":list(aggregate.values())})
        result = apply_official_total(result, quote.get("price"), request["budget_cents"])
        result["quoted_at"] = self.records[-1]["datetime"]
        result["order_items"] = list(aggregate.values())
        return result

    def evidence(self):
        return {"server":self.server_info,"records":self.records,
                "mutating_tools_called":[],"note":"仅只读业务查询与试算；没有创建订单、领券或购买会员卡。"}


def save(path, data):
    target = Path(path)
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(data,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
