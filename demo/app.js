'use strict';
let initialRequest, source, strategy = 'preference', currentResult;
let generation = 0;
const $ = (selector) => document.querySelector(selector);
const yuan = (cents) => `¥${(cents / 100).toFixed(2)}`;
const escapeHtml = (s) => String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const tagNames = {burger:'汉堡',classic:'经典口味',grilled:'板烧口味',spicy:'辣味',fries:'薯条'};
function render(result) {
  const area = $('#result');
  if (result.status !== 'planned') {
    area.innerHTML = `<div class="empty"><h3>这份预算，需要再商量一下</h3><p>${escapeHtml(result.reason)}</p>${result.shortfall_at_least_cents ? `<p>至少还差 ${yuan(result.shortfall_at_least_cents)}。试着增加预算，或取消共享小食。</p>` : ''}<p>大家的硬性要求会保留，每个人都不会被漏掉。</p></div>`;
    return;
  }
  const colors = ['peach','yellow','green','purple'];
  area.innerHTML = `<div class="summary"><div><small>四人饭局 · ${strategy === 'preference' ? '优先照顾口味' : '候选内优先省钱'}</small><strong>${yuan(result.total_cents)}</strong><p>个人餐 + 共享小食 · 金额按分计算</p></div><div class="budget-status">预算内安排完成<br><br>还剩 <b>${yuan(result.remaining_cents)}</b></div></div><div class="meal-grid">${result.allocations.map((a,i)=>`<article class="meal-card"><div class="meal-head"><div><span class="avatar ${colors[i]}">${escapeHtml(a.name.slice(-1))}</span>${escapeHtml(a.name)}</div><strong>${yuan(a.total_cents)}</strong></div><h3>${escapeHtml(a.meal)}</h3><p>${a.kcal === null ? '能量信息待核实' : `个人餐 ${a.kcal} kcal · 共享小食 ${a.shared_kcal.toFixed(1)} kcal`}<br>个人餐 ${yuan(a.meal_cents)} · 共享 ${yuan(a.shared_cents)}</p><div class="matched">${a.matched_preferences.length ? `✓ 照顾到 ${a.matched_preferences.map(t=>escapeHtml(tagNames[t]||t)).join('、')}` : '✓ 符合个人硬性要求'}</div>${a.unmet_preferences.length ? `<p>取舍：${a.unmet_preferences.map(t=>escapeHtml(tagNames[t]||t)).join('、')}偏好未满足</p>` : ''}</article>`).join('')}</div><div class="acceptance"><div class="acceptance-title"><strong>点餐验收单</strong><span>逐条核对，心里有数</span></div>${result.acceptance.map(c=>`<div class="check-row"><span>${escapeHtml(c.check)}</span><span class="${c.status==='PASS'?'pass':'pending'}">${c.status==='PASS'?'✓ 通过':'◷ 待核实'}</span></div>`).join('')}</div>`;
}
async function generate() {
  if (!initialRequest) return;
  const thisGeneration = ++generation;
  const request = structuredClone(initialRequest);
  request.budget_cents = Number($('#budget').value)*100;
  request.participants[2].exclude_tags = $('#zero').checked ? ['sugary-drink'] : [];
  if (!$('#shared').checked) request.shared = [];
  $('#generate').disabled=true;
  try {
    const response=await fetch('/api/plan',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({request,strategy})});
    const result=await response.json();
    if (!response.ok) throw new Error(result.error);
    if (thisGeneration === generation) { currentResult=result;render(result); }
  } catch(error) {
    if (thisGeneration===generation) $('#result').innerHTML=`<div class="empty"><h3>暂时无法安排</h3><p>${escapeHtml(error.message)}</p></div>`;
  } finally { if(thisGeneration===generation) $('#generate').disabled=false; }
}
$('#budget').addEventListener('input',()=>{$('#budget-value').textContent=`¥${$('#budget').value}`;});
$('#budget').addEventListener('change',generate);
$('#generate').addEventListener('click',generate);
$('#zero').addEventListener('change',generate);
$('#shared').addEventListener('change',generate);
document.querySelectorAll('[data-budget]').forEach(button=>button.addEventListener('click',()=>{$('#budget').value=button.dataset.budget;$('#budget-value').textContent=`¥${button.dataset.budget}`;generate();}));
document.querySelectorAll('[data-strategy]').forEach(button=>button.addEventListener('click',()=>{strategy=button.dataset.strategy;document.querySelectorAll('[data-strategy]').forEach(b=>b.classList.toggle('active',b===button));generate();}));
fetch('/api/data').then(r=>r.json()).then(data=>{initialRequest=data.request;source=data.source;$('#source-text').textContent=`${source.store_name} · ${source.fetched_at}`;generate();}).catch(error=>{$('#result').textContent=error.message;});
