"use strict";

const STAGES = ["Applied", "Screening", "Interview", "Offer", "Hired", "Rejected"];
const PROGRESSION = STAGES.slice(0, 5);
const FINAL = new Set(["Hired", "Rejected"]);
const STUCK_DAYS = 7;
const TZ = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";

const state = { candidates: [], openId: null, searchSeq: 0, busy: new Set() };

// ---------- tiny helpers ----------

const $ = (sel) => document.querySelector(sel);

function el(tag, props, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v == null || v === false) continue;
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat(Infinity)) {
    if (c == null || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

async function api(path, options = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(body?.error?.message || `Something went wrong (HTTP ${res.status}).`);
    err.status = res.status;
    err.body = body;
    throw err;
  }
  return body;
}

const secondsSince = (iso) => Math.max(0, (Date.now() - Date.parse(iso)) / 1000);

function shortDuration(s) {
  if (s < 3600) return `${Math.max(1, Math.floor(s / 60))}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h`;
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600);
  return d < 10 && h ? `${d}d ${h}h` : `${d}d`;
}

function longDuration(s) {
  const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;
  if (s < 3600) return plural(Math.max(1, Math.floor(s / 60)), "minute");
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600);
  if (d === 0) return plural(h, "hour");
  return h ? `${plural(d, "day")}, ${plural(h, "hour")}` : plural(d, "day");
}

const fmtWhen = (iso) => new Date(iso).toLocaleString(undefined, {
  weekday: "short", day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit",
});

function toast(message, kind = "ok") {
  const t = $("#toast");
  t.textContent = message;
  t.className = `toast ${kind === "error" ? "error" : ""}`;
  t.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { t.hidden = true; }, kind === "error" ? 6000 : 3000);
}

// ---------- data ----------

async function refresh() {
  state.candidates = await api("/api/candidates");
  renderMeta();
  renderBoard();
  if ($("#q").value.trim()) await runSearch();
  if (state.openId != null) await openDrawer(state.openId, { keepFocus: true });
}

function renderMeta() {
  const total = state.candidates.length;
  const active = state.candidates.filter((c) => !FINAL.has(c.stage)).length;
  const stuck = state.candidates.filter((c) => !FINAL.has(c.stage) && secondsSince(c.entered_stage_at) > STUCK_DAYS * 86400).length;
  $("#job-meta").textContent = total
    ? `${total} candidates, ${active} still in the pipeline${stuck ? `, ${stuck} waiting over a week` : ""}`
    : "No candidates yet";
}

// ---------- actions ----------

function actionButtons(c, size = "small") {
  const cls = size === "small" ? "btn btn-small" : "btn";
  const busy = state.busy.has(c.id);
  return el("div", { class: "card-actions" },
    c.next_stage && el("button", {
      class: `${cls} btn-advance`, type: "button", disabled: busy,
      onclick: (e) => { e.stopPropagation(); move(c, "advance"); },
    }, c.next_stage === "Hired" ? "Mark hired" : `Move to ${c.next_stage}`),
    c.can_reject && el("button", {
      class: `${cls} btn-reject`, type: "button", disabled: busy,
      onclick: (e) => { e.stopPropagation(); move(c, "reject"); },
    }, "Reject"),
  );
}

function confirmFinal(c, action) {
  const dlg = $("#confirm-dialog");
  const hiring = action === "advance";
  $("#confirm-title").textContent = hiring ? `Mark ${c.name} as hired?` : `Reject ${c.name}?`;
  $("#confirm-body").textContent = hiring
    ? "Hired is a final outcome. It can't be undone."
    : `${c.name} is in ${c.stage}. Rejection is a final outcome and can't be undone.`;
  const ok = $("#confirm-ok");
  ok.textContent = hiring ? "Mark hired" : "Reject";
  ok.className = `btn ${hiring ? "btn-primary" : "btn-danger"}`;
  $("#confirm-note").value = "";
  dlg.returnValue = "";
  dlg.showModal();
  return new Promise((resolve) => {
    dlg.addEventListener("close", () => resolve({
      ok: dlg.returnValue === "ok", note: $("#confirm-note").value.trim() || null,
    }), { once: true });
  });
}

