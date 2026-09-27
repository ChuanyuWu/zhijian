/* 帖子监控 前端逻辑：时间流 + SSE 实时推送 + 设置管理 */
const PLATFORM_NAMES = { weibo: "微博", taoguba: "淘股吧", zsxq: "知识星球", weread: "公众号" };

let currentPlatform = "";
let currentKeyword = "";
let currentAuthor = "";          // "platform:user_id"，空 = 不按人筛选
let newestIds = new Set();
let pendingNew = [];
let userProfiles = {};           // "platform:user_id" -> {nickname, avatar}
let loadedCount = 0;

/* ---------- 工具 ---------- */
function fmtTime(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  const now = new Date();
  const sameDay = d.toDateString() === now.toDateString();
  const pad = (n) => String(n).padStart(2, "0");
  const hm = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  if (sameDay) return hm;
  return `${d.getMonth() + 1}-${pad(d.getDate())} ${hm}`;
}

function esc(s) {
  const div = document.createElement("div");
  div.textContent = s || "";
  return div.innerHTML;
}

function toast(msg, color) {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.style.background = color || "#238636";
  t.style.display = "block";
  setTimeout(() => (t.style.display = "none"), 2200);
}

/* 图片统一走后端代理，绕开平台防盗链 */
function pimg(url) {
  return "/api/img?url=" + encodeURIComponent(url);
}

/* ---------- 头像 ---------- */
function avatarHtml(p, size) {
  const key = `${p.platform}:${p.author_id || p.user_id}`;
  const prof = userProfiles[key] || {};
  const name = p.author_name || prof.nickname || p.nickname || "?";
  if (prof.avatar) {
    return `<img class="avatar" style="width:${size}px;height:${size}px"
      src="${pimg(prof.avatar)}" referrerpolicy="no-referrer"
      onerror="this.outerHTML=avatarPlaceholder('${p.platform}','${esc(name[0])}',${size})">`;
  }
  return avatarPlaceholder(p.platform, esc(name[0]), size);
}

function avatarPlaceholder(platform, ch, size) {
  return `<span class="avatar avatar-ph ${platform}" style="width:${size}px;height:${size}px;font-size:${Math.round(size * 0.45)}px">${ch}</span>`;
}

async function loadProfiles() {
  try {
    const r = await fetch("/api/settings");
    const cfg = await r.json();
    userProfiles = {};
    for (const u of cfg.users || []) {
      userProfiles[`${u.platform}:${u.user_id}`] = { nickname: u.nickname, avatar: u.avatar };
    }
  } catch (e) {}
}

/* ---------- 渲染 ---------- */
const COLLAPSE_LEN = 400;   // 正文超过此字数默认折叠
let cardSeq = 0;
const postCache = {};

function plainText(html) {
  const d = document.createElement("div");
  d.innerHTML = html || "";
  return (d.textContent || "").trim();
}

/* 显示前整理 HTML（DOM 级清洗，新旧数据都受益）：
   - 移除无文本无图的空元素
   - 去掉块级元素开头的 <br>（淘股吧正文每段自带前置换行，会双倍行距）
   - 连续 3 个以上 <br> 压成两个 */
function tidyHtml(html) {
  const d = document.createElement("div");
  d.innerHTML = html || "";
  d.querySelectorAll("div,font,span,p").forEach((el) => {
    if (!el.textContent.trim() && !el.querySelector("img")) el.remove();
  });
  d.querySelectorAll("div,font,p,span").forEach((el) => {
    while (el.firstChild && el.firstChild.nodeName === "BR") el.firstChild.remove();
  });
  return d.innerHTML
    .replace(/(<br\s*\/?>\s*){3,}/gi, "<br><br>")
    .replace(/^\s*(<br\s*\/?>\s*)+/i, "");
}

function imagesHtml(urls) {
  const cls = urls.length === 1 ? "card-images one" : "card-images";
  return `<div class="${cls}">` + urls.map((u) =>
    `<img src="${pimg(u)}" loading="lazy" decoding="async" onclick="zoom('${pimg(u)}')">`
  ).join("") + "</div>";
}

