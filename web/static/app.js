// 유튜브 음악 추출 - 웹 화면
// 서버(web/server.py)의 /api/state 를 주기적으로 받아 목록을 그리고, 버튼은 /api/* 로 요청한다.
"use strict";

const $ = (s) => document.querySelector(s);
const OPT = window.YTA_OPTIONS || {};

const ui = {
  state: null,
  version: -1,
  lastMsg: 0,
  selected: new Set(),
  local: false,
  android: false, // 휴대폰 자체가 서버 → 파일이 이미 휴대폰에 있으므로 '받기'가 필요 없다
  pollTimer: null,
  rows: new Map(), // uid → <li>
};

// ---------------------------------------------------------------- 서버 통신
async function api(path, body) {
  const opts = body === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Requested-With": "yta" },
    body: JSON.stringify(body),
  };
  const res = await fetch(path, opts);
  if (res.status === 401) { showLogin(); throw new Error("login"); }
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data.ok === false) throw new Error(data.error || `오류 (${res.status})`);
  return data;
}

async function act(path, body, okText) {
  try {
    const r = await api(path, body || {});
    if (okText) toast(typeof okText === "function" ? okText(r) : okText);
    poll(true);
    return r;
  } catch (e) {
    if (e.message !== "login") toast(e.message, "error");
    return null;
  }
}

function toast(text, level) {
  const el = document.createElement("div");
  el.className = "toast" + (level === "error" ? " error" : "");
  el.textContent = text;
  $("#toasts").appendChild(el);
  setTimeout(() => el.remove(), level === "error" ? 7000 : 3500);
}

// ---------------------------------------------------------------- 로그인
function showLogin() {
  clearTimeout(ui.pollTimer);
  $("#app").hidden = true;
  $("#login").hidden = false;
  setTimeout(() => $("#pin").focus(), 50);
}

$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("#login-error").textContent = "";
  try {
    await api("/api/login", { pin: $("#pin").value });
    start();
  } catch (err) {
    if (err.message !== "login") $("#login-error").textContent = err.message;
  }
});

async function start() {
  const me = await fetch("/api/me").then((r) => r.json()).catch(() => ({}));
  if (!me.authed) return showLogin();
  ui.local = !!me.local;
  ui.android = !!me.android;
  $("#login").hidden = true;
  $("#app").hidden = false;
  document.querySelectorAll(".pc-only").forEach((el) => (el.hidden = !ui.local || ui.android));
  if (ui.local && !ui.android) loadAccess();
  poll(true);
}

// ---------------------------------------------------------------- PC: 휴대폰 접속 안내
async function loadAccess() {
  let info;
  try { info = await api("/api/access"); } catch { return; }
  if (!info.lan || !info.urls.length) return;
  $("#access").hidden = false;
  $("#access-pin").textContent = info.pin;
  const box = $("#access-urls");
  box.replaceChildren();
  // Tailscale(밖에서도) 주소를 먼저
  const urls = [...info.urls].sort((a, b) => (b.where.includes("Tailscale") ? 1 : 0) - (a.where.includes("Tailscale") ? 1 : 0));
  for (const u of urls) {
    const p = document.createElement("p");
    p.className = "access-url";
    p.textContent = u.url;
    const s = document.createElement("small");
    s.textContent = u.where;
    p.appendChild(s);
    box.appendChild(p);
  }
  drawQr(urls[0].url);
}

function drawQr(text) {
  const target = $("#access-qr");
  const render = () => {
    const qr = window.qrcode(0, "M");
    qr.addData(text);
    qr.make();
    target.innerHTML = qr.createSvgTag({ cellSize: 4, margin: 0, scalable: true });
  };
  if (window.qrcode) return render();
  const s = document.createElement("script");
  s.src = "https://cdn.jsdelivr.net/npm/qrcode-generator@1.4.4/qrcode.min.js";
  s.onload = render;
  s.onerror = () => (target.hidden = true); // 인터넷이 안 되면 QR 없이 주소만
  document.head.appendChild(s);
}

$("#btn-reset-pin").addEventListener("click", async () => {
  if (!confirm("PIN 을 새로 만들까요?\n로그인해 둔 휴대폰은 모두 새 PIN 으로 다시 로그인해야 합니다.")) return;
  const r = await act("/api/reset_pin", {}, "새 PIN 을 만들었습니다");
  if (r) $("#access-pin").textContent = r.pin;
});

