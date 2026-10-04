// app.js —— 主动回复动态控制 前端逻辑
// 通过 AstrBot 注入的 window.AstrBotPluginPage 桥接后端 Web API。

const MODE_LABEL = { block_all: "完全封锁", reply_at: "被@回复" };
const DAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]; // 对应 weekday() 0..6

let bridge = null;
let state = { overview: [], pending: [], controlled: [], settings: {}, curGroup: null };

/* ---------------- 基础设施 ---------------- */
async function waitBridge() {
  for (let i = 0; i < 120; i++) {
    if (window.AstrBotPluginPage) {
      const b = window.AstrBotPluginPage;
      if (typeof b.ready === "function") {
        try { await b.ready(); } catch (e) { /* 旧版本无 ready 时忽略 */ }
      }
      return b;
    }
    await new Promise((r) => setTimeout(r, 50));
  }
  throw new Error("Bridge SDK 未加载，请在 AstrBot 面板中打开本页。");
}
async function apiGet(endpoint, params) {
  try { return await bridge.apiGet(endpoint, params || {}); }
  catch (e) { toast("请求失败: " + (e && e.message ? e.message : e)); throw e; }
}
async function apiPost(endpoint, body) {
  try { return await bridge.apiPost(endpoint, body || {}); }
  catch (e) { toast("请求失败: " + (e && e.message ? e.message : e)); throw e; }
}
function toast(msg) {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(t._t);
  t._t = setTimeout(() => t.classList.remove("show"), 2200);
}
function el(tag, cls, html) {
  const e = document.createElement(tag);
  if (tag === "button") e.type = "button";
  if (cls) e.className = cls;
  if (html != null) e.innerHTML = html;
  return e;
}
function escapeHtml(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/* ---------------- 标签切换 ---------------- */
function initTabs() {
  document.querySelectorAll(".nav-item").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".nav-item").forEach((b) => {
        b.classList.remove("active");
        b.removeAttribute("aria-current");
      });
      document.querySelectorAll(".tab-panel").forEach((p) => p.classList.remove("active"));
      btn.classList.add("active");
      btn.setAttribute("aria-current", "page");
      document.getElementById("tab-" + btn.dataset.tab).classList.add("active");
      if (btn.dataset.tab === "overview") loadOverview();
      if (btn.dataset.tab === "behavior") loadControlled();
      if (btn.dataset.tab === "system") loadSettings();
    });
  });
}

/* ---------------- 总览 ---------------- */
async function loadOverview() {
  const q = document.getElementById("ovSearch").value.trim().toLowerCase();
  let data;
  try { data = await apiGet("api/overview"); } catch (e) { return; }
  state.overview = data.groups || [];
  state.globalEnabled = data.global_enabled;
  const pill = document.getElementById("globalStateWrap");
  if (pill) pill.classList.toggle("off", !data.global_enabled);
  document.getElementById("globalState").textContent = data.global_enabled ? "已启用" : "已停用";
  const num = (id, v) => { const n = document.getElementById(id); if (n) n.textContent = v; };
  num("statTotal", state.overview.length);
  num("statControlled", state.overview.filter((g) => g.controlled).length);
  num("statActive", state.overview.filter((g) => g.active_now).length);
  renderOverview(q);
}
function renderOverview(q) {
  const box = document.getElementById("overviewList");
  box.innerHTML = "";
  // 总览仅显示已添加(受控)的群聊
  const list = state.overview.filter((g) =>
    g.controlled &&
    (!q || g.group_name.toLowerCase().includes(q) || g.group_id.toLowerCase().includes(q))
  );
  if (!list.length) {
    box.appendChild(el("div", "empty",
      state.overview.some((g) => g.controlled)
        ? "没有匹配的群聊"
        : "尚未添加群聊，到「行为设置」页点击右上角「＋ 添加群聊」开始配置。"));
    return;
  }
  list.forEach((g) => {
    let badge, cls;
    if (!g.enabled) { badge = "已停用"; cls = "disabled"; }
    else if (g.active_now) { badge = (MODE_LABEL[g.mode] || "") + "中"; cls = "active"; }
    else { badge = MODE_LABEL[g.mode] || "—"; cls = g.mode; }

    const item = el("div", "row-item group");
    item.innerHTML = `
      <span class="drag-handle" aria-hidden="true">⋮⋮</span>
      <img class="avatar" src="${escapeHtml(g.avatar)}" onerror="this.style.visibility='hidden'"/>
      <div class="row-main">
        <div class="row-name"><span class="name-text">${escapeHtml(g.group_name)}</span></div>
        <div class="row-sub">群号 ${escapeHtml(g.group_id)}</div>
      </div>
      <span class="badge ${cls}">${escapeHtml(badge)}</span>
    `;
    const sw = el("label", "switch");
    sw.innerHTML = `<input type="checkbox" ${g.enabled ? "checked" : ""}/><span class="slider"></span>`;
    sw.querySelector("input").setAttribute("aria-label",
      (g.enabled ? "停用封锁：" : "启用封锁：") + g.group_name);
    sw.querySelector("input").addEventListener("change", async (ev) => {
      try {
        await apiPost("api/group/toggle", {
          group_id: g.group_id, group_name: g.group_name, enabled: ev.target.checked,
        });
        toast(ev.target.checked ? "已启用：" + g.group_name : "已停用：" + g.group_name);
        loadOverview();
      } catch (e) {}
    });
    item.appendChild(sw);
    box.appendChild(item);
  });
}