function toggleExpand(btn, uid) {
  const card = btn.closest(".card");
  const wrap = card.querySelector(".collapsible");
  const p = postCache[uid];
  const imgCount = p && p.images ? p.images.length : 0;
  if (wrap.classList.contains("collapsed")) {
    wrap.classList.remove("collapsed");
    // 展开时才渲染图片，避免折叠状态就发起大量图片请求
    const slot = card.querySelector(".img-slot");
    if (slot && imgCount) slot.outerHTML = imagesHtml(p.images);
    btn.textContent = "收起 ▴";
  } else {
    wrap.classList.add("collapsed");
    btn.textContent = `展开全文${imgCount ? " · 附图 " + imgCount + " 张" : ""} ▾`;
  }
}

function cardHtml(p) {
  const uid = ++cardSeq;
  postCache[uid] = p;
  const badge = `<span class="badge ${p.platform}">${PLATFORM_NAMES[p.platform] || p.platform}</span>`;
  const title = p.title ? `<div class="card-title">${esc(p.title)}</div>` : "";
  const text = plainText(p.content_html);
  const needCollapse = text.length > COLLAPSE_LEN;
  const imgCount = (p.images || []).length;

  let body = "";
  if (p.content_html) {
    const content = tidyHtml(p.content_html);
    body = needCollapse
      ? `<div class="collapsible collapsed"><div class="card-body">${content}</div><div class="fade"></div></div>`
      : `<div class="card-body">${content}</div>`;
  } else if (!p.title) {
    body = `<div class="card-body" style="color:var(--text-dim)">（正文暂未抓取，点原文查看）</div>`;
  }

  let imgs = "";
  if (imgCount) {
    // 折叠的帖子不渲染图片，点开才加载
    imgs = needCollapse ? `<div class="img-slot"></div>` : imagesHtml(p.images);
  }
  const expandBtn = needCollapse
    ? `<button class="expand-btn" onclick="toggleExpand(this, ${uid})">展开全文${imgCount ? " · 附图 " + imgCount + " 张" : ""} ▾</button>`
    : "";

  const link = p.url ? `<a href="${esc(p.url)}" target="_blank" rel="noopener">查看原文 ↗</a>` : "";
  const authorKey = `${p.platform}:${p.author_id}`;
  return `<div class="card" data-id="${p.id || ""}" data-uid="${uid}">
    <div class="card-head">${avatarHtml(p, 34)}${badge}
      <span class="author" onclick="filterAuthor('${authorKey}')" title="只看 TA 的帖子">${esc(p.author_name || p.author_id)}</span>
      <span class="time">${fmtTime(p.published_at)}</span>
      ${p.id ? `<button class="del-btn" onclick="delPost(${p.id}, this)" title="删除这条帖子">×</button>` : ""}
    </div>
    ${title}${body}${imgs}${expandBtn}
    <div class="card-foot">${link}</div>
  </div>`;
}

async function delPost(id, btn) {
  if (!confirm("删除这条帖子？（不影响其他帖子）")) return;
  const r = await fetch(`/api/posts/${id}`, { method: "DELETE" });
  if (r.ok) {
    btn.closest(".card").remove();
    toast("已删除");
  } else {
    toast("删除失败", "#b91c1c");
  }
}

function zoom(url) {
  const lb = document.getElementById("lightbox");
  lb.querySelector("img").src = url;
  lb.style.display = "flex";
}
document.getElementById("lightbox").onclick = function () { this.style.display = "none"; };

