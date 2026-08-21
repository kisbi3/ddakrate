const $ = (selector) => document.querySelector(selector);

function createConversationUserId() {
  const suffix = globalThis.crypto?.randomUUID?.()
    || `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `WEB-${suffix}`;
}

const state = {
  runtime: null,
  session: null,
  uiState: null,
  recommendations: null,
  activeQuestion: null,
  currentDetail: null,
  detailCache: new Map(),
  detailCacheRecommendationId: null,
  busy: false,
  productNames: new Map(),
  userId: createConversationUserId(),
};

const els = {
  llmPill: $('#llm-pill'),
  productCountPill: $('#product-count-pill'),
  starterPrompts: $('#starter-prompts'),
  chatScroll: $('#chat-scroll'),
  messages: $('#messages'),
  composer: $('#composer'),
  input: $('#message-input'),
  sendButton: $('#send-button'),
  newChatButton: $('#new-chat-button'),
  refreshButton: $('#refresh-button'),
  stateChips: $('#state-chips'),
  recommendations: $('#recommendations'),
  recommendationCount: $('#recommendation-count'),
  rankingToggle: $('#ranking-toggle'),
  detailBackdrop: $('#detail-backdrop'),
  detailDrawer: $('#detail-drawer'),
  detailInstitution: $('#detail-institution'),
  detailProductName: $('#detail-product-name'),
  detailContent: $('#detail-content'),
  detailClose: $('#detail-close'),
  toastStack: $('#toast-stack'),
};

const krw = new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 0 });
const rateFmt = new Intl.NumberFormat('ko-KR', { minimumFractionDigits: 0, maximumFractionDigits: 2 });

function formatWon(value) {
  if (value === null || value === undefined || value === '') return '-';
  const n = Number(value);
  return Number.isFinite(n) ? `${krw.format(n)}원` : '-';
}

function formatRate(value) {
  const n = Number(value);
  return Number.isFinite(n) ? `${rateFmt.format(n)}%` : '-';
}

function escapeHtml(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}

function safeUrl(value) {
  const text = String(value ?? '');
  return /^https?:\/\//i.test(text) ? text : null;
}

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.body !== undefined) headers['Content-Type'] = 'application/json';
  const response = await fetch(`/api${path}`, {
    ...options,
    headers,
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  });
  let payload = {};
  if (response.status !== 204) {
    try { payload = await response.json(); } catch { payload = {}; }
  }
  if (!response.ok) {
    const detail = payload.detail || payload.error || `HTTP ${response.status}`;
    const err = new Error(detail);
    err.payload = payload;
    err.status = response.status;
    throw err;
  }
  return payload;
}

function toast(message, kind = 'normal') {
  const node = document.createElement('div');
  node.className = `toast ${kind === 'error' ? 'error' : ''}`;
  node.textContent = message;
  els.toastStack.appendChild(node);
  setTimeout(() => node.remove(), 4200);
}

function setBusy(busy) {
  state.busy = busy;
  els.sendButton.disabled = busy;
  els.refreshButton.disabled = busy || !state.session;
  els.newChatButton.disabled = busy;
  for (const button of els.rankingToggle.querySelectorAll('button')) {
    button.disabled = busy || !state.session;
  }
  els.input.disabled = busy;
}

function appendMessage(role, text, { meta = null } = {}) {
  const row = document.createElement('div');
  row.className = `message ${role === 'user' ? 'user-message' : 'assistant-message'}`;
  if (role !== 'user') {
    const avatar = document.createElement('div');
    avatar.className = 'avatar';
    avatar.textContent = 'AI';
    row.appendChild(avatar);
  }
  const bubble = document.createElement('div');
  bubble.className = 'bubble';
  const body = document.createElement('div');
  body.textContent = text;
  bubble.appendChild(body);
  if (meta) {
    const metaNode = document.createElement('div');
    metaNode.style.cssText = 'margin-top:6px;font-size:10px;opacity:.68;';
    metaNode.textContent = meta;
    bubble.appendChild(metaNode);
  }
  row.appendChild(bubble);
  els.messages.appendChild(row);
  requestAnimationFrame(() => { els.chatScroll.scrollTop = els.chatScroll.scrollHeight; });
}

function appendProcessingMessage() {
  const row = document.createElement('div');
  row.className = 'message assistant-message processing-message';
  row.setAttribute('role', 'status');
  row.setAttribute('aria-live', 'polite');
  row.setAttribute('aria-label', 'AI가 입력한 내용을 처리하고 있습니다.');

  const avatar = document.createElement('div');
  avatar.className = 'avatar';
  avatar.textContent = 'AI';
  row.appendChild(avatar);

  const bubble = document.createElement('div');
  bubble.className = 'bubble processing-bubble';
  bubble.setAttribute('aria-hidden', 'true');
  bubble.innerHTML = '<span>.</span><span>.</span><span>.</span>';
  row.appendChild(bubble);

  els.messages.appendChild(row);
  requestAnimationFrame(() => { els.chatScroll.scrollTop = els.chatScroll.scrollHeight; });
  return row;
}

function removeProcessingMessage(row) {
  row?.remove();
}

function activeQuestionMessage() {
  return els.messages.querySelector('.active-question-message');
}

function finishActiveQuestionMessage() {
  const row = activeQuestionMessage();
  if (!row) return;
  row.classList.remove('active-question-message');
  row.querySelector('.inline-question-options')?.remove();
}

function appendQuestionMessage(question) {
  const text = String(question?.question || '').trim();
  if (!text) return false;
  const existing = activeQuestionMessage();
  if (existing?.dataset.questionId === question.question_id) return true;
  finishActiveQuestionMessage();

  const row = document.createElement('div');
  row.className = 'message assistant-message active-question-message';
  row.dataset.questionId = question.question_id;

  const avatar = document.createElement('div');
  avatar.className = 'avatar';
  avatar.textContent = 'AI';
  row.appendChild(avatar);

  const bubble = document.createElement('div');
  bubble.className = 'bubble question-bubble';
  const preSearch = question.question_stage === 'PRE_SEARCH';
  const context = String(question.product_context || '').trim();
  if (context && !preSearch) {
    const contextNode = document.createElement('div');
    contextNode.className = 'question-bubble-context';
    contextNode.textContent = context;
    bubble.appendChild(contextNode);
  }
  const body = document.createElement('div');
  body.className = 'question-bubble-text';
  body.textContent = text;
  bubble.appendChild(body);

  const progress = state.uiState?.search_progress;
  const completed = Number(progress?.completed_question_count || 0);
  const estimated = Number(progress?.estimated_total_question_count || 0);
  if (estimated > 0) {
    const progressNode = document.createElement('div');
    progressNode.className = 'question-bubble-progress';
    progressNode.textContent = `진행 ${completed} / 예상 ${estimated}`;
    bubble.appendChild(progressNode);
  }

  const options = document.createElement('div');
  options.className = 'inline-question-options';
  const addButton = (label, answer) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = label;
    button.addEventListener('click', () => answerQuestion(answer, label));
    options.appendChild(button);
  };
  if (question.question_kind === 'RANKING_INPUT') {
    for (const option of question.ranking_input?.allowed_options || []) {
      const field = question.ranking_input?.required_field || '';
      addButton(field.includes('amount') ? formatWon(option) : String(option), option);
    }
  } else if (question.question_kind === 'FINANCIAL_FACT' && !preSearch) {
    addButton('네', true);
    addButton('아니요', false);
    addButton('모르겠어요', 'UNKNOWN');
  } else if (question.question_kind === 'CONTRIBUTION_FEASIBILITY') {
    const clarification = question.feasibility_clarification || {};
    for (const option of clarification.feasible_options || []) {
      addButton(formatWon(option), option);
    }
    if ((clarification.allowed_resolutions || []).includes('EXCLUDE_PRODUCT')) {
      addButton('이 상품은 제외', 'EXCLUDE_PRODUCT');
    }
  }
  if (options.childElementCount) bubble.appendChild(options);

  row.appendChild(bubble);
  els.messages.appendChild(row);
  requestAnimationFrame(() => { els.chatScroll.scrollTop = els.chatScroll.scrollHeight; });
  return true;
}

function objectiveLabel(value) {
  return {
    MAX_ESTIMATED_AFTER_TAX_INTEREST: '세전 이자금순',
    MAX_ESTIMATED_PRE_TAX_INTEREST: '세전 이자금순',
    MAX_REALIZABLE_RATE: '금리순',
    MIN_ACTION_BURDEN: '관리 부담 낮은 순',
    BALANCED: '균형 기준',
  }[value] || '개인화 기준';
}

function verificationBadge(value) {
  return {
    FINANCIAL_DATA_VERIFIED: ['금융데이터 확인', 'verified'],
    USER_RESPONSE_INCLUDED: ['사용자 응답 포함', 'plan'],
    PLAN_BASED: ['계획 기준', 'plan'],
    ADDITIONAL_VERIFICATION_REQUIRED: ['기관 확인 필요', 'unknown'],
  }[value] || [String(value || '확인 필요'), 'unknown'];
}

function eligibilityBadge(value) {
  return {
    ELIGIBLE: ['가입 가능', 'verified'],
    PLAN_REQUIRED: ['계획 필요', 'plan'],
    VERIFICATION_REQUIRED: ['가입 확인 필요', 'unknown'],
    INELIGIBLE: ['가입 불가', 'danger'],
  }[value] || [String(value || ''), 'unknown'];
}

function evaluationStatusLabel(value) {
  return {
    SATISFIED: '충족',
    ACHIEVABLE: '달성 가능',
    UNSATISFIABLE: '받을 수 없음',
    UNKNOWN: '가입 시 확인',
  }[value] || String(value || '');
}

function productTypeLabel(value) {
  return {
    INSTALLMENT_SAVINGS: '적금',
    TIME_DEPOSIT: '예금',
  }[value] || value || '금융상품';
}

function humanFactLabel(factType, value) {
  const yes = value === true;
  const no = value === false;
  const mapping = {
    SALARY_ACCOUNT_CHANGE_POSSIBLE: ['급여계좌 변경', yes ? '가능' : no ? '불가' : '미정'],
    CARD_SETTLEMENT_ACCOUNT_CHANGE_POSSIBLE: ['카드 결제계좌 변경', yes ? '가능' : no ? '불가' : '미정'],
    SUPER_SOL_JOIN_LOGIN_MAINTAIN_WILLING: ['SuperSOL 가입·유지', yes ? '가능' : no ? '하지 않음' : '미정'],
    SPECIAL_RATE_COUPON_VALID: ['이벤트 우대', yes ? '해당' : no ? '해당 없음' : '미정'],
    HANA_HEALTH_SERVICE_WILLING: ['건강서비스 연동', yes ? '가능' : no ? '하지 않음' : '미정'],
  };
  return mapping[factType] || null;
}

function renderStateChips() {
  const root = els.stateChips;
  root.innerHTML = '';
  const snapshot = state.uiState;
  if (!snapshot?.intent) {
    root.innerHTML = '<span class="empty-chip">대화를 시작하면 여기에 검색 기준이 표시됩니다.</span>';
    return;
  }
  const intent = snapshot.intent;
  const chips = [];
  const add = (label, value, kind = 'intent') => chips.push({ label, value, kind });

  if (intent.contribution_plan?.desired_periodic_amount) {
    const f = intent.contribution_plan.frequency;
    const prefix = f === 'WEEKLY' ? '주 희망 납입' : f === 'DAILY' ? '일 희망 납입' : '월 희망 납입';
    add(prefix, formatWon(intent.contribution_plan.desired_periodic_amount));
  }
  if (intent.contribution_plan?.maximum_affordable_periodic_amount) {
    add('최대 납입 가능', formatWon(intent.contribution_plan.maximum_affordable_periodic_amount));
  }
  if (intent.contribution_plan?.selected_term_value && intent.contribution_plan?.selected_term_unit) {
    const unit = { YEAR: '년', MONTH: '개월', WEEK: '주', DAY: '일' }[intent.contribution_plan.selected_term_unit] || '';
    add('희망 기간', `${intent.contribution_plan.selected_term_value}${unit}`);
  }
  add('정렬', objectiveLabel(intent.ranking_objective));

  for (const capability of intent.capabilities || []) {
    const labelMap = {
      CHANGE_SALARY_ACCOUNT: '급여계좌 변경',
      NEW_CARD_ISSUANCE: '신규카드 발급',
      BRANCH_VISIT: '영업점 방문',
      CHANGE_CARD_SETTLEMENT_ACCOUNT: '카드 결제계좌 변경',
    };
    const valueMap = { CAN: '가능', CANNOT: '불가', UNKNOWN: '미정' };
    add(labelMap[capability.capability_id] || capability.capability_id, valueMap[capability.state] || capability.state);
  }

  const profileLabels = {
    SALARY_ACCOUNT_CHANGE_POSSIBLE: '급여 수령계좌',
    CARD_SETTLEMENT_ACCOUNT_CHANGE_POSSIBLE: '신한카드 결제계좌',
  };
  const profileFactTypes = new Set();
  for (const profile of snapshot.pre_search_profile_answers || []) {
    profileFactTypes.add(profile.fact_type);
    add(
      profileLabels[profile.fact_type] || '사전 확인',
      profile.summary,
      'fact',
    );
  }

  for (const declaration of snapshot.user_declarations || []) {
    if (profileFactTypes.has(declaration.fact_type)) continue;
    const human = humanFactLabel(declaration.fact_type, declaration.value);
    if (human) add(human[0], human[1], 'fact');
  }

  for (const acknowledged of snapshot.acknowledged_unknown_answers || []) {
    const human = humanFactLabel(acknowledged.fact_type, null);
    add(
      human ? human[0] : '확인 어려운 조건',
      human ? '사용자가 모름' : acknowledged.question,
      'fact',
    );
  }

  for (const choice of snapshot.product_contribution_choices || []) {
    const name = state.productNames.get(choice.product_id) || '상품별 납입';
    if (choice.field === 'preferred_start_amount') {
      add(`${name} 시작금액`, formatWon(choice.value), 'fact');
    }
  }

  if ((snapshot.excluded_product_ids || []).length) {
    add('제외 상품', `${snapshot.excluded_product_ids.length}개`, 'excluded');
  }

  const unique = [];
  const seen = new Set();
  for (const chip of chips) {
    const key = `${chip.label}:${chip.value}`;
    if (!seen.has(key)) { seen.add(key); unique.push(chip); }
  }
  for (const chip of unique) {
    const span = document.createElement('span');
    span.className = `state-chip ${chip.kind}`;
    const sourceTag = chip.kind === 'fact' ? '<em class="chip-source">사용자 응답</em>' : chip.kind === 'excluded' ? '<em class="chip-source">제외</em>' : '';
    span.innerHTML = `<strong>${escapeHtml(chip.label)}</strong> ${escapeHtml(chip.value)}${sourceTag}`;
    root.appendChild(span);
  }
}

function renderRecommendations() {
  const rec = state.recommendations;
  if (!rec) return;
  for (const button of els.rankingToggle.querySelectorAll('button')) {
    const representsLegacyInterest = (
      button.dataset.objective === 'MAX_ESTIMATED_PRE_TAX_INTEREST'
      && rec.ranking_objective === 'MAX_ESTIMATED_AFTER_TAX_INTEREST'
    );
    button.classList.toggle(
      'active',
      button.dataset.objective === rec.ranking_objective || representsLegacyInterest,
    );
  }
  const items = rec.top_products || [];
  const progress = state.uiState?.search_progress;
  const roundLabel = progress?.recalculation_round > 1
    ? `${progress.recalculation_round}차 재계산 · `
    : '';
  const poolLabel = progress ? `후보 ${progress.viable_product_count}개 중 ` : '';
  const reviewing = Boolean(state.activeQuestion);
  els.recommendationCount.textContent = items.length >= 5
    ? reviewing
      ? `${roundLabel}${poolLabel}후보 5개 · 조건 확인 중`
      : `${roundLabel}${poolLabel}Top 5`
    : `${roundLabel}${items.length}개 후보 추천`;
  els.recommendations.innerHTML = '';
  state.productNames.clear();

  if (!items.length) {
    els.recommendations.innerHTML = '<div class="empty-results"><div class="empty-illustration">0</div><h3>현재 조건에 맞는 후보가 없습니다</h3><p>검색 조건을 조금 완화하거나 제외 조건을 바꿔보세요.</p></div>';
    return;
  }

  for (const item of items) {
    state.productNames.set(item.product_id, item.product_name);
    const card = document.createElement('article');
    card.className = `recommendation-card ${item.rank === 1 ? 'top-card' : ''}`;
    card.tabIndex = 0;
    card.setAttribute('role', 'button');
    card.setAttribute('aria-label', `${item.rank}위 ${item.product_name} 상세 보기`);

    const [eligText, eligKind] = eligibilityBadge(item.eligibility_badge);
    const [verifyText, verifyKind] = verificationBadge(item.verification_badge);
    const comparison = item.ranking_comparability === 'COMPARABLE';
    const interestHtml = comparison && item.estimated_pre_tax_interest !== null
      ? `<div class="metric-label">내 예상 세전이자</div><div class="money-value">${formatWon(item.estimated_pre_tax_interest)}</div><div class="money-sub">총 납입 ${formatWon(item.estimated_total_principal)} 기준</div>`
      : `<div class="metric-label">내 예상 세전이자</div><div class="not-comparable">시작금액 선택 필요</div><div class="money-sub">선택하면 금액 기준으로 순위를 다시 계산해요.</div>`;

    card.innerHTML = `
      <div class="product-main">
        <div class="rank-badge">${item.rank}위</div>
        <div class="product-copy">
          <div class="institution">${escapeHtml(item.institution_name)}</div>
          <div class="product-name">${escapeHtml(item.product_name)}</div>
          <div class="product-meta">
            <span class="mini-badge ${eligKind}">${escapeHtml(eligText)}</span>
            <span class="mini-badge ${verifyKind}">${escapeHtml(verifyText)}</span>
          </div>
          <div class="meta-line">
            <span>${escapeHtml(item.term_summary)}</span>
            <span>${escapeHtml(item.planned_contribution_summary)}</span>
            <span>${escapeHtml(item.maximum_deposit_summary)}</span>
          </div>
          <div class="card-cta">상세 금리·근거 보기 <span aria-hidden="true">→</span></div>
        </div>
      </div>
      <div class="money-block">${interestHtml}</div>
      <div class="rate-block">
        <div class="metric-label">내 예상금리</div>
        <div class="rate-value">${formatRate(item.realizable_rate)}</div>
        <div class="ad-rate">계획대로 달성 시 · 광고 최고 ${formatRate(item.advertised_max_rate)}</div>
      </div>
    `;
    const open = () => openDetail(item.product_id);
    card.addEventListener('click', open);
    card.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); open(); }
    });
    els.recommendations.appendChild(card);
  }
  renderStateChips();
}

function renderQuestion(question) {
  state.activeQuestion = question || null;
  if (!question) {
    finishActiveQuestionMessage();
    return;
  }
  appendQuestionMessage(question);
}

async function preloadRecommendationDetails() {
  if (!state.session || !state.recommendations) return;
  const recommendationId = state.recommendations.recommendation_id || '';
  if (state.detailCacheRecommendationId !== recommendationId) {
    state.detailCache.clear();
    state.detailCacheRecommendationId = recommendationId;
  }
  const sessionId = state.session.search_session_id;
  const items = state.recommendations.top_products || [];
  await Promise.all(items.map(async (item) => {
    if (state.detailCache.has(item.product_id)) return;
    const detail = await api(
      `/search-sessions/${sessionId}/recommendations/${encodeURIComponent(item.product_id)}/preview`,
    );
    state.detailCache.set(item.product_id, detail);
  }));
}

async function syncAll({ session = null, question = undefined, recommendations = undefined } = {}) {
  if (session) state.session = session;
  if (!state.session) return;
  const id = state.session.search_session_id;

  const [uiState, rec] = await Promise.all([
    api(`/search-sessions/${id}/state`),
    recommendations !== undefined
      ? Promise.resolve(recommendations)
      : api(`/search-sessions/${id}/recommendations`).catch(() => null),
  ]);
  state.uiState = uiState;
  if (rec) state.recommendations = rec;

  let nextQuestion = question;
  if (nextQuestion === undefined) {
    nextQuestion = await api(`/search-sessions/${id}/questions/next`).catch(() => ({ question: null }));
    if (nextQuestion && nextQuestion.question === null && Object.keys(nextQuestion).length === 1) nextQuestion = null;
  }
  renderQuestion(nextQuestion);
  if (state.recommendations) {
    await preloadRecommendationDetails().catch(() => null);
  }
  if (state.recommendations) renderRecommendations();
  else renderStateChips();
  els.refreshButton.disabled = false;
  return nextQuestion;
}

async function startSearch(message) {
  setBusy(true);
  renderLoading();
  let processingMessage = null;
  try {
    const now = new Date();
    const today = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
    appendMessage('user', message);
    processingMessage = appendProcessingMessage();
    els.starterPrompts.classList.add('hidden');
    const session = await api('/search-sessions', {
      method: 'POST',
      body: {
        user_id: state.userId,
        natural_language_query: message,
        as_of: today,
        subscription_date: today,
      },
    });
    state.session = session;
    const nextQuestion = await syncAll({ session });
    removeProcessingMessage(processingMessage);
    processingMessage = null;
    if (!nextQuestion) {
      appendMessage('assistant', '말씀하신 조건으로 추천을 계산했어요. 왼쪽에서 조건을 더 말하면 결과를 계속 좁혀갈 수 있어요.');
    }
  } catch (error) {
    removeProcessingMessage(processingMessage);
    processingMessage = null;
    showStartError(error);
  } finally {
    removeProcessingMessage(processingMessage);
    setBusy(false);
  }
}

async function sendFollowup(message) {
  setBusy(true);
  let processingMessage = null;
  try {
    appendMessage('user', message);
    processingMessage = appendProcessingMessage();
    const result = await api(`/search-sessions/${state.session.search_session_id}/messages`, {
      method: 'POST',
      body: { message },
    });
    state.session = result.session;
    await syncAll({
      session: result.session,
      question: result.next_question,
      recommendations: result.recommendations === null ? undefined : result.recommendations,
    });
    removeProcessingMessage(processingMessage);
    processingMessage = null;
    if (result.assistant_message) {
      appendMessage('assistant', result.assistant_message);
    } else if (result.current_results_requested) {
      appendMessage('assistant', '현재까지 답한 조건으로 후보를 보여드릴게요. 남은 질문을 마치면 Top 5가 확정됩니다.');
    } else if (result.recommendations) {
      appendMessage('assistant', '말씀하신 조건을 반영해 추천 결과를 다시 계산했어요.');
    }
  } catch (error) {
    removeProcessingMessage(processingMessage);
    processingMessage = null;
    if (error.status === 503 && error.payload?.llm_enabled === false) {
      appendMessage('assistant', '현재 Web 런타임에 대화용 AI가 설정되어 있지 않아 자유로운 후속 문장 해석은 사용할 수 없어요. 위에 표시된 선택형 질문과 현재 결과 보기는 계속 사용할 수 있습니다.');
      toast('대화형 AI 설정을 확인해 주세요.', 'error');
    } else if (error.status === 503) {
      appendMessage('assistant', '대화형 AI 호출에 실패했어요. 금융 계산 결과는 변경하지 않았습니다. 잠시 후 다시 시도하거나 연결 상태를 확인해 주세요.');
      toast('AI 연결 상태를 확인해 주세요.', 'error');
    } else {
      appendMessage('assistant', `요청을 반영하지 못했어요: ${error.message}`);
      toast(error.message, 'error');
    }
  } finally {
    removeProcessingMessage(processingMessage);
    setBusy(false);
  }
}

async function answerQuestion(answer, label) {
  if (!state.session || !state.activeQuestion || state.busy) return;
  const answeredQuestion = state.activeQuestion;
  renderQuestion(null);
  setBusy(true);
  let processingMessage = null;
  try {
    appendMessage('user', label);
    processingMessage = appendProcessingMessage();
    const session = await api(`/search-sessions/${state.session.search_session_id}/answers`, {
      method: 'POST',
      body: { question_id: answeredQuestion.question_id, answer },
    });
    state.session = session;
    const next = await api(`/search-sessions/${state.session.search_session_id}/questions/next`).catch(() => null);
    await syncAll({ session, question: next && next.question ? next : null });
    removeProcessingMessage(processingMessage);
    processingMessage = null;
    if (!next?.question) appendMessage('assistant', '모든 질문을 반영해 Top 5와 예상 금리, 세전이자를 계산했어요.');
  } catch (error) {
    removeProcessingMessage(processingMessage);
    processingMessage = null;
    appendMessage('assistant', `답변을 반영하지 못했어요: ${error.message}`);
    renderQuestion(answeredQuestion);
    toast(error.message, 'error');
  } finally {
    removeProcessingMessage(processingMessage);
    setBusy(false);
  }
}

async function changeRankingObjective(objective) {
  if (!state.session || state.busy) return;
  if (state.uiState?.intent?.ranking_objective === objective) return;
  setBusy(true);
  try {
    const session = await api(`/search-sessions/${state.session.search_session_id}/intent`, {
      method: 'PATCH',
      body: { patch: { ranking_objective_patch: objective } },
    });
    state.session = session;
    await syncAll({ session });
    toast(`${objectiveLabel(objective)}으로 정렬했습니다.`);
  } catch (error) {
    toast(`정렬 기준을 바꾸지 못했어요: ${error.message}`, 'error');
  } finally {
    setBusy(false);
  }
}

async function resetConversation() {
  if (state.busy) return;
  const previousSessionId = state.session?.search_session_id;
  setBusy(true);
  if (previousSessionId) {
    try {
      await api(`/search-sessions/${previousSessionId}`, { method: 'DELETE' });
    } catch (error) {
      toast(`이전 대화 정리 중 문제가 있었어요: ${error.message}`, 'error');
    }
  }

  state.session = null;
  state.uiState = null;
  state.recommendations = null;
  state.activeQuestion = null;
  state.currentDetail = null;
  state.detailCache.clear();
  state.detailCacheRecommendationId = null;
  state.productNames.clear();
  state.userId = createConversationUserId();

  els.messages.innerHTML = '';
  els.starterPrompts.classList.remove('hidden');
  els.input.value = '';
  els.input.style.height = '';
  renderQuestion(null);
  renderStateChips();
  closeDetail();
  els.recommendationCount.textContent = '추천 결과 대기 중';
  els.recommendations.innerHTML = `
    <div class="empty-results">
      <div class="empty-illustration">₩</div>
      <h3>검색 조건을 알려주세요</h3>
      <p>광고 최고금리가 아니라, 실제 납입계획과 받을 수 있는 우대조건을 기준으로 비교합니다.</p>
    </div>`;
  for (const button of els.rankingToggle.querySelectorAll('button')) {
    button.classList.remove('active');
  }
  els.chatScroll.scrollTop = 0;
  setBusy(false);
  els.input.focus();
}

function renderLoading() {
  els.recommendations.innerHTML = '<div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div>';
  els.recommendationCount.textContent = '계산 중';
}

function showStartError(error) {
  els.recommendations.innerHTML = '<div class="empty-results"><div class="empty-illustration">!</div><h3>검색을 시작하지 못했습니다</h3><p>서버 설정을 확인한 뒤 다시 시도해 주세요.</p></div>';
  appendMessage('assistant', `검색을 시작하지 못했어요: ${error.message}`);
  toast(error.message, 'error');
}

function renderReasonBox(title, items, kind) {
  if (!items?.length) return '';
  return `<div class="reason-box ${kind}"><strong>${escapeHtml(title)}</strong><ul>${items.slice(0, 5).map((x) => `<li>${escapeHtml(x)}</li>`).join('')}</ul></div>`;
}

function userRuleLabel(value) {
  return String(value || '').replace(/\bbranch\b/gi, '조건');
}

function statusIcon(value) {
  return { SATISFIED: '✓', ACHIEVABLE: '→', UNSATISFIABLE: '×', UNKNOWN: '?' }[value] || '·';
}

function renderRateNode(item, child = false) {
  const source = item.source_reference || {};
  const sourceUrl = safeUrl(source.source_url);
  const sourceLabel = [source.document, source.page ? `p.${source.page}` : null, source.section].filter(Boolean).join(' · ');
  const sourceHtml = sourceUrl
    ? `<a class="source-link" href="${escapeHtml(sourceUrl)}" target="_blank" rel="noopener">공식 설명서${source.page ? ` p.${escapeHtml(source.page)}` : ''}</a>`
    : sourceLabel ? `<span>${escapeHtml(sourceLabel)}</span>` : '';
  const children = (item.children || []).map((x) => renderRateNode(x, true)).join('');
  return `
    <div class="rate-node ${child ? 'child-node' : ''}">
      <div class="rate-node-top">
        <div>
          <div class="rate-title"><span class="rate-status-icon status-${escapeHtml(item.status)}">${escapeHtml(statusIcon(item.status))}</span>${escapeHtml(userRuleLabel(item.rule_label))}</div>
          <div class="rate-evidence">
            <span class="status-chip status-${escapeHtml(item.status)}">${escapeHtml(evaluationStatusLabel(item.status))}</span>
            <span>${escapeHtml(item.evidence_basis)}</span>
            ${sourceHtml}
          </div>
        </div>
        ${item.nominal_reward_pp !== null && item.nominal_reward_pp !== undefined ? `<div class="reward">+${escapeHtml(item.nominal_reward_pp)}%p</div>` : ''}
      </div>
      ${item.action_summary ? `<div class="action-summary">필요 행동 · ${escapeHtml(item.action_summary)}</div>` : ''}
    </div>${children}`;
}

async function openDetail(productId) {
  if (!state.session) return;
  els.detailBackdrop.classList.remove('hidden');
  els.detailDrawer.classList.remove('hidden');
  const cached = state.detailCache.get(productId);
  if (cached) {
    state.currentDetail = cached;
    renderDetail(cached);
    if (!cached.explanation) hydrateDetailExplanation(productId);
    return;
  }
  els.detailInstitution.textContent = '불러오는 중';
  els.detailProductName.textContent = '상품 상세';
  els.detailContent.innerHTML = '<div class="skeleton"></div><div class="skeleton" style="margin-top:10px"></div>';
  try {
    const detail = await api(`/search-sessions/${state.session.search_session_id}/recommendations/${encodeURIComponent(productId)}`);
    state.detailCache.set(productId, detail);
    state.currentDetail = detail;
    renderDetail(detail);
  } catch (error) {
    els.detailContent.innerHTML = `<div class="empty-results"><h3>상세정보를 불러오지 못했습니다</h3><p>${escapeHtml(error.message)}</p></div>`;
  }
}

async function hydrateDetailExplanation(productId) {
  try {
    const detail = await api(
      `/search-sessions/${state.session.search_session_id}/recommendations/${encodeURIComponent(productId)}`,
    );
    state.detailCache.set(productId, detail);
    if (state.currentDetail?.product_id === productId) {
      state.currentDetail = detail;
      renderDetail(detail);
    }
  } catch {
    // The complete structured detail is already visible from the preview.
  }
}

function renderDetail(detail) {
  els.detailInstitution.textContent = detail.institution_name;
  els.detailProductName.textContent = detail.product_name;
  const reason = detail.recommendation_reason || {};
  const cap = detail.rate_cap_adjustment || {};
  const capNote = Number(cap.cap_reduction_pp || 0) > 0
    ? `<div class="cap-note">조건별 적용 가능 우대금리 합계는 +${escapeHtml(cap.pre_cap_total_pp)}%p이지만 상품 우대 한도 ${escapeHtml(cap.cap_pp)}%p가 적용되어 최종 우대는 +${escapeHtml(cap.post_cap_total_pp)}%p입니다.</div>`
    : '';

  els.detailContent.innerHTML = `
    <section class="detail-hero">
      <div class="detail-rank">현재 개인화 추천 ${detail.rank}위</div>
      <div class="detail-rate-row">
        <div class="detail-rate-card primary"><div class="label">내 예상금리</div><div class="value">${formatRate(detail.realizable_rate)}</div><div class="sub">계획대로 조건을 달성할 때</div></div>
        <div class="detail-rate-card"><div class="label">현재 확인된 금리</div><div class="value">${formatRate(detail.confirmed_rate)}</div><div class="sub">금융데이터로 이미 확인</div></div>
        <div class="detail-rate-card"><div class="label">광고 최고금리</div><div class="value">${formatRate(detail.advertised_max_rate)}</div><div class="sub">모든 우대를 충족한다고 가정</div></div>
      </div>
    </section>

    <div class="detail-grid">
      <div class="detail-info"><div class="label">기간</div><div class="value">${escapeHtml(detail.term_summary)}</div></div>
      <div class="detail-info"><div class="label">내 납입계획</div><div class="value">${escapeHtml(detail.planned_contribution_summary)}</div></div>
      <div class="detail-info"><div class="label">상품 납입한도</div><div class="value">${escapeHtml(detail.maximum_deposit_summary)}</div></div>
      <div class="detail-info"><div class="label">예상 총 납입액</div><div class="value">${formatWon(detail.estimated_total_principal)}</div></div>
      <div class="detail-info emphasis"><div class="label">예상 세전이자</div><div class="value">${formatWon(detail.estimated_pre_tax_interest)}</div></div>
    </div>

    <section class="detail-section">
      <h3>왜 현재 ${detail.rank}위인가요?</h3>
      <div class="reason-grid">
        ${renderReasonBox('좋은 점', reason.positives, 'good')}
        ${renderReasonBox('아쉬운 점', reason.limitations, 'limit')}
        ${renderReasonBox('하면 좋은 행동', reason.actions, 'action')}
        ${renderReasonBox('가입 시 확인할 사항', reason.unknowns, 'unknown')}
      </div>
    </section>

    <section class="detail-section">
      <h3>금리 구성 · 판정 근거</h3>
      <div class="rate-legend"><span><i class="status-SATISFIED">✓</i> 이미 충족</span><span><i class="status-ACHIEVABLE">→</i> 계획대로 달성 가능</span><span><i class="status-UNSATISFIABLE">×</i> 받을 수 없음</span><span><i class="status-UNKNOWN">?</i> 가입 시 기관 확인</span></div>
      <div class="rate-tree">${(detail.rate_breakdown || []).map((x) => renderRateNode(x)).join('')}</div>
      ${capNote}
    </section>

    <section class="detail-section">
      <h3>AI 요약</h3>
      <div class="ai-explanation"><span class="ai-label">구조화된 판정 결과 기준</span><br>${escapeHtml(detail.explanation || '구조화된 결과를 기준으로 설명을 생성합니다.')}</div>
    </section>
  `;
}

function closeDetail() {
  els.detailBackdrop.classList.add('hidden');
  els.detailDrawer.classList.add('hidden');
  state.currentDetail = null;
}

async function bootstrap() {
  try {
    state.runtime = await api('/runtime');
    els.productCountPill.textContent = `검증 상품 ${state.runtime.product_count}개`;
    const health = await api('/llm/health');
    state.runtime.llm_health = health;
    if (health.healthy) {
      els.llmPill.textContent = `AI 연결 · ${health.model}`;
      els.llmPill.className = 'pill pill-data';
      els.llmPill.title = `${health.provider} · ${health.api_family} · ${health.latency_ms ?? '-'}ms`;
    } else if (state.runtime.llm_provider === 'MOCK') {
      els.llmPill.textContent = '데모 모드 · 선택형 입력';
      els.llmPill.className = 'pill pill-warning';
      els.llmPill.title = '자유로운 후속 자연어 수정은 대화형 모델 연결 시 활성화됩니다.';
      const caption = document.querySelector('#state-caption');
      if (caption) caption.textContent = '선택형 질문으로 조건을 보완할 수 있습니다.';
    } else if (!health.configured) {
      els.llmPill.textContent = 'AI 설정 필요';
      els.llmPill.className = 'pill pill-warning';
      els.llmPill.title = health.detail || 'API credential 설정이 필요합니다.';
    } else {
      els.llmPill.textContent = 'AI 연결 확인 필요';
      els.llmPill.className = 'pill pill-warning';
      els.llmPill.title = health.detail || 'AI provider health check failed';
    }
  } catch (error) {
    els.llmPill.textContent = '서버 연결 확인 필요';
    els.llmPill.className = 'pill pill-warning';
    toast(error.message, 'error');
  }
}

els.composer.addEventListener('submit', async (event) => {
  event.preventDefault();
  const message = els.input.value.trim();
  if (!message || state.busy) return;
  els.input.value = '';
  els.input.style.height = '';
  if (!state.session) await startSearch(message);
  else await sendFollowup(message);
});

els.input.addEventListener('input', () => {
  els.input.style.height = 'auto';
  els.input.style.height = `${Math.min(120, els.input.scrollHeight)}px`;
});

els.input.addEventListener('keydown', (event) => {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault();
    els.composer.requestSubmit();
  }
});

els.starterPrompts.addEventListener('click', (event) => {
  const button = event.target.closest('button[data-prompt]');
  if (!button) return;
  els.input.value = button.dataset.prompt;
  els.composer.requestSubmit();
});

els.newChatButton.addEventListener('click', resetConversation);
els.rankingToggle.addEventListener('click', async (event) => {
  const button = event.target.closest('button[data-objective]');
  if (!button || !state.session || state.busy) return;
  await changeRankingObjective(button.dataset.objective);
});
els.refreshButton.addEventListener('click', async () => {
  if (!state.session || state.busy) return;
  setBusy(true);
  renderLoading();
  try {
    await syncAll();
    toast('현재 상태로 추천을 다시 불러왔습니다.');
  } catch (error) {
    toast(error.message, 'error');
  } finally {
    setBusy(false);
  }
});
els.detailClose.addEventListener('click', closeDetail);
els.detailBackdrop.addEventListener('click', closeDetail);
window.addEventListener('keydown', (event) => { if (event.key === 'Escape') closeDetail(); });

bootstrap();