/* ---------------- 添加群聊弹层 ---------------- */
function openAddGroup() {
  document.getElementById("addGroupSearch").value = "";
  const mask = document.getElementById("addGroupModal");
  mask.setAttribute("aria-hidden", "false");
  mask.classList.add("open");
  loadAddGroupList("");
}
async function loadAddGroupList(q) {
  let data;
  try { data = await apiGet("api/groups", { q }); } catch (e) { return; }
  state.pending = data.groups || [];
  renderAddGroupList();
}
function renderAddGroupList() {
  const box = document.getElementById("addGroupList");
  box.innerHTML = "";
  if (!state.pending.length) { box.appendChild(el("div", "empty", "没有可添加的群聊")); return; }
  state.pending.forEach((g) => {
    const item = el("div", "row-item group");
    item.innerHTML = `
      <span class="drag-handle" aria-hidden="true">⋮⋮</span>
      <img class="avatar" src="${escapeHtml(g.avatar)}" onerror="this.style.visibility='hidden'"/>
      <div class="row-main">
        <div class="row-name"><span class="name-text">${escapeHtml(g.group_name)}</span></div>
        <div class="row-sub">群号 ${escapeHtml(g.group_id)}</div>
      </div>
    `;
    const btn = el("button", "btn primary sm", "添加");
    btn.addEventListener("click", async () => {
      try {
        // 后端默认 enabled=true, 添加即启用
        await apiPost("api/group/add", { group_id: g.group_id, group_name: g.group_name });
        toast("已添加并启用：" + g.group_name);
        await loadAddGroupList(document.getElementById("addGroupSearch").value.trim().toLowerCase());
        loadControlled();
        loadOverview();
      } catch (e) {}
    });
    item.appendChild(btn);
    box.appendChild(item);
  });
}
function closeAddGroup() {
  const mask = document.getElementById("addGroupModal");
  mask.setAttribute("aria-hidden", "true");
  mask.classList.remove("open");
}

