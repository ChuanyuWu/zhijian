/* Local feed and connection wizard. Dynamic UI uses delegated events, not inline JS. */
const PLATFORM_NAMES = {weibo:'微博', taoguba:'淘股吧', zsxq:'知识星球', weread:'公众号'};
const CREDENTIAL_IDS = {weibo_cookie:'weiboCookie', taoguba_cookie:'taogubaCookie', zsxq_token:'zsxqToken', weread_cookie:'wereadCookie', access_password:'accessPassword'};
let currentPlatform='', currentKeyword='', currentAuthor='', loadedCount=0;
let settingsCache={users:[]}, userProfiles={}, pendingNew=new Map(), posts=new Map();
let loadVersion=0, feedAbort=null, drawerOpen=false, connections={};
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
  const name=p.author_name||profile.nickname||p.nickname||'?';
  return profile.avatar ? `<img class="avatar" width="${size}" height="${size}" src="${pimg(profile.avatar)}" alt="${esc(name[0])}">` : avatarPlaceholder(p.platform,name[0],size);
}
document.addEventListener('error',e=>{if(e.target.matches?.('img.avatar')) e.target.replaceWith(document.createTextNode(e.target.alt||'?'));},true);
async function loadProfiles() {
  const cfg=await api('/api/settings'); userProfiles={};
  for(const u of cfg.users||[]) userProfiles[`${u.platform}:${u.user_id}`]={nickname:u.nickname,avatar:u.avatar};
  return cfg;
}
function tidyHtml(html) {
  const d=document.createElement('div'); d.innerHTML=html||'';
  // Defense in depth; the server applies the authoritative HTML whitelist.
  d.querySelectorAll('script,style,iframe,object,embed,form,svg,math').forEach(e=>e.remove());
  d.querySelectorAll('*').forEach(e=>{for(const a of [...e.attributes]) if(a.name.startsWith('on')||a.name==='style')e.removeAttribute(a.name);});
  d.querySelectorAll('a').forEach(a=>{if(!/^https?:\/\//i.test(a.getAttribute('href')||'')) a.removeAttribute('href');});
  d.querySelectorAll('div,font,span,p').forEach(e=>{if(!e.textContent.trim()&&!e.querySelector('img'))e.remove();});
  return d.innerHTML.replace(/(<br\s*\/?>\s*){3,}/gi,'<br><br>');
}
function imagesHtml(urls) {
  return `<div class="card-images${urls.length===1?' one':''}">`+urls.map(u=>`<img src="${pimg(u)}" data-action="zoom" loading="lazy" decoding="async" alt="帖子配图">`).join('')+'</div>';
}
function cardHtml(p, expanded=false) {
  posts.set(Number(p.id),p);
  const collapse=plainText(p.content_html).length>400 && !expanded;
  const count=(p.images||[]).length;
  const content=tidyHtml(p.content_html);
  return `<article class="card" data-id="${Number(p.id)}">
    <div class="card-head">${avatarHtml(p,34)}<span class="badge ${esc(p.platform)}">${esc(PLATFORM_NAMES[p.platform]||p.platform)}</span>
    <span class="author" data-action="author" data-key="${esc(p.platform+':'+p.author_id)}">${esc(p.author_name||p.author_id)}</span>
    <span class="time">${esc(fmtTime(p.published_at))}</span><button class="del-btn" data-action="delete" title="删除帖子">×</button></div>
    ${p.title?`<div class="card-title">${esc(p.title)}</div>`:''}
    <div class="collapsible ${collapse?'collapsed':''}"><div class="card-body">${content||'（正文暂未抓取，可查看原文）'}</div><div class="fade"></div></div>
    <div class="img-slot">${collapse?'':imagesHtml(p.images||[])}</div>
    ${plainText(p.content_html).length>400?`<button class="expand-btn" data-action="expand">${collapse?'展开已获取正文 ▾':'收起 ▴'}</button>`:''}
    ${p.needs_fulltext?'<button class="expand-btn" data-action="fulltext">获取全文</button>':''}
    <div class="fulltext-error hint" role="status"></div>
    <div class="card-foot">${p.url?`<a href="${esc(p.url)}" target="_blank" rel="noopener noreferrer">查看原文 ↗</a>`:''}</div></article>`;
}
async function fetchFulltext(card) {
  if(card.dataset.loading==='1')return;
  card.dataset.loading='1'; const message=card.querySelector('.fulltext-error');
  message.textContent='正在获取全文…';
  try {
    const data=await api(`/api/posts/${card.dataset.id}/fulltext`,{method:'POST',body:'{}'});
    card.outerHTML=cardHtml(data.post,true);
  } catch(e) { message.textContent=e.message+'。可以再次点击全文重试，或查看原文。'; }
  finally { delete card.dataset.loading; }
}
$('feed').addEventListener('click',async e=>{
  const card=e.target.closest('.card'); if(!card)return;
  const p=posts.get(Number(card.dataset.id));
  const a=e.target.closest('.card-body a');
  if(p?.platform==='weibo'&&a&&a.textContent.includes('全文')) {e.preventDefault();await fetchFulltext(card);return;}
  const target=e.target.closest('[data-action]'); if(!target)return;
  const action=target.dataset.action;
  if(action==='fulltext')await fetchFulltext(card);
  if(action==='expand') {
    const wrap=card.querySelector('.collapsible'); const collapsed=wrap.classList.toggle('collapsed');
    target.textContent=collapsed?'展开已获取正文 ▾':'收起 ▴';
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
function filterAuthor(key){currentAuthor=key;$('authorChip').style.display='inline-flex';$('authorChip').querySelector('span').textContent='正在看：'+(userProfiles[key]?.nickname||key.split(':')[1]);loadPosts().catch(showError);}
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
function connectStream(){
  const es=new EventSource('/api/stream');
  es.onmessage=ev=>{try{const p=JSON.parse(ev.data);if(matches(p)&&!posts.has(Number(p.id))&&!pendingNew.has(p.id)){pendingNew.set(p.id,p);$('newTip').style.display='block';$('newTip').querySelector('span').textContent=`有 ${pendingNew.size} 条新帖，点击刷新`;notify(p);}}catch(e){/* Ignore malformed event. */}};
  // EventSource handles reconnection; no parallel manual reconnect loops.
}
$('newTip').onclick=()=>{loadPosts().catch(showError);window.scrollTo({top:0,behavior:'smooth'});};
function notify(p){if('Notification'in window&&Notification.permission==='granted')new Notification(`${PLATFORM_NAMES[p.platform]} · ${p.author_name||''}`,{body:p.title||plainText(p.content_html).slice(0,80)});}
async function refreshStatus(){
  const data=await api('/api/status'); let html=`📦 已存 ${data.total} 条`,alerts=[];
  const labels={ok:'正常',disabled:'未启用',pending:'等待抓取',auth_error:'登录失效',rate_limited:'限流中',error:'异常',partial_error:'部分异常'};
  for(const [key,st]of Object.entries(data.status)){
    const color=st.state==='ok'?'':st.state==='disabled'||st.state==='pending'?'gray':st.state==='rate_limited'?'warn':'err';
    html+=` <span class="st" title="${esc(st.message)}"><span class="dot ${color}"></span>${esc(PLATFORM_NAMES[key])}${esc(labels[st.state]||st.state)}</span>`;
    for(const u of st.users||[])if(u.state==='auth_error')alerts.push(`${PLATFORM_NAMES[key]}：登录已失效，请重新连接`);
  }
  $('statusBar').innerHTML=html;$('banner').textContent=[...new Set(alerts)].join('；');$('banner').classList.toggle('show',alerts.length>0);
  if(drawerOpen){$('userStatus').innerHTML=Object.entries(data.status).flatMap(([k,s])=>(s.users||[]).map(u=>`<div>${esc(PLATFORM_NAMES[k])} · ${esc(u.nickname||u.user_id)}：${esc(labels[u.state]||u.state)} ${esc(u.message||'')}</div>`)).join('');}
}
async function openSettings(){
  drawerOpen=true;$('drawer').scrollTop=0;$('drawer').classList.add('open');$('drawerMask').classList.add('open');
  try{
    const cfg=await loadProfiles();settingsCache=structuredClone(cfg);dirtySecrets.clear();
    for(const [key,id]of Object.entries(CREDENTIAL_IDS)){$(id).value='';$(id).placeholder=cfg[key]?'已保存；留空保持不变':'尚未配置';}
    $('interval').value=cfg.poll_interval_sec;renderUserList(cfg.users||[]);await refreshConnections();await refreshStatus();
  }catch(e){showError(e);}
}
function closeSettings(){drawerOpen=false;$('drawer').classList.remove('open');$('drawerMask').classList.remove('open');}
for(const [key,id]of Object.entries(CREDENTIAL_IDS))$(id).addEventListener('input',()=>dirtySecrets.add(key));
function renderUserList(users){$('userList').innerHTML=users.map((u,i)=>`<div class="user-row"><span class="badge ${esc(u.platform)}">${esc(PLATFORM_NAMES[u.platform])}</span><span class="grow">${esc(u.nickname||u.user_id)} <small>${esc(u.user_id)}</small></span><button data-toggle-user="${i}" class="btn ghost">${u.enabled===false?'启用':'暂停'}</button><button class="del" data-delete-user="${i}">×</button></div>`).join('')||'<div class="hint">连接平台后，在下方添加监控对象。</div>';}
$('userList').onclick=e=>{const del=e.target.closest('[data-delete-user]'),toggle=e.target.closest('[data-toggle-user]');if(del)settingsCache.users.splice(Number(del.dataset.deleteUser),1);if(toggle){const u=settingsCache.users[Number(toggle.dataset.toggleUser)];u.enabled=u.enabled===false;}renderUserList(settingsCache.users);};
async function addUser(){
  const platform=$('newPlatform').value;let uid=$('newUid').value.trim(),nickname='',avatar='';if(!uid)return toast('请填写用户 ID 或主页链接','#b91c1c');
  try{const d=await api('/api/users/resolve',{method:'POST',body:JSON.stringify({platform,user_id:uid})});uid=d.user_id;nickname=d.nickname;avatar=d.avatar;}
  catch(e){if(!confirm(e.message+'。是否仍然添加？保存时会检查 ID 格式。'))return;}
  settingsCache.users??=[];if(settingsCache.users.some(u=>u.platform===platform&&u.user_id===uid))return toast('该对象已存在');
  settingsCache.users.push({platform,user_id:uid,nickname,avatar,enabled:true});renderUserList(settingsCache.users);$('newUid').value='';toast('已加入列表，点击保存生效');
}
async function syncShelf(){
  $('shelfList').textContent='正在读取书架…';
  try{const d=await api('/api/weread/shelf');$('shelfList').dataset.accs=JSON.stringify(d.accounts||[]);$('shelfList').innerHTML=(d.accounts||[]).map((a,i)=>`<div class="user-row"><span class="grow">${esc(a.nickname)}</span><button class="btn ghost" data-shelf="${i}">${settingsCache.users.some(u=>u.platform==='weread'&&u.user_id===a.user_id)?'已添加':'＋ 添加'}</button></div>`).join('')||'<div class="hint">书架没有公众号，请先在手机微信读书 App 订阅。</div>';}
  catch(e){$('shelfList').textContent=e.message;}
}
function addShelfUser(i){const a=JSON.parse($('shelfList').dataset.accs||'[]')[i];if(a&&!settingsCache.users.some(u=>u.platform==='weread'&&u.user_id===a.user_id)){settingsCache.users.push({...a,platform:'weread',enabled:true});renderUserList(settingsCache.users);toast('已加入，点击保存生效');}}
$('shelfList').onclick=e=>{const b=e.target.closest('[data-shelf]');if(b)addShelfUser(Number(b.dataset.shelf));};
function exportUsers(){const users=(settingsCache.users||[]).map(({platform,user_id,nickname,avatar,enabled})=>({platform,user_id,nickname,avatar,enabled}));const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify(users,null,2)],{type:'application/json'}));a.download='监控列表.json';a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000);}
function importUsers(input){
  const file=input.files[0];input.value='';if(!file)return;if(file.size>512000)return toast('列表文件过大','#b91c1c');
  file.text().then(text=>{const list=JSON.parse(text);if(!Array.isArray(list))throw new Error('格式错误');for(const u of list){if(!PLATFORM_NAMES[u.platform]||!u.user_id)throw new Error('包含无效平台或 ID');if(!settingsCache.users.some(x=>x.platform===u.platform&&x.user_id===String(u.user_id)))settingsCache.users.push({platform:u.platform,user_id:String(u.user_id),nickname:String(u.nickname||''),avatar:String(u.avatar||''),enabled:u.enabled!==false});}renderUserList(settingsCache.users);toast('已导入，点击保存生效');}).catch(()=>toast('导入失败，请检查列表格式','#b91c1c'));
}
async function saveSettings(){
  const body={poll_interval_sec:Number($('interval').value),users:settingsCache.users||[]};
  for(const key of dirtySecrets)body[key]=$(CREDENTIAL_IDS[key]).value.trim();
  try{await api('/api/settings',{method:'PUT',body:JSON.stringify(body)});dirtySecrets.clear();closeSettings();toast('已保存，监控即将使用新设置');await loadProfiles();await refreshStatus();}
  catch(e){showError(e);}
}
const CONNECTION_LABELS={unconfigured:'未连接',pending:'待验证',ok:'已连接',public:'公开内容免登录',auth_error:'需重新登录',network_error:'网络异常',rate_limited:'请求受限',error:'连接异常',starting:'正在启动',waiting:'等待登录',cancelled:'已取消',timeout:'登录超时'};
async function refreshConnections(){connections=await api('/api/connections');renderConnections();}
function renderConnections(){
  $('connections').innerHTML=Object.entries(PLATFORM_NAMES).map(([key,name])=>{const st=connections[key]||{state:'unconfigured'},active=['starting','waiting'].includes(st.state);return `<section class="connection-card"><b>${esc(name)}</b><span class="connection-state">${esc(CONNECTION_LABELS[st.state]||st.state)}</span><div class="hint">${esc(st.message||'')}</div><div class="connection-actions"><button class="btn ghost" data-platform="${key}" data-connect-action="start" ${active?'disabled':''}>${st.state==='ok'?'重新登录':'连接账号'}</button><button class="btn ghost" data-platform="${key}" data-connect-action="validate" ${active?'disabled':''}>验证连接</button><button class="btn ghost" data-platform="${key}" data-connect-action="${active?'cancel':'disconnect'}">${active?'取消登录':'断开连接'}</button></div></section>`;}).join('');
}
$('connections').onclick=async e=>{
  const b=e.target.closest('[data-connect-action]');if(!b)return;
  const platform=b.dataset.platform,action=b.dataset.connectAction;
  if(action==='disconnect'&&!confirm(`断开${PLATFORM_NAMES[platform]}并清除本地登录会话？监控列表和帖子会保留。`))return;
  b.disabled=true;
  try{const path='/api/connections/'+platform+(action==='disconnect'?'':'/'+action);const result=await api(path,{method:action==='disconnect'?'DELETE':'POST',body:'{}'});connections[platform]=result;renderConnections();if(action==='disconnect'){const key={weibo:'weibo_cookie',taoguba:'taoguba_cookie',zsxq:'zsxq_token',weread:'weread_cookie'}[platform];$(CREDENTIAL_IDS[key]).value='';$(CREDENTIAL_IDS[key]).placeholder='尚未配置';dirtySecrets.delete(key);}toast(result.message);}
  catch(e){showError(e);}finally{b.disabled=false;}
};
document.querySelectorAll('.tab').forEach(t=>t.onclick=()=>{document.querySelectorAll('.tab').forEach(x=>x.classList.remove('active'));t.classList.add('active');currentPlatform=t.dataset.platform;loadPosts().catch(showError);});
let searchTimer;$('searchBox').oninput=e=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>{currentKeyword=e.target.value.trim();loadPosts().catch(showError);},350);};
function applyTheme(t){document.documentElement.dataset.theme=t;localStorage.setItem('pm-theme',t);$('themeSelect').value=t;}
$('themeSelect').onchange=e=>applyTheme(e.target.value);applyTheme(localStorage.getItem('pm-theme')||'dark');
$('notifyButton').onclick=async()=>{if('Notification'in window)toast('通知权限：'+await Notification.requestPermission());};
(async()=>{try{await loadProfiles();await loadPosts();await refreshStatus();connectStream();}catch(e){showError(e);}setInterval(()=>refreshStatus().catch(()=>{}),30000);setInterval(()=>{if(drawerOpen)refreshConnections().catch(()=>{});},2000);})();
