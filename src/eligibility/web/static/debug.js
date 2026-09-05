const state = {
  sessions: [],
  selectedSessionId: null,
  bundle: null,
  selectedTurnId: null,
  loadedSessionSignature: null,
  fullAuditTimeline: null,
  selectedEvaluationId: null,
};

const $ = (selector) => document.querySelector(selector);
const escapeHtml = (value) => String(value ?? "")
  .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;").replaceAll("'", "&#039;");
const pretty = (value) => JSON.stringify(value, null, 2);
const shortTime = (value) => value ? new Date(value).toLocaleTimeString("ko-KR", { hour12: false }) : "-";

const LLM_ROLE_BY_SCHEMA = {
  IntentPatch: ["초기 검색조건 해석", "첫 자연어에서 바뀐 검색조건만 구조화합니다."],
  PreSearchAnswerPlan: ["사전 질문 답변 해석", "백엔드가 정한 사전 질문에 대한 자연어 답을 구조화합니다."],
  ActiveFinancialFactPlan: ["상품별 질문 답변 해석", "현재 상품 조건 질문에 대한 답을 참·거짓·확인 전·설명 요청으로 분류합니다."],
  ConversationPlan: ["일반 멀티턴 명령 해석", "사용자 발화를 허용된 검색 상태 변경 명령으로 구조화합니다."],
  GeneratedQuestion: ["상품별 질문 문구 생성", "Engine이 고른 조건을 사용자가 답하기 쉬운 문장으로 표현합니다."],
  GeneratedExplanation: ["추천 설명 문구 생성", "Engine 계산 결과를 근거 범위 안에서 설명합니다."],
};

function llmRole(call) {
  const schemaName = call.request?.response_schema_name;
  if (LLM_ROLE_BY_SCHEMA[schemaName]) return LLM_ROLE_BY_SCHEMA[schemaName];
  if (call.purpose === "QUESTION_GENERATION") return ["상품별 질문 문구 생성", "Engine이 선택한 질문의 표현만 작성합니다."];
  if (call.purpose === "INTENT_PARSING") return ["초기 검색조건 해석", "첫 사용자 입력을 검색조건 차이로 구조화합니다."];
  return [call.purpose || "LLM 작업", "구조화 출력 결과는 ApplicationService가 검증한 뒤 사용합니다."];
}

function requestKind(request) {
  const path = request.path || "";
  if (request.method === "GET") {
    return {
      key: "read",
      label: "조회·상태 동기화",
      description: "저장된 SearchSession 또는 Engine 결과를 읽는 요청입니다. 보통 LLM 호출이 없습니다.",
    };
  }
  if (path.endsWith("/messages")) {
    return {
      key: "answer",
      label: "사용자 응답 처리",
      description: "자연어 답변을 LLM이 구조화하고 ApplicationService가 검증·반영합니다. Engine 단계는 실제 이벤트가 있을 때만 아래에 표시됩니다.",
    };
  }
  if (request.method === "POST" && path.endsWith("/search-sessions")) {
    return {
      key: "start",
      label: "검색 세션 시작",
      description: "최초 자연어를 구조화하고 후보 검색·평가·랭킹을 시작합니다.",
    };
  }
  if (path.includes("/answers")) {
    return {
      key: "answer",
      label: "구조화 답변 반영",
      description: "이미 구조화된 답을 적용하고 Engine을 다시 실행합니다.",
    };
  }
  return {
    key: "write",
    label: "상태 변경 요청",
    description: "ApplicationService가 요청을 검증하고 허용된 상태 변경을 수행합니다.",
  };
}

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
    const sourceLabel = session.source === "ARCHIVED" ? "저장됨 · 읽기 전용" : "진행 중";
    return `<button class="session-card ${session.search_session_id === state.selectedSessionId ? "active" : ""}" data-session-id="${escapeHtml(session.search_session_id)}">
      <strong><span class="session-source ${String(session.source || "LIVE").toLowerCase()}">${escapeHtml(sourceLabel)}</span>${escapeHtml(utterance)}</strong>
      <span class="session-id">${escapeHtml(session.search_session_id)}</span>
      <span>대화 ${session.turn_count ?? "-"}회 · HTTP ${session.request_count}회 / LLM ${session.llm_call_count}회 · 후보 ${session.candidate_count}개</span>
    </button>`;
  }).join("");
  list.querySelectorAll("[data-session-id]").forEach((button) => {
    button.addEventListener("click", () => selectSession(button.dataset.sessionId));
  });
}

function selectedTurnContext(snapshot) {
  const turns = state.bundle?.turns || [];
  const turnIndex = turns.findIndex((item) => item.turn_id === state.selectedTurnId);
  const turn = turnIndex >= 0 ? turns[turnIndex] : null;
  const after = [...requestsForTurn(turn)].reverse().find((request) => request.state_after)?.state_after || null;
  if (!turn || !after) {
    return {
      turn: null,
      turnIndex: turns.length - 1,
      session: snapshot.session,
      multiTurn: snapshot.multi_turn,
      candidateCount: snapshot.session.candidate_product_ids?.length || 0,
      llmCallCount: state.bundle.requests.reduce((sum, request) => sum + (request.llm_calls || []).length, 0),
    };
  }
  return {
    turn,
    turnIndex,
    session: { ...snapshot.session, ...(after.session || {}) },
    multiTurn: {
      ...snapshot.multi_turn,
      pre_search_profile: after.pre_search_profile || {},
      active_question: after.active_question || null,
    },
    candidateCount: after.candidate_product_ids?.length || 0,
    llmCallCount: turns.slice(0, turnIndex + 1).reduce((sum, item) => sum + (item.llm_call_count || 0), 0),
  };
}

