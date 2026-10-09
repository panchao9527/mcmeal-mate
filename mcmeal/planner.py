"""Small-group constraint search. All accounting uses integer cents."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from fractions import Fraction


class InputError(ValueError):
    pass


def integer(value, name, minimum=0):
    if type(value) is not int or value < minimum:
        raise InputError(f"{name} 必须是 >= {minimum} 的整数。")
    return value


def split_cents(amount: int, ids: list[str]) -> dict[str, int]:
    if not ids or len(set(ids)) != len(ids):
        raise InputError("分账成员不能为空或重复。")
    integer(amount, "金额")
    base, extra = divmod(amount, len(ids))
    return {pid: base + (index < extra) for index, pid in enumerate(ids)}


def validate(request, menu):
    integer(request.get("budget_cents"), "budget_cents")
    integer(menu.get("fee_cents", 0), "fee_cents")
    people = request.get("participants", [])
    if not 1 <= len(people) <= 8:
        raise InputError("支持 1 至 8 人；更大饭局请拆分规划。")
    ids = [p.get("id") for p in people]
    if any(not isinstance(pid, str) or not pid for pid in ids) or len(set(ids)) != len(ids):
        raise InputError("participant id 必须是非空且不重复的字符串。")
    for person in people:
        if person.get("max_kcal") is not None:
            integer(person["max_kcal"], "max_kcal")
        for field in ("exclude_tags", "prefer_tags", "required_categories"):
            values = person.get(field, [])
            if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
                raise InputError(f"{field} 必须为字符串列表。")
    offers = menu.get("offers", [])
    offer_ids = [o.get("id") for o in offers]
    if not offers or any(not isinstance(oid, str) or not oid for oid in offer_ids) or len(set(offer_ids)) != len(offer_ids):
        raise InputError("候选餐品不能为空，且 id 不得重复。")
    limits = {}
    for offer in offers:
        integer(offer.get("price_cents"), "price_cents")
        if offer.get("kcal") is not None:
            integer(offer["kcal"], "kcal")
        for field in ("tags", "known_tags", "categories"):
            if not isinstance(offer.get(field, []), list):
                raise InputError(f"候选 {field} 必须为列表。")
        if offer.get("max_quantity") is not None:
            integer(offer["max_quantity"], "max_quantity")
        coupon = offer.get("coupon")
        if coupon:
            if not coupon.get("id") or not coupon.get("owner"):
                raise InputError("优惠券必须标明 id 和所属付款账号。")
            integer(coupon.get("max_uses"), "coupon max_uses", 1)
            identity = (coupon["owner"], coupon["max_uses"])
            if coupon["id"] in limits and limits[coupon["id"]] != identity:
                raise InputError("同一优惠券的所有权或次数限制冲突。")
            limits[coupon["id"]] = identity


def violations(person, offer, payer_id):
    reasons = []
    if offer.get("available") is not True:
        reasons.append("供应状态未确认")
    if offer.get("requires_purchase"):
        reasons.append("需要另购会员卡，未计入预算")
    excluded = set(person.get("exclude_tags", []))
    if excluded & set(offer.get("tags", [])):
        reasons.append("包含明确排除项")
    if excluded - set(offer.get("known_tags", [])):
        reasons.append("排除项的信息不足")
    required = set(person.get("required_categories", ["main"]))
    if required - set(offer.get("categories", [])):
        reasons.append("缺少必选餐品类别")
    if person.get("max_kcal") is not None:
        if offer.get("kcal") is None:
            reasons.append("能量数据不完整")
        elif offer["kcal"] > person["max_kcal"]:
            reasons.append("超过用户指定能量上限")
    coupon = offer.get("coupon")
    if coupon and coupon.get("owner") != payer_id:
        reasons.append("优惠券不属于本次付款账号")
    return reasons


def preference_score(person, offer):
    return len(set(person.get("prefer_tags", [])) & set(offer.get("tags", [])))


def plan(request: dict, menu: dict, strategy="preference", node_limit=200000):
    validate(request, menu)
    if strategy not in {"preference", "economy"}:
        raise InputError("strategy 仅支持 preference 或 economy。")
    integer(node_limit, "node_limit", 1)
    people = request["participants"]
    payer = request.get("payer_id")
    offers = {o["id"]: o for o in menu["offers"]}
    ids = [p["id"] for p in people]
    shared_cost = 0
    shared_by_person = dict.fromkeys(ids, 0)
    shared_energy = dict.fromkeys(ids, Fraction(0))
    counts = Counter()
    coupon_counts = Counter()
    for shared in request.get("shared", []):
        offer = offers.get(shared.get("offer_id"))
        consumers = shared.get("participant_ids", [])
        quantity = integer(shared.get("quantity", 1), "shared quantity", 1)
        if not offer or not consumers or len(set(consumers)) != len(consumers) or set(consumers) - set(ids):
            raise InputError("共享小食必须指定有效且不重复的食用成员。")
        for person in people:
            if person["id"] in consumers:
                shared_person = {**person, "required_categories": [], "max_kcal": None}
                reasons = violations(shared_person, offer, payer)
                if reasons:
                    return {"status": "infeasible", "reason": "共享小食不符合食用成员要求", "details": reasons}
                if offer.get("kcal") is None and person.get("max_kcal") is not None:
                    return {"status":"infeasible", "reason":"共享小食能量未知，无法核对能量上限"}
                if offer.get("kcal") is not None:
                    shared_energy[person["id"]] += Fraction(offer["kcal"]*quantity,len(consumers))
        counts[offer["id"]] += quantity
        if offer.get("coupon"):
            coupon_counts[offer["coupon"]["id"]] += quantity
        amount = offer["price_cents"] * quantity
        shared_cost += amount
        for pid, share in split_cents(amount, consumers).items():
            shared_by_person[pid] += share
    base_cost = shared_cost + menu.get("fee_cents", 0)
    candidates = []
    for person in people:
        choices = [o for o in offers.values() if not violations(person, o, payer)
                   and (person.get("max_kcal") is None or
                        o["kcal"]+shared_energy[person["id"]] <= person["max_kcal"])]
        choices.sort(key=lambda o: (o["price_cents"], o["id"]))
        if not choices:
            return {"status": "infeasible", "reason": f"{person.get('name',person['id'])}没有符合硬性要求的候选餐", "details": "可调整预算、必选类别，或补齐餐品信息；不会省略此成员。"}
        candidates.append(choices)
    min_total = base_cost + sum(min(o["price_cents"] for o in choices) for choices in candidates)
    if min_total > request["budget_cents"]:
        return {"status": "infeasible", "reason": "预算低于候选餐的最低金额下界", "minimum_lower_bound_cents": min_total, "shortfall_at_least_cents": min_total-request["budget_cents"]}
    best, best_key = None, None
    visited, truncated = 0, False

    def resources_ok():
        for oid, count in counts.items():
            limit = offers[oid].get("max_quantity")
            if limit is not None and count > limit:
                return False
        for offer in offers.values():
            coupon = offer.get("coupon")
            if coupon and coupon_counts[coupon["id"]] > coupon["max_uses"]:
                return False
        return True

    def walk(index, cost, score, chosen):
        nonlocal best, best_key, visited, truncated
        if visited >= node_limit:
            truncated = True
            return
        visited += 1
        if cost > request["budget_cents"] or not resources_ok():
            return
        if index == len(people):
            signature = tuple(o["id"] for o in chosen)
            key = (cost, -score, signature) if strategy == "economy" else (-score, cost, signature)
            if best_key is None or key < best_key:
                best_key, best = key, (chosen[:], cost, score)
            return
        remaining_minimum = sum(min(o["price_cents"] for o in choices) for choices in candidates[index:])
        if cost + remaining_minimum > request["budget_cents"]:
            return
        for offer in candidates[index]:
            counts[offer["id"]] += 1
            coupon = offer.get("coupon")
            if coupon:
                coupon_counts[coupon["id"]] += 1
            walk(index+1, cost+offer["price_cents"], score+preference_score(people[index], offer), chosen+[offer])
            counts[offer["id"]] -= 1
            if coupon:
                coupon_counts[coupon["id"]] -= 1
            if truncated:
                break

    walk(0, base_cost, 0, [])
    if best is None:
        return {"status": "search_limit" if truncated else "infeasible", "reason": "搜索上限内未找到方案" if truncated else "数量或优惠券次数限制下无可行方案", "search_complete": not truncated}
    chosen, cost, score = best
    fees = split_cents(menu.get("fee_cents", 0), ids)
    allocations = []
    for person, offer in zip(people, chosen):
        allocations.append({
            "participant_id": person["id"], "name": person.get("name", person["id"]),
            "offer_id": offer["id"], "meal": offer["name"], "items": offer.get("items", []),
            "kcal": offer.get("kcal"), "meal_cents": offer["price_cents"],
            "shared_kcal":float(shared_energy[person["id"]]),
            "shared_cents": shared_by_person[person["id"]], "fee_cents": fees[person["id"]],
            "total_cents": offer["price_cents"]+shared_by_person[person["id"]]+fees[person["id"]],
            "matched_preferences": sorted(set(person.get("prefer_tags", [])) & set(offer.get("tags", []))),
            "unmet_preferences": sorted(set(person.get("prefer_tags", [])) - set(offer.get("tags", []))),
        })
    return {
        "status": "planned", "strategy": strategy, "total_cents": cost,
        "remaining_cents": request["budget_cents"]-cost, "preference_score": score,
        "allocations": allocations, "shared": request.get("shared", []),
        "search_complete": not truncated, "search_nodes": visited,
        "scope": "只在本次提供的候选餐中比较；共享小食能量按食用成员均分计入上限。",
        "source": menu.get("source", {}),
        "acceptance": [
            {"check": "成员无遗漏", "status": "PASS", "detail": f"{len(allocations)}/{len(people)}"},
            {"check": "候选金额不超预算", "status": "PASS", "detail": f"{cost}/{request['budget_cents']} 分"},
            {"check": "已知偏好排除项", "status": "PASS", "detail": "含未知信息的候选已排除；不用于过敏安全判断"},
            {"check": "必选餐品与指定能量上限", "status": "PASS", "detail": "共享小食参考能量已按食用成员均分计入"},
            {"check": "分账金额守恒", "status": "PASS" if sum(a['total_cents'] for a in allocations)==cost else "FAIL"},
            {"check": "优惠券所属账号及次数", "status": "PASS", "detail": "无券或在已提供限制内"},
            {"check": "当前整单官方价格", "status": "PENDING", "detail": "等待 calculate-price；历史快照不代表当前报价"},
        ],
    }


def apply_official_total(result, total_cents, budget_cents):
    """Reconcile authoritative total, allocating price changes proportionally."""
    integer(total_cents, "official total")
    integer(budget_cents, "budget")
    if result.get("status") != "planned":
        raise InputError("只能对已规划方案核价。")
    result = deepcopy(result)
    allocations = result["allocations"]
    weights = [a["total_cents"] for a in allocations]
    denominator = sum(weights)
    if denominator:
        shares = [total_cents*w//denominator for w in weights]
        ranks = sorted(range(len(weights)), key=lambda i: (-(total_cents*weights[i] % denominator), i))
        for i in ranks[:total_cents-sum(shares)]:
            shares[i] += 1
    else:
        shares = list(split_cents(total_cents,[a['participant_id'] for a in allocations]).values())
    for allocation, share in zip(allocations, shares):
        allocation["official_adjustment_cents"] = share-allocation["total_cents"]
        allocation["total_cents"] = share
    result["estimated_total_cents"] = result["total_cents"]
    result["total_cents"] = total_cents
    result["remaining_cents"] = budget_cents-total_cents
    result["status"] = "quoted" if total_cents <= budget_cents else "quote_over_budget"
    result["acceptance"][1] = {"check":"官方金额不超预算", "status":"PASS" if total_cents<=budget_cents else "FAIL", "detail":f"{total_cents}/{budget_cents} 分"}
    result["acceptance"][-1] = {"check":"当前整单官方价格", "status":"PASS", "detail":"calculate-price 已返回业务成功；整单价差按原分账比例分摊"}
    return result
