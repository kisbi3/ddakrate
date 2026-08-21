const state = {
  sessions: [],
  selectedSessionId: null,
  bundle: null,
  selectedRequestId: null,
  loadedSessionSignature: null,
};

const $ = (selector) => document.querySelector(selector);
const escapeHtml = (value) => String(value ?? "")
  .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;").replaceAll("'", "&#039;");
const pretty = (value) => JSON.stringify(value, null, 2);
const shortTime = (value) => value ? new Date(value).toLocaleTimeString("ko-KR", { hour12: false }) : "-";

async function fetchJson(url) {
  const response = await fetch(url, { cache: "no-store" });
  if (!response.ok) throw new Error(`${response.status} ${await response.text()}`);
  return response.json();
}

function renderSessions() {
  const list = $("#session-list");
  if (!state.sessions.length) {
    list.innerHTML = '<div class="muted">생성된 세션이 없습니다.</div>';
    return;
  }
  list.innerHTML = state.sessions.map((session) => {
    const utterance = session.source_utterances?.at(-1) || "검색 발화 없음";
    return `<button class="session-card ${session.search_session_id === state.selectedSessionId ? "active" : ""}" data-session-id="${escapeHtml(session.search_session_id)}">
      <strong>${escapeHtml(utterance)}</strong>
      <span class="session-id">${escapeHtml(session.search_session_id)}</span>
      <span>${escapeHtml(session.status)} · API ${session.request_count}회 / LLM ${session.llm_call_count}회 · 후보 ${session.candidate_count}개</span>
    </button>`;
  }).join("");
  list.querySelectorAll("[data-session-id]").forEach((button) => {
    button.addEventListener("click", () => selectSession(button.dataset.sessionId));
  });
}

function renderSummary(snapshot) {
  const session = snapshot.session;
  const ranking = snapshot.engine.ranking.output;
  const active = snapshot.multi_turn.active_question;
  const metrics = [
    ["상태", session.status],
    ["Intent version", session.intent_version],
    ["후보", `${session.candidate_product_ids.length}개`],
    ["LLM/API turn", `${state.bundle.requests.length}개`],
    ["현재 질문", active ? active.question : "없음 · 결과 준비"],
  ];
  $("#summary-strip").innerHTML = metrics.map(([label, value]) =>
    `<div class="metric"><span>${escapeHtml(label)}</span><strong title="${escapeHtml(value)}">${escapeHtml(value)}</strong></div>`
  ).join("");
  void ranking;
}

function requestSummary(request) {
  const payload = request.request_payload || {};
  return payload.natural_language_query || payload.utterance || payload.message || payload.answer || "조회/상태 요청";
}

function renderRequestList() {
  const requests = state.bundle.requests;
  $("#request-count").textContent = `${requests.length} requests`;
  $("#request-list").innerHTML = requests.map((request, index) => `
    <button class="request-row ${request.request_id === state.selectedRequestId ? "active" : ""}" data-request-id="${escapeHtml(request.request_id)}">
      <span class="turn-number">#${index + 1}</span>
      <span class="request-copy">
        <strong>${escapeHtml(request.method)} ${escapeHtml(request.path.replace("/api/", ""))}</strong>
        <span>${escapeHtml(requestSummary(request))}</span>
      </span>
      <span class="request-meta">${escapeHtml(shortTime(request.started_at))}<br><span class="llm-count">LLM ${request.llm_calls.length}</span></span>
    </button>`).join("");
  $("#request-list").querySelectorAll("[data-request-id]").forEach((button) => {
    button.addEventListener("click", () => {
      state.selectedRequestId = button.dataset.requestId;
      renderRequestList();
      renderRequestDetail();
    });
  });
}