function renderSummary(snapshot) {
  const context = selectedTurnContext(snapshot);
  const session = context.session;
  const active = context.multiTurn.active_question;
  const currentStage = active?.question_kind === "PRE_SEARCH_PROFILE"
    ? "사전 조건 확인"
    : active
    ? "상품별 조건 확인"
    : "추천 결과 확인";
  const turnPosition = context.turn ? context.turnIndex + 1 : state.bundle.turns.length;
  const metrics = [
    ["기록", state.bundle.source === "ARCHIVED" ? "저장됨 · 읽기 전용" : "진행 중"],
    ["현재 단계", currentStage],
    ["대화", `${turnPosition} / ${state.bundle.turns.length}회`],
    ["답변 반영", `${(session.answered_question_ids || []).length}건`],
    ["후보", `${context.candidateCount}개`],
    ["LLM 호출", `${context.llmCallCount}회`],
  ];
  $("#summary-strip").innerHTML = metrics.map(([label, value]) =>
    `<div class="metric"><span>${escapeHtml(label)}</span><strong title="${escapeHtml(value)}">${escapeHtml(value)}</strong></div>`
  ).join("");
}

const PROFILE_LABELS = {
  PRODUCT_TYPE: "찾는 상품",
  APPLICATION_CAPACITY: "가입 명의",
  CONTRIBUTION_AND_TERM: "금액과 기간",
  LIQUIDITY_NEED: "중도 인출 가능성",
  INSTITUTION_SCOPE: "비교할 금융회사",
  PROTECTION_AND_CMA_SCOPE: "예금자보호·CMA 범위",
  COMMON_BENEFIT_WILLINGNESS: "공통 우대 의향",
};

const PRODUCT_TYPE_LABELS = {
  INSTALLMENT_SAVINGS: "적금",
  TIME_DEPOSIT: "예금",
  PARKING_ACCOUNT: "파킹통장",
  CMA: "CMA",
};

const APPLICATION_CAPACITY_LABELS = {
  INDIVIDUAL: "개인 명의",
  SOLE_PROPRIETOR: "개인사업자 명의",
  CORPORATION: "법인 명의",
};

function formatWon(value) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? `${numeric.toLocaleString("ko-KR")}원` : value;
}

function profileSummary(key, entry) {
  const value = entry?.value || {};
  if (key === "PRODUCT_TYPE") {
    return (value.product_types || []).map((item) => PRODUCT_TYPE_LABELS[item] || item).join(", ") || "확인 전";
  }
  if (key === "APPLICATION_CAPACITY") {
    return APPLICATION_CAPACITY_LABELS[value.application_capacity]
      || value.application_capacity
      || "확인 전";
  }
  if (key === "CONTRIBUTION_AND_TERM") {
    const amount = value.desired_amount_krw || value.balance_or_lump_sum_krw || value.maximum_amount_krw;
    const term = value.term_value ? `${value.term_value}${value.term_unit === "YEAR" ? "년" : value.term_unit === "MONTH" ? "개월" : ""}` : null;
    return [amount ? formatWon(amount) : null, value.contribution_frequency, term].filter(Boolean).join(" · ") || entry.source_text || "확인 전";
  }
  return entry.rationale || entry.source_text || Object.values(value).filter((item) => typeof item === "string").join(" · ") || "확인 전";
}

function renderCoreStatusCards(snapshot) {
  const context = selectedTurnContext(snapshot);
  const multi = context.multiTurn || {};
  const profile = multi.pre_search_profile || {};
  const pointInTime = context.turn ? `${context.turn.turn_id} 직후` : "최종 상태";
  const understood = Object.entries(profile)
    .filter(([, entry]) => entry?.answer_status && entry.answer_status !== "NOT_ASKED")
    .map(([key, entry]) => `<li><span>${escapeHtml(PROFILE_LABELS[key] || key)}</span><strong>${escapeHtml(profileSummary(key, entry))}</strong></li>`)
    .join("") || '<li class="empty-copy">아직 확정된 조건이 없습니다.</li>';
  const active = multi.active_question;
  const ranking = snapshot.engine?.ranking?.output || {};
  const rankingItems = ranking.items || [];
  const topProducts = rankingItems.slice(0, 5).map((item) => `
    <li>
      <span class="rank-number">${item.rank ?? "-"}</span>
      <div><strong>${escapeHtml(item.institution_name || "기관 확인 전")} · ${escapeHtml(item.product_name || item.product_id)}</strong><small>${escapeHtml(item.term_summary || "기간 확인 전")} · ${item.realizable_rate != null ? `현재 실현 가능 금리 ${escapeHtml(item.realizable_rate)}%` : "계산 가능한 금리 없음"} · 미확인 ${item.material_unknown_count ?? 0}건</small></div>
    </li>`).join("") || '<li class="empty-copy">아직 순위 결과가 없습니다.</li>';
  const failedRequests = state.bundle.requests.filter((request) => request.error || Number(request.status_code) >= 500);
  const materialUnknowns = rankingItems.reduce((sum, item) => sum + Number(item.material_unknown_count || 0), 0);
  const stability = ranking.stability || {};
  const attentionClass = failedRequests.length ? "danger" : stability.stable ? "good" : "warning";
  const attentionTitle = failedRequests.length
    ? `실패한 요청 ${failedRequests.length}건`
    : stability.stable
    ? "현재 Top 결과가 안정적입니다"
    : "추가 확인에 따라 순위가 바뀔 수 있습니다";
  const attentionCopy = failedRequests.length
    ? failedRequests.at(-1).error || `마지막 실패 HTTP ${failedRequests.at(-1).status_code}`
    : `상위 상품의 미확인 조건 합계 ${materialUnknowns}건 · ${stability.reason_code || "안정성 정보 없음"}`;
  $("#core-status-cards").innerHTML = `
    <article class="core-card conditions-card"><div class="core-card-title"><span>현재 기억 · ${escapeHtml(pointInTime)}</span><h3>AI가 이해한 조건</h3></div><ul class="condition-list">${understood}</ul></article>
    <article class="core-card question-card"><div class="core-card-title"><span>다음 단계 · ${escapeHtml(pointInTime)}</span><h3>${active ? "당시 사용자에게 이어서 한 질문" : "질문 완료"}</h3></div>${active ? `<div class="question-product">${escapeHtml(active.product_context || active.question_kind || "공통 질문")}</div><p>${escapeHtml(active.question)}</p><small>${escapeHtml(active.explanation || "이 답변을 반영해 추천을 다시 계산합니다.")}</small>` : '<p>당시 활성 질문이 없습니다. 추천 결과를 확인할 수 있는 상태입니다.</p>'}</article>
    <article class="core-card ranking-card"><div class="core-card-title"><span>최종 결과</span><h3>추천 상위 상품</h3></div><ol class="ranking-list">${topProducts}</ol></article>
    <article class="core-card attention-card ${attentionClass}"><div class="core-card-title"><span>최종 상태 확인</span><h3>${escapeHtml(attentionTitle)}</h3></div><p>${escapeHtml(attentionCopy)}</p></article>`;
}