/* ---------- 按人筛选 ---------- */
function filterAuthor(key) {
  currentAuthor = key;
  const prof = userProfiles[key] || {};
  const name = prof.nickname || key.split(":")[1];
  const chip = document.getElementById("authorChip");
  chip.style.display = "inline-flex";
  chip.querySelector("span").textContent = `正在看：${name}`;
  loadPosts();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function clearAuthor() {
  currentAuthor = "";
  document.getElementById("authorChip").style.display = "none";
  loadPosts();
}
document.getElementById("authorChip").onclick = clearAuthor;

/* ---------- 数据加载 ---------- */
async function loadPosts(offset = 0, replace = true) {
  const params = new URLSearchParams();
  if (currentPlatform) params.set("platform", currentPlatform);
  if (currentKeyword) params.set("q", currentKeyword);
  if (currentAuthor) params.set("author", currentAuthor);
  if (offset) params.set("offset", offset);
  const r = await fetch("/api/posts?" + params);
  const data = await r.json();
  const feed = document.getElementById("feed");
  if (replace) {
    feed.querySelectorAll(".card").forEach((e) => e.remove());
    newestIds.clear();
    loadedCount = 0;
  }
  if (!data.posts.length && replace) {
    document.getElementById("empty").style.display = "block";
  } else {
    document.getElementById("empty").style.display = "none";
  }
  for (const p of data.posts) {
    feed.insertAdjacentHTML("beforeend", cardHtml(p));
    if (p.id) newestIds.add(p.id);
  }
  loadedCount += data.posts.length;
  document.getElementById("loadMore").style.display = data.posts.length >= 30 ? "block" : "none";
  return data;
}

function loadMore() {
  loadPosts(loadedCount, false);
}

/* ---------- SSE 实时推送 ---------- */
function connectStream() {
  const es = new EventSource("/api/stream");
  es.onmessage = (ev) => {
    const p = JSON.parse(ev.data);
    if (currentPlatform && p.platform !== currentPlatform) return;
    if (currentAuthor && `${p.platform}:${p.author_id}` !== currentAuthor) return;
    pendingNew.push(p);
    showNewTip();
    notify(p);
  };
  es.onerror = () => { es.close(); setTimeout(connectStream, 5000); };
}

function showNewTip() {
  const tip = document.getElementById("newTip");
  tip.style.display = "block";
  tip.querySelector("span").textContent = `有 ${pendingNew.length} 条新帖，点击刷新`;
}

document.getElementById("newTip").onclick = () => {
  const feed = document.getElementById("feed");
  for (const p of pendingNew) feed.insertAdjacentHTML("afterbegin", cardHtml(p));
  pendingNew = [];
  document.getElementById("newTip").style.display = "none";
  window.scrollTo({ top: 0, behavior: "smooth" });
};

/* ---------- 浏览器通知 ---------- */
async function notify(p) {
  if (!("Notification" in window)) return;
  if (Notification.permission !== "granted") return;
  const text = p.title || p.content_html.replace(/<[^>]+>/g, "").slice(0, 80);
  new Notification(`${PLATFORM_NAMES[p.platform]} · ${p.author_name || ""}`, { body: text });
}

async function ensureNotifyPermission() {
  if ("Notification" in window && Notification.permission === "default") {
    await Notification.requestPermission();
  }
}

/* ---------- 状态轮询 ---------- */
async function refreshStatus() {
  const r = await fetch("/api/status");
  const data = await r.json();
  const bar = document.getElementById("statusBar");
  const banner = document.getElementById("banner");
  let html = `📦 已存 ${data.total} 条`;
  let alertMsg = "";
  for (const [k, st] of Object.entries(data.status)) {
    let label = PLATFORM_NAMES[k];
    if (st.state === "ok") html += ` <span class="st"><span class="dot"></span>${label}正常 ${st.last_run || ""}</span>`;
    else if (st.state === "rate_limited") html += ` <span class="st"><span class="dot warn"></span>${label}限流中</span>`;
    else if (st.state === "auth_error") { html += ` <span class="st"><span class="dot err"></span>${label}凭证失效</span>`; alertMsg += `${label}：${st.message}　`; }
    else if (st.state === "error") html += ` <span class="st"><span class="dot err"></span>${label}异常</span>`;
    else html += ` <span class="st"><span class="dot gray"></span>${label}${st.state === "reserved" ? "预留" : "未启用"}</span>`;
  }
  bar.innerHTML = html;
  if (alertMsg) { banner.textContent = "⚠️ " + alertMsg + "请到设置页更新凭证"; banner.classList.add("show"); }
  else banner.classList.remove("show");
}

/* ---------- 设置抽屉 ---------- */
function openSettings() {
  document.getElementById("drawer").classList.add("open");
  document.getElementById("drawerMask").classList.add("open");
  loadSettings();
}
function closeSettings() {
  document.getElementById("drawer").classList.remove("open");
  document.getElementById("drawerMask").classList.remove("open");
}

let settingsCache = null;

async function loadSettings() {
  const r = await fetch("/api/settings");
  const cfg = await r.json();
  settingsCache = cfg;
  document.getElementById("weiboCookie").value = cfg.weibo_cookie;
  document.getElementById("taogubaCookie").value = cfg.taoguba_cookie;
  document.getElementById("zsxqToken").value = cfg.zsxq_token;
  document.getElementById("wereadCookie").value = cfg.weread_cookie || "";
  document.getElementById("accessPassword").value = cfg.access_password;
  document.getElementById("interval").value = cfg.poll_interval_sec;
  renderUserList(cfg.users || []);
}

function renderUserList(users) {
  const box = document.getElementById("userList");
  box.innerHTML = users.map((u, i) => `
    <div class="user-row">
      ${avatarHtml({ platform: u.platform, user_id: u.user_id, nickname: u.nickname }, 26)}
      <span class="badge ${u.platform}">${PLATFORM_NAMES[u.platform] || u.platform}</span>
      <span class="grow">${esc(u.nickname || u.user_id)} <small style="color:var(--text-dim)">${esc(u.user_id)}</small></span>
      <button class="del" onclick="delUser(${i})">✕</button>
    </div>`).join("") || `<div class="hint">还没有监控用户，在下方添加</div>`;
}

function delUser(i) {
  settingsCache.users.splice(i, 1);
  renderUserList(settingsCache.users);
}

async function addUser() {
  const platform = document.getElementById("newPlatform").value;
  let uid = document.getElementById("newUid").value.trim();
  if (!uid) return toast("请填写用户 ID", "#b91c1c");
  let nickname = "", avatar = "";
  try {
    const r = await fetch("/api/users/resolve", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ platform, user_id: uid }),
    });
    if (r.ok) {
      const d = await r.json();
      nickname = d.nickname; avatar = d.avatar;
      if (platform === "weread" && d.user_id) uid = d.user_id;
    }
  } catch (e) {}
  settingsCache.users = settingsCache.users || [];
  if (settingsCache.users.some((u) => u.platform === platform && u.user_id === uid))
    return toast("该用户已在列表中", "#b91c1c");
  settingsCache.users.push({ platform, user_id: uid, nickname, avatar, enabled: true });
  renderUserList(settingsCache.users);
  document.getElementById("newUid").value = "";
}

