const $ = (selector) => document.querySelector(selector);
const RECOMMENDATION_PAGE_SIZE = 10;
const PANE_RATIO_STORAGE_KEY = 'ddakrate.chatPaneRatio';
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
  defaultRecommendations: null,
  activeQuestion: null,
  currentDetail: null,
  detailCache: new Map(),
  detailCacheRecommendationId: null,
  busy: false,
  visibleRecommendationCount: RECOMMENDATION_PAGE_SIZE,
  renderedRecommendationsRef: null,
  expandedCardHeights: new Map(),
  expandedRegularCardHeight: null,
  expandedPodiumHeights: new Map(),
  productNames: new Map(),
  initialQuestion: null,
  pendingAnswerExample: null,
  productSearchQuery: '',
  selectedProductTypes: new Set(),
  selectedInstitutionSectors: new Set(),
  selectedInstitutionNames: new Set(),
  institutionSelectionDraft: new Set(),
  availableInstitutions: [],
  userId: createConversationUserId(),
};

const els = {
  workspace: $('.workspace'),
  paneResizer: $('#pane-resizer'),
  llmPill: $('#llm-pill'),
  productCountPill: $('#product-count-pill'),
  chatScroll: $('#chat-scroll'),
  messages: $('#messages'),
  answerExamplesDock: $('#answer-examples-dock'),
  composer: $('#composer'),
  input: $('#message-input'),
  sendButton: $('#send-button'),
  newChatButton: $('#new-chat-button'),
  searchProgress: $('#search-progress'),
  searchProgressFill: $('#search-progress-fill'),
  stateChips: $('#state-chips'),
  understoodToggle: $('#understood-toggle'),
  understoodToggleLabel: $('#understood-toggle-label'),
  understoodContent: $('#understood-content'),
  recommendations: $('#recommendations'),
  productSearch: $('#product-search'),
  productSearchToggle: $('#product-search-toggle'),
  productSearchInput: $('#product-search-input'),
  productFamilyFilter: $('#product-family-filter'),
  firstSectorFilter: $('#first-sector-filter'),
  savingsBankFilter: $('#savings-bank-filter'),
  institutionPickerButton: $('#institution-picker-button'),
  institutionSelectionCount: $('#institution-selection-count'),
  selectedInstitutionStrip: $('#selected-institution-strip'),
  institutionPickerBackdrop: $('#institution-picker-backdrop'),
  institutionPickerModal: $('#institution-picker-modal'),
  institutionPickerBody: $('#institution-picker-body'),
  institutionPickerClose: $('#institution-picker-close'),
  institutionPickerClear: $('#institution-picker-clear'),
  institutionPickerApply: $('#institution-picker-apply'),
  recommendationSection: $('.recommendation-section'),
  detailBackdrop: $('#detail-backdrop'),
  detailDrawer: $('#detail-drawer'),
  detailProductLink: $('#detail-product-link'),
  detailLinkCaption: $('#detail-link-caption'),
  detailInstitutionLogo: $('#detail-institution-logo'),
  detailLogoPlaceholder: $('#detail-logo-placeholder'),
  detailInstitution: $('#detail-institution'),
  detailProductName: $('#detail-product-name'),
  detailContent: $('#detail-content'),
  detailClose: $('#detail-close'),
  toastStack: $('#toast-stack'),
};

function paneWidthBounds() {
  const totalWidth = els.workspace?.clientWidth || window.innerWidth;
  const dividerWidth = els.paneResizer?.offsetWidth || 8;
  const minimumChatWidth = Math.min(310, Math.max(260, totalWidth * 0.3));
  const minimumResultsWidth = Math.min(560, Math.max(440, totalWidth * 0.48));
  return {
    totalWidth,
    minimum: minimumChatWidth,
    maximum: Math.max(minimumChatWidth, totalWidth - minimumResultsWidth - dividerWidth),
  };
}

function setChatPaneWidth(width, { persist = false } = {}) {
  if (!els.workspace || !els.paneResizer || window.matchMedia('(max-width: 900px)').matches) return;
  const bounds = paneWidthBounds();
  const nextWidth = Math.min(bounds.maximum, Math.max(bounds.minimum, width));
  const ratio = nextWidth / bounds.totalWidth;
  els.workspace.style.setProperty('--chat-pane-width', `${nextWidth}px`);
  els.paneResizer.setAttribute('aria-valuemin', String(Math.round(bounds.minimum / bounds.totalWidth * 100)));
  els.paneResizer.setAttribute('aria-valuemax', String(Math.round(bounds.maximum / bounds.totalWidth * 100)));
  els.paneResizer.setAttribute('aria-valuenow', String(Math.round(ratio * 100)));
  els.paneResizer.setAttribute('aria-valuetext', `채팅창 ${Math.round(ratio * 100)}%, 상품 목록 ${Math.round((1 - ratio) * 100)}%`);
  if (persist) {
    try { localStorage.setItem(PANE_RATIO_STORAGE_KEY, String(ratio)); } catch {}
  }
}

function resetChatPaneWidth() {
  try { localStorage.removeItem(PANE_RATIO_STORAGE_KEY); } catch {}
  const defaultRatio = window.innerWidth <= 1200 ? 0.32 : 0.24;
  setChatPaneWidth((els.workspace?.clientWidth || window.innerWidth) * defaultRatio, { persist: false });
}

function initializePaneResizer() {
  if (!els.workspace || !els.paneResizer) return;
  let savedRatio = null;
  try {
    const value = Number(localStorage.getItem(PANE_RATIO_STORAGE_KEY));
    if (Number.isFinite(value) && value > 0 && value < 1) savedRatio = value;
  } catch {}
  if (savedRatio !== null) setChatPaneWidth(els.workspace.clientWidth * savedRatio);
  else resetChatPaneWidth();

  let dragging = false;
  els.paneResizer.addEventListener('pointerdown', (event) => {
    if (event.button !== 0 || window.matchMedia('(max-width: 900px)').matches) return;
    dragging = true;
    els.paneResizer.setPointerCapture(event.pointerId);
    els.paneResizer.classList.add('dragging');
    document.body.classList.add('resizing-panes');
    event.preventDefault();
  });
  els.paneResizer.addEventListener('pointermove', (event) => {
    if (!dragging) return;
    const workspaceLeft = els.workspace.getBoundingClientRect().left;
    setChatPaneWidth(event.clientX - workspaceLeft);
  });
  const finishResize = (event) => {
    if (!dragging) return;
    dragging = false;
    if (els.paneResizer.hasPointerCapture(event.pointerId)) {
      els.paneResizer.releasePointerCapture(event.pointerId);
    }
    els.paneResizer.classList.remove('dragging');
    document.body.classList.remove('resizing-panes');
    const width = document.querySelector('.chat-pane')?.getBoundingClientRect().width;
    if (width) setChatPaneWidth(width, { persist: true });
  };
  els.paneResizer.addEventListener('pointerup', finishResize);
  els.paneResizer.addEventListener('pointercancel', finishResize);
  els.paneResizer.addEventListener('dblclick', resetChatPaneWidth);
  els.paneResizer.addEventListener('keydown', (event) => {
    const bounds = paneWidthBounds();
    const currentWidth = document.querySelector('.chat-pane')?.getBoundingClientRect().width || bounds.minimum;
    let nextWidth = null;
    if (event.key === 'ArrowLeft') nextWidth = currentWidth - (event.shiftKey ? 48 : 16);
    if (event.key === 'ArrowRight') nextWidth = currentWidth + (event.shiftKey ? 48 : 16);
    if (event.key === 'Home') nextWidth = bounds.minimum;
    if (event.key === 'End') nextWidth = bounds.maximum;
    if (event.key === 'Enter') {
      resetChatPaneWidth();
      event.preventDefault();
      return;
    }
    if (nextWidth === null) return;
    setChatPaneWidth(nextWidth, { persist: true });
    event.preventDefault();
  });

  let resizeFrame = 0;
  window.addEventListener('resize', () => {
    cancelAnimationFrame(resizeFrame);
    resizeFrame = requestAnimationFrame(() => {
      if (window.matchMedia('(max-width: 900px)').matches) {
        els.workspace.style.removeProperty('--chat-pane-width');
        return;
      }
      let ratio = null;
      try { ratio = Number(localStorage.getItem(PANE_RATIO_STORAGE_KEY)); } catch {}
      if (!Number.isFinite(ratio) || ratio <= 0 || ratio >= 1) {
        ratio = window.innerWidth <= 1200 ? 0.32 : 0.24;
      }
      setChatPaneWidth(els.workspace.clientWidth * ratio);
    });
  });
}

const krw = new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 0 });
const rateFmt = new Intl.NumberFormat('ko-KR', { minimumFractionDigits: 0, maximumFractionDigits: 2 });

const INSTITUTION_LOGOS = new Map([
  ['경남은행', 'BK_KYOUNGNAM'],
  ['광주은행', 'BK_KWANGJU'],
  ['국민은행', 'BK_KB'],
  ['KB국민은행', 'BK_KB'],
  ['부산은행', 'BK_BUSAN'],
  ['신한은행', 'BK_SHINHAN'],
  ['우리은행', 'BK_WOORI'],
  ['전북은행', 'BK_JEONBUK'],
  ['제주은행', 'BK_JEJU'],
  ['한국스탠다드차타드은행', 'BK_SC'],
  ['SC제일은행', 'BK_SC'],
  ['아이엠뱅크', 'BK_DAEGU'],
  ['iM뱅크', 'BK_DAEGU'],
  ['카카오뱅크', 'BK_KAKAO'],
  ['케이뱅크', 'BK_K'],
  ['하나은행', 'BK_HANA'],
  ['중소기업은행', 'BK_IBK'],
  ['IBK기업은행', 'BK_IBK'],
  ['토스뱅크', 'BK_TOSS'],
  ['한국산업은행', 'BK_KDB'],
  ['KDB산업은행', 'BK_KDB'],
  ['농협은행', 'BK_NH'],
  ['NH농협은행', 'BK_NH'],
  ['수협은행', 'BK_SH'],
  ['신협', 'BK_CU'],
  ['우정사업본부', 'BK_EPOST'],
  ['BNK저축은행', 'SB_BNK'],
  ['DB저축은행', 'SB_DB'],
  ['디비저축은행', 'SB_DB'],
  ['한국투자저축은행', 'SB_KOREAINVEST'],
  ['드림저축은행', 'SB_DREAM'],
  ['대백저축은행', 'SB_DAEBAEK'],
  ['애큐온저축은행', 'SB_ACUON'],
  ['키움예스저축은행', 'SB_KIWOOMYES'],
  ['키움저축은행', 'SB_KIWOOM'],
  ['안양저축은행', 'SB_ANYANG'],
  ['진주저축은행', 'SB_JINJU'],
  ['페퍼저축은행', 'SB_PEPPER'],
  ['다올저축은행', 'SB_DAOL'],
  ['DH저축은행', 'SB_DH'],
  ['고려저축은행', 'SB_COREA'],
  ['에스비아이저축은행', 'SB_SBI'],
  ['SBI저축은행', 'SB_SBI'],
  ['한화저축은행', 'SB_HANWHA'],
  ['예가람저축은행', 'SB_YEKARAM'],
  ['대신저축은행', 'SB_DAISHIN'],
  ['케이비저축은행', 'SB_KB'],
  ['KB저축은행', 'SB_KB'],
  ['오케이저축은행', 'SB_OK'],
  ['OK저축은행', 'SB_OK'],
  ['삼성증권', 'IV_SAMSUNG'],
  ['대신증권', 'IV_DAISHIN'],
  ['하나증권', 'IV_HANA'],
  ['DB증권', 'IV_DBFI'],
  ['유안타증권', 'IV_YUANTA'],
  ['엔에이치투자증권', 'IV_NH'],
  ['NH투자증권', 'IV_NH'],
  ['유진투자증권', 'IV_EUGENE'],
  ['SK증권', 'IV_SK'],
  ['신영증권', 'IV_SHINYOUNG'],
  ['현대차증권', 'IV_HM'],
  ['신한투자증권', 'IV_SHINHAN'],
  ['다올투자증권', 'IV_KTB'],
  ['한국투자증권', 'IV_KOREA'],
  ['메리츠증권', 'IV_MERITZ'],
  ['케이비증권', 'IV_KB'],
  ['KB증권', 'IV_KB'],
  ['한화투자증권', 'IV_HANWHA'],
  ['미래에셋증권', 'IV_MIRAE'],
  ['아이비케이투자증권', 'IV_IBK'],
  ['IBK투자증권', 'IV_IBK'],
  ['케이프투자증권', 'IV_CAPE'],
  ['우리투자증권', 'SB_WOORIFIN'],
]);

// Naver Pay exposes the same profile images for the long tail of savings banks.
// Keep the aliases separate from the short-name map so legal-name variants can
// be matched after displayInstitutionName() normalization.
const INSTITUTION_LOGO_FRAGMENTS = [
  ['상상인플러스저축은행', 'SB_SSINPLUS'],
  ['웰컴저축은행', 'SB_WELCOME'],
  ['상상인저축은행', 'SB_SSIN'],
  ['스카이저축은행', 'SB_SKY'],
  ['더케이저축은행', 'SB_THEK'],
  ['오성저축은행', 'SB_OHSUNG'],
  ['유안타저축은행', 'SB_YUANTA'],
  ['세람저축은행', 'SB_SERAM'],
  ['금화저축은행', 'SB_KUMHWA'],
  ['민국상호저축은행', 'SB_MK'],
  ['대명상호저축은행', 'SB_DAEMYUNG'],
  ['제이티친애저축은행', 'SB_JTCHINE'],
  ['청주저축은행', 'SB_CHUNGJU'],
  ['센트럴저축은행', 'SB_CENTRAL'],
  ['안국저축은행', 'SB_ANGUK'],
  ['모아저축은행', 'SB_MOA'],
  ['하나저축은행', 'SB_HANA'],
  ['머스트삼일저축은행', 'SB_MUSTSAMIL'],
  ['부림저축은행', 'SB_BOORIM'],
  ['삼정저축은행', 'SB_SAMJUNG'],
  ['평택저축은행', 'SB_PYUNGTAEK'],
  ['인천저축은행', 'SB_INCHEON'],
  ['오투저축은행', 'SB_OHTOO'],
  ['남양저축은행', 'SB_NAMYANG'],
  ['엠에스상호저축은행', 'SB_MS'],
  ['융창저축은행', 'SB_YOONGCHANG'],
  ['우리금융저축은행', 'SB_WOORI'],
  ['삼호저축은행', 'SB_SAMHO'],
  ['오에스비저축은행', 'SB_OSB'],
  ['인성저축은행', 'SB_INSUNG'],
  ['대한저축은행', 'SB_DAEHAN'],
  ['영진저축은행', 'SB_YOUNGJIN'],
  ['더블저축은행', 'SB_DOUBLE'],
  ['HB저축은행', 'SB_HB'],
  ['스타저축은행', 'SB_STAR'],
  ['바로저축은행', 'SB_BARO'],
  ['라온저축은행', 'SB_RAON'],
  ['엔에이치저축은행', 'SB_NH'],
  ['씨케이저축은행', 'SB_CK'],
  ['조은저축은행', 'SB_CHOEUN'],
  ['아산저축은행', 'SB_ASAN'],
  ['동원제일저축은행', 'SB_DONGWONJEIL'],
  ['아이비케이저축은행', 'SB_IBK'],
  ['신한저축은행', 'SB_SHINHAN'],
  ['에스앤티저축은행', 'SB_SNT'],
  ['푸른저축은행', 'SB_PURUN'],
  ['우리저축은행', 'SB_WOORI'],
  ['제이티저축은행', 'SB_JT'],
  ['참저축은행', 'SB_TRUE'],
  ['동양저축은행', 'SB_DONGYANG'],
  ['스마트저축은행', 'SB_SMART'],
  ['국제저축은행', 'SB_KOOKJE'],
  ['유니온저축은행', 'SB_UNION'],
  ['조흥저축은행', 'SB_CHOHEUNG'],
  ['대아상호저축은행', 'SB_DAEAH'],
  ['솔브레인저축은행', 'SB_SOLBRAIN'],
  ['한성저축은행', 'SB_HANSUNG'],
  ['대원상호저축은행', 'SB_DAEWON'],
  ['흥국저축은행', 'SB_HEUNGKUK'],
];

function displayInstitutionName(value) {
  return String(value || '')
    .replaceAll('(주)', '')
    .replaceAll('주식회사', '')
    .replace(/\s+/g, ' ')
    .trim();
}

function institutionLogoUrl(value) {
  const displayName = displayInstitutionName(value);
  const key = INSTITUTION_LOGOS.get(displayName)
    || INSTITUTION_LOGO_FRAGMENTS.find(([name]) => displayName.includes(name))?.[1]
    || (displayName.includes('신용협동조합') ? 'BK_CU' : null);
  return key ? `/static/assets/institution-logos/${key}.png` : null;
}

function formatWon(value) {
  if (value === null || value === undefined || value === '') return '-';
  const n = Number(value);
  return Number.isFinite(n) ? `${krw.format(n)}원` : '-';
}

function formatRate(value) {
  if (value === null || value === undefined || value === '') return '확인 전';
  const n = Number(value);
  return Number.isFinite(n) ? `${rateFmt.format(n)}%` : '확인 전';
}