function requestSummary(request) {
  const payload = request.request_payload || {};
  return payload.natural_language_query || payload.utterance || payload.message || payload.answer || "조회/상태 요청";
}

function requestsForTurn(turn) {
  const requestIds = new Set(turn?.request_ids || []);
  return state.bundle.requests.filter((request) => requestIds.has(request.request_id));
}

function primaryRequest(turn) {
  return state.bundle.requests.find((request) => request.request_id === turn?.primary_request_id) || null;
}

function lastQuestion(requests, stateKey) {
  for (const request of [...requests].reverse()) {
    const question = request?.[stateKey]?.active_question;
    if (question?.question) return question;
  }
  return null;
}

function generatedQuestionCall(question, preferredRequests = []) {
  if (!question) return null;
  const requestedRuleId = question.request?.requested_by_rule_id;
  const calls = [
    ...preferredRequests.flatMap((request) => request.llm_calls || []),
    ...(state.bundle?.requests || []).flatMap((request) => request.llm_calls || []),
  ];
  return calls.find((call) => {
    if (call.request?.response_schema_name !== "GeneratedQuestion") return false;
    if (call.output?.question === question.question) return true;
    return requestedRuleId && call.output?.rule_id === requestedRuleId;
  }) || null;
}

function questionProvenance(question, preferredRequests = []) {
  const owner = question?.explanation_details?.question_owner;
  if (owner === "DETERMINISTIC_BACKEND") {
    return {
      key: "deterministic",
      label: "APP · deterministic 질문",
      description: "백엔드가 질문의 순서와 문구를 결정",
    };
  }
  if (generatedQuestionCall(question, preferredRequests)) {
    return {
      key: "llm",
      label: "LLM · 생성 질문",
      description: "Engine이 고른 조건을 근거로 LLM이 질문 문구를 생성",
    };
  }
  return {
    key: "application",
    label: "APP · 저장된 질문",
    description: "기록에 질문은 있지만 생성 주체를 확정할 호출 정보가 없음",
  };
}

function assistantProvenance(message, requests) {
  const directCall = requests.flatMap((request) => request.llm_calls || []).find((call) => {
    const output = call.output || {};
    return [output.question, output.confirmation_question, output.assistant_message, output.explanation, output.message]
      .filter(Boolean)
      .includes(message);
  });
  if (directCall) {
    return {
      key: "llm",
      label: "LLM · 직접 응답",
      description: llmRole(directCall)[0],
    };
  }
  return {
    key: "deterministic",
    label: "APP · deterministic 응답",
    description: "LLM 구조화 결과 또는 저장 상태를 백엔드 문구로 표시",
  };
}

function turnConversation(turn) {
  const requests = requestsForTurn(turn);
  const request = primaryRequest(turn);
  if (!turn || !request) return null;
  const payload = request.response_payload || {};
  // Follow-up GETs are grouped into the same semantic Turn. Their state_before
  // already contains the question created by the primary write, so only the
  // primary request can tell us what the user actually saw before speaking.
  const beforeQuestion = request.state_before?.active_question || null;
  const responseMessages = [payload.assistant_message, payload.unresolved_warning]
    .filter((message, index, items) => message && items.indexOf(message) === index)
    .map((message) => ({ text: message, provenance: assistantProvenance(message, requests) }));
  const rawNextQuestion = payload.next_question || lastQuestion(requests, "state_after") || null;
  const sameActiveQuestion = beforeQuestion && rawNextQuestion && (
    (beforeQuestion.question_id && beforeQuestion.question_id === rawNextQuestion.question_id)
    || (!beforeQuestion.question_id && beforeQuestion.question === rawNextQuestion.question)
  );
  const nextQuestion = sameActiveQuestion ? null : rawNextQuestion;
  return {
    requests,
    request,
    beforeQuestion,
    beforeProvenance: beforeQuestion ? questionProvenance(beforeQuestion) : null,
    userInput: turn.user_input || requestSummary(request),
    responseMessages,
    nextQuestion,
    nextProvenance: nextQuestion ? questionProvenance(nextQuestion, requests) : null,
    retainedQuestion: sameActiveQuestion ? rawNextQuestion : null,
  };
}

function previewSource(provenance, action) {
  if (provenance?.key === "deterministic") return `DET ${action}`;
  if (provenance?.key === "llm") return `LLM ${action}`;
  return `APP ${action}`;
}

function turnLabel(turn) {
  if (turn.kind === "SESSION_START") return ["start", "검색 세션 시작"];
  if (turn.kind === "NATURAL_LANGUAGE_MESSAGE") return ["answer", "사용자 자연어 응답"];
  if (turn.kind === "STRUCTURED_ANSWER") return ["answer", "구조화 답변"];
  return ["write", "검색 상태 변경"];
}

function renderRequestList() {
  const turns = state.bundle.turns || [];
  $("#request-count").textContent = `${turns.length} turns · ${state.bundle.requests.length} HTTP`;
  $("#request-list").innerHTML = turns.map((turn) => {
    const request = primaryRequest(turn) || {};
    const [kindKey, kindLabel] = turnLabel(turn);
    return `
    <button class="request-row ${turn.turn_id === state.selectedTurnId ? "active" : ""}" data-turn-id="${escapeHtml(turn.turn_id)}">
      <span class="turn-number">#${turn.sequence}</span>
      <span class="request-copy">
        <strong>${escapeHtml(turn.turn_id)}</strong>
        <span class="request-kind ${kindKey}">${escapeHtml(kindLabel)}</span>
        <span>${escapeHtml(turn.user_input || requestSummary(request))}</span>
      </span>
      <span class="request-meta">${escapeHtml(shortTime(turn.started_at))}<br><span class="llm-count ${turn.llm_call_count ? "used" : "none"}">${turn.llm_call_count ? `LLM ${turn.llm_call_count}회` : "LLM 없음"}</span><br>HTTP ${turn.request_ids.length}회</span>
    </button>`;
  }).join("");
  $("#request-list").querySelectorAll("[data-turn-id]").forEach((button) => {
    button.addEventListener("click", () => {
      state.selectedTurnId = button.dataset.turnId;
      renderSummary(state.bundle.snapshot);
      renderCoreStatusCards(state.bundle.snapshot);
      renderRequestList();
      renderRequestDetail();
    });
  });
}