// ---------------------------------------------------------------- 상태 폴링
async function poll(now) {
  clearTimeout(ui.pollTimer);
  try {
    const s = await api(`/api/state?since=${ui.lastMsg}`);
    for (const m of s.messages) {
      ui.lastMsg = Math.max(ui.lastMsg, m.id);
      toast(m.text, m.level);
    }
    if (s.version !== ui.version) {
      ui.state = s;
      ui.version = s.version;
      render();
    }
  } catch (e) {
    if (e.message === "login") return;
    // 서버가 꺼졌거나 연결이 끊긴 경우: 조용히 다시 시도
  }
  const busy = ui.state && (ui.state.counts.active || ui.state.pending);
  ui.pollTimer = setTimeout(poll, document.hidden ? 5000 : busy ? 800 : 2500);
}
document.addEventListener("visibilitychange", () => { if (!document.hidden) poll(true); });

// ---------------------------------------------------------------- 화면 그리기
function render() {
  const s = ui.state;
  const c = s.counts;

  // 설정 반영
  document.querySelectorAll(".seg button").forEach((b) =>
    b.setAttribute("aria-checked", String(b.dataset.format === s.settings.format)));
  $("#auto-start").checked = !!s.settings.auto_start;

  // 경고 / 업데이트
  const w = $("#warnings");
  w.replaceChildren(...s.warnings.map((t) => { const d = document.createElement("div"); d.className = "banner"; d.textContent = t; return d; }));
  const ub = $("#update-banner");
  if (s.update && s.update.latest) {
    ub.hidden = false;
    ub.replaceChildren();
    const t = document.createElement("span");
    t.textContent = `yt-dlp 새 버전이 있습니다 (${s.update.current} → ${s.update.latest}). 유튜브 변경 대응을 위해 업데이트를 권장합니다.`;
    ub.appendChild(t);
    if (s.update.can_update) {
      const b = document.createElement("button");
      b.className = "btn primary";
      b.style.height = "36px";
      b.textContent = s.updating ? "업데이트 중..." : "업데이트";
      b.disabled = s.updating;
      b.onclick = () => act("/api/update_ytdlp");
      ub.appendChild(b);
    }
  } else ub.hidden = true;

  // 요약
  const parts = [`총 ${c.total}곡`];
  if (c.done) parts.push(`완료 ${c.done}`);
  if (c.failed) parts.push(`실패 ${c.failed}`);
  if (c.active) parts.push(`진행 ${c.active}`);
  if (s.pending) parts.push(`조회 중 ${s.pending}`);
  $("#summary-text").textContent = c.total || s.pending ? parts.join(" · ") : "목록이 비어 있습니다";
  $("#overall-bar").style.width = `${s.overall}%`;
  $("#empty").hidden = c.total > 0 || s.pending > 0;

  // 목록 (바뀐 줄만 갱신 → 스크롤/이미지가 튀지 않게)
  const list = $("#list");
  const alive = new Set();
  s.tracks.forEach((t, i) => {
    alive.add(t.uid);
    let li = ui.rows.get(t.uid);
    if (!li) {
      li = buildRow(t.uid);
      ui.rows.set(t.uid, li);
    }
    fillRow(li, t);
    if (list.children[i] !== li) list.insertBefore(li, list.children[i] || null);
  });
  for (const [uid, li] of ui.rows) {
    if (!alive.has(uid)) { li.remove(); ui.rows.delete(uid); ui.selected.delete(uid); }
  }
  const doneWithFile = ui.android ? 0 : s.tracks.filter((t) => t.has_file).length;
  $("#btn-zip-done").hidden = doneWithFile < 2;
  $("#btn-zip-done").textContent = `완료곡 받기 (${doneWithFile})`;
  renderSelection();
}

function buildRow(uid) {
  const li = document.createElement("li");
  li.className = "item";
  li.dataset.uid = uid;
  li.innerHTML = `
    <div class="chk" aria-hidden="true">✓</div>
    <img class="thumb" alt="" loading="lazy">
    <div class="info">
      <div class="t1"></div>
      <div class="t2"></div>
      <div class="st"><span class="txt"></span></div>
      <div class="fail-msg" hidden></div>
    </div>
    <div class="acts"></div>`;
  li.addEventListener("click", (e) => {
    if (e.target.closest(".acts")) return;
    toggleSelect(uid);
  });
  return li;
}

