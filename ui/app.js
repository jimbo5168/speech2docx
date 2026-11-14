const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

let lastNotice = "";

function toast(msg) {
  if (!msg) return;
  const el = $("#toast");
  el.textContent = msg;
  el.classList.remove("hidden");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => el.classList.add("hidden"), 3600);
}

function api() {
  return window.pywebview && window.pywebview.api;
}

function statusClass(status) {
  if (status === "完成") return "ok";
  if (status === "失败") return "fail";
  if (status && status !== "等待") return "run";
  return "";
}

function statusText(job) {
  if (job.status === "转录中" && job.percent) return `转录中 ${job.percent}%`;
  if (job.status === "失败" && job.error) return `失败：${job.error}`;
  return job.status || "等待";
}

function renderQueue(state) {
  const jobs = state.jobs || [];
  const list = $("#queue");
  const empty = $("#queue-empty");
  const progress = $("#global-progress");
  const bar = $("#global-bar");
  const label = $("#global-label");
  list.innerHTML = "";
  empty.style.display = jobs.length ? "none" : "block";

  const active = jobs.find((j) => !["完成", "失败", "等待"].includes(j.status));
  if (active) {
    progress.classList.remove("hidden");
    bar.style.width = `${Math.max(active.percent || 0, 4)}%`;
    label.textContent = `${active.name} · ${statusText(active)}${active.message ? " · " + active.message : ""}`;
  } else {
    progress.classList.add("hidden");
    bar.style.width = "0%";
    label.textContent = "";
  }

  for (const job of jobs) {
    const li = document.createElement("li");
    const st = statusClass(job.status);
    li.innerHTML = `
      <div class="q-name" title="${job.source || ""}">${job.name || job.source}</div>
      <div class="q-status ${st}">${statusText(job)}</div>
      <div class="q-msg">${job.status === "失败" ? (job.error || "") : (job.message || "")}</div>
    `;
    if (job.status === "完成" && job.docx) {
      const actions = document.createElement("div");
      actions.className = "q-actions";
      const openBtn = document.createElement("button");
      openBtn.className = "btn primary";
      openBtn.type = "button";
      openBtn.textContent = "打开文档";
      openBtn.addEventListener("click", () => api().open_docx(job.id));
      const revealBtn = document.createElement("button");
      revealBtn.className = "btn";
      revealBtn.type = "button";
      revealBtn.textContent = "在 Finder 中显示";
      revealBtn.addEventListener("click", () => api().reveal_docx(job.id));
      actions.append(openBtn, revealBtn);
      li.appendChild(actions);
    }
    list.appendChild(li);
  }
}

function applySettings(state) {
  const s = state.settings || {};
  const provider = s.polish_provider || "local";
  const auth = (s.auth_mode || {})[provider] || "login";
  $$(`#provider-group input[value="${provider}"]`).forEach((el) => { el.checked = true; });
  $$(`#auth-group input[value="${auth}"]`).forEach((el) => { el.checked = true; });
  $$(`#english-group input[value="${s.english_output || "bilingual"}"]`).forEach((el) => { el.checked = true; });
  $("#opt-appendix").checked = !!s.include_raw_appendix;
  $("#template-name").textContent = s.template_display || "默认版式";
  const cloud = provider !== "local";
  $("#cloud-auth").classList.toggle("hidden", !cloud);
  $("#key-row").classList.toggle("hidden", !cloud || auth !== "api_key");
  const info = (s.login_status || {})[provider] || {};
  if (!cloud) {
    $("#login-status").textContent = "";
  } else if (auth === "login") {
    const inst = info.installed ? "已安装登录程序" : "尚未安装登录程序";
    const logn = info.logged_in ? "已登录" : "尚未登录";
    $("#login-status").textContent = `${inst}，${logn}。`;
  } else {
    $("#login-status").textContent = "金钥会保存在本机钥匙圈，不会写入设定文件。";
  }
}

function render(state) {
  if (!state) return;
  $$(`#lang-group input[value="${state.lang || "zh"}"]`).forEach((el) => { el.checked = true; });
  $("#out-path").textContent = state.out_dir_display || state.out_dir || "~/Downloads";
  $("#out-path").title = state.out_dir || "";
  renderQueue(state);
  applySettings(state);
  if (state.notice && state.notice !== lastNotice) {
    lastNotice = state.notice;
    toast(state.notice);
  }
}

async function refresh() {
  if (!api()) return;
  try {
    render(await api().get_state());
  } catch (err) {
    console.error(err);
  }
}

async function call(name, ...args) {
  if (!api()) return;
  const result = await api()[name](...args);
  if (result && typeof result === "object") render(result);
  return result;
}

function bind() {
  $$(".tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      $$(".tab").forEach((t) => t.classList.toggle("active", t === tab));
      $$(".panel").forEach((p) => p.classList.toggle("active", p.id === `panel-${tab.dataset.tab}`));
    });
  });

  $("#btn-files").addEventListener("click", () => call("pick_files"));
  $("#btn-folder").addEventListener("click", () => call("pick_folder"));
  $("#btn-outdir").addEventListener("click", () => call("pick_out_dir"));
  $("#btn-url").addEventListener("click", () => call("enqueue_url", $("#youtube-url").value));
  $("#youtube-url").addEventListener("keydown", (e) => {
    if (e.key === "Enter") call("enqueue_url", $("#youtube-url").value);
  });
  $("#btn-clear").addEventListener("click", () => call("clear_finished"));

  $$("#lang-group input").forEach((el) => {
    el.addEventListener("change", () => { if (el.checked) call("set_lang", el.value); });
  });

  const saveSettings = () => call("save_settings", {
    polish_provider: ($("#provider-group input:checked") || {}).value,
    auth_mode: ($("#auth-group input:checked") || {}).value,
    include_raw_appendix: $("#opt-appendix").checked,
    english_output: ($("#english-group input:checked") || {}).value,
  });
  $$("#provider-group input, #auth-group input, #english-group input").forEach((el) => {
    el.addEventListener("change", saveSettings);
  });
  $("#opt-appendix").addEventListener("change", saveSettings);
  $("#btn-login").addEventListener("click", () => {
    const provider = ($("#provider-group input:checked") || {}).value;
    call("start_login", provider);
  });
  $("#btn-save-key").addEventListener("click", () => {
    const provider = ($("#provider-group input:checked") || {}).value;
    call("save_api_key", provider, $("#api-key").value).then(() => { $("#api-key").value = ""; });
  });
  $("#btn-glossary").addEventListener("click", () => api() && api().open_glossary());
  $("#btn-template").addEventListener("click", () => call("pick_template"));
  $("#btn-template-reset").addEventListener("click", () => call("reset_template"));

  const zone = $("#drop-zone");
  ["dragenter", "dragover"].forEach((ev) => {
    zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.add("dragover"); });
  });
  ["dragleave", "drop"].forEach((ev) => {
    zone.addEventListener(ev, (e) => { e.preventDefault(); if (ev === "dragleave") zone.classList.remove("dragover"); });
  });
  zone.addEventListener("drop", () => zone.classList.remove("dragover"));
}

window.addEventListener("pywebviewready", () => {
  bind();
  refresh();
  setInterval(refresh, 400);
});
