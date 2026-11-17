const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

let lastNotice = "";
let selectedJob = null;

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

function updateRemaining() {
  $$("#queue li").forEach((row) => {
    const time = row.querySelector(".elapsed");
    const meter = row.querySelector("progress");
    const remaining = row.querySelector(".remaining");
    if (!remaining) return;
    if (!time || !meter || meter.value <= 0) {
      remaining.textContent = " · 正在估算剩余时间";
      return;
    }
    const elapsed = Math.max(0, Date.now() / 1000 - Number(time.dataset.start));
    const minutes = Math.max(1, Math.ceil(elapsed * (100 - meter.value) / meter.value / 60));
    remaining.textContent = ` · 预计剩余 ${minutes} 分钟`;
  });
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
    updateRemaining();
    return;
  }
  renderQueue.signature = signature;
  $("#queue-count").textContent = jobs.length;
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
    li.tabIndex = 0;
    li.classList.toggle("selected", selectedJob === job.id);
    li.setAttribute("aria-label", `${job.name || "文件"} · ${statusText(job)}`);
    const select = () => {
      selectedJob = job.id;
      $$("#queue li").forEach((row) => row.classList.toggle("selected", row === li));
    };
    li.addEventListener("click", select);
    li.addEventListener("keydown", (event) => {
      if (event.target !== li) return;
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        select();
      }
      const adjacent = {ArrowDown: li.nextElementSibling, ArrowUp: li.previousElementSibling};
      const next = adjacent[event.key];
      if (next) {
        event.preventDefault();
        next.focus();
        next.click();
      }
    });
    const icon = document.createElement("div");
    icon.className = "q-icon";
    icon.setAttribute("aria-hidden", "true");
    icon.innerHTML = '<svg viewBox="0 0 24 28"><path d="M14 2H5v24h15V8zM14 2v6h6M8 14h8M8 18h8M8 22h5"/></svg>';
    li.append(icon);
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
    status.append(document.createTextNode(st === "fail" ? "失败" : statusText(job)));
    status.title = statusText(job);
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
    if (st === "run") {
      const remaining = document.createElement("span");
      remaining.className = "remaining";
      message.append(remaining);
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
      openBtn.textContent = "打开";
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
  updateRemaining();
}

