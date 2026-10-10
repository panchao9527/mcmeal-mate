"""Offline bridge: host MCP results -> plans -> bound quote receipts -> share text.

This module deliberately has no MCP client, HTTP calls or credential handling.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sys

from .planner import InputError, apply_official_total, integer, plan, validate
from . import __version__

ROOT = Path(__file__).resolve().parent.parent
CHINA = timezone(timedelta(hours=8))
MAX_AGE = timedelta(minutes=15)


def canonical(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    except (TypeError, ValueError):
        raise InputError('输入含有无法表示为标准JSON的值。') from None


def fingerprint(value):
    return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()


def instant(value):
    if not isinstance(value, str):
        raise InputError('官方报价缺少时间，请重新查询。')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        raise InputError('无法识别官方报价时间，请核对原始返回。') from None
    return parsed.replace(tzinfo=CHINA) if parsed.tzinfo is None else parsed


def business(response, mode, now=None, not_before=None):
    value = deepcopy(response)
    for _ in range(6):
        if not isinstance(value, dict):
            raise InputError('需要完整的MCP JSON返回，不能使用手填总价。')
        if value.get('isError') or 'error' in value:
            raise InputError('MCP工具返回失败，报价不可采用。')
        if 'success' in value:
            break
        if 'structuredContent' in value:
            value = value['structuredContent']
        elif 'result' in value:
            value = value['result']
        elif 'content' in value:
            texts = [c.get('text') for c in value['content'] if isinstance(c, dict) and c.get('type') == 'text']
            if len(texts) != 1:
                raise InputError('工具内容格式不明确，请保留单个原始业务JSON。')
            try:
                value = json.loads(texts[0])
            except (ValueError, TypeError):
                raise InputError('工具返回不是可解析的业务JSON。') from None
        else:
            raise InputError('无法找到官方业务结果。')
    if not isinstance(value, dict) or value.get('success') is not True or not isinstance(value.get('data'), dict):
        raise InputError('业务结果未成功，不能标记为官方核价通过。')
    integer(value['data'].get('price'), '官方价格（分）')
    if mode == 'live':
        quoted = instant(value.get('datetime'))
        now = now or datetime.now(timezone.utc)
        if now - quoted > MAX_AGE or quoted - now > timedelta(minutes=2):
            raise InputError('官方报价已过期或时间异常，请重新调用calculate-price。')
        if not_before and quoted < instant(not_before) - timedelta(seconds=5):
            raise InputError('这份报价早于当前方案，请按新方案重新核价。')
    return value


def validate_context(context):
    if not isinstance(context, dict) or not isinstance(context.get('storeCode'), str) or not context['storeCode']:
        raise InputError('必须提供从官方查询取得的storeCode。')
    if type(context.get('orderType')) is not int or type(context.get('beType')) is not int or context['orderType'] != 1 or context['beType'] != 1:
        raise InputError('当前可执行Skill流程支持到店自取（orderType=1、beType=1）。')
    if set(context) - {'storeCode', 'orderType', 'beType', 'reservationDate', 'needTableware'}:
        raise InputError('门店上下文包含不支持的参数，请按当前工具schema核对。')
    if 'reservationDate' in context and (not isinstance(context['reservationDate'], str) or not context['reservationDate']):
        raise InputError('预约时间参数无效。')
    if 'needTableware' in context and type(context['needTableware']) is not bool:
        raise InputError('餐具参数必须是布尔值。')


def validate_items(items):
    if not isinstance(items, list) or not items:
        raise InputError('候选必须保留官方商品参数items。')
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get('productCode'), str) or not item['productCode']:
            raise InputError('每个商品都需要真实productCode。')
        integer(item.get('quantity'), '商品数量', 1)
        if set(item) - {'productCode', 'quantity', 'roundList', 'modification', 'couponId', 'couponCode'}:
            raise InputError('商品含未知参数，请核对宿主工具schema。')
        for round_item in item.get('roundList', []):
            if not isinstance(round_item, dict) or not isinstance(round_item.get('round'), str) or not round_item['round']:
                raise InputError('套餐轮次必须保留官方round值。')
            choices = round_item.get('comboItemList')
            if not isinstance(choices, list) or not choices:
                raise InputError('套餐轮次缺少已选择的子项。')
            for choice in choices:
                if not isinstance(choice, dict) or not isinstance(choice.get('code'), str) or not choice['code']:
                    raise InputError('套餐子项缺少官方code。')
                integer(choice.get('quantity'), '套餐子项数量', 1)
    canonical(items)


def normalize(session, now=None):
    if not isinstance(session, dict) or session.get('mode') not in {'live', 'demo'}:
        raise InputError('必须明确mode为live或demo。')
    mode, context = session['mode'], deepcopy(session.get('context'))
    validate_context(context)
    candidates = session.get('candidates')
    if not isinstance(candidates, list) or not 1 <= len(candidates) <= 40:
        raise InputError('每轮请提供1至40个已核实候选。')
    offers, quotes = [], []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise InputError('候选必须为对象。')
        items = deepcopy(candidate.get('items'))
        validate_items(items)
        quote = candidate.get('quote', {})
        arguments = {**context, 'items': items}
        if canonical(quote.get('arguments')) != canonical(arguments):
            raise InputError('候选报价的门店、商品或套餐选择与候选不一致。')
        response = business(quote.get('response'), mode, now=now)
        known = candidate.get('known_tags', [])
        evidence = candidate.get('attribute_evidence', {})
        if not isinstance(known, list) or not isinstance(evidence, dict) or any(not isinstance(tag, str) or not isinstance(evidence.get(tag), str) or not evidence[tag].strip() for tag in known):
            raise InputError('已确认的属性必须记录其依据；未知属性不要放进known_tags。')
        if candidate.get('kcal') is not None and (not isinstance(candidate.get('nutrition_evidence'),str) or not candidate['nutrition_evidence'].strip()):
            raise InputError('能量数值必须记录官方营养匹配依据。')
        offer = {k: deepcopy(candidate[k]) for k in ('id', 'name', 'categories', 'tags', 'known_tags', 'kcal', 'available', 'items', 'coupon', 'max_quantity', 'requires_purchase') if k in candidate}
        if not isinstance(offer.get('name'), str) or not offer['name']:
            raise InputError('候选缺少名称。')
        offer['price_cents'] = response['data']['price']
        coupon_ids = {item['couponId'] for item in items if item.get('couponId')}
        if coupon_ids and (not offer.get('coupon') or coupon_ids != {offer['coupon'].get('id')}):
            raise InputError('用券商品须对应一份已核实的券归属及次数限制。')
        if offer.get('coupon') and not coupon_ids:
            raise InputError('券限制与实际商品参数不一致。')
        offers.append(offer)
        quotes.append({'candidate_id':offer.get('id'), 'quoted_at':response.get('datetime'), 'price_cents':offer['price_cents'], 'arguments_fingerprint':fingerprint(arguments)})
    menu = {'fee_cents':session.get('fee_cents', 0), 'offers':offers, 'source':{
        'kind':'host-mcp' if mode == 'live' else 'fictional-demo', 'store_name':session.get('store_name', context['storeCode']),
        'store_code':context['storeCode'], 'notice':'本次候选独立报价仅用于规划，最终整单需重新核价。'}}
    request = deepcopy(session.get('request'))
    validate(request, menu)
    return {'schema_version':1, 'mode':mode, 'context':context, 'request':request, 'menu':menu, 'candidate_quotes':quotes}


def basket(result, request, menu):
    by_id = {o['id']:o for o in menu['offers']}
    items = []
    for row in result['allocations']:
        items.extend(deepcopy(by_id[row['offer_id']]['items']))
    for shared in request.get('shared', []):
        for item in deepcopy(by_id[shared['offer_id']]['items']):
            item['quantity'] *= shared.get('quantity', 1)
            items.append(item)
    grouped = {}
    for item in items:
        key = canonical({k:v for k,v in item.items() if k != 'quantity'})
        if key not in grouped:
            grouped[key] = {**item, 'quantity':0}
        grouped[key]['quantity'] += item['quantity']
    return [grouped[key] for key in sorted(grouped)]


def plan_digest(normalized, strategy, result, arguments, created_at):
    return fingerprint({'normalized':normalized, 'strategy':strategy, 'result':result, 'arguments':arguments, 'created_at':created_at})


def prepare(normalized):
    if not isinstance(normalized, dict) or normalized.get('mode') not in {'live', 'demo'}:
        raise InputError('请先normalize候选数据。')
    request, menu, context = normalized['request'], normalized['menu'], normalized['context']
    validate_context(context)
    plans = {}
    created_at = datetime.now(timezone.utc).isoformat()
    for strategy in ('preference', 'economy'):
        result = plan(request, menu, strategy)
        entry = {'result':result}
        if result['status'] == 'planned':
            arguments = {**context, 'items':basket(result, request, menu)}
            entry['quote_request'] = {'tool':'calculate-price', 'arguments':arguments,
                'plan_fingerprint':plan_digest(normalized, strategy, result, arguments, created_at)}
        plans[strategy] = entry
    return {'schema_version':1, 'mode':normalized['mode'], 'created_at':created_at, 'normalized':deepcopy(normalized), 'plans':plans}


def finalize(draft, receipt, strategy='preference', now=None):
    if strategy not in {'preference', 'economy'}:
        raise InputError('未知配餐策略。')
    entry = draft['plans'][strategy]
    call = entry.get('quote_request')
    if not call:
        raise InputError('当前策略没有可核价方案。')
    normalized = draft['normalized']
    expected = plan_digest(normalized, strategy, entry['result'], call['arguments'], draft['created_at'])
    if draft.get('mode') != normalized.get('mode') or call.get('plan_fingerprint') != expected:
        raise InputError('方案内容已修改，请重新prepare。')
    if receipt.get('plan_fingerprint') != expected or canonical(receipt.get('arguments')) != canonical(call['arguments']):
        raise InputError('报价不属于当前方案，不能复用旧预算、旧门店或旧餐品报价。')
    response = business(receipt.get('response'), draft['mode'], now=now, not_before=draft['created_at'])
    result = apply_official_total(entry['result'], response['data']['price'], normalized['request']['budget_cents'])
    result['mode'] = draft['mode']
    result['quoted_at'] = response.get('datetime')
    result['plan_fingerprint'] = expected
    result['context'] = deepcopy(normalized['context'])
    offers = {o['id']:o for o in normalized['menu']['offers']}
    people = {p['id']:p.get('name',p['id']) for p in normalized['request']['participants']}
    result['shared_items'] = [{'name':offers[row['offer_id']]['name'], 'quantity':row.get('quantity',1),
                              'participants':[people[pid] for pid in row['participant_ids']]}
                             for row in normalized['request'].get('shared',[])]
    if draft['mode'] == 'demo':
        result['status'] = 'demo_' + result['status']
        result['acceptance'][1]['check'] = '演示金额不超预算'
        result['acceptance'][-1] = {'check':'虚构报价演练', 'status':'DEMO', 'detail':'未访问麦当劳服务，金额不代表真实售价。'}
    return result


def share(result):
    status = result.get('status')
    if status not in {'quoted', 'quote_over_budget', 'demo_quoted', 'demo_quote_over_budget'}:
        raise InputError('先导入对应的官方整单报价，再生成分享清单。')
    def money(cents):
        return f'{cents//100}.{cents%100:02d}'
    demo = result.get('mode') == 'demo'
    over = status.endswith('over_budget')
    strategy_name = {'preference':'照顾口味','economy':'省钱优先'}.get(result.get('strategy'),'配餐方案')
    labels = {'classic':'经典口味','grilled':'板烧口味','spicy':'辣味','fries':'薯条','burger':'汉堡'}
    lines = [f'# 麦麦搭子 · {strategy_name}', '',
             '**离线演示：虚构餐品和金额。**' if demo else f"门店：{result.get('source', {}).get('store_name', result['context']['storeCode'])}",
             '演示数据未进行真实核价。' if demo else f"官方核价时间：{result.get('quoted_at', '未知')}", '']
    if over:
        lines += ['**本方案超出预算，请调整后重新生成。**', '']
    for row in result['allocations']:
        lines.append(f"- {row['name']}：{row['meal']}；分摊 ¥{money(row['total_cents'])}")
        if row.get('shared_cents'):
            lines.append(f"  含共享小食 ¥{money(row['shared_cents'])}")
        if row.get('official_adjustment_cents'):
            delta=row['official_adjustment_cents']
            lines.append(f"  {'演示核价差' if demo else '官方整单价差'}分摊：{'+' if delta>0 else '-'}¥{money(abs(delta))}")
        if row.get('unmet_preferences'):
            lines.append('  未满足的偏好：' + '、'.join(labels.get(tag,tag) for tag in row['unmet_preferences']))
    if result.get('shared_items'):
        lines += ['', '共享小食：']
        for row in result['shared_items']:
            lines.append(f"- {row['name']} × {row['quantity']}；食用成员：{'、'.join(row['participants'])}")
    lines += ['', f"合计：¥{money(result['total_cents'])}；{'超预算' if over else '预算剩余'}：¥{money(abs(result['remaining_cents']))}",
              f"共享小食按食用成员均分，{'演示核价差' if demo else '官方整单价差'}按原金额比例分摊。", '']
    if result.get('search_complete') is False:
        lines.append('搜索尚未完成，目前方案不保证候选内最优。')
    return '\n'.join(lines)


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


def run_demo(destination):
    session = read(ROOT/'examples/skill-demo/session.json')
    normalized = normalize(session)
    draft = prepare(normalized)
    destination = Path(destination)
    save(destination/'normalized.json', normalized)
    save(destination/'draft.json', draft)
    totals = {}
    for strategy, entry in draft['plans'].items():
        call = entry['quote_request']
        receipt = {'plan_fingerprint':call['plan_fingerprint'], 'arguments':call['arguments'],
                   'response':{'success':True, 'datetime':'DEMO', 'data':{'price':entry['result']['total_cents']}}}
        result = finalize(draft, receipt, strategy)
        save(destination/f'{strategy}-receipt.json', receipt)
        save(destination/f'{strategy}-final.json', result)
        save(destination/f'{strategy}-share.md', share(result))
        totals[strategy] = result['total_cents']
    return {'status':'demo_complete', 'network_calls':0, 'output':str(destination.resolve()), 'fictional_totals_cents':totals}


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description='麦麦搭子 Skill 离线助手：不读Token、不联网')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('doctor')
    demo = sub.add_parser('demo'); demo.add_argument('--out', required=True)
    for command in ('normalize', 'prepare', 'share'):
        child = sub.add_parser(command); child.add_argument('--input', required=True); child.add_argument('--out', required=True)
    final = sub.add_parser('finalize')
    final.add_argument('--draft', required=True); final.add_argument('--receipt', required=True)
    final.add_argument('--strategy', choices=['preference', 'economy'], default='preference'); final.add_argument('--out', required=True)
    args = parser.parse_args()
    try:
        if args.command == 'doctor':
            result = {'status':'ready_offline', 'skill_version':__version__, 'python':sys.version.split()[0], 'credential_required':False, 'network_calls':0, 'host_mcp':'请由AI客户端检查麦当劳MCP工具是否已连接。'}
        elif args.command == 'demo':
            result = run_demo(args.out)
        else:
            if args.command == 'normalize':
                value = normalize(read(args.input))
            elif args.command == 'prepare':
                value = prepare(read(args.input))
            elif args.command == 'share':
                value = share(read(args.input))
            else:
                value = finalize(read(args.draft), read(args.receipt), args.strategy)
            save(args.out, value)
            result = {'status':'saved', 'output':str(Path(args.out).resolve())}
        print(json.dumps(result, ensure_ascii=False))
    except (InputError, KeyError, TypeError, ValueError, OSError) as error:
        print(json.dumps({'status':'error', 'message':str(error)}, ensure_ascii=False))
        raise SystemExit(2)


if __name__ == '__main__':
    main()