function productFactValue(value) {
  const text = String(value ?? '').trim();
  if (
    !text
    || /(확인 필요|미입력|입력 필요|선택 필요|미계산|대화 후)/.test(text)
  ) return '?';
  return text;
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
  els.newChatButton.disabled = busy;
  els.institutionPickerButton.disabled = busy;
  els.firstSectorFilter.disabled = busy;
  els.savingsBankFilter.disabled = busy;
  for (const button of els.productFamilyFilter.querySelectorAll('button')) {
    button.disabled = busy;
  }
  for (const button of els.selectedInstitutionStrip.querySelectorAll('button')) {
    button.disabled = busy;
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

const processingMessageTimers = new WeakMap();

function updateProcessingMessage(row, text, stage) {
  if (!row?.isConnected) return;
  const label = row.querySelector('.processing-label');
  if (label) label.textContent = text;
  row.dataset.processingStage = stage;
  row.setAttribute('aria-label', text);
}

function appendProcessingMessage({ startsWithInterpretation = true } = {}) {
  const row = document.createElement('div');
  row.className = 'message assistant-message processing-message';
  row.setAttribute('role', 'status');
  row.setAttribute('aria-live', 'polite');

  const avatar = document.createElement('div');
  avatar.className = 'avatar';
  avatar.textContent = 'AI';
  row.appendChild(avatar);

  const bubble = document.createElement('div');
  bubble.className = 'bubble processing-bubble';
  bubble.innerHTML = '<span class="processing-pulse" aria-hidden="true"></span><span class="processing-label"></span>';
  row.appendChild(bubble);

  els.messages.appendChild(row);
  const firstStage = startsWithInterpretation
    ? ['답변을 이해하고 있어요', 'INTERPRETING']
    : ['조건을 반영하고 있어요', 'APPLYING'];
  updateProcessingMessage(row, firstStage[0], firstStage[1]);
  const timers = [];
  if (startsWithInterpretation) {
    timers.push(setTimeout(() => {
      updateProcessingMessage(row, '조건을 반영하고 있어요', 'APPLYING');
    }, 4500));
  }
  timers.push(setTimeout(() => {
    updateProcessingMessage(row, '순위를 다시 계산하고 있어요', 'RANKING');
  }, startsWithInterpretation ? 8500 : 4000));
  processingMessageTimers.set(row, timers);
  requestAnimationFrame(() => { els.chatScroll.scrollTop = els.chatScroll.scrollHeight; });
  return row;
}

function removeProcessingMessage(row) {
  for (const timer of processingMessageTimers.get(row) || []) clearTimeout(timer);
  processingMessageTimers.delete(row);
  row?.remove();
}

function removeAllProcessingMessages() {
  els.messages.querySelectorAll('.processing-message').forEach(removeProcessingMessage);
}

function activeQuestionMessage() {
  return els.messages.querySelector('.active-question-message');
}

function finishActiveQuestionMessage() {
  const row = activeQuestionMessage();
  const questionId = row?.dataset.questionId;
  if (row) {
    row.classList.remove('active-question-message');
    row.querySelector('.inline-question-options')?.remove();
    row.querySelector('.institution-history-answer:not(.submitted)')?.remove();
  }
  document.querySelectorAll(
    '.institution-history-dialog, .institution-history-dialog-backdrop',
  ).forEach((node) => node.remove());
  hideActiveQuestionAnswerExamples();
  if (state.pendingAnswerExample?.questionId === questionId) {
    state.pendingAnswerExample = null;
  }
}

function hideActiveQuestionAnswerExamples() {
  els.answerExamplesDock.innerHTML = '';
  els.answerExamplesDock.hidden = true;
}

function renderAnswerExamples(question, answerExamples) {
  hideActiveQuestionAnswerExamples();
  if (!answerExamples.length) return;
  const exampleButtons = document.createElement('div');
  exampleButtons.className = 'inline-answer-example-buttons';
  answerExamples.forEach((example, index) => {
    const text = String(example).trim();
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = text;
    button.addEventListener('click', () => submitAnswerExample(question, text, index));
    exampleButtons.appendChild(button);
  });
  els.answerExamplesDock.appendChild(exampleButtons);
  els.answerExamplesDock.hidden = false;
}

function renderSearchProgress() {
  const progress = state.uiState?.search_progress;
  const completed = Number(progress?.completed_question_count || 0);
  const estimated = Number(progress?.estimated_total_question_count || 0);
  const percent = estimated > 0
    ? Math.max(0, Math.min(100, (completed / estimated) * 100))
    : 0;
  els.searchProgressFill.style.width = `${percent}%`;
  els.searchProgress.setAttribute('aria-valuenow', String(Math.round(percent)));
}

async function submitAnswerExample(question, text, exampleIndex) {
  if (state.busy) return;
  const answerExample = {
    questionId: question.question_id,
    exampleId: `${question.question_id}:EXAMPLE-${exampleIndex + 1}`,
    originalText: text,
    edited: false,
  };
  state.pendingAnswerExample = null;
  els.input.value = '';
  els.input.style.height = '';
  if (!state.session) await startSearch(text, answerExample);
  else await sendFollowup(text, answerExample);
}

function focusComposer() {
  if (state.busy || els.input.disabled) return;
  requestAnimationFrame(() => els.input.focus({ preventScroll: true }));
}

function isInstitutionHistoryQuestion(question) {
  return question?.question_kind === 'PRE_SEARCH_PROFILE'
    && question?.pre_search_key === 'INSTITUTION_PRODUCT_HOLDING_HISTORY';
}

function renderInstitutionHistoryToggles(bubble, question) {
  const options = Array.isArray(question.explanation_details?.institution_history_options)
    ? question.explanation_details.institution_history_options
    : [];
  const byKoreanName = (left, right) => displayInstitutionName(left?.name)
    .localeCompare(displayInstitutionName(right?.name), 'ko-KR');
  const banks = options.filter((item) => item?.sector === 'BANK').sort(byKoreanName);
  const savingsBanks = options.filter((item) => item?.sector === 'SAVINGS_BANK').sort(byKoreanName);
  if (!banks.length && !savingsBanks.length) return;

  const root = document.createElement('div');
  root.className = 'institution-history-answer';
  const selected = new Set();
  let savingsDraft = new Set();

  const appendInstitutionIdentity = (button, institution) => {
    const logoUrl = institutionLogoUrl(institution.name);
    if (logoUrl) {
      const logo = document.createElement('img');
      logo.src = logoUrl;
      logo.alt = '';
      logo.loading = 'lazy';
      logo.draggable = false;
      button.appendChild(logo);
    } else {
      const placeholder = document.createElement('span');
      placeholder.className = 'institution-history-logo-placeholder';
      placeholder.setAttribute('aria-hidden', 'true');
      button.appendChild(placeholder);
    }
    const label = document.createElement('span');
    label.textContent = displayInstitutionName(institution.name);
    button.appendChild(label);
  };

  const dialogBackdrop = document.createElement('div');
  dialogBackdrop.className = 'institution-history-dialog-backdrop hidden';
  const dialog = document.createElement('section');
  dialog.className = 'institution-history-dialog hidden';
  dialog.setAttribute('role', 'dialog');
  dialog.setAttribute('aria-modal', 'true');
  dialog.setAttribute('aria-label', '저축은행 선택');

  const closeSavingsDialog = () => {
    dialogBackdrop.classList.add('hidden');
    dialog.classList.add('hidden');
    root.querySelector('.institution-history-savings-trigger')
      ?.setAttribute('aria-expanded', 'false');
  };
  const openSavingsDialog = () => {
    savingsDraft = new Set(
      [...selected].filter((name) => savingsBanks.some((item) => item.name === name)),
    );
    dialog.querySelectorAll('[data-savings-history-name]').forEach((button) => {
      const active = savingsDraft.has(button.dataset.savingsHistoryName);
      button.classList.toggle('active', active);
      button.setAttribute('aria-pressed', String(active));
    });
    dialogBackdrop.classList.remove('hidden');
    dialog.classList.remove('hidden');
    root.querySelector('.institution-history-savings-trigger')
      ?.setAttribute('aria-expanded', 'true');
  };

  const update = () => {
    root.querySelectorAll('[data-institution-history-name]').forEach((button) => {
      const active = selected.has(button.dataset.institutionHistoryName);
      button.classList.toggle('active', active);
      button.setAttribute('aria-pressed', String(active));
    });
    const savingsTrigger = root.querySelector('.institution-history-savings-trigger');
    if (savingsTrigger) {
      const savingsCount = [...selected].filter((name) => (
        savingsBanks.some((item) => item.name === name)
      )).length;
      savingsTrigger.classList.toggle('active', savingsCount > 0);
      savingsTrigger.setAttribute('aria-expanded', String(!dialog.classList.contains('hidden')));
      const count = savingsTrigger.querySelector('em');
      if (count) {
        count.textContent = savingsCount ? `${savingsCount}개 선택` : `${savingsBanks.length}곳`;
      }
    }
    const done = root.querySelector('.institution-history-done');
    if (done) done.disabled = selected.size === 0;
  };
  const addToggle = (container, institution) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'institution-history-toggle';
    button.dataset.institutionHistoryName = institution.name;
    appendInstitutionIdentity(button, institution);
    button.addEventListener('click', () => {
      if (selected.has(institution.name)) selected.delete(institution.name);
      else selected.add(institution.name);
      update();
    });
    container.appendChild(button);
  };

  const helper = document.createElement('p');
  helper.className = 'institution-history-helper';
  helper.textContent = '복수 선택할 수 있어요.';
  root.appendChild(helper);

  if (banks.length) {
    const bankGrid = document.createElement('div');
    bankGrid.className = 'institution-history-grid';
    banks.forEach((institution) => addToggle(bankGrid, institution));
    root.appendChild(bankGrid);
  }
  if (savingsBanks.length) {
    const savingsTrigger = document.createElement('button');
    savingsTrigger.type = 'button';
    savingsTrigger.className = 'institution-history-savings-trigger';
    const savingsLabel = document.createElement('span');
    savingsLabel.textContent = '저축은행';
    const savingsCount = document.createElement('em');
    savingsCount.textContent = `${savingsBanks.length}곳`;
    savingsTrigger.append(savingsLabel, savingsCount);
    savingsTrigger.addEventListener('click', openSavingsDialog);
    root.appendChild(savingsTrigger);

    const dialogHeader = document.createElement('header');
    const dialogTitle = document.createElement('strong');
    dialogTitle.textContent = '저축은행 선택';
    const dialogClose = document.createElement('button');
    dialogClose.type = 'button';
    dialogClose.className = 'institution-history-dialog-close';
    dialogClose.setAttribute('aria-label', '저축은행 선택 닫기');
    dialogClose.textContent = '×';
    dialogClose.addEventListener('click', closeSavingsDialog);
    dialogHeader.append(dialogTitle, dialogClose);

    const savingsGrid = document.createElement('div');
    savingsGrid.className = 'institution-history-grid institution-history-savings-grid';
    savingsBanks.forEach((institution) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'institution-history-toggle';
      button.dataset.savingsHistoryName = institution.name;
      appendInstitutionIdentity(button, institution);
      button.addEventListener('click', () => {
        if (savingsDraft.has(institution.name)) savingsDraft.delete(institution.name);
        else savingsDraft.add(institution.name);
        const active = savingsDraft.has(institution.name);
        button.classList.toggle('active', active);
        button.setAttribute('aria-pressed', String(active));
      });
      savingsGrid.appendChild(button);
    });
    const dialogActions = document.createElement('footer');
    const dialogCancel = document.createElement('button');
    dialogCancel.type = 'button';
    dialogCancel.textContent = '취소';
    dialogCancel.addEventListener('click', closeSavingsDialog);
    const dialogApply = document.createElement('button');
    dialogApply.type = 'button';
    dialogApply.className = 'institution-history-dialog-apply';
    dialogApply.textContent = '선택';
    dialogApply.addEventListener('click', () => {
      savingsBanks.forEach((institution) => selected.delete(institution.name));
      savingsDraft.forEach((name) => selected.add(name));
      closeSavingsDialog();
      update();
    });
    dialogActions.append(dialogCancel, dialogApply);
    dialog.append(dialogHeader, savingsGrid, dialogActions);
    dialogBackdrop.addEventListener('click', closeSavingsDialog);
    document.body.append(dialogBackdrop, dialog);
  }

  const collapseToSelection = (institutions, empty = false) => {
    dialogBackdrop.remove();
    dialog.remove();
    root.innerHTML = '';
    root.classList.add('submitted');
    const summary = document.createElement('div');
    summary.className = 'institution-history-selection-summary';
    if (empty) {
      summary.textContent = '해당 없음';
    } else {
      institutions.sort(byKoreanName).forEach((institution) => {
        const chip = document.createElement('span');
        appendInstitutionIdentity(chip, institution);
        summary.appendChild(chip);
      });
    }
    root.appendChild(summary);
  };

  const actions = document.createElement('div');
  actions.className = 'institution-history-actions';
  const none = document.createElement('button');
  none.type = 'button';
  none.className = 'institution-history-none';
  none.textContent = '해당 없음';
  none.addEventListener('click', () => {
    collapseToSelection([], true);
    sendFollowup('최근 6개월 동안 은행이나 저축은행의 예금·적금·청약을 보유한 적이 없어요.');
  });
  const done = document.createElement('button');
  done.type = 'button';
  done.className = 'institution-history-done';
  done.textContent = '선택 완료';
  done.addEventListener('click', () => {
    const names = [...selected].sort((left, right) => (
      displayInstitutionName(left).localeCompare(displayInstitutionName(right), 'ko-KR')
    ));
    if (!names.length) return;
    const selectedInstitutions = options.filter((item) => selected.has(item.name));
    collapseToSelection(selectedInstitutions);
    sendFollowup(
      `최근 6개월 동안 ${names.map(displayInstitutionName).join(', ')}에 예금·적금·청약을 보유했어요.`,
    );
  });
  actions.append(none, done);
  root.appendChild(actions);
  update();
  bubble.appendChild(root);
}

function appendQuestionMessage(question) {
  const text = String(question?.question || '').trim();
  if (!text) return false;
  const existing = activeQuestionMessage();
  if (existing?.dataset.questionId === question.question_id) {
    // A partial free-text reply can leave the same question active (for
    // example, the user supplies a balance when we still need a period).
    // `sendFollowup` clears the previous examples before the request; restore
    // them here so the still-active question never appears to have stopped.
    const answerExamples = Array.isArray(question.answer_examples)
      ? question.answer_examples.filter((example) => String(example || '').trim())
      : [];
    renderAnswerExamples(question, answerExamples);
    return true;
  }
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

  const referenceLinks = Array.isArray(question.explanation_details?.reference_links)
    ? question.explanation_details.reference_links
    : [];
  const validReferenceLinks = referenceLinks
    .map((item) => ({ ...item, safeUrl: safeUrl(item?.url) }))
    .filter((item) => item.safeUrl);
  if (validReferenceLinks.length) {
    const linksNode = document.createElement('div');
    linksNode.className = 'question-reference-links';
    validReferenceLinks.forEach((item) => {
      const link = document.createElement('a');
      link.href = item.safeUrl;
      link.target = '_blank';
      link.rel = 'noopener';
      link.textContent = '자세히 보기';
      linksNode.appendChild(link);
    });
    bubble.appendChild(linksNode);
  }

  const answerExamples = Array.isArray(question.answer_examples)
    ? question.answer_examples.filter((example) => String(example || '').trim())
    : [];
  renderAnswerExamples(question, isInstitutionHistoryQuestion(question) ? [] : answerExamples);

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
  } else if (
    question.question_kind === 'FINANCIAL_FACT'
    && question.answer_mode === 'BINARY'
    && !preSearch
  ) {
    addButton('네', true);
    addButton('아니요', false);
    if (!question.confirmation_required) addButton('아직 확인 전', 'UNKNOWN');
  } else if (
    question.question_kind === 'FINANCIAL_FACT'
    && question.answer_mode === 'OPTIONS'
  ) {
    for (const option of question.question_spec?.options || []) {
      const amount = Number(option);
      addButton(amount === 0 ? '카드 사용 안 함' : `월 ${formatWon(amount)}까지`, option);
    }
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
  if (isInstitutionHistoryQuestion(question)) {
    renderInstitutionHistoryToggles(bubble, question);
  }

  row.appendChild(bubble);
  els.messages.appendChild(row);
  requestAnimationFrame(() => { els.chatScroll.scrollTop = els.chatScroll.scrollHeight; });
  return true;
}

function objectiveLabel(value) {
  return {
    MAX_ESTIMATED_AFTER_TAX_INTEREST: '예상 세후 이자',
    MAX_ESTIMATED_PRE_TAX_INTEREST: '예상 세전 이자',
    MAX_REALIZABLE_RATE: '가능한 최고 금리',
    MIN_ACTION_BURDEN: '관리 부담 낮은 순',
    BALANCED: '균형 기준',
  }[value] || '개인화 기준';
}

function verificationBadge(value) {
  return {
    FINANCIAL_DATA_VERIFIED: ['금융데이터 확인', 'verified'],
    USER_RESPONSE_INCLUDED: ['사용자 응답 포함', 'plan'],
    PLAN_BASED: ['계획 기준', 'plan'],
    ADDITIONAL_VERIFICATION_REQUIRED: ['아직 확인 전', 'unknown'],
  }[value] || [String(value || '확인 필요'), 'unknown'];
}

function eligibilityBadge(value) {
  return {
    ELIGIBLE: ['가입 가능', 'verified'],
    PLAN_REQUIRED: ['계획 필요', 'plan'],
    VERIFICATION_REQUIRED: ['가입자격 확인 전', 'unknown'],
    INELIGIBLE: ['가입 불가', 'danger'],
  }[value] || [String(value || ''), 'unknown'];
}

function evaluationStatusLabel(value) {
  return {
    SATISFIED: '충족',
    ACHIEVABLE: '달성 가능',
    UNSATISFIABLE: '받을 수 없음',
    UNKNOWN: '확인 전',
  }[value] || String(value || '');
}

function conditionDisplayStatus(item) {
  if (item?.display_status) return item.display_status;
  return {
    SATISFIED: '확인 완료',
    ACHIEVABLE: '적용 예상',
    UNSATISFIABLE: '적용 안 함',
    UNKNOWN: '확인 전',
  }[item?.status] || '확인 전';
}

function productTypeLabel(value) {
  return {
    INSTALLMENT_SAVINGS: '적금',
    TIME_DEPOSIT: '예금',
    PARKING_ACCOUNT: '파킹통장',
    CMA: 'CMA',
  }[value] || value || '금융상품';
}

function syncDeterministicToolbarFilters() {
  const intent = state.uiState?.intent;
  if (!intent) return;

  state.selectedProductTypes = new Set(intent.product_types || []);
  for (const button of els.productFamilyFilter.querySelectorAll('button[data-product-type]')) {
    const active = state.selectedProductTypes.has(button.dataset.productType);
    button.setAttribute('aria-pressed', String(active));
    button.classList.toggle('active', active);
  }

  const requiredSectors = new Set();
  for (const constraint of intent.hard_constraints || []) {
    if (
      constraint.field !== 'INSTITUTION_SECTOR'
      || constraint.constraint !== 'REQUIRE'
    ) continue;
    String(constraint.expected || '')
      .split('|')
      .filter(Boolean)
      .forEach((sector) => requiredSectors.add(sector));
  }
  state.selectedInstitutionSectors = requiredSectors;
  for (const button of [els.firstSectorFilter, els.savingsBankFilter]) {
    const active = requiredSectors.has(button.dataset.institutionSector);
    button.setAttribute('aria-pressed', String(active));
    button.classList.toggle('active', active);
  }
}