function renderCoreTurnList() {
  const turns = state.bundle.turns || [];
  $("#core-turn-count").textContent = `${turns.length} turns`;
  $("#core-turn-list").innerHTML = turns.map((turn) => {
    const conversation = turnConversation(turn);
    const request = conversation?.request || {};
    const [, kindLabel] = turnLabel(turn);
    const incoming = conversation?.beforeQuestion;
    const response = conversation?.responseMessages?.[0];
    const outgoing = conversation?.nextQuestion;
    return `<button class="core-turn-row ${turn.turn_id === state.selectedTurnId ? "active" : ""}" data-core-turn-id="${escapeHtml(turn.turn_id)}">
      <span class="turn-number">#${turn.sequence}</span>
      <span class="turn-preview">
        ${incoming ? `<span class="turn-preview-line app"><b>${escapeHtml(previewSource(conversation.beforeProvenance, "질문"))}</b><em>${escapeHtml(incoming.question)}</em></span>` : '<span class="turn-preview-line start"><b>시작</b><em>사용자가 검색을 시작함</em></span>'}
        <span class="turn-preview-line user"><b>사용자</b><em>${escapeHtml(conversation?.userInput || kindLabel)}</em></span>
        ${response ? `<span class="turn-preview-line response"><b>${escapeHtml(previewSource(response.provenance, "응답"))}</b><em>${escapeHtml(response.text)}</em></span>` : ""}
        ${outgoing ? `<span class="turn-preview-line next"><b>${escapeHtml(previewSource(conversation.nextProvenance, "질문"))}</b><em>${escapeHtml(outgoing.question)}</em></span>` : ""}
        <small>${escapeHtml(kindLabel)} · LLM ${turn.llm_call_count}회 · HTTP ${turn.request_ids.length}회 · ${escapeHtml(shortTime(request.started_at))}</small>
      </span>
    </button>`;
  }).join("") || '<div class="muted">표시할 대화 Turn이 없습니다.</div>';
  $("#core-turn-list").querySelectorAll("[data-core-turn-id]").forEach((button) => {
    button.addEventListener("click", () => {
      state.selectedTurnId = button.dataset.coreTurnId;
      renderSummary(state.bundle.snapshot);
      renderCoreStatusCards(state.bundle.snapshot);
      renderCoreTurnList();
      renderCoreTurnDetail();
      renderRequestList();
      renderRequestDetail();
    });
  });
}

function llmOutputSummary(call) {
  const output = call.output || {};
  if (output.question) return output.question;
  if (Array.isArray(output.actions) && output.actions.length) {
    return output.actions.map((action) => [action.operation, action.answer].filter((value) => value !== undefined && value !== null).join(" · ")).join(", ");
  }
  const primitives = Object.entries(output)
    .filter(([, value]) => ["string", "number", "boolean"].includes(typeof value))
    .slice(0, 3)
    .map(([key, value]) => `${key}: ${value}`);
  if (primitives.length) return primitives.join(" · ");
  return call.error ? `실패: ${call.error}` : "구조화 결과를 반환했습니다.";
}

function renderConversationMessage({ actor, provenance, text, note = null }) {
  const provenanceKey = provenance?.key || (actor === "user" ? "user" : "application");
  const label = provenance?.label || (actor === "user" ? "사용자 · 자연어 입력" : "APP");
  const description = note || provenance?.description;
  return `<article class="conversation-message ${escapeHtml(actor)} ${escapeHtml(provenanceKey)}">
    <div class="conversation-message-meta"><span>${escapeHtml(label)}</span>${description ? `<small>${escapeHtml(description)}</small>` : ""}</div>
    <p>${escapeHtml(text)}</p>
  </article>`;
}

function renderTurnConversation(conversation) {
  const messages = [];
  if (conversation.beforeQuestion) {
    messages.push(renderConversationMessage({
      actor: "application",
      provenance: conversation.beforeProvenance,
      text: conversation.beforeQuestion.question,
      note: conversation.beforeQuestion.product_context || conversation.beforeProvenance.description,
    }));
  }
  messages.push(renderConversationMessage({
    actor: "user",
    text: conversation.userInput,
    note: conversation.beforeQuestion ? "앱의 질문에 대한 답변 또는 되물음" : "검색을 시작한 최초 요청",
  }));
  conversation.responseMessages.forEach((message) => {
    messages.push(renderConversationMessage({
      actor: "application",
      provenance: message.provenance,
      text: message.text,
    }));
  });
  if (conversation.nextQuestion) {
    messages.push(renderConversationMessage({
      actor: "application",
      provenance: conversation.nextProvenance,
      text: conversation.nextQuestion.question,
      note: conversation.nextQuestion.product_context || conversation.nextProvenance.description,
    }));
  }
  if (conversation.retainedQuestion) {
    messages.push('<div class="conversation-retained"><strong>기존 활성 질문 유지</strong><span>새 질문 말풍선을 다시 보낸 것이 아니라, 사용자가 이어서 답할 수 있도록 같은 질문 상태를 유지했습니다.</span></div>');
  }
  if (!conversation.responseMessages.length && !conversation.nextQuestion) {
    if (!conversation.retainedQuestion) messages.push('<div class="conversation-end"><strong>앱 후속 메시지 없음</strong><span>이 Turn에서는 상태·추천 결과만 갱신되었습니다.</span></div>');
  }
  return `<div class="conversation-transcript">${messages.join("")}</div>`;
}

