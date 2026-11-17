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
  const stage = {download: "下载", extract: "提取音轨", transcribe: "识别", proofread: "校对", polish: "修饰", write: "生成 Word"}[job.stage];
  if (stage && !["完成", "失败", "等待"].includes(job.status)) return stage + (job.stage === "transcribe" && job.percent != null ? ` ${job.percent}%` : "中");
  if (job.status === "转录中" && job.percent) return `转录中 ${job.percent}%`;
  if (job.status === "失败" && job.error) return `失败：${job.error}`;
  return job.status || "等待";
}

function elapsedText(start, end) {
  const seconds = Math.max(0, Math.floor((end || Date.now() / 1000) - start));
  return ` · 已用时 ${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`;
}

function renderQueue(state) {
  const jobs = state.jobs || [];
  const signature = JSON.stringify(jobs);
  if (renderQueue.signature === signature) {
    $$(".elapsed").forEach((el) => { el.textContent = elapsedText(Number(el.dataset.start), Number(el.dataset.end) || null); });
    return;
  }
  renderQueue.signature = signature;
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
    const measured = active.stage === "transcribe" || (!active.stage && active.status === "转录中");
    bar.style.width = measured ? `${Math.min(100, Math.max(active.percent || 0, 4))}%` : "35%";
    bar.classList.toggle("indeterminate", !measured);
    label.textContent = `${active.name} · ${statusText(active)}${active.message ? " · " + active.message : ""}`;
  } else {
    progress.classList.add("hidden");
    bar.style.width = "0%";
    label.textContent = "";
  }

  for (const job of jobs) {
    const li = document.createElement("li");
    const st = statusClass(job.status);
    const name = document.createElement("div");
    name.className = "q-name";
    name.title = job.source || "";
    name.textContent = job.name || job.source || "未命名文件";
    const status = document.createElement("div");
    status.className = `q-status ${st}`;
    if (st === "run") {
      const spinner = document.createElement("span");
      spinner.className = "spinner";
      spinner.setAttribute("aria-hidden", "true");
      status.append(spinner);
    }
    status.append(document.createTextNode(statusText(job)));
    const message = document.createElement("div");
    message.className = "q-msg";
    const elapsed = job.started_at ? Math.max(0, Math.floor((job.finished_at || Date.now() / 1000) - job.started_at)) : null;
    message.textContent = (job.status === "失败" ? job.error : job.message) || "";
    if (elapsed !== null) {
      const time = document.createElement("span");
      time.className = "elapsed";
      time.dataset.start = job.started_at;
      time.dataset.end = job.finished_at || "";
      time.textContent = elapsedText(job.started_at, job.finished_at);
      message.append(time);
    }
    li.append(name, status, message);
    if (st === "run" && job.stage === "transcribe" && job.percent != null) {
      const meter = document.createElement("progress");
      meter.className = "job-progress";
      meter.max = 100;
      meter.value = Math.max(0, Math.min(100, job.percent));
      meter.setAttribute("aria-label", `${job.name} 识别进度`);
      li.append(meter);
    }
    if (job.status === "完成" && job.docx) {
      const actions = document.createElement("div");
      actions.className = "q-actions";
      const openBtn = document.createElement("button");
      openBtn.className = "btn primary";
      openBtn.type = "button";
      openBtn.textContent = "打开 Word";
      openBtn.addEventListener("click", () => api().open_docx(job.id));
      const revealBtn = document.createElement("button");
      revealBtn.className = "btn";
      revealBtn.type = "button";
      revealBtn.textContent = "在访达中显示";
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
  $$(`#zhasr-group input[value="${s.zh_asr_model || "buddhist"}"]`).forEach((el) => { el.checked = true; });
  $("#opt-appendix").checked = !!s.include_raw_appendix;
  if ($("#opt-summary")) $("#opt-summary").checked = !!s.include_summary;
  if ($("#opt-headings")) $("#opt-headings").checked = !!s.include_headings;
  const leadBox = $("#lead-prompt");
  if (leadBox && document.activeElement !== leadBox && !leadBox.dataset.dirty) {
    leadBox.value = s.polish_lead_prompt || s.polish_lead_prompt_default || "";
    leadBox.dataset.default = s.polish_lead_prompt_default || "";
  }
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
    $("#login-status").textContent = "密钥会保存在本机钥匙圈，不会写入设定文件。";
  }
}