function renderLlmCall(call, index) {
  const request = call.request || {};
  const messages = request.messages || [];
  const system = messages.filter((item) => item.role === "system");
  const users = messages.filter((item) => item.role === "user");
  const systemHtml = system.length
    ? system.map((item) => `<pre class="prompt">${escapeHtml(item.content)}</pre>`).join("")
    : '<div class="notice"><strong>system message 없음</strong><br>현재 구현은 아래 지시문 전체를 user message 하나에 포함해 전송합니다.</div>';
  return `<article class="llm-card">
    <div class="llm-head">
      <strong>LLM #${index + 1} · ${escapeHtml(call.purpose)}</strong>
      <span>${escapeHtml(call.provider)} / ${escapeHtml(call.model)} · ${call.latency_ms ?? "-"}ms · ${escapeHtml(call.status)}</span>
    </div>
    <div class="message-label">system prompt</div>
    ${systemHtml}
    <div class="message-label">user message · 실제 전송 prompt</div>
    ${users.map((item) => `<pre class="prompt">${escapeHtml(item.content)}</pre>`).join("") || '<div class="notice">user message 없음</div>'}
    <details class="block">
      <summary class="message-label">Structured output JSON Schema</summary>
      <pre class="json">${escapeHtml(pretty(request.response_json_schema || null))}</pre>
    </details>
    <div class="message-label">model output</div>
    <pre class="json">${escapeHtml(pretty(call.output ?? call.error ?? null))}</pre>
    <details class="block">
      <summary class="message-label">호출 metadata</summary>
      <pre class="json">${escapeHtml(pretty({
        prompt_template_id: request.prompt_template_id,
        prompt_template_version: request.prompt_template_version,
        response_schema_name: request.response_schema_name,
        temperature: request.temperature,
        timeout_seconds: request.timeout_seconds,
        token_usage: call.token_usage,
        retry_count: call.retry_count,
      }))}</pre>
    </details>
  </article>`;
}

function renderRequestDetail() {
  const request = state.bundle.requests.find((item) => item.request_id === state.selectedRequestId);
  if (!request) {
    $("#request-detail").innerHTML = '<div class="muted">왼쪽에서 요청을 선택하세요.</div>';
    return;
  }
  $("#request-detail").innerHTML = `
    <div class="detail-title">
      <h2>${escapeHtml(request.method)} ${escapeHtml(request.path)}</h2>
      <span class="status-code">HTTP ${request.status_code ?? "진행 중"} · ${request.duration_ms ?? "-"}ms</span>
    </div>
    <div class="block json-grid">
      <div><div class="block-title"><strong>브라우저 → Web API</strong><span>request</span></div><pre class="json">${escapeHtml(pretty(request.request_payload))}</pre></div>
      <div><div class="block-title"><strong>Web API → 브라우저</strong><span>response</span></div><pre class="json">${escapeHtml(pretty(request.response_payload))}</pre></div>
    </div>
    <div class="block">
      <div class="block-title"><strong>이 요청 중 실제 LLM 호출</strong><span>${request.llm_calls.length}회</span></div>
      ${request.llm_calls.length ? request.llm_calls.map(renderLlmCall).join("") : '<div class="notice">LLM 호출 없음 · 이 요청은 저장된 상태 조회 또는 결정론적 API 처리만 수행했습니다.</div>'}
    </div>`;
}

function renderStateView(snapshot) {
  const multi = snapshot.multi_turn;
  $("#view-state").innerHTML = `<div class="state-grid">
    <article class="panel-card wide"><h3>이 앱의 멀티턴 기억 방식</h3><div class="notice">이전 채팅 메시지 배열을 그대로 모델에 보내지 않습니다. 매 turn마다 아래 Working Note와 현재 Intent, 사용자 선언, 활성 질문, Top K 요약을 조립해 새 prompt의 CURRENT_CONTEXT로 넣습니다.</div></article>
    <article class="panel-card"><h3>Search Working Note</h3><pre class="prompt">${escapeHtml(multi.working_note || "없음")}</pre></article>
    <article class="panel-card"><h3>현재 Active Question</h3><pre class="json">${escapeHtml(pretty(multi.active_question))}</pre></article>
    <article class="panel-card"><h3>사용자 원문 발화 누적</h3><pre class="json">${escapeHtml(pretty(multi.source_utterances))}</pre></article>
    <article class="panel-card"><h3>답변 상태</h3><pre class="json">${escapeHtml(pretty({ answered_question_ids: multi.answered_question_ids, skipped_question_ids: multi.skipped_question_ids, answer_records: multi.answer_records }))}</pre></article>
    <article class="panel-card wide"><h3>질문 History · 생성 순서</h3><pre class="json">${escapeHtml(pretty(multi.question_history))}</pre></article>
  </div>`;
}

function engineSection(title, data, open = false) {
  return `<details class="panel-card" ${open ? "open" : ""}><summary>${escapeHtml(title)}</summary><div class="json-grid"><div><div class="message-label">input</div><pre class="json">${escapeHtml(pretty(data.input))}</pre></div><div><div class="message-label">output</div><pre class="json">${escapeHtml(pretty(data.output))}</pre></div></div></details>`;
}