/* ---------- 公众号·微信读书书架同步 ---------- */
async function syncShelf() {
  const box = document.getElementById("shelfList");
  box.innerHTML = `<div class="hint">正在读取书架…</div>`;
  try {
    const r = await fetch("/api/weread/shelf");
    const d = await r.json();
    if (!r.ok) { box.innerHTML = `<div class="hint">⚠️ ${esc(d.detail || "读取失败")}</div>`; return; }
    const accs = d.accounts || [];
    if (!accs.length) {
      box.innerHTML = `<div class="hint">书架上还没有公众号。先在手机微信读书 App 搜索并订阅，再回来点同步。</div>`;
      return;
    }
    settingsCache.users = settingsCache.users || [];
    box.innerHTML = accs.map((a, i) => {
      const added = settingsCache.users.some((u) => u.platform === "weread" && u.user_id === a.user_id);
      return `<div class="user-row">
        ${a.avatar ? `<img class="avatar" style="width:26px;height:26px" src="${pimg(a.avatar)}" onerror="this.outerHTML=avatarPlaceholder('weread','${esc((a.nickname||'?')[0])}',26)">` : avatarPlaceholder("weread", esc((a.nickname || "?")[0]), 26)}
        <span class="grow">${esc(a.nickname)}</span>
        ${added ? `<span class="hint">已添加</span>` : `<button class="btn ghost" onclick="addShelfUser(${i})">＋ 添加</button>`}
      </div>`;
    }).join("");
    box.dataset.accs = JSON.stringify(accs);
  } catch (e) {
    box.innerHTML = `<div class="hint">读取书架失败，请重试</div>`;
  }
}

