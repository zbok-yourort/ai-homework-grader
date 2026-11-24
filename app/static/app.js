/* AI 作业批改 - 前端逻辑 */
"use strict";

const $ = (s) => document.querySelector(s);
const $$ = (s) => document.querySelectorAll(s);

const state = {
  hwFiles: [],
  ansFiles: [],
  session: null,
  crops: [],
  answerIds: [],
  selected: new Set(),
};

/* ---------- 通用 ---------- */

function esc(s) {
  return String(s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

let toastTimer = null;
function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.add("hidden"), 3200);
}

function show(view) {
  $$(".view").forEach((v) => v.classList.add("hidden"));
  $(view).classList.remove("hidden");
  window.scrollTo(0, 0);
}

function setProgress(text, failed) {
  $("#progress-text").textContent = text;
  $("#progress").classList.toggle("failed", !!failed);
  $(".spinner").style.display = failed ? "none" : "block";
}

/* ---------- 上传 ---------- */

function bindFiles(inputSel, previewSel, key) {
  $(inputSel).addEventListener("change", (e) => {
    state[key] = [...e.target.files];
    const box = $(previewSel);
    box.innerHTML = "";
    state[key].forEach((f) => {
      const img = document.createElement("img");
      img.src = URL.createObjectURL(f);
      img.onload = () => URL.revokeObjectURL(img.src);
      box.appendChild(img);
    });
  });
}
bindFiles("#file-hw", "#preview-hw", "hwFiles");
bindFiles("#file-ans", "#preview-ans", "ansFiles");

$("#btn-segment").addEventListener("click", async () => {
  if (!state.hwFiles.length) return toast("请先拍摄或选择作业照片");
  const fd = new FormData();
  state.hwFiles.forEach((f) => fd.append("hw_files", f));
  state.ansFiles.forEach((f) => fd.append("ans_files", f));

  const btn = $("#btn-segment");
  btn.disabled = true;
  btn.textContent = "本地分割中…";
  try {
    const r = await fetch("/api/segment", { method: "POST", body: fd });
    const data = await r.json();
    if (!r.ok) throw new Error(data.detail || "分割失败");
    state.session = data.session;
    state.answerIds = data.answer_ids || [];
    state.crops = data.crops || [];

    const normal = state.crops.filter((c) => !c.is_full_page);
    state.selected = new Set((normal.length ? normal : state.crops).map((c) => c.id));

    renderCrops(data.method);
    show("#view-crops");
    if (data.method === "projection") toast("题号识别不稳定，已按版面切块，请人工核对勾选");
  } catch (err) {
    toast(err.message || "分割失败");
  }
  btn.disabled = false;
  btn.textContent = "✂️ 分割题目";
});

/* ---------- 裁剪确认 ---------- */

function renderCrops(method) {
  const grid = $("#crops-grid");
  grid.innerHTML = "";
  state.crops.forEach((c) => {
    const card = document.createElement("div");
    card.className = "crop-card" + (state.selected.has(c.id) ? " on" : "") + (c.is_full_page ? " full-page" : "");
    card.innerHTML = `<img src="${c.thumb}" alt="">
      <div class="crop-label">${esc(c.label || "未命名区块")}${c.page > 0 ? ` · 第${c.page + 1}页` : ""}</div>`;
    card.addEventListener("click", () => {
      if (state.selected.has(c.id)) state.selected.delete(c.id);
      else state.selected.add(c.id);
      card.classList.toggle("on");
    });
    grid.appendChild(card);
  });
}

$("#btn-back").addEventListener("click", () => show("#view-upload"));

/* ---------- 批改 ---------- */

$("#btn-grade").addEventListener("click", () => {
  if (!state.selected.size) return toast("请至少勾选一道题");
  startGrade();
});

function parseSSE(part) {
  let event = "message";
  let data = null;
  for (const line of part.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) data = line.slice(5).trim();
  }
  if (data === null) return null;
  try { return { event, data: JSON.parse(data) }; }
  catch { return { event, data }; }
}

async function startGrade() {
  show("#view-result");
  $("#progress").classList.remove("hidden");
  $("#progress").classList.remove("failed");
  setProgress("连接 AI 中…");
  $("#stream-tail-wrap").classList.remove("hidden");
  $("#stream-tail").textContent = "";
  $("#summary").classList.add("hidden");
  $("#questions").innerHTML = "";
  $("#btn-again").classList.add("hidden");

  const body = {
    session: state.session,
    crop_ids: [...state.selected],
    answer_ids: state.answerIds,
    subject: $("#subject").value.trim(),
    note: $("#note").value.trim(),
  };

  let finished = false;
  try {
    const res = await fetch("/api/grade", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const d = await res.json().catch(() => ({}));
      throw new Error(d.detail || `HTTP ${res.status}`);
    }
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf("\n\n")) >= 0) {
        const ev = parseSSE(buf.slice(0, i));
        buf = buf.slice(i + 2);
        if (!ev) continue;
        if (ev.event === "status") setProgress(ev.data.text);
        else if (ev.event === "delta") appendTail(ev.data.text);
        else if (ev.event === "done") { finished = true; renderResult(ev.data.result); }
        else if (ev.event === "error") { finished = true; setProgress("❌ " + ev.data.message, true); }
      }
    }
    if (!finished) setProgress("❌ 连接中断，请重试", true);
  } catch (err) {
    setProgress("❌ " + (err.message || "请求失败"), true);
  }
}

