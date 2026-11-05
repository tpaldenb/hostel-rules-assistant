const $ = (id) => document.getElementById(id);
const STARTERS = [
  "What time does the hostel gate close on weekdays?",
  "How do I apply for overnight leave?",
  "Does Route 1 stop at Market Square?",
  "How much is the semester bus pass?",
];
const STATUS_LABEL = { current: "In force", superseded: "Replaced", expired: "Expired", upcoming: "Not yet in force" };
const CITATION = /\[([A-Z]{2}-[\w.\-]*\w)\]/g;

const state = { today: null, history: [], sources: new Map(), busy: false, filter: "all", board: [] };

const escapeHtml = (s) => s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtDate = (iso) => new Date(`${iso}T00:00:00`).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
const asOf = () => $("asOf").value || state.today;
const statusClass = (c) => (c.status === "current" && c.temporary ? "temporary" : c.status);
const statusLabel = (c) => (c.status === "current" && c.temporary ? `Temporary, until ${fmtDate(c.effective_until)}` : STATUS_LABEL[c.status]);

function citeLabel(c) {
  const kind = c.doc_type === "manual" ? "Manual" : "Notice";
  const prefix = { superseded: "Old: ", expired: "Ended: " }[c.status] || "";
  return c.status === "upcoming" ? `From ${fmtDate(c.effective_from)}` : `${prefix}${kind}, ${fmtDate(c.issued)}`;
}

function citeHtml(id) {
  const c = state.sources.get(id);
  if (!c) return `<span class="cite unknown" title="Not a retrieved clause">Unverified: ${escapeHtml(id)}</span>`;
  const title = `${c.doc_title} (${STATUS_LABEL[c.status]})`;
  return `<button type="button" class="cite ${statusClass(c)}" data-clause="${escapeHtml(id)}" title="${escapeHtml(title)}">${escapeHtml(citeLabel(c))}</button>`;
}

function inline(text) {
  return escapeHtml(text)
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(CITATION, (_, id) => citeHtml(id));
}

function renderMarkdown(text) {
  return text.trim().split(/\n{2,}/).map((block) => {
    const lines = block.split("\n");
    if (lines.every((l) => /^\s*[-*]\s+/.test(l))) {
      return `<ul>${lines.map((l) => `<li>${inline(l.replace(/^\s*[-*]\s+/, ""))}</li>`).join("")}</ul>`;
    }
    return `<p>${lines.map(inline).join("<br>")}</p>`;
  }).join("");
}

function scrollDown() {
  const thread = $("thread");
  thread.scrollTop = thread.scrollHeight;
}

function addUser(text) {
  $("welcome")?.remove();
  const el = document.createElement("div");
  el.className = "msg-user";
  el.innerHTML = `<div>${escapeHtml(text)}</div>`;
  $("thread").append(el);
}

function addBot() {
  const el = document.createElement("article");
  el.className = "msg-bot";
  el.innerHTML = `<div class="body"><span class="thinking">Checking the manual and notices...</span></div>`;
  $("thread").append(el);
  scrollDown();
  return el;
}

function addBox(el, cls, text) {
  const box = document.createElement("div");
  box.className = cls;
  box.textContent = text;
  el.append(box);
}

async function ask(question) {
  if (state.busy) return;
  state.busy = true;
  $("send").disabled = true;
  addUser(question);
  const bot = addBot();
  const body = bot.querySelector(".body");
  let text = "";
  let date = asOf();

  const handle = (event) => {
    if (event.type === "meta") {
      date = event.as_of;
      event.sources.forEach((s) => state.sources.set(s.id, s));
    } else if (event.type === "delta") {
      text += event.text;
      body.innerHTML = renderMarkdown(text);
      scrollDown();
    } else if (event.type === "done") {
      event.notes.forEach((n) => addBox(bot, "note", n));
      event.warnings.forEach((w) => addBox(bot, "warn", w));
      const foot = document.createElement("div");
      foot.className = "foot";
      const how = event.mode === "offline" ? "Quoted directly from the clauses, no AI writing" : `Written by ${event.label} from the cited clauses`;
      foot.textContent = `${how}. Rules as of ${fmtDate(date)}.`;
      bot.append(foot);
      const first = event.cited.find((id) => state.sources.get(id)?.status === "current") || event.cited[0];
      if (first) showClause(first);
    } else if (event.type === "error") {
      addBox(bot, "warn", event.message);
    }
  };

  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, as_of: asOf(), history: state.history.slice(-6) }),
    });
    if (!response.ok) throw new Error(`The server returned ${response.status}.`);
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split("\n\n");
      buffer = parts.pop();
      parts.filter((p) => p.startsWith("data: ")).forEach((p) => handle(JSON.parse(p.slice(6))));
    }
    if (text) state.history.push({ role: "user", content: question }, { role: "assistant", content: text.slice(0, 6000) });
  } catch (err) {
    if (!text) body.textContent = "";
    addBox(bot, "warn", `Could not get an answer. ${err.message}`);
  } finally {
    state.busy = false;
    $("send").disabled = false;
    scrollDown();
    $("question").focus();
  }
}

function selectTab(name) {
  const board = name === "board";
  $("tabBoard").setAttribute("aria-selected", board);
  $("tabSource").setAttribute("aria-selected", !board);
  $("paneBoard").hidden = !board;
  $("paneSource").hidden = board;
}