function renderCoreTurnDetail() {
  const turn = (state.bundle.turns || []).find((item) => item.turn_id === state.selectedTurnId);
  const conversation = turnConversation(turn);
  if (!turn || !conversation) {
    $("#core-turn-detail").innerHTML = '<div class="muted">왼쪽에서 대화 Turn을 선택하세요.</div>';
    return;
  }
  const { requests, request } = conversation;
  const calls = requests.flatMap((item) => item.llm_calls || []);
  const delta = requestStateDelta(request);
  const failed = requests.find((item) => item.error || Number(item.status_code) >= 500);
  const llmCards = calls.map((call) => {
    const [roleLabel] = llmRole(call);
    return `<div class="compact-step ${call.status === "FAILED" ? "failed" : ""}"><span>LLM</span><div><strong>${escapeHtml(roleLabel)}</strong><small>${escapeHtml(llmOutputSummary(call))}</small></div><em>${escapeHtml(call.status || "-")} · ${call.latency_ms ?? "-"}ms</em></div>`;
  }).join("") || '<div class="compact-step"><span>앱</span><div><strong>결정론적 처리</strong><small>이 Turn에서는 LLM을 호출하지 않았습니다.</small></div></div>';
  const stateChanges = [
    delta?.intent_version ? `Intent ${delta.intent_version}` : null,
    delta?.candidate_count ? `후보 ${delta.candidate_count}` : null,
    delta?.answered_question_ids_added?.length ? `답변 ${delta.answered_question_ids_added.length}건 반영` : null,
    delta?.ranking_top_after?.length ? `Top ${delta.ranking_top_after.length} 계산` : null,
  ].filter(Boolean);
  $("#core-turn-detail").innerHTML = `
    <div class="core-turn-head"><div><span>${escapeHtml(turn.turn_id)}</span><h3>${escapeHtml(turn.user_input || turnLabel(turn)[1])}</h3></div><strong class="${failed ? "error-text" : "success-text"}">${failed ? "처리 실패" : `HTTP ${request.status_code ?? "-"}`}</strong></div>
    <div class="compact-section conversation-section"><h4>실제 사용자 화면의 질문과 응답</h4>${renderTurnConversation(conversation)}</div>
    ${failed ? `<div class="diagnostic error"><strong>확인할 오류</strong><span>${escapeHtml(failed.error || `HTTP ${failed.status_code}`)}</span></div>` : ""}
    <div class="compact-section"><h4>내부 LLM 해석·생성</h4>${llmCards}</div>
    <div class="compact-section"><h4>상태와 결과 변화</h4><div class="change-chips">${stateChanges.map((item) => `<span>${escapeHtml(item)}</span>`).join("") || '<span>상태 변화 없음</span>'}</div></div>
    <button type="button" id="open-turn-detail" class="open-detail-button">이 Turn의 Prompt와 실제 JSON 상세 보기</button>`;
  $("#open-turn-detail").addEventListener("click", () => {
    $("#technical-details").open = true;
    syncTechnicalDetailsScroll();
    document.querySelector('[data-view="turns"]').click();
    $("#technical-details").scrollIntoView({ behavior: "smooth", block: "start" });
  });
}

function renderCoreOverview(snapshot) {
  const sessionId = snapshot.session?.search_session_id || state.selectedSessionId || "";
  const sessionIdButton = $("#core-session-id");
  sessionIdButton.textContent = sessionId ? `${sessionId} · 복사` : "";
  sessionIdButton.dataset.sessionId = sessionId;
  renderCoreStatusCards(snapshot);
  renderCoreTurnList();
  renderCoreTurnDetail();
}

function syncTechnicalDetailsScroll() {
  $(".debug-main").classList.toggle("details-open", $("#technical-details").open);
}

function renderLlmCall(call, index) {
  const request = call.request || {};
  const messages = request.messages || [];
  const system = messages.filter((item) => item.role === "system");
  const users = messages.filter((item) => item.role === "user");
  const schemas = state.bundle?.schemas || {};
  const domainSchema = schemas[request.domain_schema_ref] || null;
  const transportSchema = schemas[request.transport_schema_ref]
    || schemas[request.response_schema_ref]
    || request.response_json_schema
    || null;
  const [roleLabel, roleDescription] = llmRole(call);
  const generatedQuestion = request.response_schema_name === "GeneratedQuestion" && call.output?.question
    ? `<div class="generated-question"><span>생성된 사용자 질문</span><strong>${escapeHtml(call.output.question)}</strong></div>`
    : "";
  const systemHtml = system.length
    ? system.map((item) => `<pre class="prompt">${escapeHtml(item.content)}</pre>`).join("")
    : '<div class="notice"><strong>system message 없음</strong><br>현재 구현은 아래 지시문 전체를 user message 하나에 포함해 전송합니다.</div>';
  return `<article class="llm-card">
    <div class="llm-head">
      <div><small>LLM 호출 #${index + 1}</small><strong>${escapeHtml(roleLabel)}</strong></div>
      <span>${escapeHtml(call.provider)} / ${escapeHtml(call.model)} · ${call.latency_ms ?? "-"}ms · ${escapeHtml(call.status)}</span>
    </div>
    <div class="llm-role-copy">${escapeHtml(roleDescription)}</div>
    ${generatedQuestion}
    <div class="message-label">system prompt</div>
    ${systemHtml}
    <div class="message-label">user message · 실제 전송 prompt</div>
    ${users.map((item) => `<pre class="prompt">${escapeHtml(item.content)}</pre>`).join("") || '<div class="notice">user message 없음</div>'}
    <details class="block">
      <summary class="message-label">실제 OpenAI 전송 JSON Schema</summary>
      <pre class="json">${escapeHtml(pretty(transportSchema))}</pre>
    </details>
    <details class="block">
      <summary class="message-label">Domain/Pydantic 원본 Schema</summary>
      <pre class="json">${escapeHtml(pretty(domainSchema))}</pre>
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
        actual_transport_payload: request.transport_payload,
      }))}</pre>
    </details>
  </article>`;
}

