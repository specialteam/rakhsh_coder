"use strict";
/* Dastyar web UI. No dependencies. All dynamic text goes through textContent or through
   renderMarkdown(), which escapes everything first (the agent runs shell commands, so an XSS
   here would be code execution on the machine). */

// ------------------------------------------------------------------ helpers
const $ = (id) => document.getElementById(id);

function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === false || v == null) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) {
    if (kid == null || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

function store(key, value) {
  try { if (value === undefined) return localStorage.getItem(key); localStorage.setItem(key, value); } catch (_) { /* private mode */ }
  return null;
}

class AuthError extends Error {}

async function api(path, { method = "GET", body } = {}) {
  const res = await fetch("/api" + path, {
    method,
    credentials: "same-origin",
    headers: body !== undefined ? { "Content-Type": "application/json" } : {},
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (res.status === 401) { showLogin(); throw new AuthError("auth"); }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = await res.json();
      detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch (_) { /* not json */ }
    throw new Error(detail);
  }
  return res.json();
}

// ------------------------------------------------------------------ safe markdown
function escapeHtml(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function inlineMd(raw) {
  const codes = [];
  let t = raw.replace(/`([^`\n]+)`/g, (_, c) => { codes.push(c); return "\u0000" + (codes.length - 1) + "\u0000"; });
  t = escapeHtml(t);
  t = t.replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g,
    (_, label, url) => `<a href="${url}" target="_blank" rel="noopener noreferrer">${label}</a>`);
  t = t.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
  t = t.replace(/(^|[^*\w])\*([^*\n]+)\*(?!\w)/g, "$1<em>$2</em>");
  t = t.replace(/~~([^~\n]+)~~/g, "<del>$1</del>");
  return t.replace(/\u0000(\d+)\u0000/g, (_, i) => "<code>" + escapeHtml(codes[Number(i)]) + "</code>");
}

function renderMarkdown(src) {
  const lines = src.replace(/\u0000/g, "").replace(/\r\n?/g, "\n").split("\n");
  const out = [];
  const isBlockStart = (l) => /^\s*(```|#{1,6}\s|>|[-*+]\s|\d+[.)]\s)/.test(l);
  const splitRow = (l) => l.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (/^\s*```/.test(line)) {
      const code = [];
      i++;
      while (i < lines.length && !/^\s*```\s*$/.test(lines[i])) code.push(lines[i++]);
      i++; // closing fence (or end of a still-streaming block)
      out.push(`<pre dir="ltr"><code>${escapeHtml(code.join("\n"))}</code></pre>`);
      continue;
    }
    if (/^\s*$/.test(line)) { i++; continue; }
    let m = line.match(/^(#{1,6})\s+(.*)$/);
    if (m) { out.push(`<h${m[1].length} dir="auto">${inlineMd(m[2])}</h${m[1].length}>`); i++; continue; }
    if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(line)) { out.push("<hr>"); i++; continue; }
    if (/^\s*>/.test(line)) {
      const q = [];
      while (i < lines.length && /^\s*>/.test(lines[i])) q.push(lines[i++].replace(/^\s*>\s?/, ""));
      out.push(`<blockquote dir="auto">${q.map(inlineMd).join("<br>")}</blockquote>`);
      continue;
    }
    const ul = /^\s*[-*+]\s+/, ol = /^\s*\d+[.)]\s+/;
    if (ul.test(line) || ol.test(line)) {
      const re = ul.test(line) ? ul : ol, tag = re === ul ? "ul" : "ol", items = [];
      while (i < lines.length && re.test(lines[i])) items.push(lines[i++].replace(re, ""));
      out.push(`<${tag}>${items.map((x) => `<li dir="auto">${inlineMd(x)}</li>`).join("")}</${tag}>`);
      continue;
    }
    if (line.includes("|") && i + 1 < lines.length && /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(lines[i + 1])) {
      const head = splitRow(line);
      i += 2;
      const rows = [];
      while (i < lines.length && lines[i].includes("|") && !/^\s*$/.test(lines[i])) rows.push(splitRow(lines[i++]));
      out.push("<table><thead><tr>" + head.map((c) => `<th dir="auto">${inlineMd(c)}</th>`).join("") + "</tr></thead><tbody>"
        + rows.map((r) => "<tr>" + r.map((c) => `<td dir="auto">${inlineMd(c)}</td>`).join("") + "</tr>").join("") + "</tbody></table>");
      continue;
    }
    const para = [];
    while (i < lines.length && !/^\s*$/.test(lines[i]) && (para.length === 0 || !isBlockStart(lines[i]))) para.push(lines[i++]);
    out.push(`<p dir="auto">${para.map(inlineMd).join("<br>")}</p>`);
  }
  return out.join("");
}

// ------------------------------------------------------------------ state
const TOOL_LABELS = {
  terminal: "ترمینال", run_python: "اجرای پایتون", read_file: "خواندن فایل", write_file: "نوشتن فایل",
  edit_file: "ویرایش فایل", list_dir: "فهرست پوشه", search_files: "جستجو در فایل‌ها", web_search: "جستجوی وب",
  web_fetch: "خواندن صفحه", memory: "حافظه", skills_list: "فهرست مهارت‌ها", skill_view: "خواندن مهارت",
  skill_manage: "مدیریت مهارت", session_search: "جستجوی گفتگوهای قبلی", delegate_task: "ساب‌ایجنت‌ها",
  write_todos: "برنامهٔ کار",
};
const APPROVAL_LABELS = { dangerous: "تأیید: فقط خطرناک", always: "تأیید: همیشه", never: "تأیید: خاموش ⚠" };

const state = {
  sessions: [], current: null, session: null, ws: null, wsGen: 0, retry: 0, running: false,
  pending: null, models: [], settings: {}, stream: null, toolEls: new Map(), subEls: new Map(),
  inner: null, selectedSkill: null, lastUserText: "",
};

// ------------------------------------------------------------------ message area
function messagesInner() {
  if (!state.inner || !state.inner.isConnected) {
    const box = $("messages");
    box.replaceChildren();
    state.inner = h("div", { class: "msgs-inner" });
    box.append(state.inner);
  }
  return state.inner;
}

function nearBottom() {
  const b = $("messages");
  return b.scrollHeight - b.scrollTop - b.clientHeight < 140;
}
function scrollDown(force) {
  const b = $("messages");
  if (force || nearBottom()) requestAnimationFrame(() => { b.scrollTop = b.scrollHeight; });
}

function addTo(el) {
  const stick = nearBottom();
  messagesInner().append(el);
  if (stick) scrollDown(true);
  return el;
}

function showEmpty() {
  $("messages").replaceChildren(h("div", { class: "empty" },
    h("h3", {}, "سلام! چه کاری برایتان انجام دهم؟"),
    h("p", {}, "می‌توانم دستور اجرا کنم، فایل بخوانم و بنویسم، در وب جستجو کنم و از کارهای قبلی چیز یاد بگیرم.")));
  state.inner = null;
}

function userBubble(text) { return h("div", { class: "bubble user", dir: "auto" }, text); }

function mdBubble(text) {
  const el = h("div", { class: "bubble assistant md" });
  el.innerHTML = renderMarkdown(text); // safe: renderMarkdown escapes first
  return el;
}

function errorBubble(text) { return h("div", { class: "bubble error", dir: "auto", role: "alert" }, text); }

function isNetworkError(text) {
  return /(connecterror|connectionerror|connection reset|connection refused|timed out|timeout|network|all connection attempts failed|winerror 10054|502|503|504|utf-8 bom|jsondecodeerror)/i.test(String(text || ""));
}

function networkErrorBubble(text) {
  const retry = h("button", { class: "btn primary network-continue", type: "button" }, "ادامهٔ کار");
  const card = h("div", { class: "bubble error network-error", dir: "auto", role: "alert" },
    h("strong", {}, "اتصال شبکه قطع شد"),
    h("p", {}, String(text || "ارتباط با مدل یا backend برقرار نشد.")), retry);
  retry.addEventListener("click", () => continueAfterNetworkError(retry));
  return card;
}

function continueAfterNetworkError(button) {
  if (button.disabled || !state.current) return;
  button.disabled = true;
  button.textContent = "در حال برقراری اتصال…";
  const resume = () => {
    const ok = send({
      type: "user_message",
      text: "ادامهٔ کار را دقیقاً از آخرین وضعیت ذخیره‌شده ادامه بده؛ کارهای موفق را تکرار نکن و ابتدا وضعیت فعلی را بررسی کن.",
      model: $("model-select").value || undefined,
    });
    if (!ok) {
      button.disabled = false;
      button.textContent = "تلاش دوباره برای ادامه";
      return;
    }
    setRunning(true);
    button.textContent = "در حال ادامه…";
  };
  if (state.ws && state.ws.readyState === WebSocket.OPEN) resume();
  else {
    setBanner("در حال اتصال مجدد…");
    if (state.current) connect(state.current);
    setTimeout(resume, 900);
  }
}

// -- streaming assistant text
let renderQueued = false;
function renderStream() {
  if (renderQueued || !state.stream) return;
  renderQueued = true;
  requestAnimationFrame(() => {
    renderQueued = false;
    if (!state.stream) return;
    const stick = nearBottom();
    state.stream.el.innerHTML = renderMarkdown(state.stream.text);
    state.stream.el.classList.add("cursor");
    if (stick) scrollDown(true);
  });
}
function onToken(text) {
  if (!state.stream) {
    const el = h("div", { class: "bubble assistant md" });
    addTo(el);
    state.stream = { el, text: "" };
  }
  state.stream.text += text;
  renderStream();
}
function endStream(finalText) {
  if (state.stream) {
    const { el } = state.stream;
    el.classList.remove("cursor");
    if (finalText) el.innerHTML = renderMarkdown(finalText);
    state.stream = null;
  } else if (finalText) {
    addTo(mdBubble(finalText));
  }
}

// -- tool cards
function summarize(name, a) {
  a = a || {};
  const first = (s) => String(s ?? "").split("\n").find((l) => l.trim()) || "";
  switch (name) {
    case "terminal": return a.command;
    case "run_python": return first(a.code);
    case "read_file": case "write_file": case "edit_file": case "list_dir": return a.path || ".";
    case "search_files": return a.pattern;
    case "web_search": return a.query;
    case "web_fetch": return a.url;
    case "memory": return [a.action, a.target, a.content || a.match].filter(Boolean).join(" · ");
    case "skill_manage": return [a.action, a.name].filter(Boolean).join(" · ");
    case "skill_view": return a.name;
    case "session_search": return a.query;
    case "delegate_task": return `${(a.tasks || []).length} کار موازی`;
    default: return JSON.stringify(a);
  }
}

function toolCard(id, name, args) {
  const state_ = h("span", { class: "t-state" });
  const body = h("div", { class: "tool-body" });
  const el = h("details", { class: "tool running" },
    h("summary", {},
      h("span", { class: "t-name" }, TOOL_LABELS[name] || name),
      h("span", { class: "t-sum" }, String(summarize(name, args) ?? "").slice(0, 240)),
      state_),
    body);
  fillToolBody(body, name, args, null);
  state.toolEls.set(id, { el, body, name, args });
  return el;
}

function fillToolBody(body, name, args, output) {
  const keep = body.querySelector(".subs");
  body.replaceChildren();
  if (name === "write_todos") {
    body.append(h("h5", {}, "برنامهٔ کار"), todoList(args));
  } else {
    const text = name === "terminal" ? args.command
      : name === "run_python" ? args.code
      : JSON.stringify(args, null, 2);
    body.append(h("h5", {}, "ورودی"), h("pre", { dir: "ltr" }, text ?? ""));
  }
  if (keep) body.append(keep);
  if (output != null) body.append(h("h5", {}, "خروجی"), h("pre", { dir: "ltr" }, output || "(بدون خروجی)"));
}

function todoList(args) {
  const todos = Array.isArray(args?.todos) ? args.todos : [];
  if (!todos.length) return h("div", { class: "todo-empty" }, "موردی ثبت نشده است.");
  return h("ol", { class: "todo-list" }, todos.map((item) => {
    const status = String(item?.status || "pending").toLowerCase();
    const done = status === "completed" || status === "done";
    const label = status === "in_progress" ? "در حال انجام" : done ? "انجام شد" : "در انتظار";
    return h("li", { class: `todo-item ${done ? "done" : status === "in_progress" ? "active" : ""}` },
      h("span", { class: "todo-mark", "aria-label": label }, done ? "✓" : status === "in_progress" ? "…" : "○"),
      h("span", { class: "todo-content", dir: "auto" }, item?.content || item?.title || "(بدون عنوان)"),
      h("span", { class: "todo-status" }, label));
  }));
}

function finishTool(id, name, args, output, status) {
  let rec = state.toolEls.get(id);
  if (!rec) { addTo(toolCard(id, name, args)); rec = state.toolEls.get(id); }
  fillToolBody(rec.body, name, args || rec.args, output);
  const failed = status === "error" || /^(ERROR|REFUSED)/.test(output || "");
  rec.el.classList.remove("running");
  rec.el.classList.add(failed ? "fail" : "ok");
}

// -- sub-agents (progress lines inside the delegate_task card)
function onSubagent(ev) {
  const card = [...state.toolEls.values()].reverse().find((r) => r.name === "delegate_task" && r.el.classList.contains("running"));
  if (!card) return;
  let subs = card.body.querySelector(".subs");
  if (!subs) { subs = h("div", { class: "subs" }); card.body.prepend(subs); card.el.open = true; }
  const key = card.el.dataset.k || (card.el.dataset.k = String(Math.random()));
  const id = key + ":" + ev.index;
  let row = state.subEls.get(id);
  if (!row) {
    row = { el: h("div", { class: "sub" },
      h("div", { class: "sub-head" }, h("b", {}, `ساب‌ایجنت ${ev.index + 1}`), h("span", { class: "sub-state" }, "در حال اجرا")),
      h("div", { class: "sub-task", dir: "auto" }), h("div", { class: "sub-line", dir: "ltr" })) };
    subs.append(row.el);
    state.subEls.set(id, row);
  }
  if (ev.event === "start") row.el.querySelector(".sub-task").textContent = ev.task || "";
  if (ev.event === "tool") row.el.querySelector(".sub-line").textContent = `${ev.name}: ${String(summarize(ev.name, ev.args) ?? "").slice(0, 160)}`;
  if (ev.event === "done") {
    row.el.classList.add("done");
    row.el.querySelector(".sub-state").textContent = "✓ پایان";
    row.el.querySelector(".sub-line").textContent = "کار ساب‌ایجنت با موفقیت تمام شد";
  }
}

// -- approvals
function showApproval(requests) {
  removeApproval();
  const choices = requests.map(() => ({ type: null, edited: null }));
  const submit = h("button", { class: "btn primary", type: "button", disabled: true }, "ثبت تصمیم");
  const refresh = () => { submit.disabled = !choices.every((c) => c.type); };

  const items = requests.map((r, idx) => {
    const text = r.name === "terminal" ? r.args.command : r.name === "run_python" ? r.args.code : JSON.stringify(r.args, null, 2);
    const editArea = h("textarea", { rows: 6, hidden: true, "aria-label": "ویرایش آرگومان‌ها" }, JSON.stringify(r.args, null, 2));
    const err = h("p", { class: "error" });
    const btns = {};
    const pick = (type) => {
      choices[idx].type = type;
      for (const [k, b] of Object.entries(btns)) b.classList.toggle("sel", k === type);
      editArea.hidden = type !== "edit";
      err.textContent = "";
      refresh();
    };
    const allowed = r.allowed || ["approve", "reject"];
    const label = { approve: "اجرا شود", edit: "ویرایش و اجرا", reject: "رد شود" };
    const row = h("div", { class: "ap-choice" }, allowed.map((t) => (btns[t] = h("button", { class: "btn", type: "button", onclick: () => pick(t) }, label[t] || t))));
    choices[idx].read = () => {
      if (choices[idx].type !== "edit") return true;
      try {
        const parsed = JSON.parse(editArea.value);
        if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("باید یک آبجکت JSON باشد");
        choices[idx].edited = parsed;
        return true;
      } catch (e) { err.textContent = "JSON نامعتبر: " + e.message; return false; }
    };
    choices[idx].pick = pick;
    return h("div", { class: "ap-item" },
      h("div", {}, h("b", {}, TOOL_LABELS[r.name] || r.name)),
      h("pre", {}, text ?? ""), row, editArea, err);
  });

  const all = (type) => h("button", { class: "btn", type: "button", onclick: () => choices.forEach((c) => c.pick(type)) }, type === "approve" ? "اجرای همه" : "رد همه");
  submit.addEventListener("click", () => {
    if (!choices.every((c) => c.read())) return;
    const decisions = choices.map((c, i) => {
      if (c.type === "approve") return { type: "approve" };
      if (c.type === "reject") return { type: "reject", message: "کاربر این دستور را رد کرد." };
      return { type: "edit", edited_action: { name: requests[i].name, args: c.edited } };
    });
    removeApproval();
    state.pending = null;
    setRunning(true);
    send({ type: "approval", decisions });
  });
  const el = h("div", { class: "approval", id: "approval", role: "alertdialog", "aria-label": "تأیید دستور" },
    h("h4", {}, "⚠ ایجنت برای اجرای این کار اجازه می‌خواهد"), ...items,
    h("div", { class: "ap-actions" }, submit, requests.length > 1 ? [all("approve"), all("reject")] : null));
  state.pending = requests;
  addTo(el);
  scrollDown(true);
  refresh();
  updateComposer();
}
function removeApproval() { const a = $("approval"); if (a) a.remove(); }

// ------------------------------------------------------------------ websocket
function wsUrl(id) {
  return (location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/api/chat/" + encodeURIComponent(id);
}

function connect(id) {
  const gen = ++state.wsGen;
  if (state.ws) { try { state.ws.close(); } catch (_) { /* ignore */ } }
  const ws = new WebSocket(wsUrl(id));
  state.ws = ws;
  ws.onopen = () => { state.retry = 0; setBanner(null); updateComposer(); };
  ws.onmessage = (e) => { if (gen === state.wsGen) { try { onEvent(JSON.parse(e.data)); } catch (err) { console.error(err); } } };
  ws.onclose = () => {
    if (gen !== state.wsGen || state.current !== id) return;
    if (state.running) { setRunning(false); endStream(null); addTo(networkErrorBubble("اتصال WebSocket قطع شد؛ می‌توانید از وضعیت ذخیره‌شده ادامه دهید.")); }
    setBanner("اتصال برقرار نیست؛ تلاش مجدد…");
    const wait = Math.min(5000, 200 * 2 ** state.retry++) + Math.floor(Math.random() * 150);
    setTimeout(() => { if (gen === state.wsGen && state.current === id) connect(id); }, wait);
    updateComposer();
  };
}

window.addEventListener("online", () => {
  if (state.current && (!state.ws || state.ws.readyState === WebSocket.CLOSED)) connect(state.current);
});
document.addEventListener("visibilitychange", () => {
  if (!document.hidden && state.current && (!state.ws || state.ws.readyState === WebSocket.CLOSED)) {
    connect(state.current);
  }
});

function send(obj) {
  if (state.ws && state.ws.readyState === WebSocket.OPEN) { state.ws.send(JSON.stringify(obj)); return true; }
  setBanner("اتصال برقرار نیست.");
  return false;
}

function setBanner(text) {
  const b = $("banner");
  b.hidden = !text;
  b.textContent = text || "";
  updateComposer();
}

function onEvent(ev) {
  switch (ev.type) {
    case "ready": updateComposer(); break;
    case "token": onToken(ev.text); break;
    case "model_done":
      endStream(ev.text);
      for (const c of ev.tool_calls || []) addTo(toolCard(c.id, c.name, c.args));
      break;
    case "tool_end": finishTool(ev.id, ev.name, ev.args, ev.output, ev.status); scrollDown(); break;
    case "subagent": onSubagent(ev); break;
    case "approval_request": setRunning(false); showApproval(ev.requests); break;
    case "review_note": addTo(h("div", { class: "note" }, "🧠 " + ev.text)); break;
    case "session": updateTitle(ev.id, ev.title); break;
    case "error":
      endStream(null);
      setBanner("خطای backend؛ در صورت امکان دوباره تلاش می‌شود.");
      addTo(isNetworkError(ev.message) ? networkErrorBubble(ev.message) : errorBubble(ev.message));
      break;
    case "done":
      endStream(null);
      if (!ev.interrupted && state.pending) { removeApproval(); state.pending = null; } // e.g. after Stop
      setRunning(false);
      refreshSessions();
      break;
    default: break;
  }
}

// ------------------------------------------------------------------ composer state
function setRunning(v) { state.running = v; updateComposer(); }

function updateComposer() {
  const connected = state.ws && state.ws.readyState === WebSocket.OPEN;
  const blocked = state.running || !!state.pending || !connected;
  $("send").hidden = state.running;
  $("stop").hidden = !state.running && !state.pending;
  $("send").disabled = blocked;
  $("input").placeholder = state.pending ? "ابتدا دربارهٔ درخواست تأیید تصمیم بگیرید…" : "پیام خود را بنویسید… (Enter ارسال، Shift+Enter خط جدید)";
}

function autosize() {
  const t = $("input");
  t.style.height = "auto";
  t.style.height = Math.min(t.scrollHeight, 200) + "px";
}

function submitMessage() {
  const input = $("input");
  const text = input.value.trim();
  if (!text || state.running || state.pending || !state.current) return;
  state.lastUserText = text;
  const first = !state.inner || !state.inner.isConnected;
  if (first) messagesInner();
  if (!send({ type: "user_message", text, model: $("model-select").value || undefined })) return;
  addTo(userBubble(text));
  scrollDown(true);
  input.value = "";
  autosize();
  setRunning(true);
  if (state.session) state.session.model = $("model-select").value;
}

// ------------------------------------------------------------------ sessions
function updateTitle(id, title) {
  const s = state.sessions.find((x) => x.id === id);
  if (s) s.title = title;
  if (id === state.current) $("chat-title").textContent = title || "گفتگوی جدید";
  renderSessions();
}

function renderSessions(searchResults) {
  const ul = $("sessions");
  ul.replaceChildren();
  const rows = searchResults || state.sessions.map((s) => ({ session_id: s.id, title: s.title }));
  if (!rows.length) ul.append(h("li", {}, h("span", { class: "sess-btn hint" }, searchResults ? "نتیجه‌ای پیدا نشد" : "هنوز گفتگویی ندارید")));
  for (const r of rows) {
    const id = r.session_id;
    const li = h("li", { class: id === state.current ? "active" : "" },
      h("button", { class: "sess-btn", type: "button", dir: "auto", onclick: () => { clearSearch(); openSession(id); closeSidebar(); } },
        r.title || "گفتگوی بدون عنوان", r.snippet ? h("span", { class: "sess-snippet", dir: "auto" }, r.snippet) : null));
    if (!searchResults) {
      li.append(
        h("button", { class: "sess-act", type: "button", title: "تغییر نام", "aria-label": "تغییر نام", onclick: () => renameSession(id) }, "✎"),
        h("button", { class: "sess-act", type: "button", title: "حذف", "aria-label": "حذف", onclick: () => deleteSession(id) }, "🗑"));
    }
    ul.append(li);
  }
}

async function refreshSessions() {
  try { state.sessions = await api("/sessions"); renderSessions(); } catch (e) { if (!(e instanceof AuthError)) console.error(e); }
}

async function newChat() {
  const s = await api("/sessions", { method: "POST", body: { model: $("model-select").value || null } });
  state.sessions.unshift(s);
  await openSession(s.id);
  $("input").focus();
}

async function renameSession(id) {
  const s = state.sessions.find((x) => x.id === id);
  const title = prompt("عنوان جدید:", s?.title || "");
  if (title == null) return;
  const upd = await api("/sessions/" + id, { method: "PATCH", body: { title } });
  updateTitle(id, upd.title);
}

async function deleteSession(id) {
  if (!confirm("این گفتگو برای همیشه حذف شود؟")) return;
  try { await api("/sessions/" + id, { method: "DELETE" }); } catch (e) { alert(e.message); return; }
  state.sessions = state.sessions.filter((s) => s.id !== id);
  if (state.current === id) {
    if (state.sessions.length) await openSession(state.sessions[0].id); else await newChat();
  } else renderSessions();
}

async function openSession(id) {
  state.wsGen++;
  if (state.ws) { try { state.ws.close(); } catch (_) { /* ignore */ } state.ws = null; }
  state.current = id;
  state.running = false;
  state.pending = null;
  state.stream = null;
  state.toolEls.clear();
  state.subEls.clear();
  state.inner = null;
  history.replaceState(null, "", "#/s/" + id);
  let data;
  try { data = await api("/sessions/" + id); } catch (e) {
    if (e instanceof AuthError) return;
    if (state.sessions.length) return openSession(state.sessions[0].id);
    return newChat();
  }
  state.session = data.session;
  $("chat-title").textContent = data.session.title || "گفتگوی جدید";
  if (data.session.model && [...$("model-select").options].some((o) => o.value === data.session.model)) $("model-select").value = data.session.model;
  renderHistory(data.messages);
  renderSessions();
  connect(id);
  updateComposer();
}

function renderHistory(messages) {
  if (!messages.length) { showEmpty(); return; }
  messagesInner();
  for (const m of messages) {
    if (m.role === "user") addTo(userBubble(m.content));
    else if (m.role === "assistant") addTo(mdBubble(m.content));
    else if (m.role === "note") addTo(h("div", { class: "note" }, "🧠 " + m.content));
    else if (m.role === "tool") {
      const c = m.content;
      addTo(toolCard(c.id || "h" + m.id, c.name, c.args || {}));
      finishTool(c.id || "h" + m.id, c.name, c.args || {}, c.output, c.status);
    }
  }
  scrollDown(true);
}

let searchTimer = null;
function clearSearch() { $("search").value = ""; renderSessions(); }
async function runSearch() {
  const q = $("search").value.trim();
  if (!q) { renderSessions(); return; }
  try { renderSessions(await api("/search?q=" + encodeURIComponent(q))); } catch (e) { if (!(e instanceof AuthError)) console.error(e); }
}

// ------------------------------------------------------------------ models / settings
async function loadModels() {
  const data = await api("/models");
  state.models = data.models;
  for (const sel of [$("model-select"), $("set-model")]) {
    sel.replaceChildren(...data.models.map((m) => h("option", { value: m.name }, m.available ? m.name : `${m.name} (بدون کلید API)`)));
    sel.value = data.default;
  }
}

function approvalHint() {
  const v = $("set-approval").value;
  $("approval-hint").textContent = {
    dangerous: "فقط برای دستورهای پرریسک (حذف بازگشتی، نصب بسته، نوشتن بیرون از workspace و…) تأیید می‌گیرد. این یک تشخیص ساده است و مرز امنیتی کامل نیست.",
    always: "پیش از هر دستور ترمینال، اجرای پایتون و نوشتن فایل تأیید می‌گیرد؛ امن‌ترین حالت.",
    never: "⚠ ایجنت هر دستوری را بدون پرسیدن اجرا می‌کند. اگر محتوای یک صفحهٔ وب یا فایل بتواند ایجنت را فریب دهد، آسیب واقعی ممکن است.",
  }[v];
}

async function loadSettings() {
  state.settings = await api("/settings");
  const b = $("approval-badge");
  b.textContent = APPROVAL_LABELS[state.settings.approval_mode] || "";
  b.classList.toggle("never", state.settings.approval_mode === "never");
  if (state.settings.fake_model) b.textContent += " · مدل آزمایشی";
}

function openSettings() {
  $("set-approval").value = state.settings.approval_mode;
  $("set-model").value = state.settings.default_model;
  $("settings-error").textContent = "";
  approvalHint();
  $("dlg-settings").showModal();
}

async function saveSettings() {
  try {
    const body = {};
    if ($("set-approval").value !== state.settings.approval_mode) body.approval_mode = $("set-approval").value;
    if ($("set-model").value !== state.settings.default_model) body.default_model = $("set-model").value;
    state.settings = await api("/settings", { method: "PUT", body });
    await loadSettings();
    $("dlg-settings").close();
  } catch (e) { $("settings-error").textContent = e.message; }
}

// ------------------------------------------------------------------ memory dialog
async function openMemory() {
  $("memory-error").textContent = "";
  const m = await api("/memory");
  for (const t of ["user", "memory"]) {
    $("mem-" + t).value = m[t].text;
    $("mem-" + t + "-count").textContent = `${m[t].used} / ${m[t].limit} نویسه`;
  }
  $("dlg-memory").showModal();
}
async function saveMemory() {
  try {
    await api("/memory/user", { method: "PUT", body: { text: $("mem-user").value } });
    const m = await api("/memory/memory", { method: "PUT", body: { text: $("mem-memory").value } });
    for (const t of ["user", "memory"]) $("mem-" + t + "-count").textContent = `${m[t].used} / ${m[t].limit} نویسه`;
    $("memory-error").textContent = "";
    $("dlg-memory").close();
  } catch (e) { $("memory-error").textContent = e.message; }
}

// ------------------------------------------------------------------ skills dialog
const SKILL_TEMPLATE = "---\nname: my-skill\ndescription: یک خط دربارهٔ اینکه این مهارت کی به کار می‌آید\n---\n\n# عنوان\n\n1. مرحلهٔ اول\n2. مرحلهٔ دوم\n";

async function openSkills() {
  $("skill-error").textContent = "";
  await loadSkillList();
  $("dlg-skills").showModal();
}
async function loadSkillList() {
  const list = await api("/skills");
  const ul = $("skill-list");
  ul.replaceChildren();
  if (!list.length) ul.append(h("li", { class: "hint" }, h("span", { class: "sess-btn" }, "هنوز مهارتی ذخیره نشده")));
  for (const s of list) {
    ul.append(h("li", {}, h("button", { type: "button", class: s.name === state.selectedSkill ? "active" : "", onclick: () => selectSkill(s.name) },
      h("span", { dir: "ltr" }, s.name), h("small", { dir: "auto" }, s.description))));
  }
}
async function selectSkill(name) {
  state.selectedSkill = name;
  $("skill-error").textContent = "";
  try {
    const d = await api("/skills/" + encodeURIComponent(name));
    $("skill-name").value = name;
    $("skill-name").readOnly = true;
    $("skill-content").value = d.content.replace(/\n\n\[Supporting files:.*\]$/s, "");
    await loadSkillList();
  } catch (e) { $("skill-error").textContent = e.message; }
}
function newSkill() {
  state.selectedSkill = null;
  $("skill-name").value = "";
  $("skill-name").readOnly = false;
  $("skill-content").value = SKILL_TEMPLATE;
  loadSkillList();
}
async function saveSkill() {
  const name = $("skill-name").value.trim();
  try {
    await api("/skills/" + encodeURIComponent(name), { method: "PUT", body: { content: $("skill-content").value } });
    state.selectedSkill = name;
    $("skill-name").readOnly = true;
    $("skill-error").textContent = "";
    await loadSkillList();
  } catch (e) { $("skill-error").textContent = e.message; }
}
async function deleteSkill() {
  const name = state.selectedSkill;
  if (!name || !confirm(`مهارت «${name}» حذف شود؟`)) return;
  try { await api("/skills/" + encodeURIComponent(name), { method: "DELETE" }); newSkill(); } catch (e) { $("skill-error").textContent = e.message; }
}

// ------------------------------------------------------------------ chrome
function closeSidebar() { $("sidebar").classList.remove("open"); $("scrim").classList.remove("show"); $("scrim").hidden = true; }
function openSidebar() { $("sidebar").classList.add("open"); $("scrim").hidden = false; $("scrim").classList.add("show"); }

function applyTheme(t) {
  if (t === "light" || t === "dark") document.documentElement.dataset.theme = t;
  else delete document.documentElement.dataset.theme;
}
function toggleTheme() {
  const dark = document.documentElement.dataset.theme === "dark"
    || (!document.documentElement.dataset.theme && matchMedia("(prefers-color-scheme: dark)").matches);
  const next = dark ? "light" : "dark";
  applyTheme(next);
  store("dastyar-theme", next);
}

function showLogin() { $("app").hidden = true; $("login").hidden = false; $("login-token").focus(); }

async function start() {
  const status = await fetch("/api/auth/status", { credentials: "same-origin" }).then((r) => r.json());
  if (status.auth_required && !status.authed) { showLogin(); return; }
  $("login").hidden = true;
  $("app").hidden = false;
  await Promise.all([loadModels(), loadSettings()]);
  await refreshSessions();
  const m = location.hash.match(/^#\/s\/([\w-]+)/);
  const wanted = m && state.sessions.find((s) => s.id === m[1]);
  if (wanted) await openSession(wanted.id);
  else if (state.sessions.length && !state.sessions[0].turn_count) await openSession(state.sessions[0].id);
  else await newChat();
}

function wire() {
  applyTheme(store("dastyar-theme"));
  $("composer").addEventListener("submit", (e) => { e.preventDefault(); submitMessage(); });
  $("input").addEventListener("input", autosize);
  $("input").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); submitMessage(); }
  });
  $("stop").addEventListener("click", () => {
    if (state.pending) { // the server turns Stop into "reject" and resumes; drop the stale card
      removeApproval();
      state.pending = null;
      setRunning(true);
    }
    send({ type: "stop" });
  });
  $("new-chat").addEventListener("click", () => newChat().then(closeSidebar));
  $("search").addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(runSearch, 250); });
  $("search-form").addEventListener("submit", (e) => { e.preventDefault(); runSearch(); });
  $("burger").addEventListener("click", openSidebar);
  $("scrim").addEventListener("click", closeSidebar);
  $("toggle-theme").addEventListener("click", toggleTheme);
  $("open-settings").addEventListener("click", openSettings);
  $("settings-save").addEventListener("click", saveSettings);
  $("set-approval").addEventListener("change", approvalHint);
  $("open-memory").addEventListener("click", () => openMemory().catch((e) => alert(e.message)));
  $("memory-save").addEventListener("click", saveMemory);
  $("open-skills").addEventListener("click", () => openSkills().catch((e) => alert(e.message)));
  $("skill-save").addEventListener("click", saveSkill);
  $("skill-delete").addEventListener("click", deleteSkill);
  $("skill-new").addEventListener("click", newSkill);
  $("model-select").addEventListener("change", async () => {
    if (!state.current || state.running) return;
    try { await api("/sessions/" + state.current, { method: "PATCH", body: { model: $("model-select").value } }); } catch (e) { console.error(e); }
  });
  $("login-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    $("login-error").textContent = "";
    try {
      await api("/auth/login", { method: "POST", body: { token: $("login-token").value } });
      $("login-token").value = "";
      await start();
    } catch (err) { if (!(err instanceof AuthError)) $("login-error").textContent = err.message === "wrong token" ? "توکن اشتباه است." : err.message; }
  });
  window.addEventListener("hashchange", () => {
    const m = location.hash.match(/^#\/s\/([\w-]+)/);
    if (m && m[1] !== state.current) openSession(m[1]);
  });
}

wire();
start().catch((e) => { if (!(e instanceof AuthError)) { console.error(e); setBanner("خطا در راه‌اندازی: " + e.message); $("app").hidden = false; } });
