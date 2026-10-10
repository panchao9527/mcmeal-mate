'use strict';
const $ = selector => document.querySelector(selector);
const escapeHtml = value => String(value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const yuan = cents => `¥${(cents/100).toFixed(2)}`;
const tagNames = {burger:'汉堡',classic:'经典口味',grilled:'板烧口味',spicy:'辣味',fries:'薯条'};
const colors = ['peach','yellow','green','purple'];
let members=[], snapshotSource, mode='demo', strategy='preference', revision=0, busy=false, initialized=false;
let searchId=null, storeChoices=[], demoTimer, sequence=0;
const selected = (actual,value) => actual===value?' selected':'';
const checked = value => value?' checked':'';

function memberFromRequest(p,request) {
  return {id:p.id,name:p.name,flavor:p.prefer_tags?.find(t=>['classic','grilled','spicy'].includes(t))||'',
    favoriteFries:p.prefer_tags?.includes('fries')||false, drink:p.required_categories?.includes('drink')||false,
    fries:p.required_categories?.includes('fries')||false,beef:p.exclude_tags?.includes('beef')||false,
    spicy:p.exclude_tags?.includes('spicy')||false,zero:p.exclude_tags?.includes('sugary-drink')||false,
    maxKcal:p.max_kcal??'',share:request.shared?.some(s=>s.participant_ids.includes(p.id))||false};
}

function renderMembers() {
  $('#member-count').textContent=members.length;
  $('#add-person').disabled=members.length>=8;
  $('#people').innerHTML=members.map((p,i)=>`<article class="person-card" data-id="${escapeHtml(p.id)}"><div class="person-heading"><span class="avatar ${colors[i%4]}">${escapeHtml(Array.from(p.name).slice(-1).join('')||'?')}</span><input data-field="name" aria-label="成员${i+1}姓名" value="${escapeHtml(p.name)}" maxlength="40" required><button class="remove-person" aria-label="删除成员${i+1}"${members.length===1?' disabled':''}>×</button></div><div class="form-pair"><label>口味偏好<select data-field="flavor" aria-label="成员${i+1}口味偏好"><option value="">不限</option><option value="classic"${selected(p.flavor,'classic')}>经典口味</option><option value="grilled"${selected(p.flavor,'grilled')}>板烧口味</option><option value="spicy"${selected(p.flavor,'spicy')}>辣味</option></select></label><label>能量上限 kcal<input data-field="maxKcal" aria-label="成员${i+1}能量上限" type="number" min="0" max="10000" step="1" value="${escapeHtml(p.maxKcal)}" placeholder="不限"></label></div><div class="person-options">${[['drink','必有饮料'],['fries','必有薯条'],['beef','主食不要牛肉'],['spicy','不要辣味主食'],['zero','饮料无糖'],['favoriteFries','偏爱薯条'],['share','参与分食']].map(([field,label])=>`<label><input type="checkbox" data-field="${field}" aria-label="成员${i+1}${label}"${checked(p[field])}>${label}</label>`).join('')}</div></article>`).join('');
}

function sourceText(source,quotedAt) {
  $('#source-title').textContent=mode==='live'?'麦当劳官方实时查询':'历史演示数据';
  $('#source-text').textContent=source?`${source.store_name}${source.fetched_at?` · 菜单查询 ${source.fetched_at}`:' · 菜单待查询'}${source.menu_period==='breakfast'?' · 当前为早餐候选':''}`:'请先选择门店';
  $('#source-help').textContent=mode==='live'?(quotedAt?`本方案官方核价：${quotedAt}。价格及供应以后续官方结果为准。`:'当前需求还没有完成官方核价。'):'按历史快照计算；当前官方整单价格待核实。';
}

function markChanged() {
  revision++;
  $('#dirty-note').hidden=false;
  $('#result').classList.add('stale');
  if(mode==='demo'&&!busy&&initialized) {
    clearTimeout(demoTimer);
    demoTimer=setTimeout(generate,350);
  }
}

function requestFromForm() {
  const amount=$('#budget-amount').value;
  if(!/^\d+(?:\.\d{1,2})?$/.test(amount)) throw new Error('预算请填写正数或0，最多两位小数。');
  const [whole,fraction='']=amount.split('.');
  const budget=Number(whole)*100+Number(fraction.padEnd(2,'0'));
  if(budget>1000000) throw new Error('单次预算最高10000元。');
  const participants=members.map(p=>{
    if(!p.name.trim()) throw new Error('请为每位成员填写名字。');
    if(p.maxKcal!==''&&!/^\d+$/.test(String(p.maxKcal))) throw new Error('能量上限请填写整数或留空。');
    return {id:p.id,name:p.name.trim(),required_categories:['main',...(p.drink?['drink']:[]),...(p.fries?['fries']:[])],
      prefer_tags:[...(p.flavor?[p.flavor]:[]),...(p.favoriteFries?['fries']:[])],
      exclude_tags:[...(p.beef?['beef']:[]),...(p.spicy?['spicy']:[]),...(p.zero?['sugary-drink']:[])],
      ...(p.maxKcal!==''?{max_kcal:Number(p.maxKcal)}:{})};
  });
  const consumers=members.filter(p=>p.share).map(p=>p.id);
  if($('#shared').checked&&!consumers.length) throw new Error('请选择至少一位参与分食的成员，或取消共享小食。');
  return {budget_cents:budget,participants,shared:$('#shared').checked?[{offer_id:'shared-fries',quantity:Number($('#shared-count').value),participant_ids:consumers}]:[]};
}

function render(result) {
  sourceText(result.source||snapshotSource,result.quoted_at);
  if(!['planned','quoted','quote_over_budget'].includes(result.status)) {
    $('#result').innerHTML=`<div class="empty"><h3>${result.status==='search_limit'?'搜索尚未完成':'这些要求，需要再商量一下'}</h3><p>${escapeHtml(result.reason||'当前候选中没有可行方案。')}</p>${result.shortfall_at_least_cents?`<p>至少还差 ${yuan(result.shortfall_at_least_cents)}，可调整预算或共享小食。</p>`:''}<p>人数和硬性要求会保留；不会省略任何成员。</p></div>`;
    return;
  }
  const over=result.status==='quote_over_budget';
  $('#result').innerHTML=`<div class="summary ${over?'over-budget':''}"><div><small>${result.allocations.length}人饭局 · ${strategy==='preference'?'优先照顾口味':'候选内优先省钱'} · ${result.status==='planned'?'历史估算':'官方整单报价'}</small><strong>${yuan(result.total_cents)}</strong><p>个人餐 + 共享小食 + 已计入费用</p></div><div class="budget-status">${over?'官方核价超预算':'预算内安排完成'}<br><br>${over?'超出':'还剩'} <b>${yuan(Math.abs(result.remaining_cents))}</b></div></div>${result.search_complete===false?'<div class="dirty-note">已找到可行方案，但搜索未完成，不能保证候选内最优。</div>':''}${over?'<div class="dirty-note">官方金额超过预算，请调整要求后重新生成，本方案未通过预算验收。</div>':''}<div class="meal-grid">${result.allocations.map((a,i)=>`<article class="meal-card"><div class="meal-head"><div><span class="avatar ${colors[i%4]}">${escapeHtml(Array.from(a.name).slice(-1).join(''))}</span>${escapeHtml(a.name)}</div><strong>${yuan(a.total_cents)}</strong></div><h3>${escapeHtml(a.meal)}</h3><p>${a.kcal===null?'个人餐能量未知':`个人餐 ${a.kcal} kcal`} · 共享小食 ${a.shared_kcal.toFixed(1)} kcal<br>个人餐 ${yuan(a.meal_cents)} · 共享 ${yuan(a.shared_cents)}${a.fee_cents?` · 费用 ${yuan(a.fee_cents)}`:''}${a.official_adjustment_cents?`<br>官方价差分摊 ${a.official_adjustment_cents>0?'+':'−'}${yuan(Math.abs(a.official_adjustment_cents))}`:''}</p><div class="matched">${a.matched_preferences.length?`✓ 照顾到 ${a.matched_preferences.map(t=>escapeHtml(tagNames[t]||t)).join('、')}`:'✓ 符合个人硬性要求'}</div>${a.unmet_preferences.length?`<p>取舍：${a.unmet_preferences.map(t=>escapeHtml(tagNames[t]||t)).join('、')}偏好未满足</p>`:''}</article>`).join('')}</div><div class="acceptance"><div class="acceptance-title"><strong>点餐验收单</strong><span>逐条核对，心里有数</span></div>${result.acceptance.map(c=>`<div class="check-row"><span>${escapeHtml(c.check)}</span><span class="${c.status==='PASS'?'pass':c.status==='FAIL'?'fail':'pending'}">${c.status==='PASS'?'✓ 通过':c.status==='FAIL'?'× 未通过':'◷ 待核实'}</span></div>`).join('')}</div>`;
}

async function api(path,body) {
  const response=await fetch(path,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const data=await response.json();
  if(!response.ok) throw new Error(data.error||'本机服务响应异常。');
  return data;
}

function showProgress(text,count) {
  $('#job-progress').hidden=false;
  $('#job-progress').innerHTML=`<span class="spinner"></span><div><strong>${escapeHtml(text)}</strong>${count!==undefined?`<small>已完成 ${count} 次官方查询；首次读取菜单可能需要约一分钟。</small>`:''}</div>`;
}

async function pollJob(id) {
  const start=Date.now();
  while(Date.now()-start<600000) {
    const job=await api(`/api/jobs/${encodeURIComponent(id)}`);
    if(job.status==='complete') return job.result;
    if(job.status==='failed') throw new Error(job.error);
    showProgress(job.phase,job.calls_completed);
    await new Promise(resolve=>setTimeout(resolve,900));
  }
  throw new Error('查询时间较长，尚未收到完成结果，请稍后再试。');
}

function setBusy(value) {
  busy=value;
  $('#generate').disabled=value;
  $('#search-stores').disabled=value;
  document.querySelectorAll('[data-mode]').forEach(b=>b.disabled=value);
}

async function generate() {
  clearTimeout(demoTimer);
  if(busy||!initialized) return;
  const thisSequence=++sequence, inputRevision=revision;
  try {
    const request=requestFromForm();
    if(mode==='live'&&(!searchId||!$('#store').value)) throw new Error('请先查询并选择营业门店。');
    setBusy(true);
    showProgress(mode==='live'?'正在为这顿饭查询官方数据':'正在计算历史演示方案');
    const result=mode==='live'?await pollJob((await api('/api/live',{request,strategy,search_id:searchId,store_code:$('#store').value})).job_id):await api('/api/plan',{request,strategy});
    if(thisSequence===sequence&&revision===inputRevision) {
      render(result);$('#dirty-note').hidden=true;$('#result').classList.remove('stale');
    } else {
      $('#dirty-note').textContent='需求在查询期间发生变化，请重新安排；刚才的结果未作为当前方案展示。';
      $('#dirty-note').hidden=false;
    }
  } catch(error) {
    $('#result').innerHTML=`<div class="empty"><h3>暂时无法安排</h3><p>${escapeHtml(error.message)}</p></div>`;
    $('#result').classList.remove('stale');
  } finally {
    $('#job-progress').hidden=true;setBusy(false);
    if(mode==='demo'&&revision!==inputRevision) {clearTimeout(demoTimer);demoTimer=setTimeout(generate,350);}
  }
}

async function searchStores() {
  if(busy) return;
  const city=$('#city').value.trim(),keyword=$('#keyword').value.trim();
  searchId=null;storeChoices=[];$('#store').innerHTML='<option value="">查询中……</option>';
  try {
    setBusy(true);showProgress('正在查询营业门店');
    const result=await pollJob((await api('/api/stores',{city,keyword})).job_id);
    if(city!==$('#city').value.trim()||keyword!==$('#keyword').value.trim()) throw new Error('城市或地标已修改，请重新查询。');
    searchId=result.search_id;storeChoices=result.stores;
    $('#store').innerHTML='<option value="">请选择门店</option>'+storeChoices.map(s=>`<option value="${escapeHtml(s.storeCode)}">${escapeHtml(s.storeName)}</option>`).join('');
    $('#store-address').textContent=storeChoices.length?`找到 ${storeChoices.length} 家营业门店，请选择一家。`:'没有找到营业门店，试试其他地标。';
  } catch(error) {
    $('#store').innerHTML='<option value="">请重新查询门店</option>';
    $('#store-address').textContent=error.message;
  } finally {$('#job-progress').hidden=true;setBusy(false);}
}

$('#people').addEventListener('input',event=>{
  const card=event.target.closest('.person-card'),field=event.target.dataset.field;
  if(!card||!field) return;
  const person=members.find(p=>p.id===card.dataset.id);
  person[field]=event.target.type==='checkbox'?event.target.checked:event.target.value;
  if(field==='name') card.querySelector('.avatar').textContent=Array.from(person.name).slice(-1).join('')||'?';
  markChanged();
});
$('#people').addEventListener('click',event=>{
  if(!event.target.closest('.remove-person')||members.length<=1) return;
  const id=event.target.closest('.person-card').dataset.id;
  members=members.filter(p=>p.id!==id);renderMembers();markChanged();
});
$('#add-person').addEventListener('click',()=>{
  if(members.length>=8) return;
  members.push({id:crypto.randomUUID(),name:`成员${members.length+1}`,flavor:'',favoriteFries:false,drink:true,fries:false,beef:false,spicy:false,zero:false,maxKcal:'',share:true});
  renderMembers();markChanged();
});
$('#budget-amount').addEventListener('input',()=>{$('#budget').value=Math.min(500,Number($('#budget-amount').value)||0);markChanged();});
$('#budget').addEventListener('input',()=>{$('#budget-amount').value=$('#budget').value;markChanged();});
['shared','shared-count','store'].forEach(id=>$('#'+id).addEventListener('change',()=>{
  if(id==='store') {
    const chosen=storeChoices.find(s=>s.storeCode===$('#store').value);
    $('#store-address').textContent=chosen?.address||'';
    if(mode==='live') sourceText(chosen?{store_name:chosen.storeName}:null);
  }
  markChanged();
}));
['city','keyword'].forEach(id=>$('#'+id).addEventListener('input',()=>{searchId=null;storeChoices=[];$('#store').innerHTML='<option value="">请重新查询门店</option>';$('#store-address').textContent='';markChanged();}));
$('#generate').addEventListener('click',generate);
$('#search-stores').addEventListener('click',searchStores);
document.querySelectorAll('[data-mode]').forEach(button=>button.addEventListener('click',()=>{
  mode=button.dataset.mode;document.querySelectorAll('[data-mode]').forEach(b=>b.classList.toggle('active',b===button));
  $('#location-panel').hidden=mode!=='live';
  $('#mode-help').textContent=mode==='live'?'先查询并选择门店，再点击“安排一下”。官方查询和核价在本机完成。':'使用2026-10-09的历史快照，可离线调整人数和偏好。';
  sourceText(mode==='demo'?snapshotSource:null);markChanged();
}));
document.querySelectorAll('[data-budget]').forEach(button=>button.addEventListener('click',()=>{$('#budget-amount').value=button.dataset.budget;$('#budget').value=button.dataset.budget;markChanged();}));
document.querySelectorAll('[data-strategy]').forEach(button=>button.addEventListener('click',()=>{strategy=button.dataset.strategy;document.querySelectorAll('[data-strategy]').forEach(b=>b.classList.toggle('active',b===button));markChanged();}));
api('/api/data').then(data=>{
  snapshotSource=data.source;members=data.request.participants.map(p=>memberFromRequest(p,data.request));renderMembers();sourceText(snapshotSource);
  if(!data.live_configured) $('#mode-help').textContent='历史演示可直接使用；实时配餐需在本机服务终端配置Token。';
  initialized=true;generate();
}).catch(error=>{$('#result').textContent=error.message;});