function fillRow(li, t) {
  li.classList.toggle("selected", ui.selected.has(t.uid));
  const img = li.querySelector(".thumb");
  const thumb = t.thumbnail || `https://i.ytimg.com/vi/${t.video_id}/mqdefault.jpg`;
  if (img.getAttribute("src") !== thumb) img.src = thumb;
  li.querySelector(".t1").textContent = t.title || t.video_id;
  li.querySelector(".t2").textContent = [t.artist || "가수 미상", t.album, t.duration].filter(Boolean).join(" · ");

  const st = li.querySelector(".st");
  st.className = `st ${t.status}${t.active ? " active" : ""}`;
  let text = t.status_text;
  if (["DOWNLOADING", "CONVERTING", "TAGGING"].includes(t.status)) text += ` ${Math.round(t.progress)}%`;
  if (t.status === "RETRYING") text = t.message || "재시도 대기 중";
  if (ui.android && t.status === "DONE") text = "완료 · 음악 폴더에 저장됨";
  const key = `${t.status}|${Math.round(t.progress)}|${t.already}|${text}`;
  if (st.dataset.key !== key) {
    st.dataset.key = key;
    st.replaceChildren();
    if (t.already && t.status === "READY") {
      const tag = document.createElement("span");
      tag.className = "tag";
      tag.textContent = "이미 받음";
      st.appendChild(tag);
    }
    const span = document.createElement("span");
    span.className = "txt";
    span.textContent = text;
    st.appendChild(span);
    if (t.active && t.status !== "RETRYING" && t.status !== "QUEUED") {
      const bar = document.createElement("div");
      bar.className = "pbar";
      bar.innerHTML = `<div style="width:${t.progress}%"></div>`;
      st.appendChild(bar);
    }
  }
  const fm = li.querySelector(".fail-msg");
  fm.hidden = t.status !== "FAILED" || !t.message;
  fm.textContent = t.status === "FAILED" ? t.message : "";

  // 오른쪽 버튼: 받기 / 재시도 / 편집
  const acts = li.querySelector(".acts");
  const actKey = `${t.has_file}|${t.status}|${t.active}`;
  if (acts.dataset.key !== actKey) {
    acts.dataset.key = actKey;
    acts.replaceChildren();
    if (t.has_file && !ui.android) {
      const a = document.createElement("a");
      a.className = "get";
      a.href = `/api/file/${t.uid}`;
      a.setAttribute("download", t.file_name);
      a.textContent = "받기";
      acts.appendChild(a);
    } else if (t.status === "FAILED" || t.status === "CANCELLED") {
      acts.appendChild(smallBtn("재시도", () => act("/api/retry", { uids: [t.uid] })));
    } else if (t.active) {
      acts.appendChild(smallBtn("취소", () => act("/api/cancel", { uids: [t.uid] })));
    }
    if (!t.active) acts.appendChild(smallBtn("✎", () => openEdit([t.uid]), "편집"));
  }
}

function smallBtn(text, fn, label) {
  const b = document.createElement("button");
  b.type = "button";
  b.textContent = text;
  if (label) b.setAttribute("aria-label", label);
  b.addEventListener("click", fn);
  return b;
}

// ---------------------------------------------------------------- 선택
function toggleSelect(uid) {
  ui.selected.has(uid) ? ui.selected.delete(uid) : ui.selected.add(uid);
  const li = ui.rows.get(uid);
  if (li) li.classList.toggle("selected", ui.selected.has(uid));
  renderSelection();
}

function renderSelection() {
  const n = ui.selected.size;
  $("#bar-normal").hidden = n > 0;
  $("#bar-selected").hidden = n === 0;
  $("#sel-count").textContent = `${n}곡 선택`;
  const sel = selectedTracks();
  $("#btn-sel-zip").hidden = ui.android || !sel.some((t) => t.has_file);
  $("#btn-sel-retry").hidden = !sel.some((t) => ["FAILED", "CANCELLED", "READY"].includes(t.status));
  $("#btn-sel-retry").textContent = sel.some((t) => t.status === "READY") ? "다운로드" : "재시도";
  $("#btn-sel-edit").hidden = sel.some((t) => t.active);
  $("#btn-select-all").textContent = n && n === (ui.state?.tracks.length || 0) ? "선택 해제" : "전체 선택";
}

function selectedTracks() {
  if (!ui.state) return [];
  return ui.state.tracks.filter((t) => ui.selected.has(t.uid));
}

function clearSelection() {
  ui.selected.clear();
  for (const li of ui.rows.values()) li.classList.remove("selected");
  renderSelection();
}

