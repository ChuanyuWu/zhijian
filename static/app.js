/* Local feed and connection wizard. Dynamic UI uses delegated events, not inline JS. */
const PLATFORM_NAMES = {weibo:'微博', taoguba:'淘股吧', zsxq:'知识星球', weread:'公众号'};
const CREDENTIAL_IDS = {weibo_cookie:'weiboCookie', taoguba_cookie:'taogubaCookie', zsxq_token:'zsxqToken', weread_cookie:'wereadCookie', access_password:'accessPassword'};
let currentPlatform='', currentKeyword='', currentAuthor='', loadedCount=0;
let settingsCache={users:[]}, userProfiles={}, pendingNew=new Map(), posts=new Map();
let loadVersion=0, feedAbort=null, drawerOpen=false, connections={};
let settingsDirty=false, settingsTab='monitors', candidates=[], discoveryVersion=0, scheduleState=null, lastConnectionHTML='';
const connectionBusy=new Set();
const dirtySecrets=new Set();
const $ = id => document.getElementById(id);
function esc(value) { return String(value ?? '').replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function pimg(url) { return '/api/img?url='+encodeURIComponent(url); }
function plainText(html) { const d=document.createElement('div'); d.innerHTML=html||''; return (d.textContent||'').trim(); }
function toast(message, color='#238636') { $('toast').textContent=message; $('toast').style.background=color; $('toast').style.display='block'; setTimeout(()=>$('toast').style.display='none',4000); }
async function api(path, options={}) {
  const r=await fetch(path, {headers:{'Content-Type':'application/json'}, ...options});
  const data=await r.json().catch(()=>({detail:'服务器返回异常，请重试'}));
  if (!r.ok) {
    if (r.status===401 && data.detail==='请重新登录') location.href='/login';
    throw new Error(typeof data.detail==='string' ? data.detail : '设置格式不正确，请检查用户 ID 和轮询间隔');
  }
  return data;
}
function fmtTime(iso) {
  const d=new Date(iso); if(!iso || isNaN(d)) return iso||'';
  return d.toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'});
}
function avatarPlaceholder(platform,ch,size) { return `<span class="avatar avatar-ph ${esc(platform)}" style="width:${size}px;height:${size}px">${esc(ch)}</span>`; }
function avatarHtml(p,size) {
  const profile=userProfiles[`${p.platform}:${p.author_id||p.user_id}`]||{};
  const name=displayName(p)||'?';
  const avatar=p.avatar||profile.avatar;
  return avatar ? `<img class="avatar" width="${size}" height="${size}" src="${pimg(avatar)}" alt="${esc(name[0])}">` : avatarPlaceholder(p.platform,name[0],size);
}
document.addEventListener('error',e=>{if(e.target.matches?.('img.avatar')){const span=document.createElement('span');span.className='avatar avatar-ph';span.textContent=e.target.alt||'?';span.style.width=(e.target.width||34)+'px';span.style.height=(e.target.height||34)+'px';e.target.replaceWith(span);}},true);
document.addEventListener('error',e=>{
  const img=e.target;if(!img.matches?.('.card-body img,.card-images img'))return;
  const fallback=document.createElement('span');fallback.className=img.classList.contains('inline-emoji')?'emoji-fallback':'image-fallback';
  fallback.textContent=img.classList.contains('inline-emoji')?(img.alt||'表情'):'图片暂不可用';fallback.title='可通过下方原文链接查看';img.replaceWith(fallback);
},true);
async function loadProfiles() {
  const cfg=await api('/api/settings'); userProfiles={};
  for(const u of cfg.users||[]) userProfiles[`${u.platform}:${u.user_id}`]={nickname:u.nickname,display_name:u.display_name,avatar:u.avatar};
  return cfg;
}
function displayName(p){
  const prof=userProfiles[`${p.platform}:${p.author_id||p.user_id}`]||{};
  if(p.platform==='zsxq'&&p.author_id)return p.author_name||p.nickname||prof.nickname||p.author_id;
  return p.display_name||prof.display_name||p.nickname||prof.nickname||p.author_name||p.author_id||p.user_id||'';
}
function markSettingsDirty(){settingsDirty=true;$('settingsDirty').textContent='有未保存的更改';}
function switchSettingsTab(tab){
  settingsTab=tab;
  document.querySelectorAll('[data-settings-panel]').forEach(p=>p.hidden=p.dataset.settingsPanel!==tab);
  document.querySelectorAll('[data-settings-tab]').forEach(b=>{b.classList.toggle('active',b.dataset.settingsTab===tab);b.setAttribute('aria-selected',String(b.dataset.settingsTab===tab));});
  $('settingsDirty').textContent=settingsDirty?'有未保存的更改':tab==='accounts'?'连接操作即时生效':'修改列表或偏好后保存';
  if(tab==='accounts')refreshConnections().catch(showError);
}
document.querySelector('.settings-tabs').onclick=e=>{const b=e.target.closest('[data-settings-tab]');if(b)switchSettingsTab(b.dataset.settingsTab);};
function tidyHtml(html,platform='') {
  const d=document.createElement('div'); d.innerHTML=html||'';
  // Defense in depth; the server applies the authoritative HTML whitelist.
  d.querySelectorAll('script,style,iframe,object,embed,form,svg,math').forEach(e=>e.remove());
  d.querySelectorAll('*').forEach(e=>{for(const a of [...e.attributes]) if(a.name.startsWith('on')||a.name==='style')e.removeAttribute(a.name);});
  d.querySelectorAll('a').forEach(a=>{if(!/^https?:\/\//i.test(a.getAttribute('href')||'')) a.removeAttribute('href');});
  d.querySelectorAll('div,font,span,p').forEach(e=>{if(!e.textContent.trim()&&!e.querySelector('img'))e.remove();});
  if(platform==='weibo')d.querySelectorAll('a').forEach(a=>{if(a.textContent.includes('全文')&&/\/(status|detail)\//.test(a.getAttribute('href')||''))a.remove();});
  d.querySelectorAll('img').forEach(img=>{
    let src=img.getAttribute('src')||'';try{src=new URL(src,location.origin).searchParams.get('url')||src;}catch(e){}
    if(/face\.t\.sinajs\.cn|\/expression\//i.test(src)||/^\[(?!图片|配图).+\]$/.test(img.alt)){
      img.className='inline-emoji';img.alt=img.alt||'表情';img.title=img.alt;img.loading='lazy';
    }else if(/timeline_card_small_/.test(src)){
      const badge=document.createElement('span');badge.className='inline-resource';badge.textContent=/video/.test(src)?'视频':/location/.test(src)?'位置':'链接';img.replaceWith(badge);
    }else{img.className='inline-picture';img.dataset.action='zoom';img.loading='lazy';img.alt=img.alt||'正文插图';}
  });
  return d.innerHTML.replace(/(<br\s*\/?>\s*){3,}/gi,'<br><br>');
}
function imagesHtml(urls) {
  return `<div class="card-images${urls.length===1?' one':''}">`+urls.map(u=>`<img src="${pimg(u)}" data-action="zoom" loading="lazy" decoding="async" alt="帖子配图">`).join('')+'</div>';
}
function cardHtml(p, expanded=false) {
  posts.set(Number(p.id),p);
  const canExpand=p.needs_fulltext||plainText(p.content_html).length>400;
  const collapse=canExpand&&!expanded;
  const count=(p.images||[]).length;
  const content=tidyHtml(p.content_html,p.platform);
  return `<article class="card" data-id="${Number(p.id)}">
    <div class="card-head">${avatarHtml(p,34)}<span class="badge ${esc(p.platform)}">${esc(PLATFORM_NAMES[p.platform]||p.platform)}</span>
    <span class="author" data-action="author" data-key="${esc(p.platform+':'+p.author_id)}">${esc(displayName(p))}</span>
    <span class="time">${esc(fmtTime(p.published_at))}</span><button class="del-btn" data-action="delete" title="删除帖子">×</button></div>
    ${p.title?`<div class="card-title">${esc(p.title)}</div>`:''}
    <div class="collapsible ${collapse?'collapsed':''}"><div class="card-body">${content||'（正文暂未抓取，可查看原文）'}</div><div class="fade"></div></div>
    <div class="img-slot">${collapse?'':imagesHtml(p.images||[])}</div>
    ${canExpand?`<button class="expand-btn" data-action="expand">${collapse?'展开全文 ▾':'收起 ▴'}</button>`:''}
    ${p.needs_fulltext?'<span class="preparing-note">全文后台准备中</span>':''}
    <div class="fulltext-error hint" role="status"></div>
    <div class="card-foot">${p.url?`<a href="${esc(p.url)}" target="_blank" rel="noopener noreferrer">查看原文 ↗</a>`:''}</div></article>`;
}
async function fetchFulltext(card) {
  if(card.dataset.loading==='1')return;
  card.dataset.loading='1'; const message=card.querySelector('.fulltext-error');
  const button=card.querySelector('.expand-btn');if(button){button.disabled=true;button.textContent='正在展开…';}
  message.textContent='';
  try {
    const data=await api(`/api/posts/${card.dataset.id}/fulltext`,{method:'POST',body:'{}'});
    card.outerHTML=cardHtml(data.post,true);
  } catch(e) { message.textContent=e.message+'。可稍后重试或查看原文。';const note=card.querySelector('.preparing-note');if(note)note.textContent='全文暂未补齐'; }
  finally { delete card.dataset.loading;if(button?.isConnected){button.disabled=false;button.textContent='展开全文 ▾';} }
}
$('feed').addEventListener('click',async e=>{
  const card=e.target.closest('.card'); if(!card)return;
  const p=posts.get(Number(card.dataset.id));
  const a=e.target.closest('.card-body a');
  if(p?.platform==='weibo'&&a&&a.textContent.includes('全文')) {e.preventDefault();await fetchFulltext(card);return;}
  const target=e.target.closest('[data-action]'); if(!target)return;
  const action=target.dataset.action;
  if(action==='expand'&&p.needs_fulltext){await fetchFulltext(card);return;}
  if(action==='expand') {
    const wrap=card.querySelector('.collapsible'); const collapsed=wrap.classList.toggle('collapsed');
    target.textContent=collapsed?'展开全文 ▾':'收起 ▴';
    if(!collapsed)card.querySelector('.img-slot').innerHTML=imagesHtml(p.images||[]);
  }
  if(action==='zoom')zoom(target.src);
  if(action==='author')filterAuthor(target.dataset.key);
  if(action==='delete'&&confirm('删除这条帖子？')) {
    try{await api(`/api/posts/${p.id}`,{method:'DELETE',body:'{}'});await loadPosts();toast('已删除');}catch(e){toast(e.message,'#b91c1c');}
  }
});
function zoom(url){$('lightbox').querySelector('img').src=url;$('lightbox').style.display='flex';}
$('lightbox').onclick=()=>$('lightbox').style.display='none';
function clearPending(){pendingNew.clear();$('newTip').style.display='none';}
function filterAuthor(key){currentAuthor=key;$('authorChip').style.display='inline-flex';$('authorChip').querySelector('span').textContent='正在看：'+(userProfiles[key]?.display_name||userProfiles[key]?.nickname||key.split(':')[1]);loadPosts().catch(showError);}
function clearAuthor(){currentAuthor='';$('authorChip').style.display='none';loadPosts().catch(showError);}
$('authorChip').onclick=clearAuthor;
function showError(e){if(e.name!=='AbortError')toast(e.message,'#b91c1c');}
async function loadPosts(offset=0,replace=true) {
  if(replace){feedAbort?.abort();feedAbort=new AbortController();++loadVersion;clearPending();}
  const version=loadVersion;
  const params=new URLSearchParams({platform:currentPlatform,q:currentKeyword,author:currentAuthor,offset:String(offset)});
  $('loadMore').disabled=true;
  try{
    const data=await api('/api/posts?'+params,{signal:feedAbort?.signal});
    if(version!==loadVersion)return;
    if(replace){$('feed').querySelectorAll('.card').forEach(e=>e.remove());posts.clear();loadedCount=0;}
    for(const p of data.posts)if(!posts.has(Number(p.id)))$('feed').insertAdjacentHTML('beforeend',cardHtml(p));
    loadedCount+=data.posts.length;
    $('empty').style.display=posts.size?'none':'block';
    $('loadMore').style.display=data.posts.length>=30?'block':'none';
  }finally{if(version===loadVersion)$('loadMore').disabled=false;}
}
function loadMore(){loadPosts(loadedCount,false).catch(showError);}
function matches(p){return (!currentPlatform||p.platform===currentPlatform)&&(!currentAuthor||`${p.platform}:${p.author_id}`===currentAuthor)&&(!currentKeyword||[plainText(p.content_html),p.title,p.author_name].some(x=>(x||'').toLowerCase().includes(currentKeyword.toLowerCase())));}
function applyPreparedPost(p){
  const card=document.querySelector(`.card[data-id="${Number(p.id)}"]`);
  if(card&&!card.dataset.loading){const expanded=!card.querySelector('.collapsible')?.classList.contains('collapsed');card.outerHTML=cardHtml(p,expanded);}
  if(pendingNew.has(p.id))pendingNew.set(p.id,p);
}
const notifiedPosts=new Set();
function handleNewPost(p){
  const id=Number(p.id);if(!Number.isSafeInteger(id)||id<=0)return;
  if(!posts.has(id)&&!notifiedPosts.has(id)){
    notifiedPosts.add(id);if(notifiedPosts.size>2000)notifiedPosts.delete(notifiedPosts.values().next().value);
    notify(p);
  }
  if(matches(p)&&!posts.has(id)&&!pendingNew.has(id)){
    pendingNew.set(id,p);$('newTip').style.display='block';$('newTip').querySelector('span').textContent=`有 ${pendingNew.size} 条新帖，点击刷新`;
  }
}
function connectStream(){
  const es=new EventSource('/api/stream');
  es.addEventListener('post_updated',ev=>{try{applyPreparedPost(JSON.parse(ev.data));}catch(e){}});
  es.onmessage=ev=>{try{const event=JSON.parse(ev.data);if(event.event==='post_updated'){applyPreparedPost(event.post);return;}handleNewPost(event);}catch(e){/* Ignore malformed event. */}};
  // EventSource handles reconnection; no parallel manual reconnect loops.
}
$('newTip').onclick=()=>{loadPosts().catch(showError);window.scrollTo({top:0,behavior:'smooth'});};
function updateNotificationStatus(message=''){
  const supported='Notification'in window&&window.isSecureContext;
  const permission=supported?Notification.permission:'unsupported';
  const labels={granted:'已允许浏览器通知；是否弹出还取决于 Windows 设置',denied:'通知已被阻止，请从地址栏的网站权限中改为允许',default:'尚未授权，请点击开启通知并选择允许',unsupported:'当前浏览器或地址不支持通知，请用 Edge 打开 http://127.0.0.1:8000'};
  $('notificationStatus').textContent=message||labels[permission];
  $('notifyButton').disabled=!supported||permission==='granted';
  $('testNotifyButton').disabled=permission!=='granted';
}
function sendBrowserNotification(title,body,tag){
  if(!('Notification'in window)||Notification.permission!=='granted')return false;
  try{
    const n=new Notification(title,{body,tag});
    n.onerror=()=>updateNotificationStatus('浏览器未能显示通知，请检查 Windows 中该浏览器的通知权限及勿扰模式');
    n.onclick=()=>{window.focus();n.close();};return true;
  }catch(e){updateNotificationStatus('通知发送失败，请检查网站和 Windows 通知权限');return false;}
}
function notify(p){sendBrowserNotification(`${PLATFORM_NAMES[p.platform]} · ${displayName(p)}`,p.title||plainText(p.content_html).slice(0,80),'zhijian-post-'+p.id);}
function updateCountdown(){
  const el=$('pollCountdown');if(!el||!scheduleState)return;
  const st=scheduleState;
  if(st.phase==='polling'){el.textContent='正在检查'+(PLATFORM_NAMES[st.current_platform]?' · '+PLATFORM_NAMES[st.current_platform]:'');return;}
  if(st.next_run_at){const seconds=Math.max(0,Math.ceil(st.next_run_at-(Date.now()/1000+st.clockOffset)));el.textContent=seconds?'下轮 '+seconds+' 秒':'即将检查';}
  else el.textContent='等待首次检查';
}
async function refreshStatus(){
  const data=await api('/api/status'); let html=`<span>📦 已存 ${data.total} 条</span>`,alerts=[];
  const labels={ok:'正常',disabled:'未启用',pending:'等待抓取',auth_error:'登录失效',rate_limited:'限流中',error:'异常',partial_error:'部分异常'};
  for(const [key,st]of Object.entries(data.status)){
    const color=st.state==='ok'?'':st.state==='disabled'||st.state==='pending'?'gray':st.state==='rate_limited'?'warn':'err';
    html+=`<span class="st" title="${esc(st.message)}"><span class="dot ${color}"></span>${esc(PLATFORM_NAMES[key])}${esc(labels[st.state]||st.state)}${st.last_run?` <time>${esc(st.last_run)}</time>`:''}</span>`;
    for(const u of st.users||[])if(u.state==='auth_error')alerts.push(`${PLATFORM_NAMES[key]}：登录已失效，请重新连接`);
  }
  if(data.scheduler){scheduleState={...data.scheduler,clockOffset:data.scheduler.server_time-Date.now()/1000};html+=`<span class="poll-status">轮次间隔 ${data.scheduler.interval_sec} 秒 · <span id="pollCountdown"></span></span>`;}
  $('statusBar').innerHTML=html;updateCountdown();$('banner').textContent=[...new Set(alerts)].join('；');$('banner').classList.toggle('show',alerts.length>0);
  if(drawerOpen){$('userStatus').innerHTML=Object.entries(data.status).flatMap(([k,s])=>(s.users||[]).map(u=>`<div class="user-status-row"><span>${esc(PLATFORM_NAMES[k])} · ${esc(u.nickname||u.user_id)}</span><span>${esc(labels[u.state]||u.state)} ${esc(u.last_run||'')}</span><small>${esc(u.message||'')}</small></div>`)).join('');}
}
setInterval(updateCountdown,1000);
async function openSettings(){
  if(drawerOpen)return;drawerOpen=true;settingsDirty=false;++discoveryVersion;$('discoverButton').disabled=false;document.querySelector('.manual-credentials').open=false;$('userSearch').value='';$('candidateResults').textContent='';$('addMonitor').open=false;$('drawer').scrollTop=0;$('drawer').classList.add('open');$('drawerMask').classList.add('open');
  try{
    const cfg=await loadProfiles();settingsCache=structuredClone(cfg);dirtySecrets.clear();
    for(const [key,id]of Object.entries(CREDENTIAL_IDS)){$(id).value='';$(id).placeholder=cfg[key]?'已保存；留空保持不变':'尚未配置';}
    $('interval').value=cfg.poll_interval_sec;renderUserList(cfg.users||[]);switchSettingsTab(cfg.users?.length?'monitors':'accounts');updateDiscoveryMode();await refreshConnections();await refreshStatus();
  }catch(e){showError(e);}
}
function closeSettings(){if(settingsDirty&&!confirm('有未保存的列表或偏好修改，放弃这些修改并关闭？'))return;settingsDirty=false;drawerOpen=false;++discoveryVersion;$('discoverButton').disabled=false;$('drawer').classList.remove('open');$('drawerMask').classList.remove('open');}
for(const [key,id]of Object.entries(CREDENTIAL_IDS))$(id).addEventListener('input',()=>{dirtySecrets.add(key);markSettingsDirty();});
function renderUserList(users){
  const filter=$('userSearch').value.trim().toLowerCase();$('userCount').textContent=users.length+' 个';
  const visible=users.map((u,i)=>({u,i})).filter(({u})=>[u.display_name,u.nickname,u.user_id,PLATFORM_NAMES[u.platform]].some(x=>(x||'').toLowerCase().includes(filter)));
  $('userList').innerHTML=visible.map(({u,i})=>`<div class="user-row ${u.enabled===false?'paused':''}">${avatarHtml(u,36)}<div class="user-info"><b title="${esc(u.user_id)}">${esc(u.display_name||u.nickname||u.user_id)}</b><small><span class="platform-dot ${esc(u.platform)}"></span>${esc(PLATFORM_NAMES[u.platform])}${u.enabled===false?' · 已暂停':''}</small></div><button data-toggle-user="${i}" class="text-btn">${u.enabled===false?'启用':'暂停'}</button><details class="user-more"><summary aria-label="管理对象">⋯</summary><div><button data-refresh-user="${i}">更新资料</button><button data-rename-user="${i}">编辑显示名称</button><button class="danger" data-delete-user="${i}">移除监控</button></div></details></div>`).join('')||`<div class="empty-list">${filter?'没有匹配的监控对象':'还没有监控对象，点击添加开始。'}</div>`;
}
$('userSearch').oninput=()=>renderUserList(settingsCache.users||[]);
$('userList').onclick=async e=>{
  const del=e.target.closest('[data-delete-user]'),toggle=e.target.closest('[data-toggle-user]'),rename=e.target.closest('[data-rename-user]'),refresh=e.target.closest('[data-refresh-user]');
  if(del){const i=Number(del.dataset.deleteUser),u=settingsCache.users[i];if(!confirm('移除 '+(u.display_name||u.nickname||u.user_id)+' 的监控？已抓取帖子会保留。'))return;settingsCache.users.splice(i,1);}
  if(toggle){const u=settingsCache.users[Number(toggle.dataset.toggleUser)];u.enabled=u.enabled===false;}
  if(rename){const u=settingsCache.users[Number(rename.dataset.renameUser)];const value=prompt('设置显示名称（不修改平台账号）',u.display_name||u.nickname||'');if(value===null)return;if(!value.trim()||value.length>200)return toast('名称应为 1–200 个字符','#b91c1c');u.display_name=value.trim();}
  if(refresh){const u=settingsCache.users[Number(refresh.dataset.refreshUser)];refresh.disabled=true;try{const d=await api('/api/users/resolve',{method:'POST',body:JSON.stringify({platform:u.platform,user_id:u.user_id})});const current=settingsCache.users.find(x=>x.platform===u.platform&&x.user_id===u.user_id);if(current){current.nickname=d.nickname;current.avatar=d.avatar;}toast('资料已更新，保存后生效');}catch(e){showError(e);return;}finally{refresh.disabled=false;}}
  if(del||toggle||rename||refresh){markSettingsDirty();renderUserList(settingsCache.users);}
};
$('interval').oninput=markSettingsDirty;
$('openAdd').onclick=()=>{$('addMonitor').open=true;updateDiscoveryMode();$('newUid').focus();};
function updateDiscoveryMode(){
  const p=$('newPlatform').value;
  const hints={weibo:'输入昵称搜索，或直接粘贴微博主页链接。预览后选择正确的人。',taoguba:'直接粘贴博主主页链接，不用提取 UID。',zsxq:'从已加入的星球选择，或粘贴星球主页链接。',weread:'从微信读书书架选择公众号，无需查找 ID。'};
  $('discoveryHint').textContent=hints[p];$('newUid').placeholder={weibo:'昵称或微博主页链接',taoguba:'粘贴淘股吧博主主页链接',zsxq:'星球名称或主页链接',weread:'搜索书架上的公众号'}[p];
  $('browseTargets').hidden=!['zsxq','weread'].includes(p);$('browseTargets').textContent=p==='weread'?'读取微信读书书架':'读取已加入的星球';
  $('openPlatform').href={weibo:'https://m.weibo.cn/',taoguba:'https://www.tgb.cn/',zsxq:'https://wx.zsxq.com/',weread:'https://weread.qq.com/web/shelf'}[p];
}
$('newPlatform').onchange=()=>{++discoveryVersion;$('discoverButton').disabled=false;candidates=[];$('candidateResults').textContent='';updateDiscoveryMode();};
async function discoverTargets(list=false){
  const query=list?'':$('newUid').value.trim(),platform=$('newPlatform').value;
  if(!query&&!['zsxq','weread'].includes(platform))return toast('请输入昵称或主页链接','#b91c1c');
  const version=++discoveryVersion;$('discoverButton').disabled=true;$('candidateResults').textContent='正在查找…';
  try{const result=await api('/api/users/discover',{method:'POST',body:JSON.stringify({platform,query})});if(version!==discoveryVersion)return;$('newPlatform').value=result.platform;updateDiscoveryMode();candidates=result.candidates||[];renderCandidates();}
  catch(e){if(version===discoveryVersion)$('candidateResults').textContent=e.message;}
  finally{if(version===discoveryVersion)$('discoverButton').disabled=false;}
}
function renderCandidates(){
  $('candidateResults').innerHTML=candidates.map((u,i)=>{const added=settingsCache.users.some(x=>x.platform===u.platform&&x.user_id===u.user_id);return `<div class="candidate-row">${avatarHtml(u,34)}<div class="user-info"><b>${esc(u.nickname||u.user_id)}</b><small>${esc(u.description||PLATFORM_NAMES[u.platform])}</small><small>ID ${esc(u.user_id)}</small></div><button class="btn small ghost" data-candidate="${i}" ${added?'disabled':''}>${added?'已添加':'添加'}</button></div>`;}).join('')||'<p class="hint">未找到对象。可换一个昵称，或直接粘贴主页链接。</p>';
}
$('candidateResults').onclick=e=>{const b=e.target.closest('[data-candidate]');if(!b)return;const u=candidates[Number(b.dataset.candidate)];if(settingsCache.users.some(x=>x.platform===u.platform&&x.user_id===u.user_id))return;settingsCache.users.push({platform:u.platform,user_id:u.user_id,nickname:u.nickname||'',avatar:u.avatar||'',enabled:true});markSettingsDirty();renderUserList(settingsCache.users);renderCandidates();toast('已加入，保存后开始监控');};
$('discoverButton').onclick=()=>discoverTargets();$('browseTargets').onclick=()=>discoverTargets(true);$('newUid').onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();discoverTargets();}};
async function addUser(){
  const platform=$('newPlatform').value;let uid=$('newUid').value.trim(),nickname='',avatar='';if(!uid)return toast('请填写用户 ID 或主页链接','#b91c1c');
  try{const d=await api('/api/users/resolve',{method:'POST',body:JSON.stringify({platform,user_id:uid})});uid=d.user_id;nickname=d.nickname;avatar=d.avatar;}
  catch(e){if(!confirm(e.message+'。是否仍然添加？保存时会检查 ID 格式。'))return;}
  settingsCache.users??=[];if(settingsCache.users.some(u=>u.platform===platform&&u.user_id===uid))return toast('该对象已存在');
  settingsCache.users.push({platform,user_id:uid,nickname,avatar,enabled:true});markSettingsDirty();renderUserList(settingsCache.users);$('newUid').value='';toast('已加入列表，点击保存生效');
}
function syncShelf(){$('newPlatform').value='weread';updateDiscoveryMode();discoverTargets(true);}
function exportUsers(){const users=(settingsCache.users||[]).map(({platform,user_id,nickname,display_name,avatar,enabled})=>({platform,user_id,nickname,display_name,avatar,enabled}));const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify(users,null,2)],{type:'application/json'}));a.download='监控列表.json';a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000);}
function importUsers(input){
  const file=input.files[0];input.value='';if(!file)return;if(file.size>512000)return toast('列表文件过大','#b91c1c');
  file.text().then(text=>{
    const list=JSON.parse(text);if(!Array.isArray(list)||list.length>500)throw new Error('格式错误');
    const merged=structuredClone(settingsCache.users||[]);
    for(const u of list){
      if(!u||typeof u!=='object'||!PLATFORM_NAMES[u.platform]||!u.user_id)throw new Error('包含无效平台或 ID');
      if(merged.some(x=>x.platform===u.platform&&x.user_id===String(u.user_id)))continue;
      merged.push({platform:u.platform,user_id:String(u.user_id),nickname:String(u.nickname||''),display_name:String(u.display_name||''),avatar:String(u.avatar||''),enabled:u.enabled!==false});
    }
    if(merged.length>500)throw new Error('列表过大');
    settingsCache.users=merged;markSettingsDirty();renderUserList(merged);toast('已导入，保存后生效');
  }).catch(()=>toast('导入失败，请检查列表格式','#b91c1c'));
}
async function saveSettings(){
  const body={poll_interval_sec:Number($('interval').value),users:settingsCache.users||[]};
  for(const key of dirtySecrets)body[key]=$(CREDENTIAL_IDS[key]).value.trim();
  try{await api('/api/settings',{method:'PUT',body:JSON.stringify(body)});dirtySecrets.clear();settingsDirty=false;closeSettings();toast('已保存，监控即将使用新设置');await loadProfiles();await loadPosts();await refreshStatus();}
  catch(e){showError(e);}
}
const CONNECTION_LABELS={unconfigured:'未连接',pending:'凭据已保存',ok:'已连接',public:'免登录',auth_error:'需要重新登录',network_error:'网络异常',rate_limited:'稍后重试',error:'连接异常',starting:'正在打开窗口',waiting:'等待官方窗口确认',cancelled:'已取消',timeout:'登录超时'};
async function refreshConnections(){connections=await api('/api/connections');renderConnections();}
function renderConnections(){
  const signature=JSON.stringify(connections)+[...connectionBusy].join();if(signature===lastConnectionHTML)return;lastConnectionHTML=signature;
  const open=[...$('connections').querySelectorAll('details[open]')].map(d=>d.dataset.platform);
  $('connections').innerHTML=Object.entries(PLATFORM_NAMES).map(([key,name])=>{
    const st=connections[key]||{state:'unconfigured'},active=['starting','waiting'].includes(st.state),busy=connectionBusy.has(key);
    let primary=['pending','network_error','rate_limited'].includes(st.state)?'validate':'start';
    const label=active?'等待确认…':primary==='validate'?'验证连接':st.state==='auth_error'?'重新登录':'连接';
    const message=st.state==='ok'?'登录会话可用':st.state==='public'?'公开帖子无需配置账号':st.state==='pending'?'已保存本地凭据，可验证是否可用':st.state==='unconfigured'?'点击连接，在官方窗口扫码登录':st.message||'';
    return `<section class="connection-card state-${esc(st.state)}"><div class="connection-main"><span class="platform-icon ${key}">${name[0]}</span><div class="connection-info"><b>${name}</b><small>${esc(CONNECTION_LABELS[st.state]||st.state)}</small></div>${!['ok','public'].includes(st.state)?`<button class="btn small ${active?'ghost':''}" data-platform="${key}" data-connect-action="${primary}" ${active||busy?'disabled':''}>${busy?'处理中…':label}</button>`:''}${active?`<button class="text-btn" data-platform="${key}" data-connect-action="cancel">取消</button>`:`<details class="connection-more" data-platform="${key}" ${open.includes(key)?'open':''}><summary>管理</summary><div><button data-platform="${key}" data-connect-action="validate" ${busy?'disabled':''}>验证连接</button><button data-platform="${key}" data-connect-action="start" ${busy?'disabled':''}>${st.state==='public'?'可选登录':'重新登录'}</button><button class="danger" data-platform="${key}" data-connect-action="disconnect" ${busy?'disabled':''}>断开连接</button></div></details>`}</div><p class="connection-message">${esc(message)}</p></section>`;
  }).join('');
}
$('connections').onclick=async e=>{
  const b=e.target.closest('[data-connect-action]');if(!b)return;
  const platform=b.dataset.platform,action=b.dataset.connectAction;
  if(action==='disconnect'&&!confirm(`断开${PLATFORM_NAMES[platform]}并清除本地登录会话？监控列表和帖子会保留。`))return;
  if(connectionBusy.has(platform))return;connectionBusy.add(platform);renderConnections();
  try{const path='/api/connections/'+platform+(action==='disconnect'?'':'/'+action);const result=await api(path,{method:action==='disconnect'?'DELETE':'POST',body:'{}'});connections[platform]=result;renderConnections();if(action==='disconnect'){const key={weibo:'weibo_cookie',taoguba:'taoguba_cookie',zsxq:'zsxq_token',weread:'weread_cookie'}[platform];$(CREDENTIAL_IDS[key]).value='';$(CREDENTIAL_IDS[key]).placeholder='尚未配置';dirtySecrets.delete(key);}toast(result.message);}
  catch(e){showError(e);}finally{connectionBusy.delete(platform);renderConnections();}
};
document.querySelectorAll('.tab').forEach(t=>t.onclick=()=>{document.querySelectorAll('.tab').forEach(x=>x.classList.remove('active'));t.classList.add('active');currentPlatform=t.dataset.platform;loadPosts().catch(showError);});
let searchTimer;$('searchBox').oninput=e=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>{currentKeyword=e.target.value.trim();loadPosts().catch(showError);},350);};
function applyTheme(t){document.documentElement.dataset.theme=t;localStorage.setItem('pm-theme',t);$('themeSelect').value=t;}
$('themeSelect').onchange=e=>applyTheme(e.target.value);applyTheme(localStorage.getItem('pm-theme')||'dark');
$('notifyButton').onclick=async()=>{try{if('Notification'in window)await Notification.requestPermission();updateNotificationStatus();}catch(e){updateNotificationStatus('无法请求通知权限，请检查地址栏的网站设置');}};
$('testNotifyButton').onclick=()=>{if(sendBrowserNotification('知见 · 测试通知','看到此消息说明浏览器通知已送达。','zhijian-test'))updateNotificationStatus('测试通知已提交；请确认桌面或通知中心是否收到。未收到请检查 Windows 通知和勿扰模式');};
window.addEventListener('focus',()=>updateNotificationStatus());
if(navigator.permissions)navigator.permissions.query({name:'notifications'}).then(p=>{p.onchange=()=>updateNotificationStatus();}).catch(()=>{});
updateNotificationStatus();
(async()=>{try{await loadProfiles();await loadPosts();await refreshStatus();connectStream();}catch(e){showError(e);}setInterval(()=>refreshStatus().catch(()=>{}),5000);setInterval(()=>{if(drawerOpen)refreshConnections().catch(()=>{});},2000);})();
