"""A persistent conversation workflow; host MCP performs the exported calls.

No HTTP or credential handling. A human reply is supplied by the host agent;
the code binds the reply to a preview, but cannot authenticate its human origin.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime,timezone,timedelta
from decimal import Decimal,InvalidOperation
import json
import os
from pathlib import Path
import tempfile
from uuid import uuid4

from .native import business,canonical,finalize,fingerprint,instant,normalize,prepare,read,share,unwrap_business
from .planner import InputError,apply_official_total

PAID_STATUSES={'2','4','6','8','10'}
STATUS_NAMES={'1':'待支付','2':'已支付，配餐中','4':'配送中','6':'已完成','7':'已取消','8':'已评价','10':'餐厅配餐中'}
APPROVALS={'确认下单','确认购买','确认','下单','confirm purchase','place order'}
ACTIVE_ORDER_STATES={'submission_pending','outcome_unknown','awaiting_payment','paid','completed','order_created_review_needed','payment_expired'}


def timestamp(now=None):
    return (now or datetime.now(timezone.utc)).isoformat()


def state_seal(state):
    return fingerprint({k:v for k,v in state.items() if k!='integrity'})


def load_state(path):
    state=read(path)
    if state.get('schema_version')!=1 or state.get('integrity')!=state_seal(state):
        raise InputError('饭局记录被修改或格式不兼容，请恢复原记录，不要手工改确认信息。')
    return state


def write_state(path,state):
    path=Path(path)
    state=deepcopy(state);state['integrity']=state_seal(state)
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,temp_name=tempfile.mkstemp(prefix='.mcmeal-',suffix='.json',dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8',newline='\n') as stream:
            stream.write(json.dumps(state,ensure_ascii=False,indent=2)+'\n')
            stream.flush();os.fsync(stream.fileno())
        os.replace(temp_name,path)
    finally:
        if os.path.exists(temp_name):os.unlink(temp_name)


@contextmanager
def locked(path):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    lock=path.with_name(path.name+'.lock')
    try:
        descriptor=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    except FileExistsError:
        raise InputError('另一条请求正在处理这份饭局记录，请勿并行确认或下单。') from None
    try:
        with os.fdopen(descriptor,'w',encoding='utf-8') as stream:stream.write(str(os.getpid()))
        yield
    finally:
        lock.unlink(missing_ok=True)


def start(session,now=None):
    normalized=normalize(session,now=now)
    return {'schema_version':1,'mode':session['mode'],'status':'awaiting_quotes','session':deepcopy(session),
            'draft':prepare(normalized),'quotes':{},'receipts':{},'purchase':None,'order':None,'last_query':None}


def next_quotes(state):
    return {name:entry['quote_request'] for name,entry in state['draft']['plans'].items() if 'quote_request' in entry}


def revise(state,session,now=None):
    if state['status'] in ACTIVE_ORDER_STATES or state.get('order'):
        raise InputError('本饭局已提交或已有订单，不能把修改后的需求当成原单；请先核实订单，新需求另开饭局。')
    updated=start(session,now)
    updated['previous_request']=deepcopy(state['session']['request'])
    return updated


def record_quote(state,strategy,receipt,now=None):
    if state['status'] in ACTIVE_ORDER_STATES or state.get('order'):
        raise InputError('订单已提交，不接受把新的报价覆盖原订单。')
    result=finalize(state['draft'],receipt,strategy,now=now)
    state['quotes'][strategy]=result
    state['receipts'][strategy]=deepcopy(receipt)
    state['purchase']=None
    state['status']='choices_ready'
    return result


def selected_way(result,code):
    for row in result.get('take_way_options',[]):
        if isinstance(row,dict) and row.get('code')==code:
            return row
    raise InputError('该取餐方式不在本次官方核价返回中，请重新核价并选择可用方式。')


def preview_digest(preview):
    return fingerprint({k:v for k,v in preview.items() if k not in {'confirmation_id','attempt_id','approval_reply','submitted_at'}})


def preview(state,strategy,take_way,now=None):
    if state['status'] in ACTIVE_ORDER_STATES or state.get('order'):
        raise InputError('这份饭局已提交或可能提交，先查询订单，不能重复生成下单请求。')
    result=state['quotes'].get(strategy)
    if not result or result['status'] not in {'quoted','demo_quoted'}:
        raise InputError('请选择已通过官方核价且未超预算的方案。')
    receipt=state['receipts'][strategy]
    business(receipt['response'],state['mode'],now=now)
    way=selected_way(result,take_way)
    arguments={**result['context'],'items':deepcopy(result['order_items']),'takeWayCode':take_way}
    tableware=arguments.get('needTableware')
    context_summary=(f"\n用餐方式：到店自取\n取餐方式：{way.get('title',take_way)}"+
                     (f"（{way['subtitle']}）" if way.get('subtitle') else '')+
                     f"\n预约时间：{arguments.get('reservationDate','未指定，按门店当前规则出餐')}"+
                     f"\n餐具：{'需要' if tableware is True else '不需要' if tableware is False else '未指定，使用官方默认设置'}\n")
    purchase={'strategy':strategy,'mode':state['mode'],'create_arguments':arguments,
              'quote_arguments':deepcopy(receipt['arguments']),'plan_fingerprint':result['plan_fingerprint'],
              'payable_cents':result['total_cents'],'take_way':deepcopy(way),'created_at':timestamp(now),'nonce':uuid4().hex,
              'summary':share(result)+context_summary+
                        ('这是虚构演示，不会创建真实订单。' if state['mode']=='demo' else '确认后会创建一笔待支付订单，付款需你在官方页面完成。')+
                        '\n请回复“确认下单”，或告诉我需要修改的内容。'}
    purchase['confirmation_id']=preview_digest(purchase)
    state['purchase']=purchase;state['status']='confirmation_required'
    return {'status':'confirmation_required','confirmation_id':purchase['confirmation_id'],'summary':purchase['summary'],
            'recheck_request':{'tool':'calculate-price','arguments':deepcopy(purchase['quote_arguments'])}}


def confirm(state,confirmation_id,user_reply,recheck,now=None):
    purchase=state.get('purchase')
    if state['status']!='confirmation_required' or not purchase:
        raise InputError('没有等待确认的购买清单。已提交或结果不明的请求不可重发。')
    if purchase['confirmation_id']!=preview_digest(purchase) or confirmation_id!=purchase['confirmation_id']:
        raise InputError('购买清单已经变化或确认编号不一致，请重新展示清单。')
    if not isinstance(user_reply,str) or user_reply.strip().lower() not in APPROVALS:
        raise InputError('需要用户在当前购买清单之后明确回复“确认下单”，普通“好/继续”不作为下单授权。')
    if state['mode']=='live' and (now or datetime.now(timezone.utc))-instant(purchase['created_at'])>timedelta(minutes=15):
        raise InputError('购买清单已过期，请重新预览并确认。')
    if recheck.get('confirmation_id')!=confirmation_id or canonical(recheck.get('arguments'))!=canonical(purchase['quote_arguments']):
        raise InputError('确认前复核的参数不是当前饭局，不能创建订单。')
    response=business(recheck.get('response'),state['mode'],now=now,not_before=purchase['created_at'])
    updated_receipt={'plan_fingerprint':purchase['plan_fingerprint'],'arguments':deepcopy(recheck['arguments']),'response':deepcopy(recheck['response'])}
    updated=finalize(state['draft'],updated_receipt,purchase['strategy'],now=now)
    current_way=selected_way(updated,purchase['create_arguments']['takeWayCode'])
    if canonical(current_way)!=canonical(purchase['take_way']):
        state['quotes'][purchase['strategy']]=updated;state['receipts'][purchase['strategy']]=updated_receipt
        state['purchase']=None;state['status']='choices_ready'
        return {'status':'options_changed','message':'取餐方式的说明已变化，请重新展示购买清单并确认。'}
    if updated['total_cents']!=purchase['payable_cents']:
        state['quotes'][purchase['strategy']]=updated;state['receipts'][purchase['strategy']]=updated_receipt
        state['purchase']=None;state['status']='choices_ready'
        return {'status':'price_changed','previous_cents':purchase['payable_cents'],'current_cents':updated['total_cents'],
                'message':'价格已变化，尚未导出下单请求，请重新展示新金额并取得确认。'}
    if updated['status'] not in {'quoted','demo_quoted'}:
        raise InputError('复核金额超过预算，不能创建订单。')
    purchase['attempt_id']=uuid4().hex;purchase['approval_reply']=user_reply;purchase['submitted_at']=timestamp(now)
    state['quotes'][purchase['strategy']]=updated;state['receipts'][purchase['strategy']]=updated_receipt
    state['status']='submission_pending'
    request={'arguments':deepcopy(purchase['create_arguments']),'confirmation_id':confirmation_id,'attempt_id':purchase['attempt_id'],'mode':state['mode']}
    if state['mode']=='demo':
        return {'status':'simulation_ready','simulation_request':request,'message':'仅用虚构回执演练，禁止调用真实create-order。'}
    return {'status':'submission_pending','tool_request':{'tool':'create-order',**request},
            'message':'由宿主执行一次；先保存结果，再继续。超时或未知结果须查询核实，不重发。'}


def money_string(value):
    if not isinstance(value,str):return None
    try:
        amount=Decimal(value)
        cents=amount*100
        if not amount.is_finite() or amount<0 or cents!=cents.to_integral_value():return None
        return int(cents)
    except InvalidOperation:return None


def safe_url(value,mode):
    from urllib.parse import urlsplit
    if not isinstance(value,str):return None
    try:parsed=urlsplit(value)
    except ValueError:return None
    if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password:return None
    if mode=='live' and parsed.hostname in {'example.com','example.invalid'}:return None
    return value


def order_fields(detail):
    fields=('orderId','orderStatus','status','expirePayTime','pickupCode','lockerCode','lockerQrCode','storeName','storeAddress','takeWay','realTotalAmount')
    return {key:detail[key] for key in fields if key in detail}


def current_response(state,value,not_before,now=None):
    if state['mode']=='demo':return
    observed=instant(value.get('datetime'));current=now or datetime.now(timezone.utc)
    if current-observed>timedelta(minutes=15) or observed-current>timedelta(minutes=2) or observed<instant(not_before)-timedelta(seconds=5):
        raise InputError('订单回执已过期或早于本次请求，请查询当前订单，不能据旧状态确认支付。')


def record_order(state,receipt):
    purchase=state.get('purchase')
    if state['status'] not in {'submission_pending','outcome_unknown'} or not purchase:
        raise InputError('没有等待回执的下单请求，不能导入重复或无关订单。')
    if receipt.get('mode')!=state['mode'] or receipt.get('attempt_id')!=purchase['attempt_id'] or receipt.get('confirmation_id')!=purchase['confirmation_id'] or canonical(receipt.get('arguments'))!=canonical(purchase['create_arguments']):
        raise InputError('下单回执与本次确认或参数不一致，先核实实际订单。')
    try:
        value=unwrap_business(receipt.get('response'))
        current_response(state,value,purchase['submitted_at'])
        data=value['data'];detail=data.get('orderDetail',{})
        order_id=data.get('orderId') or detail.get('orderId')
        if not isinstance(order_id,str) or not order_id or detail.get('orderId',order_id)!=order_id:
            raise InputError('订单返回缺少一致的orderId。')
        if state['mode']=='live' and order_id.startswith('DEMO'):
            raise InputError('演示订单不能当作真实订单。')
    except (InputError,TypeError,AttributeError):
        state['status']='outcome_unknown'
        return {'status':'outcome_unknown','message':'下单结果未能确认，先用order-list核实，禁止直接重试。',
                'query_request':{'tool':'order-list','arguments':{}}}
    state['order']={'order_id':order_id,'detail':order_fields(detail),'pay_url':safe_url(data.get('payH5Url'),state['mode']),
                    'created_at':value.get('datetime'),'paid_verified':False}
    total=money_string(detail.get('realTotalAmount'))
    if total is not None and total!=purchase['payable_cents']:
        state['status']='order_created_review_needed'
    else:state['status']='awaiting_payment'
    return present_order(state)


def query_request(state):
    if state.get('order'):
        request={'tool':'query-order','arguments':{'orderId':state['order']['order_id']}}
    elif state['status'] in {'submission_pending','outcome_unknown'}:
        request={'tool':'order-list','arguments':{}}
    else:
        raise InputError('当前没有已创建或待核实的订单。')
    query_id=uuid4().hex
    state['last_query']={'id':query_id,'request':request,'created_at':timestamp()}
    return {'status':'query_once','query_id':query_id,'tool_request':request,'message':'本次只查询一次；用户再次询问时再查询，不后台轮询。'}


def record_status(state,receipt,now=None):
    pending=state.get('last_query')
    if receipt.get('mode')!=state['mode'] or not pending or pending['request']['tool']!='query-order' or receipt.get('query_id')!=pending['id'] or canonical(receipt.get('arguments'))!=canonical(pending['request']['arguments']):
        raise InputError('订单查询回执不属于本次订单。')
    value=unwrap_business(receipt.get('response'));detail=value['data']
    current_response(state,value,pending['created_at'],now)
    if detail.get('orderId')!=state['order']['order_id']:
        raise InputError('查询返回的是另一笔订单，不能据此确认付款或取餐。')
    state['last_query']=None
    state['order']['detail']=order_fields(detail)
    state['order']['queried_at']=value.get('datetime')
    status=str(detail.get('orderStatus',''))
    state['order']['paid_verified']=status in PAID_STATUSES
    if status in {'6','8'}:state['status']='completed'
    elif status in PAID_STATUSES:state['status']='paid'
    elif status=='7':state['status']='cancelled'
    elif status=='1':
        state['status']='awaiting_payment'
        expires=detail.get('expirePayTime')
        if isinstance(expires,str):
            try:
                if instant(expires)<(now or datetime.now(timezone.utc)):state['status']='payment_expired'
            except InputError:pass
    else:state['status']='order_status_unknown'
    actual=money_string(detail.get('realTotalAmount'))
    if state['order']['paid_verified'] and actual is not None:
        selected=state['purchase']['strategy']
        previous=state['quotes'][selected]
        settled=apply_official_total(state['draft']['plans'][selected]['result'],actual,state['session']['request']['budget_cents'])
        settled.update({key:deepcopy(previous[key]) for key in ('mode','context','quoted_at','plan_fingerprint','order_items','take_way_options','shared_items') if key in previous})
        settled['paid_amount_cents']=actual
        settled['acceptance'][-1]={'check':'订单实际金额','status':'PASS','detail':'来自本次订单查询返回。'}
        if state['mode']=='demo':
            settled['status']='demo_'+settled['status']
            settled['acceptance'][1]['check']='演示金额不超预算'
            settled['acceptance'][-1]={'check':'虚构订单金额','status':'DEMO'}
        state['quotes'][selected]=settled
        state['order']['actual_amount_cents']=actual
    return present_order(state)


def reconcile(state,receipt,order_id,user_reply,now=None):
    pending=state.get('last_query')
    if state['status'] not in {'submission_pending','outcome_unknown'} or not pending or pending['request']['tool']!='order-list':
        raise InputError('当前不在下单结果核实阶段。')
    if user_reply not in {'确认认领订单','这就是刚才那单'}:
        raise InputError('请先让用户核对候选订单并确认这是刚才那单，不能自动认领历史订单。')
    if receipt.get('mode')!=state['mode'] or receipt.get('query_id')!=pending['id'] or canonical(receipt.get('arguments'))!='{}':
        raise InputError('历史订单回执与当前核实请求不一致。')
    value=unwrap_business(receipt.get('response'))
    current_response(state,value,pending['created_at'],now)
    data=value['data']
    rows=data.get('list')
    if not isinstance(rows,list):raise InputError('订单列表格式变化，不能自动认领订单。')
    matches=[row for row in rows if isinstance(row,dict) and row.get('orderId')==order_id]
    if len(matches)!=1:raise InputError('未找到唯一的指定订单，保持结果不明，不能重新下单。')
    row=matches[0]
    args=state['purchase']['create_arguments']
    if row.get('storeCode')!=args['storeCode'] or str(row.get('orderType'))!=str(args['orderType']) or str(row.get('beType'))!=str(args['beType']):
        raise InputError('核实订单的门店、订单类型或业务类型不同。')
    expected={}
    for item in args['items']:expected[item['productCode']]=expected.get(item['productCode'],0)+item['quantity']
    actual={}
    for item in row.get('orderProductList',[]):
        code=item.get('productCode');quantity=item.get('quantity')
        if not isinstance(code,str) or type(quantity) is not int:raise InputError('历史订单商品不完整，需人工核实。')
        actual[code]=actual.get(code,0)+quantity
    if actual!=expected:raise InputError('历史订单商品或数量不匹配，不能认领。')
    expected_children={}
    for item in args['items']:
        if item.get('roundList'):
            for part in item['roundList']:
                for choice in part['comboItemList']:
                    expected_children[choice['code']]=expected_children.get(choice['code'],0)+choice['quantity']*item['quantity']
    if expected_children:
        actual_children={}
        for item in row.get('orderProductList',[]):
            for choice in item.get('comboItemList',[]):
                code,quantity=choice.get('productCode'),choice.get('quantity')
                if not isinstance(code,str) or type(quantity) is not int:raise InputError('订单套餐子项无法核对，保持结果不明。')
                actual_children[code]=actual_children.get(code,0)+quantity
        if actual_children!=expected_children:raise InputError('订单套餐子项不同，不能认领。')
    if money_string(row.get('realTotalAmount'))!=state['purchase']['payable_cents']:
        raise InputError('历史订单金额不匹配，需进一步人工核实。')
    created_at=instant(row.get('createTime'))
    if created_at<instant(state['purchase']['submitted_at'])-timedelta(seconds=5):
        raise InputError('这是之前的订单，不能作为本次下单成功依据。')
    if created_at>(now or datetime.now(timezone.utc))+timedelta(minutes=2):
        raise InputError('订单创建时间异常，保持结果不明。')
    state['order']={'order_id':order_id,'detail':{'orderId':order_id},'pay_url':None,'paid_verified':False}
    state['last_query']=None;state['status']='awaiting_payment'
    return {'status':'order_found','order_id':order_id,'message':'已认领匹配订单，请再query-order核实完整状态、金额和取餐选项；不创建第二单。'}


def present_order(state):
    order=state['order'];detail=order['detail'];paid=order.get('paid_verified') is True
    result={'status':state['status'],'mode':state['mode'],'order_id':order['order_id'],
            'order_status':STATUS_NAMES.get(str(detail.get('orderStatus','')),'待查询核实'),
            'paid_verified':paid,'store_name':detail.get('storeName',state['session'].get('store_name'))}
    if state['status']=='awaiting_payment':
        result['pay_url']=order.get('pay_url');result['expire_pay_time']=detail.get('expirePayTime')
        result['message']='订单已创建，付款由用户在官方支付页或麦当劳App完成。付款后回复“付好了”，再查询一次状态。'
        if not order.get('pay_url'):result['message']='订单已创建，但未返回可用支付链接。请在麦当劳App订单页付款，完成后告诉我再查询。'
    elif state['status']=='order_created_review_needed':
        result['confirmed_total_cents']=state['purchase']['payable_cents']
        result['returned_total_cents']=money_string(detail.get('realTotalAmount'))
        result['pay_url']=order.get('pay_url')
        result['message']='订单已经创建，但返回金额与确认金额不同。请在官方页面核实后自行决定，不能自动重下单。'
    elif state['status']=='payment_expired':result['message']='订单仍未确认支付且支付期限已过，请在官方App核实。'
    elif paid:
        result['pickup_code']=detail.get('pickupCode');result['locker_code']=detail.get('lockerCode');result['locker_qr_code']=safe_url(detail.get('lockerQrCode'),state['mode'])
        result['message']='官方订单状态已确认支付；若取餐信息尚未返回，说明暂无取餐码，等待用户下一次询问再查。'
    elif state['status']=='cancelled':result['message']='官方返回订单已取消，退款或实际款项需在官方渠道核实。'
    else:result['message']='当前状态尚未核实，不能声称付款完成或再次创建订单。'
    if state['mode']=='demo':
        result['demo_notice']='虚构订单和支付链接，未调用真实写操作，不能用于真实付款或取餐。'
        result['message']='演示状态：'+result['message']
    return result


def bill(state):
    purchase=state.get('purchase')
    if not purchase:raise InputError('请先选择方案并展示购买清单。')
    result=state['quotes'][purchase['strategy']]
    text=share(result)
    if state.get('order') and state['order'].get('paid_verified'):
        prefix='演示账单：虚构付款状态。' if state['mode']=='demo' else '付款已由官方订单状态核实。'
        if state['order'].get('actual_amount_cents') is None:prefix+=' 实际金额未完整返回，分账暂按最近报价估算。'
        return prefix+'\n'+text
    return '预计分账：尚未核实付款，最终以订单实际金额为准。\n'+text


def repeat_request(state):
    if state['status'] in {'submission_pending','outcome_unknown','order_status_unknown','order_created_review_needed'}:
        raise InputError('上次订单结果或金额尚未核实，请先查清，不能用复购绕过未解决订单。')
    return {'status':'needs_current_menu','request':deepcopy(state['session']['request']),
            'previous_context':{key:value for key,value in state['session']['context'].items() if key!='reservationDate'},
            'previous_meals':[row['meal'] for row in state['quotes'].get((state.get('purchase') or {}).get('strategy','preference'),{}).get('allocations',[])],
            'message':'读取上次需求后，重新查询当前门店菜单并核价，使用新的state；不可直接重复旧下单请求。'}


def cli(argv):
    parser=argparse.ArgumentParser(description='麦麦搭子饭局与购买流程（宿主执行MCP请求）')
    sub=parser.add_subparsers(dest='command',required=True)
    for name in ('start','revise'):
        cmd=sub.add_parser(name);cmd.add_argument('--input',required=True);cmd.add_argument('--state',required=True)
    cmd=sub.add_parser('quote');cmd.add_argument('--state',required=True);cmd.add_argument('--receipt',required=True);cmd.add_argument('--strategy',choices=['preference','economy'],required=True)
    cmd=sub.add_parser('preview');cmd.add_argument('--state',required=True);cmd.add_argument('--strategy',choices=['preference','economy'],required=True);cmd.add_argument('--take-way',required=True)
    cmd=sub.add_parser('confirm');cmd.add_argument('--state',required=True);cmd.add_argument('--confirmation-id',required=True);cmd.add_argument('--user-reply',required=True);cmd.add_argument('--recheck',required=True)
    for name in ('record','status'):
        cmd=sub.add_parser(name);cmd.add_argument('--state',required=True);cmd.add_argument('--receipt',required=True)
    cmd=sub.add_parser('reconcile');cmd.add_argument('--state',required=True);cmd.add_argument('--receipt',required=True);cmd.add_argument('--order-id',required=True);cmd.add_argument('--user-reply',required=True)
    for name in ('show','query','bill','repeat'):
        cmd=sub.add_parser(name);cmd.add_argument('--state',required=True)
    args=parser.parse_args(argv)
    try:
        with locked(args.state):
            if args.command=='start':
                if Path(args.state).exists():raise InputError('饭局记录已存在，不能覆盖；修改请用revise，新订单另开记录。')
                state=start(read(args.input));result={'status':state['status'],'quote_requests':next_quotes(state)}
            else:
                state=load_state(args.state)
                if args.command=='revise':state=revise(state,read(args.input));result={'status':state['status'],'quote_requests':next_quotes(state)}
                elif args.command=='quote':result=record_quote(state,args.strategy,read(args.receipt))
                elif args.command=='preview':result=preview(state,args.strategy,args.take_way)
                elif args.command=='confirm':result=confirm(state,args.confirmation_id,args.user_reply,read(args.recheck))
                elif args.command=='record':result=record_order(state,read(args.receipt))
                elif args.command=='query':result=query_request(state)
                elif args.command=='status':result=record_status(state,read(args.receipt))
                elif args.command=='reconcile':result=reconcile(state,read(args.receipt),args.order_id,args.user_reply)
                elif args.command=='bill':result={'status':state['status'],'text':bill(state)}
                elif args.command=='repeat':result=repeat_request(state)
                else:result={'status':state['status'],'mode':state['mode'],'quote_requests':next_quotes(state),'quotes':state['quotes'],
                             'order':present_order(state) if state.get('order') else None}
            if args.command not in {'show','bill','repeat'}:write_state(args.state,state)
        print(json.dumps(result,ensure_ascii=False))
    except (InputError,KeyError,TypeError,ValueError,OSError) as error:
        print(json.dumps({'status':'error','message':str(error)},ensure_ascii=False));raise SystemExit(2)


def demo_purchase(session,destination):
    """Demonstrate every purchase phase with synthetic receipts, zero MCP calls."""
    destination=Path(destination);destination.mkdir(parents=True,exist_ok=True)
    state=start(session)
    way={'code':'demo-takeout','title':'演示外带','subtitle':'虚构取餐方式'}
    transcript=['# 麦麦搭子买餐演练','', '**全程虚构数据，未创建真实订单，不可付款或取餐。**','']
    for strategy,entry in state['draft']['plans'].items():
        call=entry['quote_request']
        receipt={'plan_fingerprint':call['plan_fingerprint'],'arguments':call['arguments'],
                 'response':{'success':True,'datetime':'DEMO','data':{'price':entry['result']['total_cents'],'takeWayList':[way]}}}
        record_quote(state,strategy,receipt)
    shown=preview(state,'economy',way['code'])
    transcript+=['用户：就买省钱方案。','', '助手展示购买清单：',shown['summary'],'','用户：确认下单。','']
    check={'confirmation_id':shown['confirmation_id'],'arguments':shown['recheck_request']['arguments'],
           'response':{'success':True,'datetime':'DEMO','data':{'price':state['purchase']['payable_cents'],'takeWayList':[way]}}}
    ready=confirm(state,shown['confirmation_id'],'确认下单',check)
    assert 'tool_request' not in ready
    p=state['purchase'];amount=f"{p['payable_cents']//100}.{p['payable_cents']%100:02d}"
    created={'mode':'demo','confirmation_id':p['confirmation_id'],'attempt_id':p['attempt_id'],'arguments':p['create_arguments'],
             'response':{'success':True,'datetime':'DEMO','data':{'orderId':'DEMO-ORDER-001','payH5Url':'https://example.invalid/demo-payment',
                        'orderDetail':{'orderId':'DEMO-ORDER-001','orderStatus':'1','realTotalAmount':amount,'storeName':'虚构演示门店'}}}}
    pending=record_order(state,created)
    transcript+=['助手：模拟订单已创建，状态为待支付。演示链接不可付款。','', '用户：付好了。','']
    query=query_request(state)
    status={'mode':'demo','query_id':query['query_id'],'arguments':query['tool_request']['arguments'],
            'response':{'success':True,'datetime':'DEMO','data':{'orderId':'DEMO-ORDER-001','orderStatus':'2','realTotalAmount':amount,'pickupCode':'DEMO-A01','storeName':'虚构演示门店'}}}
    paid=record_status(state,status)
    transcript+=['助手：模拟查询返回已支付、配餐中；演示取餐码 DEMO-A01 不可取餐。','',bill(state)]
    write_state(destination/'meal.json',state)
    (destination/'buying-transcript.md').write_text('\n'.join(transcript)+'\n',encoding='utf-8')
    (destination/'buying-receipts.json').write_text(json.dumps({'mode':'demo','created':created,'status':status},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return {'status':'demo_purchase_complete','network_calls':0,'real_orders_created':0,'demo_total_cents':p['payable_cents'],
            'payment_status':paid['status'],'transcript':str(destination/'buying-transcript.md')}