function humanFactLabel(factType, value) {
  const yes = value === true;
  const no = value === false;
  const mapping = {
    SALARY_ACCOUNT_CHANGE_POSSIBLE: ['급여계좌 변경', yes ? '가능' : no ? '불가' : '확인 전'],
    CARD_SETTLEMENT_ACCOUNT_CHANGE_POSSIBLE: ['카드 결제계좌 변경', yes ? '가능' : no ? '불가' : '확인 전'],
    SUPER_SOL_JOIN_LOGIN_MAINTAIN_WILLING: ['SuperSOL 가입·유지', yes ? '가능' : no ? '하지 않음' : '확인 전'],
    SPECIAL_RATE_COUPON_VALID: ['이벤트 우대', yes ? '해당' : no ? '해당 없음' : '확인 전'],
    HANA_HEALTH_SERVICE_WILLING: ['건강서비스 연동', yes ? '가능' : no ? '하지 않음' : '확인 전'],
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

  if ((intent.product_types || []).length) {
    add('상품 형태', intent.product_types.map(productTypeLabel).join('·'));
  }
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
  add('추천 기준', objectiveLabel(intent.ranking_objective));

  for (const capability of intent.capabilities || []) {
    const labelMap = {
      CHANGE_SALARY_ACCOUNT: '급여계좌 변경',
      NEW_CARD_ISSUANCE: '신규카드 발급',
      BRANCH_VISIT: '영업점 방문',
      CHANGE_CARD_SETTLEMENT_ACCOUNT: '카드 결제계좌 변경',
    };
    const valueMap = { CAN: '가능', CANNOT: '불가', UNKNOWN: '확인 전' };
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

  const typedProfileLabels = {
    CURRENT_SALARY_BANK: '현재 급여은행',
    CURRENT_CARD_PAYMENT_BANK: '현재 카드 결제계좌',
  };
  for (const profile of snapshot.pre_search_typed_facts || []) {
    add(typedProfileLabels[profile.fact_type] || profile.fact_type, profile.value, 'fact');
  }

  const preSearchLabels = {
    PRODUCT_TYPE: '상품 형태',
    APPLICATION_CAPACITY: '가입 명의',
    CONTRIBUTION_AND_TERM: '금액·주기·기간',
    PROTECTION_AND_CMA_SCOPE: '보호·CMA 범위',
    INSTITUTION_SCOPE: '금융기관 범위',
    COMMON_BENEFIT_WILLINGNESS: '추가 우대조건 활용',
  };
  const willingnessLabels = {
    WILLING: '가능',
    UNWILLING: '하지 않음',
    CONDITIONAL: '조건에 따라 가능',
    NOT_APPLICABLE: '해당 없음',
  };
  const applicationCapacityLabels = {
    INDIVIDUAL: '개인 명의',
    SOLE_PROPRIETOR: '개인사업자 명의',
    CORPORATION: '법인 명의',
  };
  for (const entry of snapshot.pre_search_profile || []) {
    if (entry.answer_status === 'NOT_ASKED') continue;
    let value = entry.answer_status === 'ACKNOWLEDGED_UNKNOWN'
      ? '사용자가 아직 확인하지 못함'
      : entry.answer_status === 'NOT_APPLICABLE'
        ? '해당 없음'
        : null;
    const profileValue = entry.value || {};
    const actionPreferences = profileValue.action_preferences || null;
    const distinctActionAnswers = actionPreferences
      ? new Set(Object.values(actionPreferences)).size
      : 0;
    if (!value && actionPreferences && (!profileValue.willingness || distinctActionAnswers > 1)) {
      const actionLabels = {
        FIRST_TRANSACTION_BENEFIT: '새 은행',
        SALARY_BENEFIT: '급여계좌',
        CARD_BENEFIT: '카드',
      };
      value = Object.entries(actionPreferences)
        .map(([key, answer]) => `${actionLabels[key] || key} ${willingnessLabels[answer] || answer}`)
        .join(' · ');
    }
    if (!value && profileValue.application_capacity) {
      value = applicationCapacityLabels[profileValue.application_capacity]
        || profileValue.application_capacity;
    }
    if (!value && profileValue.willingness) value = willingnessLabels[profileValue.willingness] || profileValue.willingness;
    if (!value && profileValue.institution_scope) {
      value = {
        FIRST_SECTOR_ONLY: '1금융권만',
        PREFER_FIRST_SECTOR: '1금융권 우선',
        BANKS_AND_SAVINGS_BANKS: '은행·저축은행',
        BANKS_AND_SECURITIES: '은행·증권사',
        SECURITIES_ONLY: '증권사만',
        ANY: '제한 없음',
      }[profileValue.institution_scope] || profileValue.institution_scope;
    }
    if (!value && profileValue.ranking_objective) value = objectiveLabel(profileValue.ranking_objective);
    if (!value && entry.rationale) value = entry.rationale;
    if (value) add(preSearchLabels[entry.question_key] || entry.question_key, value, 'fact');
  }

  for (const declaration of snapshot.user_declarations || []) {
    if (profileFactTypes.has(declaration.fact_type)) continue;
    const human = humanFactLabel(declaration.fact_type, declaration.value);
    if (human) add(human[0], human[1], 'fact');
  }

  for (const acknowledged of snapshot.acknowledged_unknown_answers || []) {
    const human = humanFactLabel(acknowledged.fact_type, null);
    add(
      human ? human[0] : '아직 확인 전인 조건',
      human ? '사용자가 아직 확인하지 못함' : acknowledged.question,
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
    span.innerHTML = `<span class="state-chip-copy"><strong>${escapeHtml(chip.label)}</strong> ${escapeHtml(chip.value)}${sourceTag}</span>`;
    const editButton = document.createElement('button');
    editButton.type = 'button';
    editButton.className = 'state-chip-edit';
    editButton.textContent = '수정';
    editButton.setAttribute('aria-label', `${chip.label} 조건 수정`);
    editButton.addEventListener('click', () => {
      const prefix = `${chip.label} 조건을 수정하고 싶어요. `;
      els.input.value = prefix;
      els.input.dispatchEvent(new Event('input'));
      focusComposer();
    });
    span.appendChild(editButton);
    root.appendChild(span);
  }
}

function collectAvailableInstitutions(rankedItems) {
  state.availableInstitutions = [...new Map(
    rankedItems
      .filter((item) => String(item.institution_name || '').trim())
      .map((item) => [String(item.institution_name).trim(), {
        rawName: String(item.institution_name).trim(),
        displayName: displayInstitutionName(item.institution_name),
        sector: item.institution_sector || 'UNKNOWN',
      }]),
  ).values()].sort((a, b) => a.displayName.localeCompare(b.displayName, 'ko-KR'));

  const availableNames = new Set(state.availableInstitutions.map((item) => item.rawName));
  state.selectedInstitutionNames = new Set(
    [...state.selectedInstitutionNames].filter((name) => availableNames.has(name)),
  );
  const count = state.selectedInstitutionNames.size;
  els.institutionSelectionCount.textContent = count ? String(count) : '';
  els.institutionSelectionCount.hidden = count === 0;
  els.institutionPickerButton.classList.toggle('active', count > 0);
  els.institutionPickerButton.setAttribute(
    'aria-label',
    count ? `금융기관 ${count}개 선택됨` : '은행 선택',
  );
  renderSelectedInstitutionStrip();
}

function renderSelectedInstitutionStrip() {
  const root = els.selectedInstitutionStrip;
  root.innerHTML = '';
  const selected = state.availableInstitutions.filter((institution) => (
    state.selectedInstitutionNames.has(institution.rawName)
  ));
  root.hidden = selected.length === 0;
  for (const institution of selected) {
    const chip = document.createElement('span');
    chip.className = 'selected-institution-chip';
    const logoUrl = institutionLogoUrl(institution.rawName);
    if (logoUrl) {
      const logo = document.createElement('img');
      logo.src = logoUrl;
      logo.alt = '';
      logo.draggable = false;
      chip.appendChild(logo);
    }
    const label = document.createElement('span');
    label.textContent = institution.displayName;
    chip.appendChild(label);
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'selected-institution-remove';
    remove.textContent = '×';
    remove.setAttribute('aria-label', `${institution.displayName} 선택 해제`);
    remove.addEventListener('click', (event) => {
      event.stopPropagation();
      if (state.busy) return;
      state.selectedInstitutionNames.delete(institution.rawName);
      state.institutionSelectionDraft.delete(institution.rawName);
      state.visibleRecommendationCount = RECOMMENDATION_PAGE_SIZE;
      renderRecommendations();
    });
    remove.disabled = state.busy;
    chip.appendChild(remove);
    root.appendChild(chip);
  }
}

function enableHorizontalDragScroll(root) {
  let pointerId = null;
  let startX = 0;
  let startScrollLeft = 0;
  root.addEventListener('pointerdown', (event) => {
    if (event.button !== 0 || event.target.closest('button')) return;
    pointerId = event.pointerId;
    startX = event.clientX;
    startScrollLeft = root.scrollLeft;
    root.setPointerCapture(pointerId);
    root.classList.add('dragging');
  });
  root.addEventListener('pointermove', (event) => {
    if (event.pointerId !== pointerId) return;
    root.scrollLeft = startScrollLeft - (event.clientX - startX);
  });
  const finish = (event) => {
    if (event.pointerId !== pointerId) return;
    if (root.hasPointerCapture(pointerId)) root.releasePointerCapture(pointerId);
    pointerId = null;
    root.classList.remove('dragging');
  };
  root.addEventListener('pointerup', finish);
  root.addEventListener('pointercancel', finish);
}

function renderInstitutionPicker() {
  const groups = [
    ['BANK', '1금융권'],
    ['SAVINGS_BANK', '저축은행'],
    ['SECURITIES', '증권사'],
  ];
  els.institutionPickerBody.innerHTML = '';
  for (const [sector, label] of groups) {
    const institutions = state.availableInstitutions.filter((item) => item.sector === sector);
    if (!institutions.length) continue;
    const section = document.createElement('section');
    section.className = 'institution-picker-group';
    const heading = document.createElement('div');
    heading.className = 'institution-picker-group-title';
    heading.innerHTML = `<h3>${label}</h3><span>${institutions.length}개</span>`;
    const grid = document.createElement('div');
    grid.className = 'institution-picker-grid';
    for (const institution of institutions) {
      const button = document.createElement('button');
      button.type = 'button';
      button.dataset.institutionName = institution.rawName;
      button.className = 'institution-option';
      const selected = state.institutionSelectionDraft.has(institution.rawName);
      button.classList.toggle('selected', selected);
      button.setAttribute('aria-pressed', String(selected));
      const logoUrl = institutionLogoUrl(institution.rawName);
      if (logoUrl) {
        const logo = document.createElement('img');
        logo.src = logoUrl;
        logo.alt = '';
        logo.loading = 'lazy';
        button.appendChild(logo);
      } else {
        const placeholder = document.createElement('span');
        placeholder.className = 'institution-option-logo-placeholder';
        placeholder.setAttribute('aria-hidden', 'true');
        button.appendChild(placeholder);
      }
      const name = document.createElement('span');
      name.textContent = institution.displayName;
      button.appendChild(name);
      const check = document.createElement('i');
      check.textContent = selected ? '✓' : '';
      check.setAttribute('aria-hidden', 'true');
      button.appendChild(check);
      grid.appendChild(button);
    }
    section.append(heading, grid);
    els.institutionPickerBody.appendChild(section);
  }
}

function openInstitutionPicker() {
  if (state.busy) return;
  state.institutionSelectionDraft = new Set(state.selectedInstitutionNames);
  renderInstitutionPicker();
  els.institutionPickerBackdrop.classList.remove('hidden');
  els.institutionPickerModal.classList.remove('hidden');
  els.institutionPickerButton.setAttribute('aria-expanded', 'true');
  document.body.classList.add('modal-open');
  els.institutionPickerClose.focus();
}

function closeInstitutionPicker() {
  els.institutionPickerBackdrop.classList.add('hidden');
  els.institutionPickerModal.classList.add('hidden');
  els.institutionPickerButton.setAttribute('aria-expanded', 'false');
  document.body.classList.remove('modal-open');
}

function preserveRenderedRecommendationHeights() {
  const renderedCards = [...els.recommendations.querySelectorAll('.recommendation-card')];
  state.expandedCardHeights = new Map();
  state.expandedPodiumHeights = new Map();
  const regularHeights = [];
  for (const renderedCard of renderedCards) {
    const height = renderedCard.getBoundingClientRect().height;
    state.expandedCardHeights.set(renderedCard.dataset.productId, height);
    const rankText = renderedCard.querySelector('.rank-badge')?.textContent || '';
    const rank = Number.parseInt(rankText, 10);
    if (renderedCard.classList.contains('podium-card') && Number.isFinite(rank)) {
      state.expandedPodiumHeights.set(rank, height);
    } else {
      regularHeights.push(height);
    }
  }
  state.expandedRegularCardHeight = regularHeights.length
    ? Math.max(...regularHeights)
    : renderedCards.at(-1)?.getBoundingClientRect().height || null;
}

function loadNextRecommendationPage() {
  if (state.busy || !state.recommendations) return;
  const sentinel = els.recommendations.querySelector('.recommendation-scroll-sentinel');
  const totalCount = Number.parseInt(sentinel?.dataset.totalCount || '0', 10);
  if (!totalCount || state.visibleRecommendationCount >= totalCount) return;
  const previousScrollTop = els.recommendationSection.scrollTop;
  preserveRenderedRecommendationHeights();
  state.visibleRecommendationCount = Math.min(
    state.visibleRecommendationCount + 10,
    totalCount,
    100,
  );
  renderRecommendations();
  requestAnimationFrame(() => {
    els.recommendationSection.scrollTop = previousScrollTop;
  });
}

function expandRecommendationsForViewport(totalCount) {
  if (state.busy || !state.recommendations || !totalCount) return;
  const container = els.recommendationSection;
  const cards = [...els.recommendations.querySelectorAll('.recommendation-card')];
  if (!container || !cards.length) return;
  const containerRect = container.getBoundingClientRect();
  const listRect = els.recommendations.getBoundingClientRect();
  const availableHeight = Math.max(
    0,
    container.clientHeight - Math.max(0, listRect.top - containerRect.top),
  );
  const measuredHeight = cards.reduce(
    (sum, card) => sum + card.getBoundingClientRect().height,
    0,
  );
  const averageHeight = measuredHeight / cards.length;
  if (!Number.isFinite(averageHeight) || averageHeight <= 0) return;
  const target = Math.min(
    totalCount,
    100,
    Math.max(RECOMMENDATION_PAGE_SIZE, Math.ceil(availableHeight / averageHeight) + 1),
  );
  if (target <= state.visibleRecommendationCount) return;
  preserveRenderedRecommendationHeights();
  state.visibleRecommendationCount = target;
  renderRecommendations();
}

function maybeLoadMoreRecommendations(event) {
  if (event?.type === 'wheel' && event.deltaY <= 0) return;
  const container = els.recommendationSection;
  if (!container || (event?.type !== 'wheel' && container.scrollTop <= 0)) return;
  const distanceFromBottom = container.scrollHeight
    - container.scrollTop
    - container.clientHeight;
  if (distanceFromBottom <= 180) loadNextRecommendationPage();
}

function semanticMemoItems(_source) {
  return [];
}

function semanticMemosHtml(_source, _className) {
  return '';
}

function renderSemanticReviewStatus(rec) {
  const review = rec.semantic_review;
  if (!review || ['DISABLED', 'DEFERRED'].includes(review.status)) return;
  const note = document.createElement('div');
  note.className = 'semantic-review-note';
  note.setAttribute('role', 'status');
  const text = document.createElement('p');
  const pending = Number(review.pending_product_count || 0);
  const unresolved = Number(review.unresolved_clause_count || 0);
  text.textContent = pending > 0
    ? `추가 우대조건을 아직 분석하지 않은 상위권 후보가 ${pending}개 있습니다. 현재 추천은 잠정 결과입니다.`
    : unresolved > 0
      ? `원문 우대조건 중 ${unresolved}개는 자동 판단하지 않았습니다. 확인되지 않은 우대는 현재 예상금리에 추가하지 않습니다.`
      : review.status === 'PARTIAL'
        ? '일부 우대조건만 해석했습니다. 나머지는 잠정 결과입니다.'
      : review.ai_interpreted
        ? '일부 우대조건은 원문을 AI로 해석했습니다. 사용자 답변에 따른 예상값이며, 서류·금융기관 확인과 구분됩니다.'
        : '이번 후보군의 추가 우대조건 검토를 마쳤습니다.';
  note.appendChild(text);
  const canRetry = review.error_code === 'SEMANTIC_COMPILATION_FAILED'
    || review.status === 'FAILED'
    || review.status === 'PARTIAL';
  if ((pending > 0 || canRetry) && state.session) {
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = canRetry && pending === 0 ? '추가 조건 분석 재시도' : '추가 조건 분석 계속';
    button.disabled = state.busy;
    button.addEventListener('click', async () => {
      if (state.busy || !state.session) return;
      const sessionId = state.session.search_session_id;
      setBusy(true);
      button.disabled = true;
      text.textContent = '현재 상위권 후보의 추가 우대조건을 분석하고 있습니다.';
      try {
        await api(`/search-sessions/${sessionId}/semantic-review`, {
          method: 'POST', body: {
            retry_failed: review.status === 'FAILED'
              || review.error_code === 'SEMANTIC_COMPILATION_FAILED',
          },
        });
        if (state.session?.search_session_id === sessionId) await syncAll();
      } catch (error) {
        text.textContent = `추가 조건 분석을 완료하지 못했습니다. ${error.message}`;
      } finally {
        setBusy(false);
        button.disabled = false;
      }
    });
    note.appendChild(button);
  }
  els.recommendations.appendChild(note);
}

function renderRecommendations() {
  const rec = state.recommendations;
  if (!rec) return;
  if (state.renderedRecommendationsRef !== rec) {
    state.renderedRecommendationsRef = rec;
    state.visibleRecommendationCount = RECOMMENDATION_PAGE_SIZE;
    state.expandedCardHeights = new Map();
    state.expandedRegularCardHeight = null;
    state.expandedPodiumHeights = new Map();
    els.recommendationSection.scrollTop = 0;
  }
  // View filters must run against the complete catalog/ranking. The normalized
  // runtime can contain thousands of products, and cutting the global list to
  // 100 here made lower-rate families such as time deposits appear empty.
  const rankedItems = rec.ranked_products || rec.top_products || [];
  const previewMode = Boolean(rec.preview_mode);
  collectAvailableInstitutions(rankedItems);
  const query = state.productSearchQuery;
  const filteredItems = rankedItems.filter((item) => {
    if (
      state.selectedProductTypes.size
      && !state.selectedProductTypes.has(item.product_type)
    ) return false;
    if (
      state.selectedInstitutionNames.size
      && !state.selectedInstitutionNames.has(item.institution_name)
    ) return false;
    if (
      state.selectedInstitutionSectors.size
      && !state.selectedInstitutionSectors.has(item.institution_sector)
    ) return false;
    if (!query) return true;
    return String(item.product_name || '')
      .toLocaleLowerCase('ko-KR')
      .includes(query);
  });
  const hasActiveViewFilter = Boolean(
    state.selectedProductTypes.size
    || state.selectedInstitutionNames.size
    || state.selectedInstitutionSectors.size
    || query,
  );
  // Product name, family, and institution controls are view filters. They must
  // not renumber the recommendation ranking: a product ranked 10th in the
  // complete result remains 10th when it is the only matching search result.
  const allItems = filteredItems.slice(0, 100);
  const items = allItems.slice(0, state.visibleRecommendationCount);
  els.recommendations.innerHTML = '';
  renderSemanticReviewStatus(rec);
  els.recommendations.classList.toggle(
    'expanded-results',
    state.visibleRecommendationCount >= RECOMMENDATION_PAGE_SIZE
      && items.length >= RECOMMENDATION_PAGE_SIZE,
  );
  els.recommendations.classList.toggle('filtered-results', hasActiveViewFilter);
  state.productNames.clear();
  for (const item of rankedItems) {
    state.productNames.set(item.product_id, item.product_name);
  }

  if (!allItems.length) {
    els.recommendations.innerHTML = query
      ? '<div class="empty-results"><div class="empty-illustration">⌕</div><h3>검색 결과가 없습니다</h3><p>상품명을 다시 확인해 주세요.</p></div>'
      : '<div class="empty-results"><div class="empty-illustration">0</div><h3>현재 조건에 맞는 후보가 없습니다</h3><p>검색 조건을 조금 완화하거나 제외 조건을 바꿔보세요.</p></div>';
    return;
  }

  for (const item of items) {
    const card = document.createElement('article');
    const podiumClass = item.rank === 1 ? 'podium-card rank-first top-card'
      : item.rank === 2 ? 'podium-card rank-second'
        : item.rank === 3 ? 'podium-card rank-third' : '';
    card.className = `recommendation-card ${podiumClass} ${previewMode ? 'preview-only' : ''}`;
    card.dataset.productId = item.product_id;
    if (els.recommendations.classList.contains('expanded-results')) {
      const preservedHeight = state.expandedCardHeights.get(item.product_id)
        || state.expandedPodiumHeights.get(item.rank)
        || state.expandedRegularCardHeight;
      if (preservedHeight) card.style.height = `${preservedHeight}px`;
    }
    card.tabIndex = 0;
    card.setAttribute('role', 'button');
    card.setAttribute('aria-label', `${item.rank}위 ${item.product_name} 상세 보기`);

    const comparison = item.ranking_comparability === 'COMPARABLE';
    const performanceLinked = item.return_kind === 'PERFORMANCE_LINKED';
    const conditionalUpperRate = item.user_specific_conditional_upper_rate;
    const hasConditionalUpside = conditionalUpperRate !== null
      && conditionalUpperRate !== undefined
      && (item.realizable_rate === null
        || item.realizable_rate === undefined
        || Number(conditionalUpperRate) > Number(item.realizable_rate));
    const expectedRate = item.realizable_rate;
    const expectedRateHtml = performanceLinked
      ? '<div class="metric-label">예상 수익률</div><div class="expected-rate-value variable-rate">실적에 따라 변동</div>'
      : expectedRate !== null && expectedRate !== undefined
      ? `<div class="metric-label">예상 금리</div><div class="expected-rate-value">${formatRate(expectedRate)}</div>`
      : '<div class="metric-label">예상 금리</div><div class="expected-rate-value unknown-rate">계산 전</div>';
    const expectedInterest = item.estimated_pre_tax_interest;
    const interestHtml = performanceLinked ? '' : comparison && expectedInterest !== null && expectedInterest !== undefined
      ? `<div class="metric-label">예상 이자</div><div class="money-value">${formatWon(expectedInterest)}</div>`
      : '<div class="metric-label">예상 이자</div><div class="money-value unknown-money">?원</div>';
    const hasPublishedReference = Boolean(item.published_rate_summary);
    // advertised_max_rate is recalculated for the resolved maturity.  Prefer
    // it over the catalog-wide published summary so both card fields describe
    // the same term.
    const displayedRateValue = hasConditionalUpside
      ? formatRate(conditionalUpperRate)
      : item.advertised_max_rate !== null
        ? formatRate(item.advertised_max_rate)
      : hasPublishedReference
        ? escapeHtml(item.published_rate_summary)
        : '?%';
    const displayedRateLabel = performanceLinked
      ? '최근 기간수익률(참고)'
      : hasConditionalUpside
        ? '조건 충족 시 최고'
        : '최고금리';
    const rateBlockHtml = performanceLinked
      ? '<div class="rate-block"><div class="metric-label">수익 방식</div><div class="rate-value reference-rate">실적배당 상품</div><div class="ad-rate">운용실적에 따라 수익 변동</div></div>'
      : `<div class="rate-block"><div class="metric-label">${displayedRateLabel}</div><div class="published-rate-row"><div class="rate-value reference-rate">${displayedRateValue}</div></div></div>`;
    const institutionLogo = institutionLogoUrl(item.institution_name);
    const institutionLogoHtml = institutionLogo
      ? `<img class="product-institution-logo" src="${escapeHtml(institutionLogo)}" alt="" loading="lazy">`
      : '';
    card.innerHTML = `
      <div class="card-primary-row">
        <div class="product-main">
          <div class="rank-badge">${item.rank}위</div>
          ${institutionLogoHtml}
          <div class="product-copy">
            <div class="product-name">${escapeHtml(item.product_name)}</div>
            <div class="institution">${escapeHtml(displayInstitutionName(item.institution_name))}</div>
          </div>
        </div>
        <div class="product-plan-summary">
          <div><span>만기</span><strong>${escapeHtml(productFactValue(item.term_summary))}</strong></div>
        </div>
      </div>
      <div class="card-metrics-row">
        <div class="money-block">${interestHtml}</div>
        <div class="expected-rate-block">${expectedRateHtml}</div>
        ${rateBlockHtml}
      </div>
      ${semanticMemosHtml(item, 'card-semantic-memos')}
    `;
    const open = () => openDetail(item.product_id, previewMode);
    card.addEventListener('click', open);
    card.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); open(); }
    });
    els.recommendations.appendChild(card);
  }
  if (items.length < allItems.length && items.length < 100) {
    const sentinel = document.createElement('div');
    sentinel.className = 'recommendation-scroll-sentinel';
    sentinel.dataset.totalCount = String(Math.min(allItems.length, 100));
    sentinel.setAttribute('aria-hidden', 'true');
    els.recommendations.appendChild(sentinel);
  }
  requestAnimationFrame(() => expandRecommendationsForViewport(allItems.length));
  renderStateChips();
}