function appendTail(text) {
  const el = $("#stream-tail");
  el.textContent = (el.textContent + text).slice(-600);
  el.scrollTop = el.scrollHeight;
}

const VERDICT_CLS = (v) =>
  v.includes("部分") ? "mid" :
  v.includes("错") || v.includes("误") ? "bad" :
  v.includes("对") || v.includes("正确") ? "ok" : "unk";

function renderResult(r) {
  $("#progress").classList.add("hidden");
  $("#stream-tail-wrap").classList.add("hidden");

  const s = r.summary || {};
  $("#summary").classList.remove("hidden");
  $("#summary").innerHTML = `
    <div class="sum-title">批改完成<small>${esc(r.subject || "")}${r.elapsed ? " · " + r.elapsed + "s" : ""}</small></div>
    <div class="sum-stats">
      <div class="stat ok"><b>${s.right ?? 0}</b><span>对</span></div>
      <div class="stat bad"><b>${s.wrong ?? 0}</b><span>错</span></div>
      <div class="stat mid"><b>${s.partial ?? 0}</b><span>部分对</span></div>
      <div class="stat unk"><b>${s.unknown ?? 0}</b><span>待定</span></div>
    </div>
    ${s.advice ? `<p class="advice">💡 ${esc(s.advice)}</p>` : ""}`;

  const thumbs = r.thumbs || [];
  $("#questions").innerHTML = (r.questions || [])
    .map((q, i) => qCard(q, thumbs[i]))
    .join("");
  $("#btn-again").classList.remove("hidden");
}

function qCard(q, thumb) {
  const v = q.verdict || "无法判断";
  const cls = VERDICT_CLS(v);
  const no = q.no ? `第 ${esc(String(q.no))} 题` : `题目 ${esc(String(q.index ?? "?"))}`;
  return `<div class="card q-card">
    <div class="q-head">
      ${thumb ? `<img class="q-thumb" src="${thumb}">` : ""}
      <div class="q-title">
        <span class="q-no">${no}</span>
        <span class="badge ${cls}">${esc(v)}</span>
        ${q.score ? `<span class="q-score">${esc(String(q.score))}</span>` : ""}
      </div>
    </div>
    <div class="q-body">
      ${q.student_answer ? `<p><label>我的作答</label>${esc(String(q.student_answer))}</p>` : ""}
      ${q.correct_answer ? `<p><label>正确答案</label>${esc(String(q.correct_answer))}</p>` : ""}
      ${q.mistake ? `<p class="mistake"><label>错因定位</label>${esc(String(q.mistake))}</p>` : ""}
      ${q.explanation ? `<p class="expl"><label>讲解</label>${esc(String(q.explanation))}</p>` : ""}
      ${(q.tags && q.tags.length) ? `<div class="tags">${q.tags.map((t) => `<span>${esc(String(t))}</span>`).join("")}</div>` : ""}
    </div>
  </div>`;
}

$("#btn-again").addEventListener("click", () => {
  state.hwFiles = []; state.ansFiles = [];
  state.session = null; state.crops = []; state.selected = new Set();
  $("#preview-hw").innerHTML = "";
  $("#preview-ans").innerHTML = "";
  $("#file-hw").value = "";
  $("#file-ans").value = "";
  show("#view-upload");
});

/* ---------- 历史 ---------- */

$("#btn-history").addEventListener("click", openHistory);
$("#btn-close-history").addEventListener("click", closeHistory);
$("#drawer-mask").addEventListener("click", closeHistory);

function closeHistory() {
  $("#history-drawer").classList.add("hidden");
  $("#drawer-mask").classList.add("hidden");
}

async function openHistory() {
  $("#history-drawer").classList.remove("hidden");
  $("#drawer-mask").classList.remove("hidden");
  const list = $("#history-list");
  list.innerHTML = `<div class="loading">加载中…</div>`;
  try {
    const r = await fetch("/api/history");
    const items = await r.json();
    list.innerHTML = items.length
      ? items.map((h) => `
        <div class="hist-item" data-id="${esc(h.id)}">
          <div class="hist-main">
            <b>${esc(h.subject || "未注明科目")}</b>
            <small>${new Date(h.created_at * 1000).toLocaleString()}</small>
          </div>
          <div class="hist-badges">
            <span class="mini ok">对 ${h.right ?? 0}</span>
            <span class="mini bad">错 ${h.wrong ?? 0}</span>
          </div>
        </div>`).join("")
      : `<div class="loading">还没有批改记录</div>`;
    list.querySelectorAll(".hist-item").forEach((el) =>
      el.addEventListener("click", () => loadHistory(el.dataset.id))
    );
  } catch {
    list.innerHTML = `<div class="loading">加载失败</div>`;
  }
}

async function loadHistory(id) {
  const r = await fetch(`/api/history/${id}`);
  if (!r.ok) return toast("记录不存在");
  const rec = await r.json();
  closeHistory();
  renderResult(rec);
  show("#view-result");
}