async function move(c, action) {
  if (state.busy.has(c.id)) return;
  let note = null;
  if (action === "reject" || c.next_stage === "Hired") {
    const answer = await confirmFinal(c, action);
    if (!answer.ok) return;
    note = answer.note;
  }
  state.busy.add(c.id);
  try {
    await api(`/api/candidates/${c.id}/transitions`, {
      method: "POST",
      body: JSON.stringify({ action, expected_stage: c.stage, note }),
    });
    toast(action === "reject" ? `Rejected ${c.name}` : `Moved ${c.name} to ${c.next_stage}`);
  } catch (err) {
    toast(err.message, "error");
  } finally {
    state.busy.delete(c.id);
    await refresh();
  }
}

// ---------- board ----------

function track(stage) {
  if (stage === "Rejected") return el("div", { class: "track", "aria-hidden": "true" }, el("i", { class: "on" }));
  const reached = PROGRESSION.indexOf(stage) + 1;
  return el("div", { class: "track", "aria-hidden": "true" },
    PROGRESSION.map((_, i) => el("i", { class: i < reached ? "on" : "" })));
}

function waitText(c) {
  const s = secondsSince(c.entered_stage_at);
  return FINAL.has(c.stage) ? `${shortDuration(s)} ago` : `${shortDuration(s)} in stage`;
}

function card(c) {
  const s = secondsSince(c.entered_stage_at);
  const stuck = !FINAL.has(c.stage) && s > STUCK_DAYS * 86400;
  const pct = Math.min(100, (s / (14 * 86400)) * 100);
  return el("li", { class: `card${stuck ? " is-stuck" : ""}` },
    el("button", { class: "card-open", type: "button", onclick: () => openDrawer(c.id) },
      el("span", { class: "card-name" }, c.name),
      c.email && el("span", { class: "card-email" }, c.email)),
    el("div", { class: "card-meta", title: `Entered ${c.stage} ${fmtWhen(c.entered_stage_at)}` },
      el("span", { "data-wait": c.entered_stage_at, "data-final": FINAL.has(c.stage) ? "1" : "" }, waitText(c)),
      !FINAL.has(c.stage) && el("span", { class: "wait-bar" }, el("span", { style: `width:${pct}%` }))),
    actionButtons(c));
}

const EMPTY_COLUMN = {
  Applied: "No new applicants.", Screening: "Nobody in screening.", Interview: "No interviews in progress.",
  Offer: "No open offers.", Hired: "Nobody hired yet.", Rejected: "Nobody rejected.",
};

function renderBoard() {
  const board = $("#board");
  board.replaceChildren(...STAGES.map((stage) => {
    // Longest-waiting first: they're the ones most likely to need a nudge.
    const people = state.candidates
      .filter((c) => c.stage === stage)
      .sort((a, b) => Date.parse(a.entered_stage_at) - Date.parse(b.entered_stage_at));
    return el("section", { class: `column${stage === "Rejected" ? " column-rejected" : ""}`, "aria-label": `${stage}, ${people.length}` },
      el("header", { class: "column-head" }, el("h2", {}, stage), el("span", { class: "count" }, people.length)),
      track(stage),
      people.length ? el("ol", { class: "cards" }, people.map(card)) : el("p", { class: "column-empty" }, EMPTY_COLUMN[stage]));
  }));
}

// ---------- search ----------

function setQuery(q) {
  const input = $("#q");
  input.value = q;
  input.focus();
  runSearch();
}

async function runSearch() {
  const q = $("#q").value.trim();
  const view = $("#search-view");
  if (!q) {
    view.hidden = true;
    $("#board").hidden = false;
    return;
  }
  const seq = ++state.searchSeq;
  let data = null, error = null;
  try {
    data = await api(`/api/search?q=${encodeURIComponent(q)}&tz=${encodeURIComponent(TZ)}`);
  } catch (err) {
    if (err.status !== 400) { toast(err.message, "error"); return; }
    error = err.body;
  }
  if (seq !== state.searchSeq) return; // a newer keystroke already answered
  view.replaceChildren(error ? renderQueryError(error) : renderResults(data));
  view.hidden = false;
  $("#board").hidden = true;
}