$("#btn-select-all").addEventListener("click", () => {
  const all = ui.state?.tracks || [];
  if (ui.selected.size === all.length) return clearSelection();
  all.forEach((t) => ui.selected.add(t.uid));
  for (const li of ui.rows.values()) li.classList.add("selected");
  renderSelection();
});
$("#btn-sel-clear").addEventListener("click", clearSelection);

$("#btn-sel-delete").addEventListener("click", async () => {
  const sel = selectedTracks();
  const busy = sel.filter((t) => t.active).length;
  const msg = busy ? `진행 중인 ${busy}곡은 취소하고, ${sel.length}곡을 목록에서 삭제할까요?` : `${sel.length}곡을 목록에서 삭제할까요?\n(이미 저장된 파일은 지워지지 않습니다)`;
  if (!confirm(msg)) return;
  await act("/api/delete", { uids: [...ui.selected] });
  clearSelection();
});
$("#btn-sel-retry").addEventListener("click", async () => {
  const sel = selectedTracks();
  const ready = sel.filter((t) => t.status === "READY").map((t) => t.uid);
  const failed = sel.filter((t) => ["FAILED", "CANCELLED"].includes(t.status)).map((t) => t.uid);
  if (ready.length) await act("/api/download", { uids: ready });
  if (failed.length) await act("/api/retry", { uids: failed });
  clearSelection();
});
$("#btn-sel-edit").addEventListener("click", () => openEdit([...ui.selected]));
$("#btn-sel-zip").addEventListener("click", () => {
  const withFile = selectedTracks().filter((t) => t.has_file);
  if (withFile.length === 1) return downloadUrl(`/api/file/${withFile[0].uid}`);
  downloadUrl(`/api/zip?uids=${withFile.map((t) => t.uid).join(",")}`);
});
$("#btn-zip-done").addEventListener("click", () => {
  const uids = (ui.state?.tracks || []).filter((t) => t.has_file).map((t) => t.uid);
  if (uids.length) downloadUrl(`/api/zip?uids=${uids.join(",")}`);
});

function downloadUrl(url) {
  const a = document.createElement("a");
  a.href = url;
  a.setAttribute("download", "");
  document.body.appendChild(a);
  a.click();
  a.remove();
  toast("휴대폰으로 받는 중... (여러 곡은 zip 으로 묶어서 받습니다)");
}

// ---------------------------------------------------------------- 링크 추가
const urlInput = $("#url-input");
urlInput.addEventListener("input", autoGrow);
urlInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); $("#add-form").requestSubmit(); }
});
function autoGrow() {
  urlInput.style.height = "auto";
  urlInput.style.height = Math.min(urlInput.scrollHeight + 2, 140) + "px";
}

$("#add-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const text = urlInput.value.trim();
  if (!text) return;
  let items;
  try { items = (await api("/api/analyze", { text })).items; } catch (err) { return toast(err.message, "error"); }
  const bad = items.filter((i) => i.kind === "invalid");
  if (bad.length) toast(bad.slice(0, 3).map((i) => `${i.url.slice(0, 40)} → ${i.reason}`).join("\n"), "error");
  const jobs = [];
  for (const i of items) {
    if (i.kind === "invalid") continue;
    if (i.kind === "video_in_playlist") {
      const answer = await askPlaylist(i.url);
      if (answer === "skip") continue;
      jobs.push({ url: i.url, playlist: answer === "all" });
    } else jobs.push({ url: i.url, playlist: i.kind === "playlist" });
  }
  if (!jobs.length) return;
  urlInput.value = "";
  autoGrow();
  await act("/api/add", { jobs }, (r) => `${r.count}개 링크 조회 중...`);
});

function askPlaylist(url) {
  const dlg = $("#dlg-playlist");
  $("#playlist-url").textContent = url;
  dlg.returnValue = "";
  dlg.showModal();
  return new Promise((resolve) => dlg.addEventListener("close", () => resolve(dlg.returnValue || "skip"), { once: true }));
}

// ---------------------------------------------------------------- 옵션 / 버튼
document.querySelectorAll(".seg button").forEach((b) =>
  b.addEventListener("click", () => act("/api/settings", { settings: { format: b.dataset.format } })));
$("#auto-start").addEventListener("change", (e) => act("/api/settings", { settings: { auto_start: e.target.checked } }));

