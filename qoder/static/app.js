// 个性化复习助手 —— 前端逻辑（原生 JS，无构建步骤）
// 通过同源 /api/* 与 FastAPI 后端通信；后端再调用 PowerContext HTTP API。

const API = "";  // 同源；如需跨域可填 "http://127.0.0.1:8080"

const el = (id) => document.getElementById(id);

const state = {
  learner: "",
  subject: "",
  session: null,   // { session_id, questions, index, correct }
  subjects: [],
};

// --------------------------------------------------------------------- //
// 通用请求
// --------------------------------------------------------------------- //
async function api(path, options = {}) {
  const res = await fetch(API + path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = { detail: text }; }
  if (!res.ok) {
    const msg = (data && (data.detail || data.message)) || `HTTP ${res.status}`;
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return data;
}

function toast(msg, ms = 2600) {
  const t = el("toast");
  t.textContent = msg;
  t.classList.remove("hidden");
  clearTimeout(toast._timer);
  toast._timer = setTimeout(() => t.classList.add("hidden"), ms);
}

// --------------------------------------------------------------------- //
// 初始化
// --------------------------------------------------------------------- //
async function init() {
  el("learner").value = localStorage.getItem("review.learner") || "";
  el("learner").addEventListener("change", () => {
    localStorage.setItem("review.learner", el("learner").value.trim());
  });
  el("startBtn").addEventListener("click", onStart);
  el("overviewBtn").addEventListener("click", onOverview);

  await refreshHealth();
  await loadSubjects();
}

async function refreshHealth() {
  const box = el("status");
  try {
    const h = await api("/api/health");
    if (h.powercontext_alive) {
      box.className = "status status-ok";
      box.textContent = `PowerContext 已连接 · ${h.powercontext_url}`;
    } else {
      box.className = "status status-bad";
      box.textContent = "PowerContext 未就绪";
    }
  } catch (e) {
    box.className = "status status-bad";
    box.textContent = "后端不可达";
  }
}

async function loadSubjects() {
  try {
    const data = await api("/api/subjects");
    state.subjects = data.subjects || [];
    const sel = el("subject");
    sel.innerHTML = "";
    for (const s of state.subjects) {
      const opt = document.createElement("option");
      opt.value = s.name;
      opt.textContent = `${labelOf(s.name)}（${s.count} 题）`;
      sel.appendChild(opt);
    }
  } catch (e) {
    toast("加载学科失败：" + e.message);
  }
}

function labelOf(subject) {
  return { python: "Python", math: "数学", english: "英语" }[subject] || subject;
}

function readControls() {
  state.learner = el("learner").value.trim();
  state.subject = el("subject").value;
  if (!state.learner) {
    toast("请先填写学习者 ID");
    return false;
  }
  localStorage.setItem("review.learner", state.learner);
  return true;
}

// --------------------------------------------------------------------- //
// 开始一轮复习
// --------------------------------------------------------------------- //
async function onStart() {
  if (!readControls()) return;
  const size = parseInt(el("size").value || "5", 10);
  el("startBtn").disabled = true;
  try {
    const data = await api("/api/session/start", {
      method: "POST",
      body: JSON.stringify({ learner_id: state.learner, subject: state.subject, size }),
    });
    state.session = {
      scope_id: data.scope_id,
      session_id: data.session_id,
      questions: data.questions || [],
      index: 0,
      correct: 0,
      answered: 0,
    };
    el("sessionCard").classList.remove("hidden");
    el("overviewCard").classList.add("hidden");
    renderMemory(data);
    if (state.session.questions.length === 0) {
      renderDone("题库暂无该学科题目");
    } else {
      renderQuestion();
    }
    el("sessionCard").scrollIntoView({ behavior: "smooth" });
  } catch (e) {
    toast("开始复习失败：" + e.message);
  } finally {
    el("startBtn").disabled = false;
  }
}

function renderMemory(data) {
  el("rememberedSummary").textContent = data.remembered_summary || "";
  const chips = el("weakChips");
  chips.innerHTML = "";
  for (const w of (data.weak_points || []).slice(0, 8)) {
    const c = document.createElement("span");
    c.className = "chip";
    c.textContent = `${w.kp} · 错${w.misses}`;
    chips.appendChild(c);
  }
  const details = el("recallDetails");
  if (data.recall_text) {
    el("recallText").textContent = data.recall_text;
    details.classList.remove("hidden");
  } else {
    details.classList.add("hidden");
  }
  updateProgress();
}

function updateProgress() {
  const s = state.session;
  if (!s) return;
  const total = s.questions.length;
  el("progressText").textContent = `${s.answered} / ${total}`;
  el("scoreText").textContent = `答对 ${s.correct}`;
  el("progressBar").style.width = total ? `${(s.answered / total) * 100}%` : "0%";
}

// --------------------------------------------------------------------- //
// 渲染题目与作答
// --------------------------------------------------------------------- //
function renderQuestion() {
  const s = state.session;
  const q = s.questions[s.index];
  el("sessionDone").classList.add("hidden");
  const box = el("questionBox");
  box.innerHTML = "";

  const meta = document.createElement("div");
  meta.className = "q-meta";
  meta.innerHTML =
    `第 ${s.index + 1} 题 · 知识点：${q.kp}` +
    `<span class="q-tag tag-${q.origin}">${originLabel(q.origin)}</span>`;
  box.appendChild(meta);

  const prompt = document.createElement("div");
  prompt.className = "q-prompt";
  prompt.textContent = q.prompt;
  box.appendChild(prompt);

  const opts = document.createElement("div");
  opts.className = "options";
  q.options.forEach((text, i) => {
    const o = document.createElement("div");
    o.className = "option";
    o.dataset.index = String(i);
    o.innerHTML = `<span class="idx">${String.fromCharCode(65 + i)}</span><span>${escapeHtml(text)}</span>`;
    o.addEventListener("click", () => selectOption(opts, i));
    opts.appendChild(o);
  });
  box.appendChild(opts);

  const actions = document.createElement("div");
  actions.className = "q-actions";
  const submit = document.createElement("button");
  submit.className = "btn btn-primary";
  submit.textContent = "提交答案";
  submit.disabled = true;
  submit.addEventListener("click", () => onSubmit(q, opts, submit));
  actions.appendChild(submit);
  box.appendChild(actions);

  const fb = document.createElement("div");
  fb.id = "feedback";
  box.appendChild(fb);
}

function originLabel(origin) {
  return {
    weak_point: "靶向薄弱点",
    wrong_question: "错题重练",
    diagnostic: "诊断题",
    filler: "巩固题",
  }[origin] || origin;
}

function selectOption(container, index) {
  [...container.children].forEach((c) => c.classList.remove("selected"));
  const target = container.querySelector(`[data-index="${index}"]`);
  if (target) target.classList.add("selected");
  const btn = container.parentElement.querySelector(".q-actions .btn-primary");
  if (btn) btn.disabled = false;
}

function getSelected(container) {
  const sel = container.querySelector(".option.selected");
  return sel ? parseInt(sel.dataset.index, 10) : null;
}

async function onSubmit(q, optsContainer, submitBtn) {
  const given = getSelected(optsContainer);
  if (given === null) return;
  submitBtn.disabled = true;
  const s = state.session;
  try {
    const res = await api("/api/answer", {
      method: "POST",
      body: JSON.stringify({
        learner_id: state.learner,
        subject: q.subject,
        session_id: s.session_id,
        question_id: q.id,
        kp: q.kp,
        prompt: q.prompt,
        options: q.options,
        correct_index: q.correct_index,
        given_index: given,
      }),
    });
    // 揭示正确/错误
    [...optsContainer.children].forEach((c) => {
      const i = parseInt(c.dataset.index, 10);
      c.classList.add("disabled");
      c.style.pointerEvents = "none";
      if (i === res.correct_index) c.classList.add("correct");
      if (i === given && !res.is_correct) c.classList.add("wrong");
    });
    const fb = el("feedback");
    fb.className = "feedback " + (res.is_correct ? "ok" : "bad");
    fb.innerHTML =
      `<b>${res.is_correct ? "✓ 回答正确" : "✗ 回答错误"}</b>` +
      (res.explanation ? `<div class="explain">解析：${escapeHtml(res.explanation)}</div>` : "") +
      (res.weak_point && !res.is_correct
        ? `<div class="explain">已记录薄弱点「${res.weak_point.kp}」，累计错 ${res.weak_point.misses} 次。</div>`
        : "");

    s.answered += 1;
    if (res.is_correct) s.correct += 1;
    updateProgress();

    // 下一题按钮
    const next = document.createElement("button");
    next.className = "btn";
    next.textContent = s.index + 1 < s.questions.length ? "下一题 →" : "查看本轮小结";
    next.addEventListener("click", () => {
      if (s.index + 1 < s.questions.length) {
        s.index += 1;
        renderQuestion();
      } else {
        renderDone();
      }
    });
    submitBtn.replaceWith(next);
  } catch (e) {
    toast("提交失败：" + e.message);
    submitBtn.disabled = false;
  }
}

function renderDone(customText) {
  const s = state.session;
  el("questionBox").innerHTML = "";
  const done = el("sessionDone");
  done.classList.remove("hidden");
  if (customText) {
    done.innerHTML = `<div class="big">🤔</div><p>${customText}</p>`;
    return;
  }
  const total = s.questions.length;
  const acc = total ? Math.round((s.correct / total) * 100) : 0;
  done.innerHTML =
    `<div class="big">${acc >= 80 ? "🎉" : acc >= 60 ? "👍" : "💪"}</div>` +
    `<p>本轮答对 <b>${s.correct}</b> / ${total}（正确率 ${acc}%）。` +
    `错题与薄弱点已写入 PowerContext，下轮将自动复盘。</p>` +
    `<button class="btn btn-primary" id="againBtn">再来一轮</button>`;
  el("againBtn").addEventListener("click", onStart);
}

// --------------------------------------------------------------------- //
// 学习总览
// --------------------------------------------------------------------- //
async function onOverview() {
  if (!readControls()) return;
  try {
    const d = await api(`/api/overview?learner_id=${encodeURIComponent(state.learner)}`);
    el("overviewCard").classList.remove("hidden");
    renderOverview(d);
    el("overviewCard").scrollIntoView({ behavior: "smooth" });
  } catch (e) {
    toast("加载总览失败：" + e.message);
  }
}

function renderOverview(d) {
  const t = d.totals || {};
  el("overviewTotals").innerHTML = [
    ["答题总数", t.answers || 0],
    ["错题数", t.wrong_questions || 0],
    ["薄弱知识点", t.weak_points || 0],
    ["复习轮次", t.sessions || 0],
  ].map(([k, v]) => `<div class="total-pill">${k}<b>${v}</b></div>`).join("");

  el("overviewWeak").innerHTML = (d.weak_points || []).length
    ? table(["学科", "知识点", "错误", "作答", "正确率"],
        d.weak_points.map((w) => [labelOf(w.subject), w.kp, w.misses, w.attempts, `${Math.round(w.accuracy * 100)}%`]))
    : emptyRow("暂无薄弱知识点记录");

  el("overviewWrong").innerHTML = (d.wrong_questions || []).length
    ? table(["学科", "知识点", "题目", "你的答案", "正确答案"],
        d.wrong_questions.slice(0, 20).map((w) => [
          labelOf(w.subject), w.kp, truncate(w.prompt, 34),
          optLabel(w.options, w.given_index), optLabel(w.options, w.correct_index),
        ]))
    : emptyRow("错题本为空，继续保持！");

  el("overviewProgress").innerHTML = (d.latest_progress || []).length
    ? table(["会话", "学科", "答对/总数", "正确率", "重点薄弱点"],
        d.latest_progress.map((p) => [
          p.session_id, labelOf(p.subject), `${p.correct}/${p.total}`,
          `${Math.round(p.accuracy * 100)}%`, (p.weak_kps || []).slice(0, 3).join("、") || "—",
        ]))
    : emptyRow("暂无复习进度");
}

// --------------------------------------------------------------------- //
// 小工具
// --------------------------------------------------------------------- //
function table(headers, rows) {
  const th = headers.map((h) => `<th>${h}</th>`).join("");
  const trs = rows.map((r) => `<tr>${r.map((c) => `<td>${escapeHtml(String(c))}</td>`).join("")}</tr>`).join("");
  return `<table><thead><tr>${th}</tr></thead><tbody>${trs}</tbody></table>`;
}
function emptyRow(text) { return `<div class="empty">${text}</div>`; }
function optLabel(options, idx) {
  if (Array.isArray(options) && options[idx] != null) return `${String.fromCharCode(65 + idx)}. ${options[idx]}`;
  return idx == null ? "—" : String.fromCharCode(65 + idx);
}
function truncate(s, n) { return s && s.length > n ? s.slice(0, n) + "…" : (s || ""); }
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

init();