function renderRequestFlow(requests) {
  const primary = requests[0] || {};
  const kind = requestKind(primary);
  const calls = requests.flatMap((request) => request.llm_calls || []);
  const events = requests.flatMap((request) => request.application_events || []);
  const eventTypes = new Set(events.map((event) => event.event_type));
  const interpretationCalls = calls.filter((call) => call.request?.response_schema_name !== "GeneratedQuestion");
  const questionCalls = calls.filter((call) => call.request?.response_schema_name === "GeneratedQuestion");
  const hasAny = (...names) => names.some((name) => eventTypes.has(name));
  const nodes = ["사용자 입력"];
  if (interpretationCalls.length) nodes.push(...interpretationCalls.map((call) => llmRole(call)[0]));
  else nodes.push("결정론적 입력 처리");
  if (events.length || primary.state_before || primary.state_after) nodes.push("ApplicationService 검증·상태 반영");
  if (hasAny("CANDIDATE_FILTERED", "CANDIDATE_RETAINED")) nodes.push("후보 검색");
  if (hasAny("CANDIDATE_EVALUATED", "STATUS_DERIVED", "RATE_SUMMARY_CREATED")) nodes.push("조건·금리 평가");
  if (hasAny("RANKING_CALCULATED", "TOP_K_STABILITY_CHECKED")) nodes.push("랭킹 계산");
  if (hasAny("QUESTION_CANDIDATE_SCORED", "QUESTION_SELECTED")) nodes.push("질문 대상 선택");
  if (questionCalls.length) nodes.push(`LLM 질문 문구 생성 ${questionCalls.length}회`);
  if (hasAny("RECOMMENDATION_CREATED")) nodes.push("추천 결과 생성");
  nodes.push("브라우저 응답");
  return `<div class="request-classification ${kind.key}"><strong>${escapeHtml(kind.label)}</strong><span>${escapeHtml(kind.description)}</span></div>
    <div class="request-flow">${nodes.map((node, index) => `${index ? '<span class="flow-arrow">→</span>' : ''}<span class="flow-node">${escapeHtml(node)}</span>`).join("")}</div>
    <div class="notice neutral">위 흐름은 고정 예시가 아니라 이 Turn에 저장된 LLM 호출과 Application/Engine audit event로 조립했습니다.</div>`;
}

function requestStateDelta(request) {
  const before = request.state_before;
  const after = request.state_after;
  if (!after) return null;
  const beforeSession = before?.session || {};
  const afterSession = after.session || {};
  const beforeCandidates = new Set(before?.candidate_product_ids || []);
  const afterCandidates = new Set(after.candidate_product_ids || []);
  const beforeAnswered = new Set(beforeSession.answered_question_ids || []);
  const afterAnswered = new Set(afterSession.answered_question_ids || []);
  return {
    intent_version: `${beforeSession.intent_version ?? "세션 생성 전"} → ${afterSession.intent_version ?? "-"}`,
    ranking_run_id: `${beforeSession.ranking_run_id ?? "없음"} → ${afterSession.ranking_run_id ?? "없음"}`,
    active_question_id: `${beforeSession.active_question_id ?? "없음"} → ${afterSession.active_question_id ?? "없음"}`,
    candidate_count: `${beforeCandidates.size} → ${afterCandidates.size}`,
    candidate_ids_added: [...afterCandidates].filter((id) => !beforeCandidates.has(id)),
    candidate_ids_removed: [...beforeCandidates].filter((id) => !afterCandidates.has(id)),
    answered_question_ids_added: [...afterAnswered].filter((id) => !beforeAnswered.has(id)),
    ranking_top_before: before?.ranking?.top_product_ids || [],
    ranking_top_after: after.ranking?.top_product_ids || [],
    operations_executed: request.response_payload?.operations_executed || [],
  };
}

function renderApplicationEvents(request) {
  const events = request.application_events || [];
  if (!events.length) {
    return '<div class="notice neutral">이 요청에서 새로 기록된 Engine/Application audit event가 없습니다. GET 조회이거나 상태 변화가 없는 요청일 수 있습니다.</div>';
  }
  return `<div class="execution-events">${events.map((event, index) => `
    <div class="execution-event">
      <span>${index + 1}</span>
      <div><strong>${escapeHtml(event.component)} · ${event.count || 1}회</strong><code>${escapeHtml(event.event_type)}</code></div>
      <details><summary>대표 payload</summary><pre class="json">${escapeHtml(pretty({ entity_refs: event.sample_entity_refs, payload: event.sample_payload, first: event.first_occurred_at, last: event.last_occurred_at }))}</pre></details>
    </div>`).join("")}</div>`;
}