function renderQuestion(question) {
  state.activeQuestion = question || null;
  if (!question) {
    finishActiveQuestionMessage();
    return;
  }
  // A real question replaces the transient typing indicator atomically. Detail
  // preloading may continue after this render, but both bubbles must never be
  // visible at the same time.
  removeAllProcessingMessages();
  appendQuestionMessage(question);
}

function renderInitialQuestion() {
  if (!state.initialQuestion) return;
  renderQuestion(state.initialQuestion);
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
  syncDeterministicToolbarFilters();
  renderSearchProgress();
  if (rec) state.recommendations = rec;

  let nextQuestion = question;
  if (nextQuestion === undefined) {
    nextQuestion = await api(`/search-sessions/${id}/questions/next`).catch(() => ({ question: null }));
    if (nextQuestion && nextQuestion.question === null && Object.keys(nextQuestion).length === 1) nextQuestion = null;
  }
  renderQuestion(nextQuestion);
  if (state.recommendations) {
    // These are structured preview payloads, not AI summaries. Warm their
    // cache in the background, including during pre-search, but never make a
    // conversation turn wait for all visible product details to finish.
    renderRecommendations();
    preloadRecommendationDetails().catch(() => null);
  } else {
    renderStateChips();
  }
  return nextQuestion;
}

function clearBrowseOnlyFiltersForNewSearch() {
  // These controls only narrow the current browser view; they are not part of
  // the conversational search intent. A newly-created session can still be in
  // its initial questioning stage (and therefore have no intent to sync yet),
  // so carrying any of them over can hide valid engine results.
  state.productSearchQuery = '';
  state.selectedProductTypes = new Set();
  state.selectedInstitutionSectors = new Set();
  state.selectedInstitutionNames = new Set();
  state.institutionSelectionDraft = new Set();
  state.visibleRecommendationCount = RECOMMENDATION_PAGE_SIZE;
  els.productSearchInput.value = '';
  els.productSearch.classList.remove('is-expanded');
  els.productSearchToggle.setAttribute('aria-expanded', 'false');
  els.institutionSelectionCount.hidden = true;
  els.institutionSelectionCount.textContent = '';
  els.institutionPickerButton.classList.remove('active');
  els.institutionPickerButton.setAttribute('aria-label', '은행 선택');
  for (const button of els.productFamilyFilter.querySelectorAll('button[data-product-type]')) {
    button.classList.remove('active');
    button.setAttribute('aria-pressed', 'false');
  }
  for (const button of [els.firstSectorFilter, els.savingsBankFilter]) {
    button.classList.remove('active');
    button.setAttribute('aria-pressed', 'false');
  }
  renderSelectedInstitutionStrip();
  closeInstitutionPicker();
}

async function startSearch(message, answerExample = null) {
  setBusy(true);
  // Keep the catalog visible while the first answer is being interpreted.
  // The first engine result replaces it only after the request completes, so
  // starting a conversation never turns the recommendation pane into a blank
  // or loading-only screen.
  let processingMessage = null;
  try {
    const now = new Date();
    const today = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
    renderQuestion(null);
    appendMessage('user', message);
    processingMessage = appendProcessingMessage();
    const session = await api('/search-sessions', {
      method: 'POST',
      body: {
        user_id: state.userId,
        natural_language_query: message,
        initial_pre_search_question_id: state.initialQuestion?.question_id || null,
        ...(answerExample ? {
          answer_origin: answerExample.edited
            ? 'ANSWER_EXAMPLE_EDITED'
            : 'ANSWER_EXAMPLE',
          answer_example_id: answerExample.exampleId,
        } : {}),
        as_of: today,
        subscription_date: today,
      },
    });
    state.session = session;
    clearBrowseOnlyFiltersForNewSearch();
    const nextQuestion = await syncAll({ session });
    removeProcessingMessage(processingMessage);
    processingMessage = null;
    if (!nextQuestion) {
      appendMessage('assistant', completionMessage(session));
    }
  } catch (error) {
    removeProcessingMessage(processingMessage);
    processingMessage = null;
    showStartError(error);
  } finally {
    removeProcessingMessage(processingMessage);
    setBusy(false);
    focusComposer();
  }
}

function completionMessage(session) {
  const reason = session?.completion_reason;
  if (reason === 'STABLE_TOP3') {
    return '현재 조건에서 3위 안의 순위를 바꿀 추가 확인 사항이 없습니다. 확인은 여기까지예요.';
  }
  if (reason === 'PROVISIONAL_USER_STOPPED') {
    return '확인을 원하지 않은 조건은 그대로 남겨 두었어요. 현재 추천은 미확인 조건이 남은 잠정 결과예요.';
  }
  if (reason === 'PROVISIONAL_DATA_INCOMPLETE') {
    return '더 물을 수 있는 조건은 없지만 일부 상품 정보가 불완전해 현재 추천은 잠정 결과예요. 가입 전 공식 원문을 확인해 주세요.';
  }
  if (reason === 'INSUFFICIENT_ELIGIBLE_PRODUCTS') {
    return '현재 조건으로 추천할 수 있는 상품이 충분하지 않아요. 제외 조건이나 가입 범위를 바꾸면 다시 찾아볼 수 있어요.';
  }
  return '말씀하신 조건으로 추천을 계산했어요. 조건을 더 말하면 결과를 계속 좁혀갈 수 있어요.';
}