/* ---------------- 已添加 ---------------- */
async function loadControlled() {
  const q = document.getElementById("controlledSearch").value.trim().toLowerCase();
  let data;
  try { data = await apiGet("api/controlled", { q }); } catch (e) { return; }
  state.controlled = data.groups || [];
  renderControlled();
}
function renderControlled() {
  const box = document.getElementById("controlledList");
  box.innerHTML = "";
  if (!state.controlled.length) { box.appendChild(el("div", "empty", "尚未添加任何群聊")); return; }
  state.controlled.forEach((g) => {
    const item = el("div", "row-item group");
    item.innerHTML = `
      <span class="drag-handle" aria-hidden="true">⋮⋮</span>
      <img class="avatar" src="${escapeHtml(g.avatar)}" onerror="this.style.visibility='hidden'"/>
      <div class="row-main">
        <div class="row-name"><span class="name-text">${escapeHtml(g.group_name)}</span>
          ${g.active_now ? '<span class="badge active">生效中</span>' : ""}
        </div>
        <div class="row-sub">群号 ${escapeHtml(g.group_id)} · 模式 ${MODE_LABEL[g.mode] || "—"} · ${g.schedules.length ? "时间段 " + g.schedules.length : "全天"}</div>
      </div>
    `;
    const actions = el("div", "row-actions");

    // 模式切换
    const sel = el("select", "sm");
    sel.style.flex = "0 0 auto";
    sel.setAttribute("aria-label", "切换封锁模式");
    sel.innerHTML = `<option value="block_all">完全封锁</option><option value="reply_at">被@回复</option>`;
    sel.value = g.mode;
    sel.addEventListener("change", async () => {
      try { await apiPost("api/group/mode", { group_id: g.group_id, mode: sel.value }); toast("模式已更新"); loadControlled(); }
      catch (e) {}
    });
    actions.appendChild(sel);

    // 开关
    const sw = el("label", "switch");
    sw.innerHTML = `<input type="checkbox" ${g.enabled ? "checked" : ""}/><span class="slider"></span>`;
    sw.querySelector("input").setAttribute("aria-label",
      (g.enabled ? "停用封锁：" : "启用封锁：") + g.group_name);
    sw.querySelector("input").addEventListener("change", async (ev) => {
      try { await apiPost("api/group/toggle", { group_id: g.group_id, enabled: ev.target.checked }); loadControlled(); }
      catch (e) {}
    });
    actions.appendChild(sw);

    // 时间段
    const btnS = el("button", "btn sm", "时间段");
    btnS.addEventListener("click", () => openSchedule(g));
    actions.appendChild(btnS);

    // 移除
    const btnR = el("button", "btn danger sm", "移除");
    btnR.addEventListener("click", async () => {
      try { await apiPost("api/group/remove", { group_id: g.group_id }); toast("已移除"); loadControlled(); loadOverview(); }
      catch (e) {}
    });
    actions.appendChild(btnR);

    item.appendChild(actions);
    box.appendChild(item);
  });
}