function renderRequestDetail() {
  const turn = (state.bundle.turns || []).find((item) => item.turn_id === state.selectedTurnId);
  const requests = requestsForTurn(turn);
  const request = primaryRequest(turn);
  if (!turn || !request) {
    $("#request-detail").innerHTML = '<div class="muted">왼쪽에서 대화 Turn을 선택하세요.</div>';
    return;
  }
  const calls = requests.flatMap((item) => item.llm_calls || []);
  const events = requests.flatMap((item) => item.application_events || []);
  const completedLlm = calls.some((call) => call.status === "COMPLETED");
  const failedLlm = calls.some((call) => call.status === "FAILED");
  const postLlmFailure = Number(request.status_code) >= 500 && completedLlm && !failedLlm;
  const diagnostic = postLlmFailure
    ? `<div class="diagnostic error"><strong>LLM 출력 이후 애플리케이션 실패</strong><span>모델 출력은 정상 반환됐고, 그 다음 상태 반영·재평가·랭킹·질문 계획 중 오류가 발생했습니다. ${escapeHtml(request.error || "")}</span></div>`
    : request.error
    ? `<div class="diagnostic error"><strong>요청 실패</strong><span>${escapeHtml(request.error)}</span></div>`
    : "";
  const stateDelta = requestStateDelta(request);
  $("#request-detail").innerHTML = `
    <div class="detail-title">
      <h2>${escapeHtml(turn.turn_id)} · ${escapeHtml(request.method)} ${escapeHtml(request.path)}</h2>
      <span class="status-code">HTTP ${request.status_code ?? "진행 중"} · ${request.duration_ms ?? "-"}ms</span>
    </div>
    ${renderRequestFlow(requests)}
    ${diagnostic}
    <div class="request-snapshot-note"><strong>선택한 요청 시점의 상태</strong><span>아래 before/after는 현재 최신 화면이 아니라 이 Web API 요청 직전과 직후에 저장된 snapshot입니다.</span></div>
    <div class="block">
      <div class="block-title"><strong>ApplicationService 상태 변화</strong><span>request-scoped delta</span></div>
      <pre class="json">${escapeHtml(pretty(stateDelta))}</pre>
    </div>
    <div class="block json-grid">
      <div><div class="block-title"><strong>브라우저 → Web API</strong><span>request</span></div><pre class="json">${escapeHtml(pretty(request.request_payload))}</pre></div>
      <div><div class="block-title"><strong>Web API → 브라우저</strong><span>response</span></div><pre class="json">${escapeHtml(pretty(request.response_payload))}</pre></div>
    </div>
    <div class="block">
      <div class="block-title"><strong>이 대화 Turn에서 실행된 LLM 작업</strong><span>${calls.length ? `${calls.length}회` : "없음"}</span></div>
      ${calls.length ? calls.map(renderLlmCall).join("") : '<div class="notice">LLM 호출 없음 · 결정론적 API 처리만 수행했습니다.</div>'}
    </div>
    <div class="block">
      <div class="block-title"><strong>이 대화 Turn에서 실행된 ApplicationService / Engine 단계</strong><span>${events.length} events</span></div>
      ${renderApplicationEvents({ application_events: events })}
    </div>
    <details class="block">
      <summary class="message-label">이 Turn에 포함된 HTTP 요청 ${requests.length}개</summary>
      <pre class="json">${escapeHtml(pretty(requests.map((item) => ({ request_id: item.request_id, method: item.method, path: item.path, status_code: item.status_code, llm_call_count: (item.llm_calls || []).length }))))}</pre>
    </details>
    <details class="block">
      <summary class="message-label">요청 직전·직후 상태 원문</summary>
      <div class="json-grid block"><div><div class="message-label">before</div><pre class="json">${escapeHtml(pretty(request.state_before))}</pre></div><div><div class="message-label">after</div><pre class="json">${escapeHtml(pretty(request.state_after))}</pre></div></div>
    </details>`;
}