async function sendFollowup(message, answerExample = null) {
  setBusy(true);
  let processingMessage = null;
  try {
    hideActiveQuestionAnswerExamples();
    appendMessage('user', message);
    processingMessage = appendProcessingMessage();
    const result = await api(`/search-sessions/${state.session.search_session_id}/messages`, {
      method: 'POST',
      body: {
        message,
        ...(answerExample ? {
          answer_origin: answerExample.edited
            ? 'ANSWER_EXAMPLE_EDITED'
            : 'ANSWER_EXAMPLE',
          answer_example_id: answerExample.exampleId,
        } : {}),
      },
    });
    state.session = result.session;
    await syncAll({
      session: result.session,
      question: result.next_question,
      recommendations: result.recommendations === null ? undefined : result.recommendations,
    });
    removeProcessingMessage(processingMessage);
    processingMessage = null;
    const operations = result.operations_executed || [];
    const needsVisibleReply = operations.includes('EXPLAIN_ACTIVE_QUESTION')
      || operations.includes('NO_OP');
    // Most answers are sufficiently confirmed by the next deterministic
    // question. Explanations and no-op guidance must still be visible even
    // while that question remains active; otherwise the conversation appears
    // to have stopped.
    if (!result.next_question && result.recommendations) {
      appendMessage(
        'assistant',
        completionMessage(result.session),
      );
    } else if (result.assistant_message && (!result.next_question || needsVisibleReply)) {
      appendMessage('assistant', result.assistant_message);
    } else if (result.current_results_requested) {
      appendMessage('assistant', '현재까지 답한 조건으로 후보를 보여드릴게요. 남은 질문을 마치면 Top 5가 확정됩니다.');
    } else if (result.recommendations) {
      appendMessage('assistant', '말씀하신 조건을 반영해 추천 결과를 다시 계산했어요.');
    }
  } catch (error) {
    removeProcessingMessage(processingMessage);
    processingMessage = null;
    if (await recoverExpiredSearchSession(error)) {
      return;
    } else if (error.status === 503 && error.payload?.llm_enabled === false) {
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
    focusComposer();
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
    processingMessage = appendProcessingMessage({ startsWithInterpretation: false });
    const session = await api(`/search-sessions/${state.session.search_session_id}/answers`, {
      method: 'POST',
      body: { question_id: answeredQuestion.question_id, answer },
    });
    state.session = session;
    const next = await api(`/search-sessions/${state.session.search_session_id}/questions/next`).catch(() => null);
    await syncAll({ session, question: next && next.question ? next : null });
    removeProcessingMessage(processingMessage);
    processingMessage = null;
    if (!next?.question) appendMessage(
      'assistant',
      completionMessage(session),
    );
  } catch (error) {
    removeProcessingMessage(processingMessage);
    processingMessage = null;
    if (await recoverExpiredSearchSession(error)) return;
    appendMessage('assistant', `답변을 반영하지 못했어요: ${error.message}`);
    renderQuestion(answeredQuestion);
    toast(error.message, 'error');
  } finally {
    removeProcessingMessage(processingMessage);
    setBusy(false);
    focusComposer();
  }
}

function isExpiredSearchSessionError(error) {
  return error?.status === 404
    && /Unknown search_session_id/i.test(String(error.message || ''));
}

async function recoverExpiredSearchSession(error) {
  if (!isExpiredSearchSessionError(error)) return false;
  // Search sessions are intentionally in-memory. A server restart therefore
  // cannot resume the old identifier; switch to a clean first question instead
  // of showing that implementation detail as a failed user request.
  state.session = null;
  setBusy(false);
  await resetConversation();
  toast('서버가 업데이트되어 이전 검색을 새로 시작했어요.', 'normal');
  return true;
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
  renderSearchProgress();
  state.recommendations = state.defaultRecommendations;
  state.activeQuestion = null;
  state.currentDetail = null;
  state.detailCache.clear();
  state.detailCacheRecommendationId = null;
  state.visibleRecommendationCount = RECOMMENDATION_PAGE_SIZE;
  state.renderedRecommendationsRef = null;
  state.expandedCardHeights = new Map();
  state.expandedRegularCardHeight = null;
  state.expandedPodiumHeights = new Map();
  state.productNames.clear();
  state.pendingAnswerExample = null;
  state.productSearchQuery = '';
  state.selectedProductTypes = new Set();
  state.selectedInstitutionSectors = new Set();
  state.selectedInstitutionNames = new Set();
  state.institutionSelectionDraft = new Set();
  state.availableInstitutions = [];
  state.userId = createConversationUserId();

  els.messages.innerHTML = '';
  els.input.value = '';
  els.productSearchInput.value = '';
  els.productSearch.classList.remove('is-expanded');
  els.productSearchToggle.setAttribute('aria-expanded', 'false');
  for (const button of els.productFamilyFilter.querySelectorAll('button')) {
    button.setAttribute('aria-pressed', 'false');
    button.classList.remove('active');
  }
  for (const button of [els.firstSectorFilter, els.savingsBankFilter]) {
    button.setAttribute('aria-pressed', 'false');
    button.classList.remove('active');
  }
  els.institutionSelectionCount.hidden = true;
  els.institutionSelectionCount.textContent = '';
  els.institutionPickerButton.classList.remove('active');
  closeInstitutionPicker();
  els.understoodToggle.setAttribute('aria-expanded', 'false');
  els.understoodToggleLabel.textContent = 'AI는 잘 이해했나?';
  els.understoodContent.hidden = true;
  els.input.style.height = '';
  renderInitialQuestion();
  renderStateChips();
  closeDetail();
  renderRecommendations();
  els.chatScroll.scrollTop = 0;
  setBusy(false);
  focusComposer();
}

function renderLoading() {
  els.recommendations.innerHTML = '<div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div>';
}

function showStartError(error) {
  els.recommendations.innerHTML = '<div class="empty-results"><div class="empty-illustration">!</div><h3>검색을 시작하지 못했습니다</h3><p>서버 설정을 확인한 뒤 다시 시도해 주세요.</p></div>';
  appendMessage('assistant', `검색을 시작하지 못했어요: ${error.message}`);
  toast(error.message, 'error');
}

function userRuleLabel(value) {
  return String(value || '').replace(/\bbranch\b/gi, '조건');
}

function statusIcon(value) {
  return { SATISFIED: '✓', ACHIEVABLE: '→', UNSATISFIABLE: '×', UNKNOWN: '?' }[value] || '·';
}

function cleanPreferentialSentence(value) {
  return String(value || '')
    .replace(/^[①-⑳]\s*/, '')
    .replace(/\s*(?:연\s*)?\d+(?:\.\d+)?\s*%p?\s*$/, '')
    .replace(/\s+/g, ' ')
    .trim();
}

function preferentialConditionPresentation(item) {
  const structured = item.presentation || {};
  const authoredSummary = structured.summary_text || item.summary_text || item.condition_summary;
  const authoredDetails = structured.detail_texts || item.detail_texts || item.condition_details;
  if (Object.keys(structured).length || authoredSummary) {
    return {
      category: structured.category || item.short_title || item.title || item.rule_label || '우대조건',
      summary: authoredSummary || '세부 우대조건은 공식 상품 페이지에서 확인해 주세요.',
      details: Array.isArray(authoredDetails) ? authoredDetails : authoredDetails ? [authoredDetails] : [],
      aiAuthored: Boolean(authoredSummary) && structured.authorship?.kind === 'AI_STRUCTURED',
      relation: structured.relation || null,
    };
  }

  const raw = item.condition_text || item.action_summary || item.rule_label || item.title || '';
  const lines = String(raw).split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  const category = String(item.short_title || item.title || item.rule_label || '우대조건').replace(/\s*:\s*(?:연\s*)?\d+(?:\.\d+)?\s*%.*$/, '').trim();
  const content = lines.filter((line, index) => {
    if (line.startsWith('※')) return false;
    if (index !== 0) return true;
    return !(line.includes('우대') && /\d+(?:\.\d+)?\s*%/.test(line) && lines.length > 1);
  });
  const noteDetails = lines.filter((line) => line.startsWith('※')).map((line) => line.replace(/^※\s*/, '').trim());
  const summary = cleanPreferentialSentence(content[0] || raw);
  return { category, summary, details: noteDetails, aiAuthored: false, relation: null };
}

function disclosureRewardText(item) {
  const reward = item.extracted_reward_candidate || {};
  return reward.value == null ? '금리 확인 필요' : `+${reward.value}%p`;
}

function renderDisclosureNode(item, sharedDetails = []) {
  const pending = item.verification_status === 'PENDING_OFFICIAL_VERIFICATION' || item.evaluator_eligible === false;
  const presentation = preferentialConditionPresentation(item);
  const details = [...new Set([...presentation.details, ...sharedDetails])];
  const authoredBadge = presentation.aiAuthored ? '<span class="status-chip status-ACHIEVABLE">AI 핵심 요약</span>' : '<span class="status-chip">수집 원문 기준</span>';
  const verificationBadge = pending ? '<span class="status-chip status-UNKNOWN">공식 확인 필요</span>' : '';
  const showReward = presentation.relation?.display_reward !== false;
  return `<div class="rate-node preferential-node"><div class="rate-node-top"><div class="rate-evidence"><span class="rate-status-icon status-${pending ? 'UNKNOWN' : 'ACHIEVABLE'}">${pending ? '?' : '→'}</span><span class="condition-category">${escapeHtml(presentation.category)}</span>${authoredBadge}${verificationBadge}</div>${showReward ? `<div class="reward">${escapeHtml(disclosureRewardText(item))}</div>` : ''}</div><div class="condition-summary">${escapeHtml(presentation.summary || '세부 적용 조건을 확인해 주세요.')}</div>${details.length ? `<div class="condition-details"><strong>상세 설명</strong><span>${details.map(escapeHtml).join('<br>')}</span></div>` : ''}</div>`;
}

function renderDisclosureEntries(disclosures) {
  const actionable = disclosures.filter((item) => item.presentation?.display_role !== 'CONTEXT_ONLY');
  const visibleDisclosures = actionable.length ? actionable : disclosures.slice(0, 1);
  const grouped = new Map();
  visibleDisclosures.forEach((item) => {
    const relation = preferentialConditionPresentation(item).relation;
    if (relation?.group_id) {
      if (!grouped.has(relation.group_id)) grouped.set(relation.group_id, []);
      grouped.get(relation.group_id).push(item);
    }
  });
  const renderedGroups = new Set();
  let html = '';
  for (const item of visibleDisclosures) {
    const relation = preferentialConditionPresentation(item).relation;
    if (!relation?.group_id) {
      html += renderDisclosureNode(item);
      continue;
    }
    if (renderedGroups.has(relation.group_id)) continue;
    renderedGroups.add(relation.group_id);
    const group = grouped.get(relation.group_id) || [item];
    const groupDetails = relation.group_detail_texts || [];
    const relationCopy = {
      EXCLUSIVE: '아래 조건 중 하나만 적용돼요.',
      OR: '아래 조건 중 하나를 충족하면 돼요.',
      AND: '아래 조건을 모두 충족해야 해요.',
      CAPPED: '조건별 금리는 표시된 한도까지 적용돼요.',
      TIERED: '충족 구간에 따라 우대금리가 달라져요.',
    }[relation.operator] || '조건 간 적용 관계를 확인해 주세요.';
    const cap = relation.cap;
    const capHtml = cap?.value != null ? `<em>최대 +${escapeHtml(cap.value)}%p</em>` : '';
    const groupDetailHtml = groupDetails.length ? `<div class="condition-details"><strong>공통 안내</strong><span>${groupDetails.map(escapeHtml).join('<br>')}</span></div>` : '';
    html += `<div class="preferential-group"><div class="preferential-group-heading"><div><strong>${escapeHtml(relation.group_label || '묶음 우대')}</strong><span>${escapeHtml(relationCopy)}</span></div>${capHtml}</div><div class="preferential-group-options">${group.map((entry) => renderDisclosureNode(entry)).join('')}</div>${groupDetailHtml}</div>`;
  }
  return html;
}

function preferentialActionGuide(item) {
  if (item.status === 'SATISFIED') {
    return {
      label: '유지할 조건',
      text: item.action_summary || '현재 충족 중이에요. 가입·유지 시 이 조건이 계속 적용되는지 확인하세요.',
    };
  }
  if (item.status === 'ACHIEVABLE') {
    return {
      label: '받는 방법',
      text: item.action_summary || `${userRuleLabel(item.rule_label)} 조건을 충족하세요.`,
    };
  }
  if (item.status === 'UNSATISFIABLE') {
    return { label: '현재 상태', text: '현재 입력한 조건으로는 이 우대금리를 받을 수 없어요.' };
  }
  return {
    label: '확인할 것',
    text: item.action_summary || '공식 상품 페이지에서 세부 기준과 적용 여부를 확인하세요.',
  };
}

function renderRateNode(item, child = false) {
  const source = item.source_reference || {};
  const sourceUrl = safeUrl(source.source_url);
  const sourceLabel = [source.document, source.page ? `p.${source.page}` : null, source.section].filter(Boolean).join(' · ');
  const sourceHtml = sourceUrl
    ? `<a class="source-link" href="${escapeHtml(sourceUrl)}" target="_blank" rel="noopener">공식 설명서${source.page ? ` p.${escapeHtml(source.page)}` : ''}</a>`
    : sourceLabel ? `<span>${escapeHtml(sourceLabel)}</span>` : '';
  const children = (item.children || []).map((x) => renderRateNode(x, true)).join('');
  const actionGuide = preferentialActionGuide(item);
  const presentation = preferentialConditionPresentation(item);
  const displayStatus = conditionDisplayStatus(item);
  const detailHtml = presentation.details.length
    ? `<div class="condition-details"><strong>상세 설명</strong><span>${presentation.details.map(escapeHtml).join('<br>')}</span></div>`
    : '';
  return `
    <div class="rate-node ${child ? 'child-node' : ''}">
      <div class="rate-node-top">
        <div>
          <div class="rate-title"><span class="rate-status-icon status-${escapeHtml(item.status)}">${escapeHtml(statusIcon(item.status))}</span>${escapeHtml(presentation.category || userRuleLabel(item.rule_label))}</div>
          <div class="rate-evidence">
            <span class="status-chip status-${escapeHtml(item.status)}">${escapeHtml(displayStatus)}</span>
            <span>${escapeHtml(item.evidence_basis)}</span>
            ${sourceHtml}
          </div>
        </div>
        ${item.nominal_reward_pp !== null && item.nominal_reward_pp !== undefined ? `<div class="reward">+${escapeHtml(item.nominal_reward_pp)}%p</div>` : ''}
      </div>
      <div class="action-summary"><strong>${escapeHtml(actionGuide.label)}</strong><span>${escapeHtml(item.presentation ? presentation.summary : actionGuide.text)}</span></div>
      ${detailHtml}
    </div>${children}`;
}

function rateCalculationMessage(detail) {
  if (detail.realizable_rate !== null && detail.realizable_rate !== undefined) {
    return {
      kind: 'ready',
      title: '내 조건으로 금리를 계산했어요',
      description: `현재 입력한 조건과 ${detail.planned_contribution_summary || '납입 계획'}을 반영한 값입니다.`,
    };
  }
  if (detail.return_kind === 'PERFORMANCE_LINKED') {
    return {
      kind: 'performance',
      title: '실적배당 상품이에요',
      description: 'MMF·MMW 같은 실적배당 상품은 운용 결과에 따라 수익이 달라져 금리와 예상이자를 표시하지 않습니다.',
    };
  }
  if (detail.rate_calculation_reason === 'APPLICABLE_BASE_RATE_NOT_AVAILABLE') {
    return {
      kind: 'unknown',
      title: '내 조건에 적용할 금리를 아직 고르지 못했어요',
      description: '공식 금리 수치가 있어도 가입 명의·잔액 구간 등 현재 조건과 연결하는 규칙이 충분하지 않아 계산을 보류했습니다.',
    };
  }
  return {
    kind: 'unknown',
    title: '계산 가능한 공식 금리가 아직 없어요',
    description: '확인되지 않은 값을 0%로 바꾸지 않았습니다. 필요한 내용은 우대조건에서 확인 필요로 표시했어요.',
  };
}

function hasPossibleRateUpside(detail) {
  const upper = detail.rate_evaluation?.user_specific_conditional_upper_rate
    ?? detail.advertised_max_rate;
  const realizable = detail.realizable_rate;
  return (upper !== null && upper !== undefined
    && (realizable === null || realizable === undefined || Number(upper) > Number(realizable)))
    || Number(detail.additional_possible_rate_pp || 0) > 0;
}

function detailRankMessage(detail) {
  const ahead = Number(detail.confirmed_or_achievable_products_ahead || 0);
  const unknownCount = Number(detail.material_unknown_count || 0);
  const provisional = hasPossibleRateUpside(detail);
  if (detail.eligibility_status === 'UNKNOWN') {
    return {
      kind: 'unknown',
      description: ahead > 0
        ? `가입 가능하거나 계획으로 달성 가능한 상품 ${ahead}개를 먼저 보여준 뒤 배치되어 현재 ${detail.rank}위입니다.${unknownCount ? ` 확인할 가입 조건은 ${unknownCount}건입니다.` : ''}`
        : `가입대상 조건을 확인하기 전의 임시 순위입니다.${unknownCount ? ` 확인할 조건은 ${unknownCount}건입니다.` : ''}`,
    };
  }
  if (detail.eligibility_status === 'ACHIEVABLE') {
    return {
      kind: 'plan',
      description: `아래의 필요한 행동과 우대조건을 지킬 수 있는지 확인해 주세요.${provisional ? ' 현재 순위는 조건 확인 전 임시 순위입니다.' : ''}`,
    };
  }
  if (provisional) {
    return {
      kind: 'unknown',
      description: '확인 전 우대조건을 포함한 가능한 최고 금리 기준의 임시 순위입니다.',
    };
  }
  return {
    kind: 'ready',
    description: `${objectiveLabel(detail.ranking_objective)} 기준으로 계산 가능한 상품과 비교한 결과입니다.`,
  };
}

function flattenRateBreakdown(items = []) {
  return items.flatMap((item) => [item, ...flattenRateBreakdown(item.children || [])]);
}

function detailSummaryCopy(detail) {
  const rank = Number(detail.rank || 0);
  const objective = detail.ranking_objective;
  const rankingBasis = {
    MAX_ESTIMATED_AFTER_TAX_INTEREST: '예상 세후 이자금',
    MAX_ESTIMATED_PRE_TAX_INTEREST: '예상 세전 이자금',
    MAX_REALIZABLE_RATE: '조건 충족 시 가능한 최고 금리',
    BALANCED: '금리와 예상 이자금의 균형',
  }[objective] || '내 조건에 맞는 비교 기준';
  const possibleUpperRate = detail.rate_evaluation?.user_specific_conditional_upper_rate;
  const rankingRate = possibleUpperRate ?? detail.realizable_rate ?? detail.advertised_max_rate;
  const rankingValue = objective === 'MAX_ESTIMATED_AFTER_TAX_INTEREST'
    ? formatWon(detail.estimated_after_tax_interest)
    : objective === 'MAX_ESTIMATED_PRE_TAX_INTEREST'
      ? formatWon(detail.estimated_pre_tax_interest)
      : formatRate(rankingRate);
  const provisional = objective === 'MAX_REALIZABLE_RATE' && hasPossibleRateUpside(detail);
  const rankText = rank === 1
    ? `${rankingBasis}${rankingValue !== '-' && rankingValue !== '확인 전' ? ` ${rankingValue}` : ''}를 기준으로 비교했을 때 가장 유리해 ${rank}위로 추천됐어요.${provisional ? ' 현재 순위는 조건 확인 전 임시 순위예요.' : ''}`
    : `${rankingBasis}${rankingValue !== '-' && rankingValue !== '확인 전' ? ` ${rankingValue}` : ''}를 기준으로 비교한 결과 ${rank}위예요.${provisional ? ' 현재 순위는 조건 확인 전 임시 순위예요.' : ''}`;

  const rateItems = flattenRateBreakdown(detail.rate_breakdown || []);
  const eligibleRateItems = rateItems.filter((item) => (
    ['SATISFIED', 'ACHIEVABLE'].includes(item.status)
    && item.nominal_reward_pp !== null
    && item.nominal_reward_pp !== undefined
  ));
  const uniqueRewards = [...new Map(eligibleRateItems.map((item) => [
    `${item.rule_label}:${item.nominal_reward_pp}`,
    `${item.rule_label} +${item.nominal_reward_pp}%p`,
  ])).values()];
  const rewardText = uniqueRewards.length
    ? uniqueRewards.slice(0, 3).join(' · ')
    : '조건 충족 시 가능한 우대금리를 아직 확정하지 못했어요.';

  const actionableItems = rateItems.filter((item) => (
    ['SATISFIED', 'ACHIEVABLE'].includes(item.status)
    && (item.action_summary || item.presentation?.summary_text || item.rule_label)
  ));
  const uniqueActions = [...new Set(actionableItems.map((item) => {
    const action = item.action_summary || item.presentation?.summary_text || `${item.rule_label} 조건을 충족하세요.`;
    return item.status === 'SATISFIED' ? `현재 충족 중: ${action}` : action;
  }))];
  const actionText = uniqueActions.length
    ? uniqueActions.slice(0, 3).join(' · ')
    : '우대금리를 받기 위한 조건은 아래 상세 목록에서 공식 기준을 확인해 주세요.';

  return {
    title: rank === 1 ? '내 조건에서 가장 유리한 이유' : `${rank}위로 추천된 이유`,
    reason: (rankText || '상품별 적용 조건과 금리를 비교해 추천했어요.') + ((detail.semantic_interpretations || []).some((item) => item.status === 'AI_INTERPRETED') ? ' 일부 우대조건은 원문을 AI로 해석한 잠정 판단이며 공식 검증과 구분됩니다.' : ''),
    reward: rewardText,
    action: actionText,
  };
}

function officialProductSource(detail) {
  const sources = detail.official_sources || [];
  const priority = {
    OFFICIAL_PRODUCT_PAGE: 0,
    OFFICIAL_PRODUCT_API: 1,
    OFFICIAL_PRODUCT_DETAIL_AND_RATE_SOURCE: 1,
    OFFICIAL_PRODUCT_DISCLOSURE: 1,
    OFFICIAL_PRODUCT_DISCLOSURE_HTML: 1,
    OFFICIAL_PRODUCT_TERMS: 1,
    OFFICIAL_PRODUCT_LIST: 2,
    OFFICIAL_INSTITUTION_HOME: 3,
    OFFICIAL_CENTRAL_ASSOCIATION_DIRECTORY: 4,
  };
  return sources
    .filter((source) => safeUrl(source.url || source.source_url))
    .sort((left, right) => (priority[left.document_type] ?? 5) - (priority[right.document_type] ?? 5))[0] || null;
}

function officialProductUrl(detail) {
  const source = officialProductSource(detail);
  return safeUrl(source?.official_home_url || source?.url || source?.source_url);
}

function officialProductLinkLabel(source) {
  return '홈페이지로 이동';
}

function renderDetailHeader(detail) {
  const institutionName = displayInstitutionName(detail.institution_name);
  const logoUrl = institutionLogoUrl(detail.institution_name);
  const productSource = officialProductSource(detail);
  const productUrl = officialProductUrl(detail);
  const productLinkLabel = officialProductLinkLabel(productSource);
  els.detailInstitution.textContent = institutionName;
  els.detailProductName.textContent = detail.product_name || '상품 상세';
  els.detailLinkCaption.textContent = productLinkLabel;
  if (logoUrl) {
    els.detailInstitutionLogo.src = logoUrl;
    els.detailInstitutionLogo.hidden = false;
    els.detailLogoPlaceholder.hidden = true;
  } else {
    els.detailInstitutionLogo.removeAttribute('src');
    els.detailInstitutionLogo.hidden = true;
    els.detailLogoPlaceholder.hidden = false;
    els.detailLogoPlaceholder.textContent = institutionName.slice(0, 2) || '금융';
  }
  if (productUrl) {
    els.detailProductLink.href = productUrl;
    els.detailProductLink.removeAttribute('aria-disabled');
    els.detailProductLink.setAttribute('aria-label', `${institutionName} ${detail.product_name} ${productLinkLabel} 열기`);
  } else {
    els.detailProductLink.removeAttribute('href');
    els.detailProductLink.setAttribute('aria-disabled', 'true');
    els.detailProductLink.setAttribute('aria-label', `${institutionName} ${detail.product_name}`);
  }
}

const PRODUCT_FAMILY_DETAIL = {
  INSTALLMENT_SAVINGS: { label: '적금', heading: '적금 핵심정보' },
  TIME_DEPOSIT: { label: '예금', heading: '예금 핵심정보' },
  PARKING_ACCOUNT: { label: '파킹통장', heading: '파킹통장 핵심정보' },
  CMA: { label: 'CMA', heading: 'CMA 핵심정보' },
};

const DETAIL_VALUE_LABELS = {
  PERIODIC: '정기 납입',
  LUMP_SUM: '한 번에 예치',
  ON_DEMAND: '수시입출금',
  DAILY: '매일',
  WEEKLY: '매주',
  MONTHLY: '매월',
  IRREGULAR: '자유롭게',
  INTEREST: '이자 지급',
  POSTED_RATE: '공시금리',
  POSTED_YIELD: '공시수익률',
  PERFORMANCE_LINKED: '운용실적에 따른 수익',
  PROTECTED: '예금자보호 대상',
  PARTIALLY_PROTECTED: '일부 예금자보호',
  NOT_PROTECTED: '예금자보호 대상 아님',
  ON_DEMAND_ACCESS: '수시입출금',
  MATURITY: '만기 시 지급',
  AT_MATURITY: '만기 시 지급',
  REDEMPTION_REQUIRED: '환매 후 출금',
  EARLY_TERMINATION_ALLOWED: '중도해지 가능',
  SIMPLE: '단리',
  COMPOUND: '복리',
  FIXED_AT_PURCHASE: '매수 시점 수익률 고정',
  DAILY_RESET: '매일 수익률 갱신',
  VARIABLE: '변동',
  NONE: '재투자 없음',
  AUTOMATIC: '자동 재투자',
  MARGINAL: '잔액 구간별 부분 적용',
  MARGINAL_TRANCHE: '잔액 구간별 부분 적용',
  WHOLE_BALANCE: '해당 구간 금리를 전체 잔액에 적용',
  WHOLE_BALANCE_TIER: '해당 구간 금리를 전체 잔액에 적용',
  CAPPED_PORTION: '한도 내 잔액에만 적용',
  TAXABLE: '일반과세',
  INDIVIDUAL: '개인',
  CORPORATION: '법인',
  SOLE_PROPRIETOR: '개인사업자',
  RP: 'RP형',
  MMF: 'MMF형',
  MMW: 'MMW형',
  ISSUED_NOTE: '발행어음형',
  WRAP: 'Wrap형',
  MERCHANT_BANK: '종금형',
  FIXED_AT_DEPOSIT: '입금 시점 수익률 확정',
  REPRICED_ON_POSTED_RATE_CHANGE: '공시수익률 변경 시 함께 변경',
  VARIABLE_DAILY_POSTED: '매일 공시수익률에 따라 변동',
  VARIABLE_POSTED: '공시수익률에 따라 변동',
  NOT_GUARANTEED: '보장되지 않음',
  GUARANTEED: '보장',
  FLEXIBLE: '자유롭게',
  BRANCH: '영업점',
  MOBILE: '모바일',
  INTERNET: '인터넷',
  INSTALLMENT_CASHFLOW: '회차별 납입액과 남은 기간으로 계산',
  LUMP_SUM_TERM: '일시 예치 원금과 선택 만기로 계산',
  ON_DEMAND_BALANCE: '잔액 구간과 보유일수로 계산',
  CMA_POSTED_YIELD: '공시수익률 기준',
  CMA_PERFORMANCE_LINKED: '운용실적 연동(확정금리 없음)',
  PER_RP_LOT: 'RP 매수 건별로 계산',
  PER_RP_LOT_ELAPSED_TERM: 'RP 매수 건별 보유 기간으로 계산',
  MARGINAL_BALANCE_PER_RP_LOT: '잔액 구간별·RP 매수 건별로 계산',
  PER_DEPOSIT_LOT_ELAPSED_TERM: '입금한 건별 보유 기간으로 계산',
  CURRENT_POSTED_RATE_AUTO_REPRICING: '공시수익률이 바뀌면 자동 변경',
  DAILY_BALANCE: '매일 최종 잔액으로 계산',
  END_OF_DAY_BALANCE: '매일 최종 잔액 기준',
  };

function detailLabel(value, fallback = '') {
  const raw = String(value ?? '').trim();
  if (!raw) return fallback;
  return DETAIL_VALUE_LABELS[raw] || raw.replaceAll('_', ' ');
}

function detailPolicyBundle(detail) {
  const product = detail.product || {};
  const snapshot = product.condition_snapshot || {};
  return {
    family: detail.product_type || detail.product_family || product.product_family || '',
    subtype: detail.product_subtype || product.product_subtype || detail.search_facts?.product_subtype,
    term: detail.term_policy || product.term_policy || {},
    cashFlow: detail.cash_flow_policy || product.cash_flow_policy || {},
    returnPolicy: product.return_policy || {
      return_kind: detail.return_kind,
      calculation_method: detail.calculation_method,
      rate_entries: detail.rate_entries || [],
    },
    fee: detail.fee_policy || product.fee_policy || {},
    tax: detail.tax_policy || product.tax_policy || {},
    liquidity: detail.liquidity_policy || product.liquidity_policy || {},
    protection: product.protection_policy || (
      detail.protection_status ? { coverage_status: detail.protection_status } : {}
    ),
    facts: detail.search_facts || product.search_facts || {},
    displayFacts: snapshot.product_facts || {},
    rateEvaluation: detail.rate_evaluation || {},
  };
}

function termPolicyText(policy, fallback = '') {
  const unit = { DAY: '일', WEEK: '주', MONTH: '개월', YEAR: '년' }[policy.unit] || '';
  if (policy.kind === 'OPEN_ENDED') return '만기 없음';
  if (policy.kind === 'FIXED') return `${policy.fixed_value ?? policy.value}${unit}`;
  if (policy.kind === 'DISCRETE' && (policy.allowed_values || []).length) {
    return `${policy.allowed_values.join(' · ')}${unit}`;
  }
  if (policy.kind === 'RANGE' && policy.min_value != null && policy.max_value != null) {
    return `${policy.min_value}~${policy.max_value}${unit}`;
  }
  return fallback || '상품별 확인';
}

function detailInfoCard(label, value) {
  if (value === null || value === undefined || String(value).trim() === '') return '';
  return `<div class="detail-info-card"><span class="detail-info-card-label">${escapeHtml(label)}</span><strong class="detail-info-card-value">${escapeHtml(value)}</strong></div>`;
}

function familyDetailRows(detail) {
  const policy = detailPolicyBundle(detail);
  const facts = policy.facts;
  const shown = policy.displayFacts;
  const family = policy.family;
  const protection = detailLabel(facts.protection_status || policy.protection.coverage_status, '확인 필요');
  const term = shown.term_text || termPolicyText(policy.term, detail.term_summary);
  const funding = detailLabel(facts.funding_type || policy.cashFlow.funding_type, '상품별 확인');
  const contributionFrequency = (shown.contribution_frequency_options || [])
    .map((value) => detailLabel(value)).join(' · ')
    || detailLabel(facts.contribution_frequency || shown.contribution_frequency, '상품별 확인');
  const customerScope = (facts.customer_scopes || []).map((value) => detailLabel(value)).join(' · ');
  const interestPayment = detailLabel(
    shown.interest_payment || facts.interest_payment_method,
    '상품별 확인',
  );
  const returnKind = detailLabel(facts.return_kind || policy.returnPolicy.return_kind, '금리 방식 확인 필요');
  const accessMode = detailLabel(policy.liquidity.access_mode,
    (facts.open_ended || policy.cashFlow.funding_type === 'ON_DEMAND') ? '수시입출금' : '만기 전 해지 조건 확인');
  const amount = shown.amount_text || detail.maximum_deposit_summary || detail.contribution_summary;
  const calculation = detailLabel(policy.returnPolicy.calculation_method || detail.calculation_method, returnKind);
  const evaluationBasis = detailLabel(policy.rateEvaluation.calculation_mode, calculation);

  if (family === 'INSTALLMENT_SAVINGS') {
    return [
      ['가입 기간', term],
      ['납입 방식', shown.saving_method || funding],
      ['납입 주기', contributionFrequency],
      ['납입 금액', amount],
      ['계산 기준', evaluationBasis],
      ['이자 지급', interestPayment],
    ];
  }
  if (family === 'TIME_DEPOSIT') {
    return [
      ['가입 기간', term],
      ['예치 방식', funding],
      ['예치 금액', amount],
      ['계산 기준', evaluationBasis],
      ['이자 지급', interestPayment],
      ['중도해지', accessMode],
      ['가입 대상', customerScope || detail.target_customer_summary || '상품별 확인'],
    ];
  }
  if (family === 'PARKING_ACCOUNT') {
    const payment = parkingPaymentOverview(detail);
    const interestAccrual = policy.returnPolicy.interest_accrual || {};
    const parkingCalculation = policy.returnPolicy.calculation_method
      || detail.calculation_method
      || (interestAccrual.frequency === 'DAILY' && interestAccrual.balance_basis === 'END_OF_DAY_BALANCE'
        ? 'DAILY_BALANCE'
        : '');
    return [
      ['입출금 방식', funding],
      ['금리 적용 방식', detailLabel(facts.balance_tier_method || policy.returnPolicy.balance_tier_method, '단일 금리 또는 잔액별 적용')],
      ['이자 계산 기준', detailLabel(parkingCalculation, '확인 필요')],
      ['이자 입금일', payment.value],
      ['가입 대상', customerScope || detail.target_customer_summary || '상품별 확인'],
      ['예금자보호', protection],
    ];
  }
  if (family === 'CMA') {
    const reinvestment = policy.returnPolicy.reinvestment_policy || {};
    const reinvestmentInterval = reinvestment.interval || {};
    const reinvestmentUnit = { DAY: '일', WEEK: '주', MONTH: '개월', YEAR: '년' }[reinvestmentInterval.unit] || '';
    const reinvestmentText = reinvestmentInterval.value
      ? `${reinvestmentInterval.value}${reinvestmentUnit}마다 자동 재예치`
      : detailLabel(facts.reinvestment_mode || reinvestment.mode, '상품 설명 확인 필요');
    const calculationText = detailLabel(
      policy.returnPolicy.calculation_method || detail.calculation_method,
      '상품 설명 확인 필요',
    );
    const subscriptionChannels = (facts.subscription_channels || detail.subscription_channels || [])
      .map((channel) => detailLabel(channel)).filter(Boolean).join(' · ');
    return [
      ['CMA 유형', detailLabel(policy.subtype, '유형 확인 필요')],
      ['가입 방법', subscriptionChannels || '공식 상품 설명 확인 필요'],
      ['수익 계산', calculationText],
      ['입출금', accessMode],
      ['자동 재예치', reinvestmentText],
      ['예금자보호', protection],
    ];
  }
  return [['가입 기간', term], ['자금 납입', funding], ['수익 방식', returnKind], ['예금자보호', protection]];
}

function rateRangeText(appliesTo) {
  const range = appliesTo?.range || appliesTo || {};
  const basis = appliesTo?.basis || '';
  const authoredRange = String(range.source_text || '').trim();
  if (/[\uac00-\ud7a3]/.test(authoredRange)) return authoredRange;
  if (range.fixed_value != null) {
    const fixedUnit = ({ DAY: '일', WEEK: '주', MONTH: '개월', YEAR: '년' }[range.unit] || '');
    return `${range.fixed_value}${fixedUnit}`;
  }
  if (range.min_value == null && range.max_value == null) return '';
  const moneyBasis = ['BALANCE', 'BALANCE_PORTION', 'CASH_DEPOSIT_BALANCE'].includes(basis);
  const unit = moneyBasis ? '원' : ({ DAY: '일', WEEK: '주', MONTH: '개월', YEAR: '년' }[range.unit] || '');
  const render = (value) => moneyBasis ? Number(value).toLocaleString('ko-KR') : value;
  if (range.min_value != null && range.max_value != null && String(range.min_value) === String(range.max_value)) {
    return `${render(range.min_value)}${unit}`;
  }
  if (range.min_value == null) return `${render(range.max_value)}${unit} ${range.max_inclusive === false ? '미만' : '이하'}`;
  if (range.max_value == null) return `${render(range.min_value)}${unit} ${range.min_inclusive === false ? '초과' : '이상'}`;
  return `${render(range.min_value)}${unit} ${range.min_inclusive === false ? '초과' : '이상'} ~ ${render(range.max_value)}${unit} ${range.max_inclusive === false ? '미만' : '이하'}`;
}

function normalizedRateScopes(appliesTo) {
  if (Array.isArray(appliesTo)) return appliesTo;
  if (appliesTo && typeof appliesTo === 'object') return [appliesTo];
  return [];
}

function familyRateSchedule(detail) {
  const policy = detailPolicyBundle(detail);
  if (policy.returnPolicy.return_kind === 'PERFORMANCE_LINKED') return '';
  const entries = (policy.returnPolicy.rate_entries || detail.rate_entries || [])
    .filter((entry) => !['PREFERENTIAL', 'ADVERTISED_MAXIMUM'].includes(entry.role));
  const unique = [...new Map(entries.map((entry) => {
    const calculation = entry.calculation || {};
    const asOf = entry.as_of || calculation.snapshot_as_of || '';
    const scopes = normalizedRateScopes(entry.applies_to).map(rateRangeText).filter(Boolean).join(' · ');
    const customer = entry.customer_scope ? detailLabel(entry.customer_scope) : '';
    const value = calculation.value == null ? '' : `${calculation.value}%`;
    const key = `${entry.role || ''}|${scopes}|${customer}|${value}|${asOf}`;
    return [key, { label: scopes || customer || entry.official_label || '기본 적용', value, asOf }];
  })).values()].filter((row) => row.value);
  if (!unique.length) return '';
  const rows = unique.map((row) => `<div class="family-rate-row"><span>${escapeHtml(row.label)}</span><strong>${escapeHtml(row.value)}</strong>${row.asOf ? `<small>${escapeHtml(row.asOf)} 기준</small>` : ''}</div>`).join('');
  const scheduleLabel = policy.family === 'CMA' ? '수익률 적용 구조' : '금리 적용 구조';
  return `<details class="family-rate-schedule" ${unique.length <= 4 ? 'open' : ''}><summary><span>${scheduleLabel}</span><em>${unique.length}개 구간</em></summary><div class="family-rate-rows">${rows}</div></details>`;
}

function parkingRateForBalance(detail, balance = 1000000) {
  const entries = (detailPolicyBundle(detail).returnPolicy.rate_entries || [])
    .filter((entry) => !['PREFERENTIAL', 'ADVERTISED_MAXIMUM'].includes(entry.role));
  const matched = entries.find((entry) => normalizedRateScopes(entry.applies_to).some((scope) => {
    if (!['BALANCE', 'BALANCE_PORTION', 'CASH_DEPOSIT_BALANCE'].includes(scope.basis)) return false;
    const min = scope.range?.min_value == null ? -Infinity : Number(scope.range.min_value);
    const max = scope.range?.max_value == null ? Infinity : Number(scope.range.max_value);
    const aboveMin = scope.range?.min_inclusive === false ? balance > min : balance >= min;
    const belowMax = scope.range?.max_inclusive === false ? balance < max : balance <= max;
    return aboveMin && belowMax;
  }));
  return numericRateValue(matched) ?? numericRateValue(entries[0]);
}

function installmentTermMonths(detail) {
  const policy = detailPolicyBundle(detail).term;
  const unit = policy.unit;
  let value = policy.representative_value;
  if (value == null && policy.kind === 'FIXED') value = policy.fixed_value ?? policy.value;
  if (value == null && policy.kind === 'DISCRETE') {
    const allowed = policy.allowed_values || [];
    value = allowed.includes(12) ? 12 : allowed[0];
  }
  if (value == null && policy.kind === 'RANGE') {
    const minimum = Number(policy.min_value);
    const maximum = Number(policy.max_value);
    value = Number.isFinite(minimum) && Number.isFinite(maximum) && minimum <= 12 && maximum >= 12
      ? 12
      : policy.min_value;
  }
  const numeric = Number(value);
  if (!Number.isFinite(numeric) || numeric <= 0) return null;
  if (unit === 'YEAR') return Math.round(numeric * 12);
  if (unit === 'MONTH') return Math.round(numeric);
  if (unit === 'WEEK') return Math.max(1, Math.round(numeric * 12 / 52));
  if (unit === 'DAY') return Math.max(1, Math.round(numeric * 12 / 365));
  return null;
}

function installmentBaseRate(detail) {
  const direct = detail.base_rate ?? detail.listing_base_rate;
  if (Number.isFinite(Number(direct))) return Number(direct);
  const entries = (detailPolicyBundle(detail).returnPolicy.rate_entries || [])
    .filter((entry) => entry.role === 'BASE');
  return numericRateValue(entries[0]);
}

function installmentPaymentOverview(detail) {
  const policy = detailPolicyBundle(detail);
  const raw = policy.displayFacts.interest_payment || policy.facts.interest_payment_method;
  const value = detailLabel(raw, '지급 시점 확인 필요');
  if (raw === 'MATURITY' || raw === 'AT_MATURITY') {
    return { value, sub: '만기 해지 시 원금과 함께 받아요' };
  }
  return {
    value,
    sub: raw ? '정확한 지급일은 공식 상품 설명을 확인하세요' : '공식 상품 설명에서 확인해 주세요',
  };
}

function renderSavingsCatalogHero(detail, personalized = false) {
  const monthlyAmount = 300000;
  const months = installmentTermMonths(detail);
  const baseRate = installmentBaseRate(detail);
  const evaluatedRate = detail.rate_evaluation || {};
  const realizableRate = evaluatedRate.realizable_rate ?? detail.realizable_rate;
  const personalizedInterest = detail.estimated_pre_tax_interest;
  const referenceInterest = baseRate == null || months == null
    ? null
    : Math.round(monthlyAmount * (baseRate / 100) / 12 * months * (months + 1) / 2);
  const rateValue = personalized && realizableRate != null ? formatRate(realizableRate) : formatRate(baseRate);
  const interestValue = personalized && personalizedInterest != null
    ? formatWon(personalizedInterest)
    : referenceInterest == null ? '확인 필요' : `약 ${formatWon(referenceInterest)}`;
  const termText = detail.term_summary || termPolicyText(detailPolicyBundle(detail).term);
  const planText = personalized
    ? [detail.planned_contribution_summary, termText]
      .filter((value) => value && !String(value).includes('확인 필요')).join(' · ')
    : `월 30만원 · ${termText}`;
  const advertised = detail.advertised_max_rate ?? detail.listing_advertised_max_rate;
  const payment = installmentPaymentOverview(detail);
  return `<section class="detail-hero savings-detail-hero">
    <div class="cma-hero-heading"><span>적금 한눈에 보기</span>${detail.listing_as_of ? `<em>${escapeHtml(String(detail.listing_as_of).slice(0, 10))} 기준</em>` : ''}</div>
    <div class="detail-rate-row">
      <div class="detail-rate-card primary"><div class="label">${personalized ? '내 적용 금리' : '현재 기본 금리'}</div><div class="value">${escapeHtml(rateValue)}</div><div class="sub">${personalized ? '입력한 조건을 반영한 세전 연 금리' : advertised != null ? `우대조건 충족 시 최고 ${escapeHtml(formatRate(advertised))}` : '세전 연 금리'}</div></div>
      <div class="detail-rate-card"><div class="label">${personalized ? '내 예상 이자' : `${escapeHtml(planText)} 예상 이자`}</div><div class="value money">${escapeHtml(interestValue)}</div><div class="sub">${escapeHtml(planText)} 기준 · 세전 단순 예시</div></div>
      <div class="detail-rate-card"><div class="label">이자 받는 때</div><div class="value text-value">${escapeHtml(payment.value)}</div><div class="sub">${escapeHtml(payment.sub)}</div></div>
    </div>
  </section>`;
}

function parkingPaymentOverview(detail) {
  const interestCrediting = detailPolicyBundle(detail).returnPolicy.interest_crediting || {};
  const rawText = String(interestCrediting.raw_text || '');
  const frequency = interestCrediting.frequency || {};
  if (frequency.unit === 'MONTH') {
    const dayMatch = rawText.match(/익월\s*(\d{1,2})일/);
    return {
      label: '이자 받는 때',
      value: dayMatch ? `매월 ${dayMatch[1]}일` : '매월',
      sub: '전월 잔액을 계산해 지급해요',
    };
  }
  return {
    label: '이자 받는 때',
    value: '지급일 확인 필요',
    sub: '공식 상품 설명에서 확인해 주세요',
  };
}

function renderParkingCatalogHero(detail, personalized = false) {
  const referenceBalance = 1000000;
  const referenceDays = 30;
  const referenceRate = parkingRateForBalance(detail, referenceBalance);
  const referenceInterest = referenceRate == null
    ? null
    : Math.round(referenceBalance * (referenceRate / 100) * (referenceDays / 365));
  const evaluatedRate = detail.rate_evaluation || {};
  const realizableRate = evaluatedRate.realizable_rate ?? detail.realizable_rate;
  const estimatedInterest = detail.estimated_after_tax_interest ?? detail.estimated_pre_tax_interest;
  const payment = parkingPaymentOverview(detail);
  const advertisedRate = detail.advertised_max_rate == null ? null : formatRate(detail.advertised_max_rate);
  const rateValue = personalized && realizableRate != null ? formatRate(realizableRate) : formatRate(referenceRate);
  const interestValue = personalized && estimatedInterest != null
    ? formatWon(estimatedInterest)
    : referenceInterest == null ? '확인 필요' : `약 ${formatWon(referenceInterest)}`;
  const plan = personalized
    ? [detail.planned_contribution_summary, detail.term_summary]
      .filter((value) => value && !String(value).includes('확인 필요'))
      .join('·')
    : '100만원·30일';
  return `<section class="detail-hero parking-detail-hero">
    <div class="cma-hero-heading"><span>파킹통장 한눈에 보기</span>${detail.listing_as_of ? `<em>${escapeHtml(String(detail.listing_as_of).slice(0, 10))} 기준</em>` : ''}</div>
    <div class="detail-rate-row">
      <div class="detail-rate-card primary"><div class="label">${personalized ? '내 적용 금리' : '100만원 적용 기본 금리'}</div><div class="value">${escapeHtml(rateValue)}</div><div class="sub">${personalized ? '입력한 조건을 반영한 세전 연 금리' : advertisedRate ? `우대조건 충족 시 최고 ${escapeHtml(advertisedRate)}` : '세전 연 금리·잔액에 따라 달라져요'}</div></div>
      <div class="detail-rate-card"><div class="label">${personalized ? '내 예상 이자' : '100만원·30일 예상 이자'}</div><div class="value money">${escapeHtml(interestValue)}</div><div class="sub">${escapeHtml(plan)} 기준·${detail.estimated_after_tax_interest != null ? '세후' : '세전'}</div></div>
      <div class="detail-rate-card"><div class="label">${escapeHtml(payment.label)}</div><div class="value text-value">${escapeHtml(payment.value)}</div><div class="sub">${escapeHtml(payment.sub)}</div></div>
    </div>
  </section>`;
}

function renderParkingPreferentialRules(returnPolicy) {
  return (returnPolicy.preferential_policy?.rules || []).map((rule) => {
    const reward = rule.reward || {};
    const unit = reward.unit === 'PERCENTAGE_POINT' ? '%p' : reward.unit === 'PERCENT' ? '%' : '';
    const rewardText = reward.value == null ? '금리 확인' : `+${reward.value}${unit}`;
    const title = String(rule.title || '우대 조건').trim();
    const scope = rule.application?.principal_scope?.range || {};
    const scopeText = scope.source_text
      || (scope.max_value == null ? '' : `${Number(scope.max_value).toLocaleString('ko-KR')}원 이하 잔액에 적용`);
    const conditionText = [title, scopeText].filter(Boolean).join(' · ');
    return `<div class="rate-node"><div class="rate-node-top"><div><div class="rate-title">${escapeHtml(title)}</div></div><div class="reward">${escapeHtml(rewardText)}</div></div><div class="action-summary"><strong>받는 조건</strong><span>${escapeHtml(conditionText)}</span></div></div>`;
  }).join('');
}

function renderFamilyDetailSection(detail) {
  const policy = detailPolicyBundle(detail);
  const config = PRODUCT_FAMILY_DETAIL[policy.family] || { label: '금융상품', heading: '상품 핵심정보' };
  const rows = familyDetailRows(detail).map(([label, value]) => detailInfoCard(label, value)).join('');
  const feeDisclosure = policy.family === 'CMA' && String(policy.fee.disclosure_text || '').trim()
    ? `<details class="family-policy-disclosure"><summary>수수료 면제 조건 자세히</summary><p>${escapeHtml(policy.fee.disclosure_text)}</p></details>`
    : '';
  const familyBadge = ['CMA', 'PARKING_ACCOUNT'].includes(policy.family) ? '' : `<span class="family-detail-badge">${escapeHtml(config.label)}</span>`;
  return `<section class="detail-section family-detail-section" data-product-family="${escapeHtml(policy.family)}"><div class="detail-section-heading family-detail-heading"><div>${familyBadge}<h3>${escapeHtml(config.heading)}</h3></div></div><div class="detail-info-card-grid">${rows}</div>${familyRateSchedule(detail)}${feeDisclosure}</section>`;
}

function numericRateValue(entry) {
  const value = Number(entry?.calculation?.value);
  return Number.isFinite(value) ? value : null;
}

function cmaRateOverview(detail) {
  const policy = detailPolicyBundle(detail);
  const returnPolicy = policy.returnPolicy;
  const performanceLinked = returnPolicy.return_kind === 'PERFORMANCE_LINKED';
  const protectionStatus = policy.facts.protection_status || policy.protection.coverage_status;
  const protection = detailLabel(protectionStatus, '확인 필요');
  const entries = (returnPolicy.rate_entries || [])
    .filter((entry) => !['PREFERENTIAL', 'ADVERTISED_MAXIMUM'].includes(entry.role))
    .map((entry) => ({ ...entry, numericValue: numericRateValue(entry) }))
    .filter((entry) => entry.numericValue !== null);
  const observations = (returnPolicy.performance_observations || [])
    .filter((entry) => Number.isFinite(Number(entry.value)));
  const latestObservation = observations.at(-1) || null;
  const fixing = detailLabel(returnPolicy.rate_fixing_policy?.kind, performanceLinked ? '운용실적에 따라 변동' : '상품별 확인');
  const asOf = latestObservation?.as_of || entries.map((entry) => entry.as_of).filter(Boolean).sort().at(-1) || detail.listing_as_of || '';

  if (performanceLinked) {
    const window = latestObservation?.observation_window || {};
    const windowUnit = { DAY: '일', WEEK: '주', MONTH: '개월', YEAR: '년' }[window.unit] || '';
    const observationLabel = latestObservation
      ? `최근 ${window.value || ''}${windowUnit} 수익률`.replace('최근  수익률', '최근 수익률')
      : '수익률';
    return {
      performanceLinked,
      headlineLabel: observationLabel,
      headlineValue: latestObservation ? `${latestObservation.value}%` : '실적에 따라 변동',
      headlineSub: latestObservation
        ? `${latestObservation.annualized ? '연환산' : '기간 수익률'} · 확정 수익률 아님`
        : '확정 수익률이 없는 상품',
      fixing,
      protection,
      protectionStatus,
      asOf,
    };
  }

  const values = entries.map((entry) => entry.numericValue);
  if (!values.length && Number.isFinite(Number(detail.base_rate))) values.push(Number(detail.base_rate));
  const min = values.length ? Math.min(...values) : null;
  const max = values.length ? Math.max(...values) : null;
  const maxEntry = entries.find((entry) => entry.numericValue === max);
  const maxScope = normalizedRateScopes(maxEntry?.applies_to).map(rateRangeText).filter(Boolean).join(' · ');
  const headlineValue = min === null ? '확인 필요'
    : min === max ? `${max}%` : `${min}~${max}%`;
  return {
    performanceLinked,
    headlineLabel: min === max ? '현재 기본 수익률' : '보유기간별 기본 수익률',
    headlineValue,
    headlineSub: maxScope ? `최고 ${max}% · ${maxScope}` : fixing.includes('확정') ? '세전 기준 · 입금 시점에 확정' : '세전 기준 · 공시 변경 시 변동 가능',
    fixing,
    protection,
    protectionStatus,
    asOf,
  };
}

function cmaRateForHoldingDays(detail, holdingDays = 30) {
  const entries = (detailPolicyBundle(detail).returnPolicy.rate_entries || [])
    .filter((entry) => !['PREFERENTIAL', 'ADVERTISED_MAXIMUM'].includes(entry.role));
  const matched = entries.find((entry) => normalizedRateScopes(entry.applies_to).some((scope) => {
    if (scope.basis !== 'ELAPSED_TERM' || scope.range?.unit !== 'DAY') return false;
    const min = scope.range.min_value == null ? -Infinity : Number(scope.range.min_value);
    const max = scope.range.max_value == null ? Infinity : Number(scope.range.max_value);
    return holdingDays >= min && holdingDays <= max;
  }));
  return numericRateValue(matched) ?? numericRateValue(entries[0]);
}

function cmaPaymentOverview(detail) {
  const returnPolicy = detailPolicyBundle(detail).returnPolicy;
  const payment = returnPolicy.interest_payment_policy || {};
  const reinvestment = returnPolicy.reinvestment_policy || {};
  const frequency = payment.frequency;
  if (frequency === 'MONTHLY') {
    return {
      label: '이자 받는 때',
      value: `매월${payment.payment_day_of_month ? ` ${payment.payment_day_of_month}일` : ''}`,
      sub: payment.or_at_termination ? '해지할 때도 함께 정산돼요' : '월 지급일은 휴일 등에 따라 달라질 수 있어요',
    };
  }
  if (frequency) {
    return { label: '이자 받는 때', value: detailLabel(frequency), sub: payment.or_at_termination ? '해지할 때도 함께 정산돼요' : '정확한 지급일은 공식 설명을 확인하세요' };
  }
  if (detail.product_id === 'INST-KR-000793-4-0002') {
    return {
      label: '이자 받는 때',
      value: '출금 시 지급',
      sub: '출금 요청 시 이자를 계산해 원금과 함께 지급해요',
    };
  }
  const interval = reinvestment.interval || {};
  const intervalUnit = { DAY: '일', WEEK: '주', MONTH: '개월', YEAR: '년' }[interval.unit] || '';
  const hasDailyCalculation = returnPolicy.calculation_method === 'PER_DEPOSIT_LOT_ELAPSED_TERM'
    || (returnPolicy.rate_entries || []).some((entry) => normalizedRateScopes(entry.applies_to)
      .some((scope) => scope.basis === 'ELAPSED_TERM' && scope.range?.unit === 'DAY'));
  return {
    label: hasDailyCalculation ? '수익 계산 주기' : '이자 받는 때',
    value: hasDailyCalculation ? '매일 계산' : '지급 시점 확인 필요',
    sub: hasDailyCalculation
      ? '실제 지급 시점은 상품별로 달라요'
      : interval.value
        ? `${interval.value}${intervalUnit}마다 자동 재예치되지만 이자 지급일은 별도 확인이 필요해요`
        : '공식 상품 설명에서 이자 지급일을 확인하세요',
  };
}

function cmaRuleTitle(rule) {
  return String(rule?.display?.summary || rule?.title || '추가 수익률 조건')
    .replaceAll('개인고객', '개인 고객')
    .replaceAll('비대면 가입', '모바일·온라인 가입');
}

function cmaPreferentialSummary(detail) {
  const rule = (detailPolicyBundle(detail).returnPolicy.preferential_policy?.rules || [])[0];
  if (!rule) return '';
  const reward = rule.reward || {};
  const unit = reward.unit === 'PERCENTAGE_POINT' ? '%p' : reward.unit === 'PERCENT' ? '%' : '';
  const rewardText = rule.display?.reward_text || (reward.value == null ? '' : `+${reward.value}${unit}`);
  return [cmaRuleTitle(rule), rewardText].filter(Boolean).join(' 시 ');
}

function renderCmaRiskCaution(detail, overview = cmaRateOverview(detail)) {
  const protectionClass = overview.protectionStatus === 'PROTECTED' ? 'protected' : 'not-protected';
  const cautionTitle = overview.performanceLinked
    ? '운용 결과에 따라 수익이 달라져요'
    : overview.protectionStatus === 'PROTECTED'
      ? '예금자보호 대상 CMA예요'
      : '예금자보호 대상이 아니에요';
  const cautionCopy = overview.performanceLinked
    ? '표시된 최근 수익률은 과거 운용 결과이며 앞으로의 수익이나 원금을 보장하지 않습니다.'
    : overview.protectionStatus === 'PROTECTED'
      ? '보호 한도와 적용 범위는 가입 전 공식 상품 설명에서 다시 확인하세요.'
      : '은행 예금과 보호 방식이 다릅니다. 원금 보장 여부와 운용 구조를 가입 전에 확인하세요.';
  return `<section class="cma-caution ${overview.performanceLinked ? 'performance' : protectionClass}">
    <span class="cma-caution-icon" aria-hidden="true">${overview.protectionStatus === 'PROTECTED' ? '✓' : '!'}</span>
    <div><strong>${escapeHtml(cautionTitle)}</strong><p>${escapeHtml(cautionCopy)}</p></div>
  </section>`;
}

function renderCmaCatalogHero(detail, personalized = false) {
  const overview = cmaRateOverview(detail);
  const evaluatedRate = detail.rate_evaluation || {};
  const realizableRate = evaluatedRate.realizable_rate ?? detail.realizable_rate;
  const estimatedInterest = detail.estimated_after_tax_interest ?? detail.estimated_pre_tax_interest;
  const payment = cmaPaymentOverview(detail);
  const referenceDays = 30;
  const referencePrincipal = 1000000;
  const referenceRate = cmaRateForHoldingDays(detail, referenceDays);
  const referenceInterest = referenceRate == null
    ? null
    : Math.round(referencePrincipal * (referenceRate / 100) * (referenceDays / 365));
  const rateLabel = personalized && realizableRate != null ? '내 적용 수익률' : overview.headlineLabel;
  const rateValue = personalized && realizableRate != null ? formatRate(realizableRate) : overview.headlineValue;
  const rateSub = personalized && realizableRate != null ? '입력한 조건을 반영한 세전 연 수익률' : overview.headlineSub;
  const personalizedPlan = [detail.planned_contribution_summary, detail.term_summary]
    .filter((value) => value && !String(value).includes('확인 필요'))
    .join('·');
  const interestLabel = personalized && estimatedInterest != null ? '내 예상 이자' : '100만원·30일 예상 이자';
  const interestValue = personalized && estimatedInterest != null
    ? formatWon(estimatedInterest)
    : overview.performanceLinked ? '계산할 수 없음' : referenceInterest == null ? '확인 필요' : `약 ${formatWon(referenceInterest)}`;
  const interestSub = personalized && estimatedInterest != null
    ? [personalizedPlan, detail.estimated_after_tax_interest != null ? '예상 세후 금액' : '예상 세전 금액']
      .filter(Boolean).join(' 기준 · ')
    : overview.performanceLinked ? '운용실적에 따라 수익이 달라져요'
      : referenceRate == null ? '잔액과 보유 기간을 알면 계산할 수 있어요' : `연 ${referenceRate}%를 단순 적용한 세전 예시`;
  const preferentialSummary = personalized ? '' : cmaPreferentialSummary(detail);
  const displayedRateSub = preferentialSummary ? `${rateSub} · ${preferentialSummary}` : rateSub;
  return `
    <section class="detail-hero cma-detail-hero">
      <div class="cma-hero-heading"><span>CMA 한눈에 보기</span>${overview.asOf ? `<em>${escapeHtml(String(overview.asOf).slice(0, 10))} 기준</em>` : ''}</div>
      <div class="detail-rate-row">
        <div class="detail-rate-card primary"><div class="label">${escapeHtml(rateLabel)}</div><div class="value">${escapeHtml(rateValue)}</div><div class="sub">${escapeHtml(displayedRateSub)}</div></div>
        <div class="detail-rate-card"><div class="label">${escapeHtml(interestLabel)}</div><div class="value money">${escapeHtml(interestValue)}</div><div class="sub">${escapeHtml(interestSub)}</div></div>
        <div class="detail-rate-card"><div class="label">${escapeHtml(payment.label)}</div><div class="value text-value">${escapeHtml(payment.value)}</div><div class="sub">${escapeHtml(payment.sub)}</div></div>
      </div>
    </section>
    ${overview.protectionStatus === 'PROTECTED' && !overview.performanceLinked ? '' : renderCmaRiskCaution(detail, overview)}`;
}

function cmaPreferentialConditionText(rule) {
  const predicates = [];
  const visit = (node) => {
    if (!node || typeof node !== 'object') return;
    if (node.predicate) predicates.push(node.predicate);
    (node.children || []).forEach(visit);
    if (node.child) visit(node.child);
  };
  visit(rule.condition);
  const labels = predicates.map((predicate) => {
    const key = predicate.fact_key;
    const value = predicate.expected_value;
    if (key === 'CUSTOMER.TYPE' && value === 'INDIVIDUAL') return '개인 고객';
    if (key === 'CUSTOMER.TYPE' && value === 'CORPORATE') return '법인 고객';
    if (key === 'SUBSCRIPTION.CHANNEL_MODE' && value === 'NON_FACE_TO_FACE') return '모바일·온라인으로 가입';
    return '';
  }).filter(Boolean);
  if (labels.length) return `${labels.join('이면서 ')}하면 적용돼요.`;
  const summary = String(rule.display?.summary || rule.title || '').trim();
  return summary ? `${summary} 조건을 충족하면 적용돼요.` : '적용 조건은 공식 상품 설명에서 확인해 주세요.';
}

function renderCmaPreferentialRules(returnPolicy) {
  return (returnPolicy.preferential_policy?.rules || []).map((rule) => {
    const reward = rule.reward || {};
    const unit = reward.unit === 'PERCENTAGE_POINT' ? '%p' : reward.unit === 'PERCENT' ? '%' : '';
    const rewardText = rule.display?.reward_text || (reward.value == null ? '수익률 확인' : `+${reward.value}${unit}`);
    const title = cmaRuleTitle(rule);
    return `<div class="rate-node"><div class="rate-node-top"><div><div class="rate-title">${escapeHtml(title)}</div></div><div class="reward">${escapeHtml(rewardText)}</div></div><div class="action-summary"><strong>받는 조건</strong><span>${escapeHtml(cmaPreferentialConditionText(rule))}</span></div></div>`;
  }).join('');
}

function renderDetailDisclaimer() {
  return `<p class="detail-disclaimer">※ 본 정보는 AI가 수집·정리한 참고 정보로, 실제 상품 내용과 다르거나 최신 정보가 아닐 수 있습니다. 가입 전 금융회사의 공식 상품설명서와 약관을 반드시 확인해 주세요.</p>`;
}

function savingsConditionTitle(condition) {
  return String(condition.title || '우대 금리 조건')
    .replace(/\s*[:,，]?\s*연\s*\d+(?:\.\d+)?%\s*$/, '')
    .replaceAll('이 예금 신규 직전', '가입 직전')
    .replaceAll('신규 직전', '가입 직전')
    .replaceAll('신규금액', '가입금액')
    .replaceAll('으로 가입한 가입 직전', '으로 가입하고, 가입 직전')
    .replace(/\s*고객\s*중\s*/g, ' ')
    .replace(/없는 경우\s*$/g, '없으면')
    .trim();
}

function renderSavingsSnapshotCondition(condition) {
  const unit = condition.reward_unit === 'PERCENTAGE_POINT' ? '%p'
    : condition.reward_unit === 'PERCENT' ? '%' : '';
  const reward = condition.reward_value == null ? '조건 확인' : `+${condition.reward_value}${unit}`;
  const title = savingsConditionTitle(condition);
  const method = /(?:하|없으?)면$/.test(title) ? `${title} 적용돼요.` : `${title} 조건을 충족하면 적용돼요.`;
  const raw = String(condition.condition_text || condition.title || '').trim();
  const rawDetail = raw && raw !== title
    ? `<details class="condition-source-detail"><summary>전체 조건 보기</summary><p>${escapeHtml(raw)}</p></details>`
    : '';
  return `<div class="rate-node"><div class="rate-node-top"><div><div class="rate-title">${escapeHtml(title)}</div></div><div class="reward">${escapeHtml(reward)}</div></div><div class="action-summary"><strong>받는 방법</strong><span>${escapeHtml(method)}</span></div>${rawDetail}</div>`;
}

function savingsRelationNotice(relations) {
  if (!(relations || []).length) return '';
  const labels = [];
  if (relations.some((relation) => ['MAX_OF', 'EXCLUSIVE_ONE', 'MUTUALLY_EXCLUSIVE'].includes(relation.operator || relation.type))) {
    labels.push('일부 우대조건은 중복되지 않고 가장 높은 금리 하나만 적용돼요.');
  }
  if (relations.some((relation) => ['SUM_WITH_CAP', 'CUMULATIVE_WITH_GLOBAL_CAP'].includes(relation.operator || relation.type))) {
    labels.push('우대금리를 합산하더라도 상품에 정해진 최고 한도까지만 적용돼요.');
  }
  return labels.map((label) => `<p class="detail-condition-note">${escapeHtml(label)}</p>`).join('');
}

function renderCatalogDetail(detail) {
  const product = detail.product || {};
  const returnPolicy = product.return_policy || {};
  const isCma = (detail.product_type || detail.product_family || product.product_family) === 'CMA';
  const isParking = (detail.product_type || detail.product_family || product.product_family) === 'PARKING_ACCOUNT';
  const isSavings = (detail.product_type || detail.product_family || product.product_family) === 'INSTALLMENT_SAVINGS';
  const conditionSnapshot = product.condition_snapshot || {};
  const eligibility = conditionSnapshot.eligibility || {};
  const preferentialSnapshot = conditionSnapshot.preferential_rate || {};
  const productFacts = conditionSnapshot.product_facts || {};
  const performanceLinked = detail.return_kind === 'PERFORMANCE_LINKED' || returnPolicy.return_kind === 'PERFORMANCE_LINKED';
  const disclosures = performanceLinked ? [] : returnPolicy.preferential_condition_disclosures || [];
  const preferentialEntries = (performanceLinked ? [] : returnPolicy.rate_entries || [])
    .filter((entry) => entry.role === 'PREFERENTIAL');
  const snapshotConditions = performanceLinked ? [] : preferentialSnapshot.conditions || [];
  const uniqueSnapshotConditions = [...new Map(snapshotConditions.map((condition) => [
    `${condition.condition_text || ''}\u0000${condition.reward_value || ''}`,
    condition,
  ])).values()];
  const snapshotRateEntries = uniqueSnapshotConditions.map((condition) => {
    if (isSavings) return renderSavingsSnapshotCondition(condition);
    const unit = condition.reward_unit === 'PERCENTAGE_POINT' ? '%p'
      : condition.reward_unit === 'PERCENT' ? '%' : '';
    const reward = condition.reward_value == null ? '조건 확인' : `+${condition.reward_value}${unit}`;
    const method = condition.condition_text || '공식 상품 페이지에서 적용 방법을 확인해 주세요.';
    return `<div class="rate-node"><div class="rate-node-top"><div><div class="rate-title">${escapeHtml(condition.title || '우대 금리 조건')}</div></div><div class="reward">${escapeHtml(reward)}</div></div><div class="action-summary"><strong>받는 방법</strong><span>${escapeHtml(method)}</span></div></div>`;
  }).join('');
  const disclosureEntries = renderDisclosureEntries(disclosures);
  const structuredRateEntries = preferentialEntries.map((entry) => {
    const calculation = entry.calculation || {};
    const unit = calculation.unit === 'PERCENTAGE_POINT' ? '%p' : calculation.unit === 'PERCENT' ? '%' : '';
    const value = calculation.value != null ? `${calculation.value}${unit}` : '수치 확인 전';
    const title = entry.official_label || entry.condition_text || '우대금리 조건';
    const method = entry.condition_text || entry.official_label || '공식 상품 페이지에서 적용 방법을 확인하세요.';
    return `<div class="rate-node"><div class="rate-node-top"><div><div class="rate-title">${escapeHtml(title)}</div></div><div class="reward">+${escapeHtml(value)}</div></div><div class="action-summary"><strong>받는 방법</strong><span>${escapeHtml(method)}</span></div></div>`;
  }).join('');
  const cmaPreferentialEntries = isCma && !performanceLinked ? renderCmaPreferentialRules(returnPolicy) : '';
  const parkingPreferentialEntries = isParking && !performanceLinked ? renderParkingPreferentialRules(returnPolicy) : '';
  const hasStructuredBenefits = Boolean(cmaPreferentialEntries || parkingPreferentialEntries || snapshotRateEntries || disclosureEntries || structuredRateEntries);
  const noBenefitCopy = isSavings && preferentialSnapshot.status === 'NO_CONDITIONS_DISCLOSED'
    ? '<div class="detail-empty-note">별도 우대금리가 공시되지 않은 상품이에요.</div>'
    : '<div class="detail-empty-note">우대조건을 아직 확인하지 못했습니다. 상단의 공식 상품 페이지에서 최신 조건을 확인해 주세요.</div>';
  const rateEntries = cmaPreferentialEntries || parkingPreferentialEntries || snapshotRateEntries || disclosureEntries || structuredRateEntries || noBenefitCopy;
  const relationNotice = isSavings ? savingsRelationNotice(preferentialSnapshot.relations) : '';
  const eligibilityText = eligibility.display_text || product.eligibility_policy?.eligibility_text || '가입 대상은 공식 상품 페이지에서 확인해 주세요.';
  const eligibilityLabel = isParking
    ? '가입 대상'
    : eligibility.status === 'STRUCTURED' ? '가입 조건' : '가입 조건 원문';
  const cadenceLabels = { DAILY: '매일', WEEKLY: '매주', MONTHLY: '매월', IRREGULAR: '선택형' };
  const termUnitLabels = { DAY: '일', WEEK: '주', MONTH: '개월', YEAR: '년' };
  const optionRows = ((productFacts.contribution_options || []).length ? [] : productFacts.term_options || []).map((option) => {
    const values = (option.available_values || []).join(' · ');
    const unit = termUnitLabels[option.term_unit] || '';
    const frequency = cadenceLabels[option.contribution_frequency] || option.contribution_frequency || '';
    return detailInfoCard(option.label || '가입 옵션', `${values}${unit} · ${frequency}`);
  }).join('');
  const contributionOptionRows = (productFacts.contribution_options || []).map((option) => {
    const frequency = cadenceLabels[option.contribution_frequency] || option.contribution_frequency || '';
    const rule = (option.amount_rules || [])[0] || {};
    const amount = rule.min_value == null ? ''
      : rule.max_value == null ? `회당 ${Number(rule.min_value).toLocaleString()}원 이상`
        : rule.min_value === rule.max_value ? `회당 ${Number(rule.min_value).toLocaleString()}원`
          : `회당 ${Number(rule.min_value).toLocaleString()}~${Number(rule.max_value).toLocaleString()}원`;
    const values = (option.available_values || []).join(' · ');
    const unit = termUnitLabels[option.term_unit] || '';
    return detailInfoCard(option.label || '납입 옵션', [values ? `${values}${unit}` : '', frequency, amount].filter(Boolean).join(' · '));
  }).join('');
  const advertised = detail.advertised_max_rate == null ? '?%' : `${detail.advertised_max_rate}%`;
  const catalogRateHero = performanceLinked
    ? '<div class="detail-rate-card primary"><div class="label">예상 금리</div><div class="value">?%</div></div><div class="detail-rate-card"><div class="label">예상 이자</div><div class="value money">?원</div></div><div class="detail-rate-card"><div class="label">최고 금리</div><div class="value">?%</div><div class="sub">실적배당 상품</div></div>'
    : `<div class="detail-rate-card primary"><div class="label">예상 금리</div><div class="value">?%</div></div><div class="detail-rate-card"><div class="label">예상 이자</div><div class="value money">?원</div></div><div class="detail-rate-card"><div class="label">최고 금리</div><div class="value">${escapeHtml(advertised)}</div></div>`;
  renderDetailHeader(detail);
  els.detailContent.innerHTML = `
    ${isCma ? renderCmaCatalogHero(detail) : isParking ? renderParkingCatalogHero(detail) : isSavings ? renderSavingsCatalogHero(detail) : `<section class="detail-hero">
      <div class="detail-rate-row">
        ${catalogRateHero}
      </div>
    </section>`}
    ${isCma || isParking || isSavings ? '' : `<section class="detail-summary-card">
      <span class="detail-summary-label">한눈에 요약</span>
      <strong>아직 개인 조건을 반영하지 않은 공식 공시 정보예요.</strong>
      <p>상단의 상품명을 누르면 연결된 공식 사이트에서 가입 대상과 최신 조건을 확인할 수 있습니다.</p>
    </section>`}
    ${renderFamilyDetailSection(detail)}
    ${optionRows || contributionOptionRows ? `<section class="detail-section"><div class="detail-section-heading"><div><h3>선택 가능한 가입 옵션</h3></div></div><div class="detail-info-card-grid">${optionRows}${contributionOptionRows}</div></section>` : ''}
    ${isCma && !eligibility.display_text && !product.eligibility_policy?.eligibility_text ? '' : `<section class="detail-section">
      <div class="detail-section-heading"><div><h3>${escapeHtml(eligibilityLabel)}</h3></div></div>
      <div class="detail-summary-card"><p>${escapeHtml(eligibilityText)}</p></div>
    </section>`}
    ${isCma && !hasStructuredBenefits ? '' : `<section class="detail-section detail-section-first">
      <div class="detail-section-heading"><div><h3>${isCma ? '우대 수익률' : '우대 금리'}</h3></div></div>
      ${relationNotice}<div class="rate-tree">${rateEntries}</div>
    </section>`}
    ${renderDetailDisclaimer()}`;
}

async function openDetail(productId, previewMode = false) {
  els.detailBackdrop.classList.remove('hidden');
  els.detailDrawer.classList.remove('hidden');
  // Once a conversation exists, every detail page—including cards from the
  // full browse catalog—uses the session evaluation. Static catalog detail is
  // reserved for the pre-conversation browse state.
  const catalogMode = !state.session;
  const cacheKey = catalogMode ? `catalog:${productId}` : productId;
  const cached = state.detailCache.get(cacheKey);
  if (cached) {
    state.currentDetail = cached;
    if (catalogMode) renderCatalogDetail(cached);
    else renderDetail(cached);
    return;
  }
  els.detailInstitution.textContent = '불러오는 중';
  els.detailProductName.textContent = '상품 상세';
  els.detailProductLink.removeAttribute('href');
  els.detailProductLink.setAttribute('aria-disabled', 'true');
  els.detailInstitutionLogo.hidden = true;
  els.detailLogoPlaceholder.hidden = false;
  els.detailLogoPlaceholder.textContent = '금융';
  els.detailContent.innerHTML = '<div class="skeleton"></div><div class="skeleton" style="margin-top:10px"></div>';
  try {
    const detail = catalogMode
      ? await api(`/catalog/products/${encodeURIComponent(productId)}`)
      : await api(`/search-sessions/${state.session.search_session_id}/recommendations/${encodeURIComponent(productId)}/preview`);
    state.detailCache.set(cacheKey, detail);
    state.currentDetail = detail;
    if (catalogMode) renderCatalogDetail(detail);
    else renderDetail(detail);
  } catch (error) {
    els.detailContent.innerHTML = `<div class="empty-results"><h3>상세정보를 불러오지 못했습니다</h3><p>${escapeHtml(error.message)}</p></div>`;
  }
}

function renderDetail(detail) {
  renderDetailHeader(detail);
  const isCma = detailPolicyBundle(detail).family === 'CMA';
  const isParking = detailPolicyBundle(detail).family === 'PARKING_ACCOUNT';
  const isSavings = detailPolicyBundle(detail).family === 'INSTALLMENT_SAVINGS';
  const evaluatedRate = detail.rate_evaluation || {};
  const cap = detail.rate_cap_adjustment || {};
  const rateState = rateCalculationMessage(detail);
  const rankState = detailRankMessage(detail);
  const performanceLinked = (evaluatedRate.return_kind || detail.return_kind) === 'PERFORMANCE_LINKED';
  const capNote = cap.cap_reduction_pp !== null && cap.cap_reduction_pp !== undefined && Number(cap.cap_reduction_pp) > 0
    ? `<div class="cap-note">우대금리 합계는 +${escapeHtml(cap.pre_cap_total_pp)}%p이지만 상품 한도가 적용되어 최종 우대는 +${escapeHtml(cap.post_cap_total_pp)}%p예요.</div>`
    : '';
  const fallbackConditions = (detail.preferential_conditions || []).filter((item) => !item.disclosure_id).map((item) => {
    const title = item.title || item.condition_type || item.custom_code || '공식 우대조건';
    return `<div class="rate-node"><div class="rate-node-top"><div><div class="rate-title"><span class="rate-status-icon status-UNKNOWN">?</span>${escapeHtml(title)}</div><div class="rate-evidence"><span class="status-chip status-UNKNOWN">확인 필요</span></div></div></div><div class="action-summary"><strong>확인할 것</strong><span>공식 상품 페이지에서 세부 기준과 적용 방법을 확인하세요.</span></div></div>`;
  }).join('');
  const pendingDisclosures = (detail.preferential_conditions || [])
    .filter((item) => item.disclosure_id && item.evaluator_eligible === false);
  const pendingDisclosureEntries = renderDisclosureEntries(pendingDisclosures);
  const conditionNodes = (detail.rate_breakdown || []).map((item) => renderRateNode(item)).join('')
    || fallbackConditions
    || (pendingDisclosureEntries ? '' : '<div class="detail-empty-note">별도로 적용할 우대조건이 없거나 아직 확인되지 않았습니다.</div>');
  const conditionalUpperRate = evaluatedRate.user_specific_conditional_upper_rate;
  const additionalPossibleRate = detail.additional_possible_rate_pp;
  const highestRate = conditionalUpperRate ?? evaluatedRate.realizable_rate ?? detail.realizable_rate ?? detail.advertised_max_rate;
  const highestRateLabel = additionalPossibleRate !== null && additionalPossibleRate !== undefined && Number(additionalPossibleRate) > 0
    ? '확인 전 조건 포함 최고'
    : '공시 최고 금리';
  const rateCards = performanceLinked
    ? '<div class="detail-rate-card primary full"><div class="label">수익 방식</div><div class="value">실적배당 상품</div><div class="sub">운용실적에 따라 수익이 달라져 예상 이자를 표시하지 않아요.</div></div>'
    : `<div class="detail-rate-card primary"><div class="label">예상 금리</div><div class="value">${formatRate(evaluatedRate.realizable_rate ?? detail.realizable_rate)}</div></div><div class="detail-rate-card"><div class="label">예상 이자금</div><div class="value money">${formatWon(detail.estimated_pre_tax_interest)}</div></div><div class="detail-rate-card"><div class="label">${escapeHtml(highestRateLabel)}</div><div class="value">${formatRate(highestRate)}</div></div>`;
  const planParts = [detail.planned_contribution_summary, detail.term_summary].filter(Boolean).join(' · ');
  const eligibilityCopy = {
    SATISFIED: ['현재 조건으로 가입 가능해요', 'ready'],
    ACHIEVABLE: ['조건을 충족하면 가입할 수 있어요', 'plan'],
    UNSATISFIABLE: ['현재 조건으로 가입하기 어려워요', 'blocked'],
    UNKNOWN: ['가입 가능 여부를 확인해 주세요', 'unknown'],
  }[detail.eligibility_status] || ['가입 가능 여부를 확인해 주세요', 'unknown'];
  const summary = detailSummaryCopy(detail);
  const eligibilityDescription = detail.target_customer_summary || rankState.description;
  const calculationCaution = rateState.kind === 'ready'
    ? ''
    : `<p class="detail-eligibility-caution">${escapeHtml(rateState.description)}</p>`;
  const upsideNotice = additionalPossibleRate !== null
    && additionalPossibleRate !== undefined
    && Number(additionalPossibleRate) > 0
    ? `<p class="detail-condition-note">아직 확인 전인 조건을 모두 충족하면 현재 예상금리보다 최대 +${escapeHtml(additionalPossibleRate)}%p 더 받을 수 있어요.</p>`
    : '';

  els.detailContent.innerHTML = `
    ${isCma ? renderCmaCatalogHero(detail, true) : isParking ? renderParkingCatalogHero(detail, true) : isSavings ? renderSavingsCatalogHero(detail, true) : `<section class="detail-hero">
      <div class="detail-rate-row">${rateCards}</div>
      ${planParts ? `<div class="detail-plan-line">${escapeHtml(planParts)} 기준</div>` : ''}
    </section>`}

    <section class="detail-summary-card">
      <span class="detail-summary-label">한눈에 요약</span>
      <strong>${escapeHtml(summary.title)}</strong>
      <div class="detail-summary-breakdown">
        <p><b>추천 이유</b><span>${escapeHtml(summary.reason)}</span></p>
        <p><b>조건 충족 시 가능한 우대금리</b><span>${escapeHtml(summary.reward)}</span></p>
        <p><b>받는 방법</b><span>${escapeHtml(summary.action)}</span></p>
      </div>
    </section>

    ${renderFamilyDetailSection(detail)}

    <section class="detail-eligibility ${escapeHtml(eligibilityCopy[1])}">
      <span class="detail-status-symbol" aria-hidden="true">${eligibilityCopy[1] === 'ready' ? '✓' : eligibilityCopy[1] === 'plan' ? '→' : eligibilityCopy[1] === 'blocked' ? '×' : '?'}</span>
      <div><span class="detail-section-label">가입 가능 여부</span><strong>${escapeHtml(eligibilityCopy[0])}</strong><p>${escapeHtml(eligibilityDescription)}</p>${calculationCaution}</div>
    </section>

    <section class="detail-section detail-section-first">
      <div class="detail-section-heading"><div><h3>${isCma ? '우대 수익률' : '우대 금리'}</h3></div><span class="detail-as-of">기준일 ${escapeHtml(evaluatedRate.rate_as_of || detail.rate_as_of || '확인 전')}</span></div>
      <p class="detail-section-description">조건별 우대금리와 받는 방법을 보여드려요. 검증 전 조건은 '공식 확인 필요'로 표시합니다.</p>
      ${semanticMemosHtml(detail, 'detail-semantic-memos')}
      <div class="rate-legend"><span><i class="status-SATISFIED">✓</i> 적용</span><span><i class="status-ACHIEVABLE">→</i> 받을 수 있음</span><span><i class="status-UNSATISFIABLE">×</i> 적용 불가</span><span><i class="status-UNKNOWN">?</i> 확인 필요</span></div>
      <div class="rate-tree">${conditionNodes}${pendingDisclosureEntries}</div>
      ${upsideNotice}
      ${capNote}
    </section>
    ${renderDetailDisclaimer()}
  `;
}

function closeDetail() {
  els.detailBackdrop.classList.add('hidden');
  els.detailDrawer.classList.add('hidden');
  state.currentDetail = null;
}

function buildDefaultRecommendations(catalog) {
  const items = (catalog?.items || []).map((item) => {
    // In the browse view, preserve the provider's displayed rate snapshot.
    // Personalized recommendations still use the contractual calculation model.
    const advertisedValue = item.listing_advertised_max_rate ?? item.advertised_max_rate;
    const baseValue = item.listing_base_rate ?? item.base_rate;
    const advertised = advertisedValue === null ? NaN : Number(advertisedValue);
    const base = baseValue === null ? NaN : Number(baseValue);
    const hasAdvertised = Number.isFinite(advertised);
    const hasBase = Number.isFinite(base);
    const referenceRate = hasAdvertised ? advertised : hasBase ? base : null;
    return { item, referenceRate, hasAdvertised, baseValue, advertisedValue };
  }).sort((a, b) => {
    if (a.referenceRate === null && b.referenceRate !== null) return 1;
    if (a.referenceRate !== null && b.referenceRate === null) return -1;
    if (a.referenceRate !== b.referenceRate) return (b.referenceRate || 0) - (a.referenceRate || 0);
    const institutionOrder = String(a.item.institution_name || '').localeCompare(
      String(b.item.institution_name || ''), 'ko-KR',
    );
    return institutionOrder || String(a.item.product_name || '').localeCompare(
      String(b.item.product_name || ''), 'ko-KR',
    );
  });
  let previousRate = Symbol('initial-rate');
  let sharedRank = 0;
  const rankedProducts = items.map(({
    item,
    referenceRate,
    hasAdvertised,
    baseValue,
    advertisedValue,
  }, index) => {
    if (referenceRate !== previousRate) {
      sharedRank = index + 1;
      previousRate = referenceRate;
    }
    return {
      rank: sharedRank,
      product_id: item.product_id,
      institution_id: item.institution_id,
      institution_name: item.institution_name,
      institution_sector: item.institution_sector || 'UNKNOWN',
      product_name: item.product_name,
      product_type: item.product_family,
      realizable_rate: null,
      base_rate: baseValue,
      advertised_max_rate: advertisedValue,
      return_kind: item.return_kind,
      published_rate_label: referenceRate === null
        ? null
        : hasAdvertised
          ? (item.listing_provider ? '네이버 표시 최고금리' : '공시 최고금리')
          : (item.listing_provider ? '네이버 표시 기본금리' : '공시 기본금리'),
      published_rate_summary: referenceRate === null ? null : `${rateFmt.format(referenceRate)}%`,
      published_rate_as_of: null,
      term_summary: item.term_summary || '기간 확인 필요',
      contribution_summary: item.contribution_summary || '납입주기 확인 필요',
      maximum_deposit_summary: item.maximum_deposit_summary || '납입금액 확인 필요',
      planned_contribution_summary: item.planned_contribution_summary || '납입금액 확인 필요',
      estimated_total_principal: null,
      estimated_pre_tax_interest: null,
      estimated_after_tax_interest: null,
      ranking_comparability: 'NOT_COMPARABLE',
      eligibility_badge: 'VERIFICATION_REQUIRED',
      verification_badge: 'ADDITIONAL_VERIFICATION_REQUIRED',
      material_unknown_count: Number(item.data_gap_count || 0),
    };
  });
  return {
    preview_mode: true,
    ranking_objective: 'MAX_REALIZABLE_RATE',
    ranked_products: rankedProducts,
    top_products: rankedProducts.slice(0, 7),
  };
}

async function bootstrap() {
  try {
    let catalog;
    [state.runtime, state.initialQuestion, catalog] = await Promise.all([
      api('/runtime'),
      api('/pre-search/initial-question'),
      api('/catalog/products'),
    ]);
    state.defaultRecommendations = buildDefaultRecommendations(catalog);
    // The catalog request is substantially larger than a first conversational
    // search. If the user starts typing before it returns, never let this late
    // bootstrap response replace the session's personalized result list.
    if (!state.session) {
      state.recommendations = state.defaultRecommendations;
      try { sessionStorage.removeItem('ddakrate.activeSearchSessionId'); } catch {}
      renderInitialQuestion();
      renderRecommendations();
    }
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
  } finally {
    focusComposer();
  }
}

els.composer.addEventListener('submit', async (event) => {
  event.preventDefault();
  const message = els.input.value.trim();
  if (!message || state.busy) return;
  const answerExample = state.pendingAnswerExample;
  state.pendingAnswerExample = null;
  els.input.value = '';
  els.input.style.height = '';
  if (!state.session) await startSearch(message, answerExample);
  else await sendFollowup(message, answerExample);
});

els.input.addEventListener('input', () => {
  if (state.pendingAnswerExample) {
    state.pendingAnswerExample.edited = (
      els.input.value !== state.pendingAnswerExample.originalText
    );
  }
  els.input.style.height = 'auto';
  els.input.style.height = `${Math.min(120, els.input.scrollHeight)}px`;
});

els.input.addEventListener('keydown', (event) => {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault();
    els.composer.requestSubmit();
  }
});