function addShelfUser(i) {
  const accs = JSON.parse(document.getElementById("shelfList").dataset.accs || "[]");
  const a = accs[i];
  if (!a) return;
  settingsCache.users = settingsCache.users || [];
  if (!settingsCache.users.some((u) => u.platform === "weread" && u.user_id === a.user_id)) {
    settingsCache.users.push({ platform: "weread", user_id: a.user_id, nickname: a.nickname, avatar: a.avatar, enabled: true });
    renderUserList(settingsCache.users);
    toast(`已加入「${a.nickname}」，点「保存」生效`);
  }
  syncShelf();
}

/* ---------- 导出/导入监控列表（只含用户清单，不含任何凭证） ---------- */
function exportUsers() {
  const users = (settingsCache.users || []).map((u) => ({
    platform: u.platform, user_id: u.user_id, nickname: u.nickname, avatar: u.avatar, enabled: u.enabled,
  }));
  if (!users.length) return toast("列表是空的", "#b91c1c");
  const blob = new Blob([JSON.stringify(users, null, 2)], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "监控列表.json";
  a.click();
  URL.revokeObjectURL(a.href);
  toast("已导出，把这个 json 发给朋友即可");
}

function importUsers(input) {
  const file = input.files[0];
  input.value = "";
  if (!file) return;
  const reader = new FileReader();
  reader.onload = () => {
    try {
      const list = JSON.parse(reader.result);
      if (!Array.isArray(list)) throw new Error("格式不对");
      settingsCache.users = settingsCache.users || [];
      let added = 0;
      for (const u of list) {
        if (!u.platform || !u.user_id) continue;
        if (settingsCache.users.some((x) => x.platform === u.platform && x.user_id === u.user_id)) continue;
        settingsCache.users.push({
          platform: u.platform, user_id: String(u.user_id),
          nickname: u.nickname || "", avatar: u.avatar || "", enabled: u.enabled !== false,
        });
        added++;
      }
      renderUserList(settingsCache.users);
      toast(added ? `导入成功 ${added} 人，点「保存」生效` : "列表里的人你都已有");
    } catch (e) {
      toast("导入失败：文件格式不对", "#b91c1c");
    }
  };
  reader.readAsText(file);
}

async function saveSettings() {
  const body = {
    weibo_cookie: document.getElementById("weiboCookie").value.trim(),
    taoguba_cookie: document.getElementById("taogubaCookie").value.trim(),
    zsxq_token: document.getElementById("zsxqToken").value.trim(),
    weread_cookie: document.getElementById("wereadCookie").value.trim(),
    access_password: document.getElementById("accessPassword").value,
    poll_interval_sec: parseInt(document.getElementById("interval").value) || 120,
    users: settingsCache.users || [],
  };
  const r = await fetch("/api/settings", {
    method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  if (r.ok) {
    toast("已保存，下一轮轮询生效");
    closeSettings();
    await loadProfiles();
    refreshStatus();
  }
  else toast("保存失败", "#b91c1c");
}

/* ---------- 顶栏事件 ---------- */
document.querySelectorAll(".tab").forEach((t) => {
  t.onclick = () => {
    document.querySelectorAll(".tab").forEach((x) => x.classList.remove("active"));
    t.classList.add("active");
    currentPlatform = t.dataset.platform;
    pendingNew = [];
    document.getElementById("newTip").style.display = "none";
    loadPosts();
  };
});

let searchTimer;
document.getElementById("searchBox").oninput = (e) => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => { currentKeyword = e.target.value.trim(); loadPosts(); }, 400);
};

/* ---------- 主题 ---------- */
function applyTheme(t) {
  document.documentElement.dataset.theme = t;
  localStorage.setItem("pm-theme", t);
  const sel = document.getElementById("themeSelect");
  if (sel) sel.value = t;
}
document.getElementById("themeSelect").onchange = (e) => applyTheme(e.target.value);
applyTheme(localStorage.getItem("pm-theme") || "dark");

/* ---------- 启动 ---------- */
(async function init() {
  ensureNotifyPermission();
  await loadProfiles();
  await loadPosts();
  refreshStatus();
  setInterval(refreshStatus, 30000);
  setInterval(loadProfiles, 60000);
  connectStream();
})();