$("#btn-download-all").addEventListener("click", () => {
  if (!ui.state || !ui.state.counts.total) return toast("다운로드할 곡이 없습니다. 링크를 먼저 추가하세요.");
  act("/api/download", {}, (r) => (r.count ? `${r.count}곡 다운로드 시작` : "새로 받을 곡이 없습니다"));
});
$("#btn-cancel-all").addEventListener("click", () => {
  if (ui.state?.counts.active && confirm("진행 중인 다운로드를 모두 취소할까요?")) act("/api/cancel", {});
});
$("#btn-clear-done").addEventListener("click", () => act("/api/clear_finished"));

// ---------------------------------------------------------------- 편집
let editUids = [];
function openEdit(uids) {
  const tracks = (ui.state?.tracks || []).filter((t) => uids.includes(t.uid));
  if (!tracks.length) return;
  if (tracks.some((t) => t.active)) return toast("진행 중인 곡은 편집할 수 없습니다.");
  editUids = tracks.map((t) => t.uid);
  const multi = tracks.length > 1;
  const f = $("#edit-form");
  $("#edit-title").textContent = multi ? `태그 편집 (${tracks.length}곡 일괄)` : "태그 편집";
  $("#edit-note").hidden = !multi;
  for (const key of ["title", "artist", "album", "album_artist", "track_no", "year", "genre"]) {
    const input = f.elements[key];
    const values = new Set(tracks.map((t) => (t[key] ?? "") + ""));
    input.disabled = multi && (key === "title" || key === "track_no");
    input.placeholder = input.disabled ? "(일괄 편집 불가)" : "";
    input.value = input.disabled ? "" : values.size === 1 ? [...values][0] : "";
  }
  f.elements.remember.checked = true;
  $("#dlg-edit").showModal();
}
$("#dlg-edit").addEventListener("close", async () => {
  if ($("#dlg-edit").returnValue !== "ok") return;
  const f = $("#edit-form");
  const values = {};
  for (const key of ["title", "artist", "album", "album_artist", "track_no", "year", "genre"]) {
    if (!f.elements[key].disabled) values[key] = f.elements[key].value;
  }
  await act("/api/edit", { uids: editUids, values, remember: f.elements.remember.checked }, "저장했습니다");
});

// ---------------------------------------------------------------- 설정
function fillSelect(sel, entries) {
  sel.replaceChildren(...entries.map(([v, label]) => new Option(label, v)));
}
(function initSettingsForm() {
  const f = $("#settings-form").elements;
  fillSelect(f.filename_style, Object.entries(OPT.filename_styles || {}));
  fillSelect(f.on_exists, Object.entries(OPT.on_exists || {}));
  fillSelect(f.mp3_bitrate, (OPT.mp3_bitrates || []).map((v) => [v, v]));
  fillSelect(f.wav_sample_rate, (OPT.wav_sample_rates || []).map((v) => [v, `${v} Hz`]));
  fillSelect(f.wav_bit_depth, (OPT.wav_bit_depths || []).map((v) => [v, `${v} bit`]));
  fillSelect(f.cookies_browser, (OPT.browsers || []).map((v) => [v, v || "사용 안 함"]));
})();

$("#btn-settings").addEventListener("click", () => {
  if (!ui.state) return;
  const s = ui.state.settings;
  const form = $("#settings-form");
  for (const el of form.elements) {
    if (!el.name || !(el.name in s)) continue;
    if (el.type === "checkbox") el.checked = !!s[el.name];
    else el.value = s[el.name];
  }
  $("#dlg-settings").showModal();
});
$("#dlg-settings").addEventListener("close", () => {
  if ($("#dlg-settings").returnValue !== "ok") return;
  const out = {};
  for (const el of $("#settings-form").elements) {
    if (!el.name || el.name === "remember") continue;
    out[el.name] = el.type === "checkbox" ? el.checked : el.value;
  }
  act("/api/settings", { settings: out }, "설정을 저장했습니다");
});
$("#btn-open-folder").addEventListener("click", () => act("/api/open_folder"));

// 대화상자 바깥(어두운 부분)을 누르면 닫기
document.querySelectorAll("dialog").forEach((d) =>
  d.addEventListener("click", (e) => { if (e.target === d) d.close(); }));

// 키보드 (PC): Delete = 선택 삭제, Esc = 선택 해제
document.addEventListener("keydown", (e) => {
  if (document.querySelector("dialog[open]") || /INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)) return;
  if (e.key === "Delete" && ui.selected.size) $("#btn-sel-delete").click();
  if (e.key === "Escape") clearSelection();
});

start();