function applySettings(state) {
  const s = state.settings || {};
  const provider = s.polish_provider || "none";
  const localOption = $('#provider-group input[value="local"]');
  localOption.disabled = !s.local_polish_available;
  $('#local-polish-note').textContent = s.local_polish_available ? "千问已就绪，可按需开启润色。" : "润色需要 LM Studio，可不用";
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
  const cloud = ["claude", "gpt", "gemini"].includes(provider);
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

let modelState = {status: "idle"};
const downloadBytes = (value) => `${(value / 1e9).toFixed(2)} GB`;
const downloadTime = (seconds) => seconds == null ? "测量中" : `${Math.max(0, Math.ceil(seconds / 60))} 分钟`;

function drawModelDownload(state) {
  modelState = state;
  const percent = state.total ? Math.min(100, 100 * state.downloaded / state.total) : 0;
  $("#model-bar").style.width = `${percent}%`;
  $("#model-bar").parentElement.setAttribute("aria-valuenow", Math.round(percent));
  const labels = {idle: "等待测速", checking: "正在校验本机文件…", probing: "正在测速 Hugging Face 与 ModelScope…",
    prepared: "准备就绪，请确认后开始下载", downloading: "正在下载", verifying: "正在校验 SHA256…",
    paused: "已暂停 · 重启后可继续", done: "全部文件 SHA256 验证通过 · 可离线识别", error: "下载未完成，可重试"};
  $("#model-label").textContent = `${labels[state.status] || state.status} · ${Math.round(percent)}% · ${downloadBytes(state.downloaded || 0)} / ${downloadBytes(state.total || 0)}`;
  const probes = state.probes || [];
  $("#model-source").textContent = state.source
    ? `${state.source} · ${(state.speed / 1e6).toFixed(2)} MB/s · 预计${state.status === "prepared" ? "下载用时" : "剩余"} ${downloadTime(state.remaining)}`
    : probes.length ? "两个下载源暂不可用，请重新测速。" : "正在检查本机模型与下载源…";
  const sourceStatus = {done: "全部文件验证通过", paused: "已暂停", verifying: "正在逐个验证 SHA256"};
  if (state.source && sourceStatus[state.status]) {
    $("#model-source").textContent = `${state.source} · ${sourceStatus[state.status]}`;
  }
  $("#model-error").textContent = state.error || "";
  const button = $("#btn-model-pause");
  button.textContent = {prepared: "开始下载", paused: "继续下载", error: "重新测速", done: "已验证"}[state.status] || "暂停";
  button.disabled = ["idle", "checking", "probing", "verifying", "done"].includes(state.status);
}

async function openModelDownload(onlyIfNeeded = false) {
  if (!api()) return;
  if (!onlyIfNeeded) $("#model-dialog").showModal();
  try {
    drawModelDownload(await api().prepare_model_download());
    if (onlyIfNeeded) {
      // Wait for local verification before deciding whether first-run setup is needed.
      const timer = setInterval(async () => {
        const state = await api().get_model_download();
        if (["checking", "idle"].includes(state.status)) return;
        clearInterval(timer);
        drawModelDownload(state);
        if (!state.ready && !$("#model-dialog").open) $("#model-dialog").showModal();
      }, 500);
    }
  } catch (err) { toast(err.message || String(err)); }
}

function bind() {
  const navigate = (queue) => {
    $("#panel-convert").classList.toggle("queue-view", queue);
    $("#nav-convert").classList.toggle("selected", !queue);
    $("#nav-queue").classList.toggle("selected", queue);
    $("#view-title").textContent = queue ? "队列与历史" : "转换";
  };
  $("#nav-convert").addEventListener("click", () => navigate(false));
  $("#nav-queue").addEventListener("click", () => navigate(true));
  try { document.documentElement.dataset.theme = localStorage.getItem("wwws-theme") || "dark"; } catch (_) {}
  $("#btn-theme").addEventListener("click", () => {
    const theme = document.documentElement.dataset.theme === "light" ? "dark" : "light";
    document.documentElement.dataset.theme = theme;
    try { localStorage.setItem("wwws-theme", theme); } catch (_) {}
  });
  $("#btn-model").addEventListener("click", openModelDownload);
  $("#btn-close-model").addEventListener("click", () => $("#model-dialog").close());
  $("#btn-model-pause").addEventListener("click", async () => {
    const method = ["downloading", "verifying"].includes(modelState.status)
      ? "pause_model_download"
      : modelState.status === "error" ? "prepare_model_download" : "start_model_download";
    try { drawModelDownload(await api()[method]()); }
    catch (err) { toast(err.message || String(err)); }
  });
  setInterval(async () => {
    if (api() && $("#model-dialog").open) {
      try { drawModelDownload(await api().get_model_download()); }
      catch (err) { console.error(err); }
    }
  }, 500);
  $("#btn-settings").addEventListener("click", () => $("#settings-dialog").showModal());
  $("#btn-close-settings").addEventListener("click", () => $("#settings-dialog").close());
  $("#btn-welcome").addEventListener("click", async () => {
    const provider = $("input[name='welcome-provider']:checked").value;
    const result = await call("save_settings", {polish_provider: provider});
    if (!result) return;
    try { localStorage.setItem("wwws-welcome-seen", "1"); } catch (_) {}
    $("#welcome-dialog").close();
    if (["claude", "gpt", "gemini"].includes(provider)) $("#settings-dialog").showModal();
    openModelDownload();
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
  else openModelDownload(true);
  setInterval(refresh, 400);
});