function renderQueryError(body) {
  const { message, hint, span, fix } = body.error;
  const q = body.query;
  let echo = [q];
  if (span) echo = [q.slice(0, span[0]), el("mark", {}, q.slice(span[0], span[1])), q.slice(span[1])];
  return el("div", { class: "notice notice-error", role: "alert" },
    el("h2", {}, message),
    el("p", { class: "echo" }, echo),
    hint && el("p", {}, hint),
    fix && el("div", { class: "suggestions" },
      el("button", { class: "suggestion", type: "button", onclick: () => setQuery(fix) }, `Search for ${fix}`)));
}

function reading(data) {
  const parts = [];
  data.interpretation.forEach((text, i) => {
    if (i) parts.push(el("span", { class: "and" }, "and"));
    parts.push(el("span", { class: "clause" }, text));
  });
  return [
    el("p", { class: "reading" }, el("span", { class: "reading-label" }, "Showing candidates where"), parts),
    data.notes.length ? el("p", { class: "reading-notes" }, data.notes.join(" ")) : null,
  ];
}

function renderResults(data) {
  if (!data.count) return el("div", {}, reading(data), renderEmpty(data.empty));
  const hasName = data.results.some((r) => r.score < 1);
  return el("div", {},
    reading(data),
    el("p", { class: "result-count" }, `${data.count} ${data.count === 1 ? "match" : "matches"} `,
      el("span", {}, hasName ? "best name match first" : "longest in their stage first")),
    el("ol", { class: "results" }, data.results.map((r, i) =>
      el("li", { class: "result" },
        el("span", { class: "rank" }, i + 1),
        el("span", {},
          el("button", { class: "name-link", type: "button", onclick: () => openDrawer(r.id) }, r.name),
          hasName && el("span", { class: "match" }, `${Math.round(r.score * 100)}% name match`)),
        el("span", { class: "stage-cell" }, el("span", { class: `badge badge-${r.stage}` }, r.stage), " ",
          el("span", { class: "reasons", "data-wait": r.entered_stage_at, "data-final": FINAL.has(r.stage) ? "1" : "" }, waitText(r))),
        el("span", { class: "reasons" }, r.reasons.join(". ")),
        actionButtons(r)))));
}

function renderEmpty(empty) {
  const showBreakdown = empty.breakdown.length > 1;
  return el("div", { class: "notice" },
    el("h2", {}, "No matches"),
    el("p", {}, empty.message),
    showBreakdown && el("ul", { class: "breakdown" }, empty.breakdown.map((b) =>
      el("li", {}, `${b.clause}: `, el("span", { class: b.matches ? "" : "zero" },
        b.matches === 0 ? "nobody" : `${b.matches} ${b.matches === 1 ? "person" : "people"}`)))),
    empty.suggestions.length > 0 && el("div", { class: "suggestions" }, empty.suggestions.map((s) =>
      el("button", { class: "suggestion", type: "button", onclick: () => setQuery(s) }, s))));
}

// ---------- drawer ----------

function eventLabel(e) {
  if (e.action === "applied") return "Applied";
  if (e.action === "rejected") return `Rejected at ${e.from_stage}`;
  return e.to_stage === "Hired" ? "Hired" : `Moved to ${e.to_stage}`;
}

function stepsFor(d) {
  const reached = new Set(d.history.map((h) => h.to_stage));
  const stoppedAt = d.stage === "Rejected" ? d.history[d.history.length - 1].from_stage : null;
  return el("div", { class: "steps", "aria-label": "Progress" }, PROGRESSION.map((s) =>
    el("span", { class: s === stoppedAt ? "stopped" : reached.has(s) ? "done" : "" }, s)));
}