function renderStateView(snapshot) {
  const multi = snapshot.multi_turn;
  const activeOwner = multi.active_question?.question_kind === "PRE_SEARCH_PROFILE"
    ? "백엔드가 질문 순서와 문구를 결정하고, LLM은 사용자 답만 해석합니다."
    : multi.active_question
    ? "Engine이 질문 대상을 선택하고, 허용되는 상품별 질문은 LLM이 문구를 표현할 수 있습니다."
    : "현재 활성 질문이 없습니다.";
  const pointInTime = state.bundle.source === "ARCHIVED"
    ? "저장된 세션의 마지막 전체 snapshot입니다. 과거 Turn 시점은 ‘대화 Turn별 실제 호출’의 before/after에서 확인하세요."
    : "현재 살아 있는 SearchSession의 최신 상태입니다. 과거 Turn 시점은 ‘대화 Turn별 실제 호출’의 before/after에서 확인하세요.";
  $("#view-state").innerHTML = `<div class="state-grid">
    <article class="panel-card wide"><h3>표시 시점</h3><div class="notice neutral">${escapeHtml(pointInTime)}</div></article>
    <article class="panel-card wide"><h3>현재 질문의 소유권</h3><div class="notice">${escapeHtml(activeOwner)}</div></article>
    <article class="panel-card wide"><h3>이 앱의 멀티턴 기억 방식</h3><div class="notice">이전 채팅 메시지 배열을 그대로 모델에 보내지 않습니다. 매 turn마다 Working Note, 현재 Intent, 사전 질문 프로필, 사용자 선언, 활성 질문, Top K 요약을 조립해 CURRENT_CONTEXT로 전달합니다.</div></article>
    <article class="panel-card"><h3>Search Working Note</h3><pre class="prompt">${escapeHtml(multi.working_note || "없음")}</pre></article>
    <article class="panel-card"><h3>현재 Active Question</h3><pre class="json">${escapeHtml(pretty(multi.active_question))}</pre></article>
    <article class="panel-card wide"><h3>결정론적 사전 질문 상태</h3><pre class="json">${escapeHtml(pretty(multi.pre_search_profile))}</pre></article>
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
  const evaluations = engine.evaluation.output || {};
  const evaluationOptions = Object.entries(evaluations).map(([productId, item]) =>
    `<option value="${escapeHtml(productId)}">${escapeHtml(item.product_name || productId)}</option>`
  ).join("");
  const pointInTime = state.bundle.source === "ARCHIVED" ? "저장된 마지막 Engine snapshot" : "현재 최신 Engine snapshot";
  $("#view-engine").innerHTML = `<div class="engine-stack">
    <article class="panel-card"><h3>표시 시점</h3><div class="notice neutral">${escapeHtml(pointInTime)}입니다. 선택한 과거 Turn의 실행 여부와 상태 변화는 Turn 탭에서 확인하세요.</div></article>
    <article class="panel-card"><h3>현재 코드의 호출 순서와 책임</h3><div class="notice">LLM은 Engine tool을 직접 호출하지 않습니다. 쓰기 요청에서 구조화된 해석을 반환하면 ApplicationService가 검증 후 아래 Engine 단계를 실행합니다. GET 상태 조회는 별도 읽기 경로입니다.</div><pre class="json">${escapeHtml(pretty(engine.call_order))}</pre></article>
    <article class="panel-card"><h3>오케스트레이션 경계</h3><pre class="json">${escapeHtml(pretty(engine.orchestration_boundary))}</pre></article>
    ${engineSection("1. CandidateRetriever · 상품 후보 추리기", engine.retrieval, true)}
    ${engineSection("2. FinancialEligibilityEngine · 조건/금리/이자 평가", engine.evaluation)}
    <article class="panel-card"><h3>상품별 전체 평가 · 필요할 때 로드</h3>
      <select id="evaluation-select"><option value="">상품을 선택하세요</option>${evaluationOptions}</select>
      <pre id="evaluation-detail" class="json">전체 후보의 큰 평가 payload는 기본 bundle에 넣지 않습니다.</pre>
    </article>
    ${engineSection("3. RankingService · 순위 계산", engine.ranking, true)}
    ${engineSection("4. QuestionPlanner · 사전 질문 또는 금리순 상품별 질문 선택", engine.question_planner, true)}
  </div>`;
  $("#evaluation-select").addEventListener("change", async (event) => {
    const productId = event.target.value;
    if (!productId) return;
    state.selectedEvaluationId = productId;
    $("#evaluation-detail").textContent = "불러오는 중…";
    try {
      const detail = await fetchJson(
        `/api/debug/sessions/${encodeURIComponent(state.selectedSessionId)}/evaluations/${encodeURIComponent(productId)}`
      );
      if (state.selectedEvaluationId === productId) {
        $("#evaluation-detail").textContent = pretty(detail);
      }
    } catch (error) {
      $("#evaluation-detail").textContent = `오류 · ${error.message}`;
    }
  });
}

function renderAuditView(snapshot, events = state.fullAuditTimeline || snapshot.audit_timeline) {
  $("#view-audit").innerHTML = `<div class="section-heading"><h2>Append-only Audit Timeline</h2><span class="count-badge">${events.length} events</span></div>
    ${state.fullAuditTimeline ? "" : '<div class="notice">목록은 가볍게 표시하고, Audit 탭을 열면 전체 payload를 불러옵니다.</div>'}
    <table class="audit-table"><thead><tr><th>#</th><th>시간</th><th>Component</th><th>Event</th><th>Entity / Payload</th></tr></thead><tbody>
    ${events.map((event, index) => `<tr><td>${index + 1}</td><td>${escapeHtml(shortTime(event.occurred_at))}</td><td><code>${escapeHtml(event.component)}</code></td><td><code>${escapeHtml(event.event_type)}</code></td><td><details><summary>${escapeHtml(Object.values(event.entity_refs || {}).join(", ") || "보기")}</summary><pre class="json">${escapeHtml(pretty(event.payload))}</pre></details></td></tr>`).join("")}
    </tbody></table>`;
}

async function loadFullAuditTimeline() {
  if (!state.bundle || state.fullAuditTimeline) return;
  try {
    const payload = await fetchJson(state.bundle.snapshot.audit_timeline_endpoint);
    state.fullAuditTimeline = payload.events;
    renderAuditView(state.bundle.snapshot);
  } catch (error) {
    $("#view-audit").insertAdjacentHTML(
      "afterbegin",
      `<div class="notice">Audit payload 로드 실패 · ${escapeHtml(error.message)}</div>`
    );
  }
}

function renderBundle() {
  const snapshot = state.bundle.snapshot;
  $("#empty-state").classList.add("hidden");
  $("#session-content").classList.remove("hidden");
  if (!state.selectedTurnId || !(state.bundle.turns || []).some((item) => item.turn_id === state.selectedTurnId)) {
    state.selectedTurnId = state.bundle.turns?.at(-1)?.turn_id || null;
  }
  renderSummary(snapshot);
  renderCoreOverview(snapshot);
  renderRequestList();
  renderRequestDetail();
  renderStateView(snapshot);
  renderEngineView(snapshot);
  renderAuditView(snapshot);
}

async function selectSession(sessionId, { preserveRequest = false } = {}) {
  state.selectedSessionId = sessionId;
  state.fullAuditTimeline = null;
  state.selectedEvaluationId = null;
  if (!preserveRequest) state.selectedTurnId = null;
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
    const history = payload.runtime.history || {};
    const droppedWrites = Number(history.dropped_write_count || 0);
    const connectionPill = $("#connection-pill");
    connectionPill.classList.toggle("ok", droppedWrites === 0);
    connectionPill.classList.toggle("warning", droppedWrites > 0);
    connectionPill.textContent = droppedWrites > 0
      ? `${payload.runtime.provider} · ${payload.runtime.model} · 저장 유실 ${droppedWrites}건`
      : `${payload.runtime.provider} · ${payload.runtime.model}`;
    connectionPill.title = droppedWrites > 0
      ? `마지막 유실 ${history.last_dropped_at || "시각 확인 전"} · 유형별 ${JSON.stringify(history.dropped_write_count_by_kind || {})}`
      : "디버그 히스토리 저장 오류 없음";
    if (!state.selectedSessionId && state.sessions.length) state.selectedSessionId = state.sessions[0].search_session_id;
    if (state.selectedSessionId && !state.sessions.some((item) => item.search_session_id === state.selectedSessionId)) {
      state.selectedSessionId = state.sessions[0]?.search_session_id || null;
      state.selectedTurnId = null;
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
    $("#connection-pill").classList.remove("warning");
  }
}

$("#refresh-button").addEventListener("click", () => refresh());
$("#core-session-id").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  const sessionId = button.dataset.sessionId;
  if (!sessionId) return;
  try {
    await navigator.clipboard.writeText(sessionId);
    button.textContent = `${sessionId} · 복사됨`;
    setTimeout(() => { button.textContent = `${sessionId} · 복사`; }, 1200);
  } catch (_error) {
    button.textContent = sessionId;
  }
});
$("#technical-details").addEventListener("toggle", syncTechnicalDetailsScroll);
document.querySelectorAll(".view-tabs button").forEach((button) => {
  button.addEventListener("click", async () => {
    document.querySelectorAll(".view-tabs button").forEach((item) => item.classList.toggle("active", item === button));
    document.querySelectorAll(".view-panel").forEach((panel) => panel.classList.add("hidden"));
    $(`#view-${button.dataset.view}`).classList.remove("hidden");
    if (button.dataset.view === "audit") await loadFullAuditTimeline();
  });
});

await refresh();
setInterval(() => refresh({ automatic: true }), 2500);