function renderEngineView(snapshot) {
  const engine = snapshot.engine;
  $("#view-engine").innerHTML = `<div class="engine-stack">
    <article class="panel-card"><h3>실제 호출 순서와 책임</h3><pre class="json">${escapeHtml(pretty(engine.call_order))}</pre></article>
    ${engineSection("1. CandidateRetriever · 상품 후보 추리기", engine.retrieval, true)}
    ${engineSection("2. FinancialEligibilityEngine · 조건/금리/이자 평가", engine.evaluation)}
    ${engineSection("3. RankingService · 순위 계산", engine.ranking, true)}
    ${engineSection("4. QuestionPlanner · 다음 정보가치 질문 선택", engine.question_planner, true)}
  </div>`;
}

function renderAuditView(snapshot) {
  const events = snapshot.audit_timeline;
  $("#view-audit").innerHTML = `<div class="section-heading"><h2>Append-only Audit Timeline</h2><span class="count-badge">${events.length} events</span></div>
    <table class="audit-table"><thead><tr><th>#</th><th>시간</th><th>Component</th><th>Event</th><th>Entity / Payload</th></tr></thead><tbody>
    ${events.map((event, index) => `<tr><td>${index + 1}</td><td>${escapeHtml(shortTime(event.occurred_at))}</td><td><code>${escapeHtml(event.component)}</code></td><td><code>${escapeHtml(event.event_type)}</code></td><td><details><summary>${escapeHtml(Object.values(event.entity_refs || {}).join(", ") || "보기")}</summary><pre class="json">${escapeHtml(pretty(event.payload))}</pre></details></td></tr>`).join("")}
    </tbody></table>`;
}

function renderBundle() {
  const snapshot = state.bundle.snapshot;
  $("#empty-state").classList.add("hidden");
  $("#session-content").classList.remove("hidden");
  renderSummary(snapshot);
  if (!state.selectedRequestId || !state.bundle.requests.some((item) => item.request_id === state.selectedRequestId)) {
    state.selectedRequestId = state.bundle.requests.at(-1)?.request_id || null;
  }
  renderRequestList();
  renderRequestDetail();
  renderStateView(snapshot);
  renderEngineView(snapshot);
  renderAuditView(snapshot);
}

async function selectSession(sessionId, { preserveRequest = false } = {}) {
  state.selectedSessionId = sessionId;
  if (!preserveRequest) state.selectedRequestId = null;
  renderSessions();
  try {
    state.bundle = await fetchJson(`/api/debug/sessions/${encodeURIComponent(sessionId)}`);
    const selected = state.sessions.find((item) => item.search_session_id === sessionId);
    state.loadedSessionSignature = selected
      ? `${selected.updated_at}:${selected.request_count}:${selected.llm_call_count}`
      : null;
    renderBundle();
  } catch (error) {
    $("#connection-pill").textContent = `오류 · ${error.message}`;
    $("#connection-pill").classList.remove("ok");
  }
}

async function refresh({ automatic = false } = {}) {
  try {
    const payload = await fetchJson("/api/debug/sessions");
    state.sessions = payload.sessions;
    $("#connection-pill").textContent = `${payload.runtime.provider} · ${payload.runtime.model}`;
    $("#connection-pill").classList.add("ok");
    if (!state.selectedSessionId && state.sessions.length) state.selectedSessionId = state.sessions[0].search_session_id;
    if (state.selectedSessionId && !state.sessions.some((item) => item.search_session_id === state.selectedSessionId)) {
      state.selectedSessionId = state.sessions[0]?.search_session_id || null;
      state.selectedRequestId = null;
    }
    renderSessions();
    if (state.selectedSessionId) {
      const selected = state.sessions.find((item) => item.search_session_id === state.selectedSessionId);
      const signature = selected
        ? `${selected.updated_at}:${selected.request_count}:${selected.llm_call_count}`
        : null;
      if (!automatic || signature !== state.loadedSessionSignature) {
        await selectSession(state.selectedSessionId, { preserveRequest: automatic });
      }
    }
  } catch (error) {
    $("#connection-pill").textContent = `연결 실패 · ${error.message}`;
    $("#connection-pill").classList.remove("ok");
  }
}

$("#refresh-button").addEventListener("click", () => refresh());
document.querySelectorAll(".view-tabs button").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll(".view-tabs button").forEach((item) => item.classList.toggle("active", item === button));
    document.querySelectorAll(".view-panel").forEach((panel) => panel.classList.add("hidden"));
    $(`#view-${button.dataset.view}`).classList.remove("hidden");
  });
});

await refresh();
setInterval(() => refresh({ automatic: true }), 2500);