els.newChatButton.addEventListener('click', resetConversation);
els.understoodToggle.addEventListener('click', () => {
  const expanded = els.understoodToggle.getAttribute('aria-expanded') === 'true';
  const nextExpanded = !expanded;
  els.understoodToggle.setAttribute('aria-expanded', String(nextExpanded));
  els.understoodContent.hidden = !nextExpanded;
  els.understoodToggleLabel.textContent = expanded
    ? 'AI는 잘 이해했나?'
    : '이해한 내용 접기';
});

els.productFamilyFilter.addEventListener('click', (event) => {
  const button = event.target.closest('button[data-product-type]');
  if (!button || state.busy) return;
  const nextType = button.dataset.productType || '';
  if (state.selectedProductTypes.has(nextType)) state.selectedProductTypes.delete(nextType);
  else state.selectedProductTypes.add(nextType);
  for (const candidate of els.productFamilyFilter.querySelectorAll('button')) {
    const active = state.selectedProductTypes.has(candidate.dataset.productType);
    candidate.setAttribute('aria-pressed', String(active));
    candidate.classList.toggle('active', active);
  }
  state.visibleRecommendationCount = RECOMMENDATION_PAGE_SIZE;
  if (state.recommendations) renderRecommendations();
});

for (const button of [els.firstSectorFilter, els.savingsBankFilter]) {
  button.addEventListener('click', () => {
    const sector = button.dataset.institutionSector;
    const wasActive = state.selectedInstitutionSectors.has(sector);
    state.selectedInstitutionSectors.clear();
    if (!wasActive) state.selectedInstitutionSectors.add(sector);
    for (const candidate of [els.firstSectorFilter, els.savingsBankFilter]) {
      const active = state.selectedInstitutionSectors.has(candidate.dataset.institutionSector);
      candidate.setAttribute('aria-pressed', String(active));
      candidate.classList.toggle('active', active);
    }
    state.visibleRecommendationCount = RECOMMENDATION_PAGE_SIZE;
    if (state.recommendations) renderRecommendations();
  });
}