async function showClause(id) {
  const response = await fetch(`/api/clauses/${encodeURIComponent(id)}?as_of=${asOf()}`);
  selectTab("source");
  if (!response.ok) {
    $("paneSource").innerHTML = `<p class="empty">This clause does not exist on the selected date.</p>`;
    return;
  }
  const { clause: c, history } = await response.json();
  const steps = [...history].reverse().map((h) => {
    const old = h.status === "superseded" || h.status === "expired";
    const span = h.effective_until ? `${fmtDate(h.effective_from)} to ${fmtDate(h.effective_until)}` : `from ${fmtDate(h.effective_from)}`;
    return `<li class="${h.status === "current" ? "on" : ""}">
      <button type="button" data-clause="${escapeHtml(h.id)}" class="${old ? "old" : ""}">
        <span class="status ${statusClass(h)}">${escapeHtml(statusLabel(h))}</span>
        <div class="when">${h.doc_type === "manual" ? "Manual" : "Notice"} dated ${fmtDate(h.issued)}, in effect ${span}</div>
        <div class="snip">${escapeHtml(h.text.length > 110 ? `${h.text.slice(0, 110)}...` : h.text)}</div>
      </button></li>`;
  }).join("");
  $("paneSource").innerHTML = `<div class="clause">
    <span class="status ${statusClass(c)}">${escapeHtml(statusLabel(c))}</span>
    <h3>${escapeHtml(c.heading)}</h3>
    <p class="src">${escapeHtml(c.doc_title)}<br>Issued ${fmtDate(c.issued)} by ${escapeHtml(c.issued_by)}</p>
    <p class="text">${escapeHtml(c.text)}</p>
    ${history.length > 1 ? `<h4>How this rule changed</h4><ol class="timeline">${steps}</ol>` : ""}
  </div>`;
}

const FILTERS = [["all", "All"], ["hostel", "Hostel"], ["transport", "Transport"], ["current", "In force"], ["upcoming", "Coming up"], ["old", "Outdated"]];

function renderBoard() {
  $("filters").innerHTML = FILTERS.map(([k, label]) => `<button type="button" data-filter="${k}" aria-pressed="${state.filter === k}">${label}</button>`).join("");
  const f = state.filter;
  const items = state.board.filter((c) => f === "all" || c.category === f || c.status === f || (f === "old" && ["superseded", "expired"].includes(c.status)));
  $("boardList").innerHTML = items.map((c) => `<button type="button" class="card" data-clause="${escapeHtml(c.id)}">
      <span class="status ${statusClass(c)}">${escapeHtml(statusLabel(c))}</span>
      <strong>${escapeHtml(c.heading)}</strong>
      <small>${c.doc_type === "manual" ? "Manual" : "Notice"} dated ${fmtDate(c.issued)}</small>
    </button>`).join("") || `<p class="empty">Nothing matches this filter.</p>`;
}

async function loadBoard() {
  const response = await fetch(`/api/clauses?as_of=${asOf()}`);
  state.board = (await response.json()).clauses;
  renderBoard();
}

function dateChanged() {
  const past = asOf() !== state.today;
  $("resetDate").hidden = !past;
  $("pastBanner").hidden = !past;
  $("pastBanner").textContent = past ? `Showing rules as they stood on ${fmtDate(asOf())}. Earlier answers above keep their own dates.` : "";
  $("paneSource").innerHTML = `<p class="empty">Select a citation in an answer to see the full clause and how the rule has changed.</p>`;
  loadBoard();
}

document.addEventListener("click", (e) => {
  const clause = e.target.closest("[data-clause]");
  if (clause) showClause(clause.dataset.clause);
  const filter = e.target.closest("[data-filter]");
  if (filter) { state.filter = filter.dataset.filter; renderBoard(); }
});
$("tabSource").addEventListener("click", () => selectTab("source"));
$("tabBoard").addEventListener("click", () => selectTab("board"));
$("asOf").addEventListener("change", dateChanged);
$("resetDate").addEventListener("click", () => { $("asOf").value = state.today; dateChanged(); });
$("askForm").addEventListener("submit", (e) => {
  e.preventDefault();
  const q = $("question").value.trim();
  if (!q || state.busy) return;
  $("question").value = "";
  $("question").style.height = "auto";
  ask(q);
});
$("question").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("askForm").requestSubmit(); }
});
$("question").addEventListener("input", (e) => {
  e.target.style.height = "auto";
  e.target.style.height = `${e.target.scrollHeight}px`;
});

(async function init() {
  $("starters").innerHTML = STARTERS.map((q) => `<button type="button">${escapeHtml(q)}</button>`).join("");
  $("starters").addEventListener("click", (e) => { if (e.target.tagName === "BUTTON") ask(e.target.textContent); });
  const info = await (await fetch("/api/status")).json();
  state.today = info.today;
  $("asOf").value = info.today;
  const badge = $("modeBadge");
  const offline = info.mode === "offline";
  badge.textContent = offline ? "Offline mode" : info.label;
  badge.classList.toggle("live", !offline);
  badge.title = offline ? "No API key set. Answers quote the clauses directly." : `Answers written by ${info.model}`;
  loadBoard();
})();