/* ---------------- 时间段弹层 ---------------- */
function openSchedule(g) {
  state.curGroup = g;
  document.getElementById("scheduleTitle").textContent = "时间段设置 · " + g.group_name;
  const body = document.getElementById("scheduleBody");
  body.innerHTML = "";

  // 现有时间段
  if (g.schedules.length) {
    g.schedules.forEach((s) => {
      const row = el("div", "sched");
      let desc;
      if (s.type === "daily") desc = `每天 ${s.start}-${s.end}`;
      else if (s.type === "weekly") desc = `${s.days.map((d) => DAYS[d]).join("、")} ${s.start}-${s.end}`;
      else desc = `${s.date} ${s.start}-${s.end}（不重复）`;
      row.innerHTML = `<span class="sched-txt">${escapeHtml(desc)}</span>`;
      const del = el("button", "btn danger sm", "删");
      del.addEventListener("click", async () => {
        try { await apiPost("api/schedule/remove", { group_id: g.group_id, schedule_id: s.id }); toast("已删除时间段"); g.schedules = g.schedules.filter((x) => x.id !== s.id); openSchedule(g); }
        catch (e) {}
      });
      row.appendChild(del);
      body.appendChild(row);
    });
  } else {
    body.appendChild(el("div", "muted", "未配置时间段：当前默认全天封锁。如需仅在特定时段封锁，请在下方添加。"));
  }

  // 新增表单
  const form = el("div", "field");
  form.innerHTML = `
    <div class="field"><label>类型</label>
      <select id="sfType">
        <option value="daily">每天</option>
        <option value="weekly">自定义星期</option>
        <option value="once">不重复（指定日期）</option>
      </select>
    </div>
    <div class="field"><label>开始时间</label><input type="time" id="sfStart" class="input" value="16:00"/></div>
    <div class="field"><label>结束时间</label><input type="time" id="sfEnd" class="input" value="18:00"/></div>
    <div class="field" id="sfDaysWrap" style="display:none"><label>星期（可多选）</label>
      <div class="day-grid" id="sfDays"></div>
    </div>
    <div class="field" id="sfDateWrap" style="display:none"><label>生效日期</label><input type="date" id="sfDate" class="input"/></div>
  `;
  body.appendChild(form);

  const typeSel = form.querySelector("#sfType");
  const daysWrap = form.querySelector("#sfDaysWrap");
  const dateWrap = form.querySelector("#sfDateWrap");
  const daysBox = form.querySelector("#sfDays");
  const picked = new Set();
  DAYS.forEach((d, i) => {
    const chip = el("button", "day-chip", d);
    chip.addEventListener("click", () => {
      if (picked.has(i)) { picked.delete(i); chip.classList.remove("on"); }
      else { picked.add(i); chip.classList.add("on"); }
    });
    daysBox.appendChild(chip);
  });
  typeSel.addEventListener("change", () => {
    daysWrap.style.display = typeSel.value === "weekly" ? "block" : "none";
    dateWrap.style.display = typeSel.value === "once" ? "block" : "none";
  });

  const addBtn = el("button", "btn primary", "添加时间段");
  addBtn.addEventListener("click", async () => {
    const payload = {
      group_id: g.group_id,
      schedule: {
        type: typeSel.value,
        start: form.querySelector("#sfStart").value,
        end: form.querySelector("#sfEnd").value,
      },
    };
    if (typeSel.value === "weekly") payload.schedule.days = [...picked].sort();
    if (typeSel.value === "once") payload.schedule.date = form.querySelector("#sfDate").value;
    try {
      const r = await apiPost("api/schedule/add", payload);
      if (r && r.ok) { toast("时间段已添加"); g.schedules.push(r.schedule); openSchedule(g); }
    } catch (e) {}
  });
  const foot = el("div", "modal-foot");
  foot.appendChild(addBtn);
  body.appendChild(foot);

  const mask = document.getElementById("scheduleModal");
  mask.setAttribute("aria-hidden", "false");
  mask.classList.add("open");
}
function closeSchedule() {
  const mask = document.getElementById("scheduleModal");
  mask.setAttribute("aria-hidden", "true");
  mask.classList.remove("open");
}
/* ---------------- 系统设置 ---------------- */
async function loadSettings() {
  let data;
  try { data = await apiGet("api/settings"); } catch (e) { return; }
  state.settings = data;
  document.getElementById("setEnabled").checked = !!data.enabled;
  document.getElementById("setBypassAdmins").checked = !!data.bypass_admins;
  document.getElementById("setBypassCommands").checked = !!data.bypass_commands;
  document.getElementById("setNoticeMode").value = data.notice_mode || "none";
  document.getElementById("setNoticeText").value = data.notice_text || "";
  document.getElementById("setLogLevel").value = data.log_level || "INFO";
}
async function saveSettings() {
  const payload = {
    enabled: document.getElementById("setEnabled").checked,
    bypass_admins: document.getElementById("setBypassAdmins").checked,
    bypass_commands: document.getElementById("setBypassCommands").checked,
    notice_mode: document.getElementById("setNoticeMode").value,
    notice_text: document.getElementById("setNoticeText").value,
    log_level: document.getElementById("setLogLevel").value,
  };
  try { await apiPost("api/settings", payload); toast("设置已保存"); loadSettings(); }
  catch (e) {}
}
async function resetData() {
  if (!confirm("确定重置全部数据？所有受控群与时间段将被清空。")) return;
  try { await apiPost("api/reset", {}); toast("已重置"); loadSettings(); loadOverview(); loadControlled(); }
  catch (e) {}
}

/* ---------------- 初始化 ---------------- */
async function main() {
  initTabs();
  document.getElementById("ovSearch").addEventListener("input", () => renderOverview(document.getElementById("ovSearch").value.trim().toLowerCase()));
  document.getElementById("ovRefresh").addEventListener("click", loadOverview);
  let ct;
  document.getElementById("controlledSearch").addEventListener("input", () => { clearTimeout(ct); ct = setTimeout(loadControlled, 250); });
  document.getElementById("addGroupBtn").addEventListener("click", openAddGroup);
  document.getElementById("addGroupClose").addEventListener("click", closeAddGroup);
  document.getElementById("addGroupModal").addEventListener("click", (e) => { if (e.target.id === "addGroupModal") closeAddGroup(); });
  let at;
  document.getElementById("addGroupSearch").addEventListener("input", () => {
    clearTimeout(at);
    at = setTimeout(() => loadAddGroupList(document.getElementById("addGroupSearch").value.trim().toLowerCase()), 250);
  });
  document.getElementById("scheduleClose").addEventListener("click", closeSchedule);
  document.getElementById("scheduleModal").addEventListener("click", (e) => { if (e.target.id === "scheduleModal") closeSchedule(); });
  document.getElementById("setSave").addEventListener("click", saveSettings);
  document.getElementById("setReset").addEventListener("click", resetData);

  try { bridge = await waitBridge(); }
  catch (e) { toast(e.message); return; }
  await loadOverview();
}
main();