els.productSearchInput.addEventListener('input', (event) => {
  state.productSearchQuery = String(event.target.value || '')
    .trim()
    .toLocaleLowerCase('ko-KR');
  state.visibleRecommendationCount = RECOMMENDATION_PAGE_SIZE;
  if (state.recommendations) renderRecommendations();
});
els.productSearchToggle.addEventListener('click', () => {
  els.productSearch.classList.add('is-expanded');
  els.productSearchToggle.setAttribute('aria-expanded', 'true');
  requestAnimationFrame(() => els.productSearchInput.focus());
});
els.productSearchInput.addEventListener('keydown', (event) => {
  if (event.key !== 'Escape') return;
  event.preventDefault();
  els.productSearch.classList.remove('is-expanded');
  els.productSearchToggle.setAttribute('aria-expanded', 'false');
  els.productSearchToggle.focus();
});
els.productSearchInput.addEventListener('blur', () => {
  if (els.productSearchInput.value) return;
  els.productSearch.classList.remove('is-expanded');
  els.productSearchToggle.setAttribute('aria-expanded', 'false');
});
els.recommendationSection.addEventListener('scroll', maybeLoadMoreRecommendations, { passive: true });
els.recommendationSection.addEventListener('wheel', maybeLoadMoreRecommendations, { passive: true });
window.addEventListener('resize', () => {
  const totalCount = Math.min(
    (state.recommendations?.ranked_products || state.recommendations?.top_products || []).length,
    100,
  );
  requestAnimationFrame(() => expandRecommendationsForViewport(totalCount));
});
els.institutionPickerButton.addEventListener('click', openInstitutionPicker);
els.institutionPickerClose.addEventListener('click', closeInstitutionPicker);
els.institutionPickerBackdrop.addEventListener('click', closeInstitutionPicker);
els.institutionPickerBody.addEventListener('click', (event) => {
  const button = event.target.closest('button[data-institution-name]');
  if (!button) return;
  const name = button.dataset.institutionName;
  if (state.institutionSelectionDraft.has(name)) state.institutionSelectionDraft.delete(name);
  else state.institutionSelectionDraft.add(name);
  renderInstitutionPicker();
});
els.institutionPickerClear.addEventListener('click', () => {
  state.institutionSelectionDraft.clear();
  renderInstitutionPicker();
});
els.institutionPickerApply.addEventListener('click', () => {
  state.selectedInstitutionNames = new Set(state.institutionSelectionDraft);
  state.visibleRecommendationCount = RECOMMENDATION_PAGE_SIZE;
  closeInstitutionPicker();
  renderRecommendations();
});
els.detailClose.addEventListener('click', closeDetail);
els.detailBackdrop.addEventListener('click', closeDetail);
window.addEventListener('keydown', (event) => {
  if (event.key !== 'Escape') return;
  if (!els.institutionPickerModal.classList.contains('hidden')) closeInstitutionPicker();
  else closeDetail();
});

enableHorizontalDragScroll(els.selectedInstitutionStrip);
initializePaneResizer();
bootstrap();