async function openDrawer(id, { keepFocus = false } = {}) {
  let d;
  try {
    d = await api(`/api/candidates/${id}`);
  } catch (err) {
    toast(err.message, "error");
    return;
  }
  state.openId = id;
  const final = FINAL.has(d.stage);
  const nowLine = final
    ? `${d.stage === "Hired" ? "Hired" : "Rejected"} ${longDuration(secondsSince(d.entered_stage_at))} ago`
    : `In ${d.stage} for ${longDuration(secondsSince(d.entered_stage_at))}`;

  $("#drawer-body").replaceChildren(
    el("div", { class: "drawer-top" },
      el("div", {}, el("h2", { id: "drawer-name" }, d.name), el("p", { class: "sub" }, d.email || "No email on file")),
      el("button", { class: "btn btn-quiet", type: "button", id: "drawer-close", onclick: closeDrawer, "aria-label": "Close" }, "Close")),
    el("div", { class: "now" },
      el("strong", { "data-now": d.entered_stage_at, "data-stage": d.stage }, nowLine),
      el("p", {}, `Since ${fmtWhen(d.entered_stage_at)}`),
      stepsFor(d)),
    actionButtons(d, "large"),
    el("h3", { class: "history-title" }, "History"),
    el("ol", { class: "history" }, d.history.map((e) =>
      el("li", { class: `event${e.action === "rejected" ? " rejected" : ""}${e.current ? " current" : ""}` },
        el("div", { class: "event-what" }, eventLabel(e)),
        el("div", { class: "event-when" }, fmtWhen(e.at),
          FINAL.has(e.to_stage) ? "" : e.current ? `, here ${longDuration(e.duration_seconds)} so far` : `, stayed ${longDuration(e.duration_seconds)}`),
        e.note && el("p", { class: "event-note" }, e.note)))),
    el("p", { class: "permanent" }, "History is permanent. Entries can't be edited or removed."));

  const drawer = $("#drawer");
  drawer.hidden = false;
  if (!keepFocus) $("#drawer-close").focus();
}

function closeDrawer() {
  state.openId = null;
  $("#drawer").hidden = true;
}

// ---------- add candidate ----------

function setupAdd() {
  const dlg = $("#add-dialog");
  const form = $("#add-form");
  $("#add-open").addEventListener("click", () => {
    form.reset();
    $("#add-error").hidden = true;
    dlg.showModal();
    $("#add-name").focus();
  });
  form.addEventListener("submit", async (e) => {
    if (e.submitter && e.submitter.value === "cancel") return; // let the dialog close
    e.preventDefault();
    const name = $("#add-name").value.trim();
    const email = $("#add-email").value.trim() || null;
    const errBox = $("#add-error");
    if (!name) {
      errBox.textContent = "Enter the candidate's name.";
      errBox.hidden = false;
      return;
    }
    $("#add-submit").disabled = true;
    try {
      await api("/api/candidates", { method: "POST", body: JSON.stringify({ name, email }) });
      dlg.close();
      toast(`Added ${name} to Applied`);
      await refresh();
    } catch (err) {
      errBox.textContent = err.message;
      errBox.hidden = false;
    } finally {
      $("#add-submit").disabled = false;
    }
  });
}

// ---------- wiring ----------

function tick() {
  document.querySelectorAll("[data-wait]").forEach((n) => {
    const s = secondsSince(n.dataset.wait);
    n.textContent = n.dataset.final ? `${shortDuration(s)} ago` : `${shortDuration(s)} in stage`;
  });
  document.querySelectorAll("[data-now]").forEach((n) => {
    const s = secondsSince(n.dataset.now);
    const st = n.dataset.stage;
    n.textContent = FINAL.has(st) ? `${st} ${longDuration(s)} ago` : `In ${st} for ${longDuration(s)}`;
  });
}

function init() {
  const input = $("#q");
  let timer;
  input.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(runSearch, 180); });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { input.value = ""; runSearch(); }
  });
  document.querySelectorAll(".example").forEach((b) => b.addEventListener("click", () => setQuery(b.dataset.q)));
  document.addEventListener("keydown", (e) => {
    const typing = /INPUT|TEXTAREA/.test(document.activeElement?.tagName);
    if (e.key === "/" && !typing) { e.preventDefault(); input.focus(); }
    if (e.key === "Escape" && !$("#drawer").hidden && !document.querySelector("dialog[open]")) closeDrawer();
  });
  setupAdd();
  setInterval(tick, 30000);
  refresh().catch((err) => toast(err.message, "error"));
}

init();