function render(state) {
  if (!state) return;
  $$(`#lang-group input[value="${state.lang || "zh"}"]`).forEach((el) => { el.checked = true; });
  $("#out-path").textContent = state.out_dir_display || state.out_dir || "~/Downloads";
  $("#out-path").title = state.out_dir || "";
  $(".app").classList.toggle("has-jobs", !!(state.jobs && state.jobs.length));
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
  try {
    const result = await api()[name](...args);
    if (result && typeof result === "object") render(result);
    return result;
  } catch (err) {
    toast("操作未完成，请重试。" + (err.message || ""));
    return null;
  }
}

function bind() {
  $("#btn-settings").addEventListener("click", () => $("#settings-dialog").showModal());
  $("#btn-close-settings").addEventListener("click", () => $("#settings-dialog").close());
  $("#btn-welcome").addEventListener("click", async () => {
    const provider = $("input[name='welcome-provider']:checked").value;
    const result = await call("save_settings", {polish_provider: provider});
    if (!result) return;
    try { localStorage.setItem("wwws-welcome-seen", "1"); } catch (_) {}
    $("#welcome-dialog").close();
    if (provider !== "local") $("#settings-dialog").showModal();
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

  const saveSettings = (extra) => call("save_settings", Object.assign({
    polish_provider: ($("#provider-group input:checked") || {}).value,
    auth_mode: ($("#auth-group input:checked") || {}).value,
    include_raw_appendix: $("#opt-appendix").checked,
    include_summary: !!($("#opt-summary") && $("#opt-summary").checked),
    include_headings: !!($("#opt-headings") && $("#opt-headings").checked),
    polish_lead_prompt: ($("#lead-prompt") && $("#lead-prompt").value) || "",
    english_output: ($("#english-group input:checked") || {}).value,
    zh_asr_model: ($("#zhasr-group input:checked") || {}).value,
  }, extra || {}));
  $("#lead-prompt").addEventListener("input", () => { $("#lead-prompt").dataset.dirty = "1"; });
  $$("#provider-group input, #auth-group input, #english-group input, #zhasr-group input").forEach((el) => {
    el.addEventListener("change", () => saveSettings());
  });
  $("#opt-appendix").addEventListener("change", () => saveSettings());
  if ($("#opt-summary")) $("#opt-summary").addEventListener("change", () => saveSettings());
  if ($("#opt-headings")) $("#opt-headings").addEventListener("change", () => saveSettings());
  if ($("#btn-save-lead")) $("#btn-save-lead").addEventListener("click", () => saveSettings().then((result) => { if (result) { delete $("#lead-prompt").dataset.dirty; toast("校对提示词已保存"); } }));
  if ($("#btn-reset-lead")) $("#btn-reset-lead").addEventListener("click", () => {
    const box = $("#lead-prompt");
    if (box) box.value = box.dataset.default || "";
    saveSettings().then((result) => { if (result) delete box.dataset.dirty; });
  });
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
  zone.addEventListener("drop", (event) => {
    zone.classList.remove("dragover");
    // Native file drops remain handled by Python's pywebview drop listener.
    if (!event.dataTransfer.files.length) {
      const text = event.dataTransfer.getData("text/plain");
      if (text) { $("#youtube-url").value = text; call("enqueue_url", text); }
    }
  });
}

window.addEventListener("pywebviewready", () => {
  if (window.uiBound) return;
  window.uiBound = true;
  bind();
  refresh();
  let seen = false;
  try { seen = localStorage.getItem("wwws-welcome-seen") === "1"; } catch (_) { seen = true; }
  if (!seen) $("#welcome-dialog").showModal();
  setInterval(refresh, 400);
});
