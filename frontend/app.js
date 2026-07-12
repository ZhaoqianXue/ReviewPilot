/* ReviewPilot workspace UI.
 *
 * Single-page workspace: project selection, new review setup, and workflow
 * actions update in-place under /workspace. Text entry belongs in dialogs or
 * the assistant panel; the main canvas is reserved for click-based controls.
 */
(function () {
  'use strict';

  const WORKSPACE_SNAPSHOT_KEY = 'reviewpilot.workspace.snapshot.v3';
  const OLD_NEW_REVIEW_WELCOME = 'What are you researching? Describe your research topic in a sentence, and I will draft the Search Setup.';
  const DOUBLE_ESCAPED_NEW_REVIEW_WELCOME_FRAGMENT = 'I&amp;#39;ll guide you through a systematic literature review';
  const DEFAULT_MAX_RESULTS_PER_PLATFORM = '10';
  const TASK_POLL_INTERVAL_MS = 1000;
  const TASK_MAX_POLLS = 1800;
  const RAW_NEW_PROJECT = window.RP_NEW_PROJECT_DATA || window.RP_DATA || {};
  const STARTER_TOPICS = [
    'I want to review how LLMs are used in biomedical research and clinical care.',
    'I want to review how LLMs are changing human-computer interaction.',
    'I want to review how LLMs support urban planning and smart cities.',
  ];
  let D = normalizeData(escapeData(window.RP_DATA || {}));
  const NEW_PROJECT_TEMPLATE = normalizeData(escapeData(RAW_NEW_PROJECT));
  let MAX = maxPlatformValue(D.platforms);
  const initialActionStartedAt = Date.parse(D.activeTask?.created_at || '');

  const state = {
    step: initialStep(D),
    tab: 'fields',
    dialog: '',
    actionError: '',
    activeProjectId: D.project.id || '',
    setupDraft: setupDraftFromData(D),
    keywordDraft: '',
    chatInputFocus: false,
    chatPending: false,
    quickStartOpen: false,
    actionPending: D.activeTask?.action || '',
    actionStartedAt: D.activeTask ? (Number.isNaN(initialActionStartedAt) ? Date.now() : initialActionStartedAt) : 0,
    preservedChatMessages: [],
    catDraft: categorizationDraftFromData(D),
  };
  let actionTicker = null;

  restoreWorkspaceSnapshot();

  function esc(value) {
    return String(value ?? '').replace(/[&<>"']/g, (ch) => ({
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      '"': '&quot;',
      "'": '&#39;',
    }[ch]));
  }

  function unescapePayloadValue(value) {
    return String(value ?? '')
      .replace(/&quot;/g, '"')
      .replace(/&#39;/g, "'")
      .replace(/&lt;/g, '<')
      .replace(/&gt;/g, '>')
      .replace(/&amp;/g, '&');
  }

  function escapeData(value) {
    if (typeof value === 'string') return esc(value);
    if (Array.isArray(value)) return value.map(escapeData);
    if (value && typeof value === 'object') {
      return Object.fromEntries(Object.entries(value).map(([key, val]) => [key, escapeData(val)]));
    }
    return value;
  }

  function readWorkspaceSnapshot() {
    try {
      const raw = sessionStorage.getItem(WORKSPACE_SNAPSHOT_KEY);
      if (!raw) return null;
      const snapshot = JSON.parse(raw);
      if (!snapshot || snapshot.version !== 1 || !snapshot.data || !snapshot.ui) return null;
      return snapshot;
    } catch (_err) {
      return null;
    }
  }

  function restoreWorkspaceSnapshot() {
    const snapshot = readWorkspaceSnapshot();
    if (!snapshot) return;
    const shouldRestoreSnapshotData = shouldRestoreSnapshotDataForRoute(snapshot);
    if (shouldRestoreSnapshotData) {
      D = migrateWorkspaceSnapshotData(snapshot.data);
    }
    MAX = maxPlatformValue(D.platforms);

    const ui = snapshot.ui || {};
    const snapshotProjectId = (snapshot.data && snapshot.data.project && snapshot.data.project.id) || ui.activeProjectId || '';
    const sameProject = !!D.project.id && snapshotProjectId === D.project.id;
    const stepKeys = new Set(D.steps.map((s) => s.key));
    state.step = shouldRestoreSnapshotData && stepKeys.has(ui.step) ? ui.step : initialStep(D);
    state.tab = (shouldRestoreSnapshotData || sameProject) && ui.tab === 'preview' ? 'preview' : 'fields';
    state.dialog = '';
    state.actionError = '';
    state.activeProjectId = D.project.id || (shouldRestoreSnapshotData ? ui.activeProjectId : '') || '';
    const baseDraft = setupDraftFromData(D);
    if ((shouldRestoreSnapshotData || sameProject) && ui.setupDraft) {
      const mergedDraft = { ...baseDraft, ...ui.setupDraft };
      mergedDraft.source_limits = normalizeSourceLimits(
        mergedDraft.source_limits || baseDraft.source_limits,
        mergedDraft.platforms || baseDraft.platforms,
        mergedDraft.max_results || baseDraft.max_results
      );
      state.setupDraft = mergedDraft;
    } else {
      state.setupDraft = baseDraft;
    }
    state.keywordDraft = '';
    state.catDraft = (shouldRestoreSnapshotData || sameProject) && ui.catDraft
      ? { ...categorizationDraftFromData(D), ...ui.catDraft }
      : categorizationDraftFromData(D);
    state.chatInputFocus = false;
  }

  function shouldRestoreSnapshotDataForRoute(snapshot) {
    if (!snapshot || !snapshot.data) return false;
    if (D.activeTask) return false;
    const path = window.location && window.location.pathname ? window.location.pathname : '';
    const isWorkspaceRoute = path === '' || path === '/' || path === '/workspace';
    if (isWorkspaceRoute) return true;
    return D.isNewProject && !!snapshot.data?.isNewProject;
  }

  function writeWorkspaceSnapshot() {
    try {
      sessionStorage.setItem(WORKSPACE_SNAPSHOT_KEY, JSON.stringify({
        version: 1,
        savedAt: Date.now(),
        data: D,
        ui: {
          step: state.step,
          tab: state.tab,
          activeProjectId: state.activeProjectId,
          setupDraft: state.setupDraft,
          catDraft: state.catDraft,
        },
      }));
    } catch (_err) {
      // Storage can be unavailable in private or locked-down browser contexts.
    }
  }

  function migrateWorkspaceSnapshotData(data) {
    const migrated = normalizeData(data);
    const templateSteps = NEW_PROJECT_TEMPLATE.steps || [];
    if (migrated.isNewProject && templateSteps.length > migrated.steps.length) {
      const existingStepsByKey = new Map(migrated.steps.map((step) => [step.key, step]));
      migrated.steps = templateSteps.map((step) => ({
        ...step,
        ...(existingStepsByKey.get(step.key) || {}),
      }));
    }
    if (migrated.isNewProject) {
      migrated.activityByStep = { ...NEW_PROJECT_TEMPLATE.activityByStep, ...migrated.activityByStep };
      migrated.ctxLabels = { ...NEW_PROJECT_TEMPLATE.ctxLabels, ...migrated.ctxLabels };
      migrated.quietLabels = { ...NEW_PROJECT_TEMPLATE.quietLabels, ...migrated.quietLabels };
      migrated.quietActions = { ...NEW_PROJECT_TEMPLATE.quietActions, ...migrated.quietActions };
    }
    const currentWelcome = NEW_PROJECT_TEMPLATE.messages[0] && NEW_PROJECT_TEMPLATE.messages[0].text;
    const firstText = (migrated.messages[0] && migrated.messages[0].text) || '';
    if (
      migrated.isNewProject
      && currentWelcome
      && migrated.messages[0]
      && migrated.messages[0].role === 'a'
      && (firstText === OLD_NEW_REVIEW_WELCOME || firstText.includes(DOUBLE_ESCAPED_NEW_REVIEW_WELCOME_FRAGMENT))
    ) {
      migrated.messages = [{ ...migrated.messages[0], text: currentWelcome }, ...migrated.messages.slice(1)];
    }
    return migrated;
  }

  function normalizeData(data) {
    return {
      isNewProject: !!data.isNewProject,
      activeTask: data.activeTask || null,
      project: data.project || { id: '', title: 'ReviewPilot', status: 'No project', model: '', date: '' },
      researchQuestion: data.researchQuestion || '',
      setup: data.setup || {},
      steps: data.steps || [],
      fields: data.fields || [],
      schemaWorkbench: data.schemaWorkbench || { status: 'missing', primary_action: 'Generate Schema', can_finalize: false },
      optionalCapabilities: data.optionalCapabilities || [],
      platforms: data.platforms || [],
      platformIssues: data.platformIssues || [],
      keywords: data.keywords || [],
      groups: data.groups || [],
      retrieved: data.retrieved || [],
      previewFields: data.previewFields || [],
      previewPaper: data.previewPaper || { title: 'No paper preview available', ref: '', countLabel: '0 / 0' },
      messages: data.messages || [],
      activityByStep: data.activityByStep || {},
      quietLabels: data.quietLabels || {},
      quietActions: data.quietActions || {},
      ctxLabels: data.ctxLabels || {},
      history: data.history || [],
      screeningMetrics: data.screeningMetrics || { identified: 0, afterDedup: 0, included: 0 },
      retrievalSummary: data.retrievalSummary || { retrieved: 0, total: 0, openAccess: 0, viaInstitution: 0, unavailable: 0 },
      categorizationSummary: data.categorizationSummary || { papers: 0, groups: 0 },
      categorizationWorkflow: data.categorizationWorkflow || emptyCategorizationWorkflow(),
      resultOverview: data.resultOverview || [],
      evidenceMatrix: data.evidenceMatrix || [],
      categorizationAnalysis: data.categorizationAnalysis || { field: '', categories: [] },
      exportPackage: data.exportPackage || [],
    };
  }

  function emptyCategorizationWorkflow() {
    return {
      done: false,
      metrics: { papersExtracted: 0, fields: 0 },
      fieldNames: [],
      recommendedField: '',
      selectedField: '',
      mode: 'multiple',
      suggestedCategories: [],
      categoryDescriptions: {},
      fieldProfiles: {},
      analysisDistributions: [],
      categorizedDistribution: { field: '', values: [] },
      fullResults: { columns: [], rows: [] },
    };
  }

  function categorizationDraftFromData(data) {
    const workflow = data.categorizationWorkflow || emptyCategorizationWorkflow();
    const categories = workflow.suggestedCategories || [];
    return {
      field: workflow.selectedField || workflow.recommendedField || (workflow.fieldNames && workflow.fieldNames[0]) || '',
      mode: workflow.mode || 'multiple',
      categoriesText: categories.join('\n'),
      confirmed: !!workflow.done && categories.length > 0,
      skipped: false,
    };
  }

  function maxPlatformValue(platforms) {
    return Math.max(1, ...platforms.map((p) => Number(p[1]) || 0));
  }

  function initialStep(data) {
    return (data.steps.find((s) => s.status === 'active')
      || data.steps.find((s) => s.status !== 'done')
      || data.steps[data.steps.length - 1]
      || { key: 'search' }).key;
  }

  function workflowProgressIndex(steps) {
    const activeIndex = steps.findIndex((s) => s.status === 'active');
    if (activeIndex >= 0) return activeIndex;
    for (let i = steps.length - 1; i >= 0; i -= 1) {
      if (steps[i].status === 'done') return i;
    }
    return 0;
  }

  function formatElapsed(ms) {
    const totalSeconds = Math.max(0, Math.floor((Number(ms) || 0) / 1000));
    const minutes = Math.floor(totalSeconds / 60);
    const seconds = totalSeconds % 60;
    return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
  }

  function setupDraftFromData(data) {
    const sourceNames = data.platforms.map((p) => platformKey(p[0]));
    const setup = data.setup || {};
    const selectedPlatforms = (setup.platforms && setup.platforms.length) ? setup.platforms : (sourceNames.length ? sourceNames : ['pubmed', 'openalex', 'arxiv']);
    const fallbackMaxResults = String(setup.max_results || DEFAULT_MAX_RESULTS_PER_PLATFORM);
    return {
      project_name: setup.project_name || (data.isNewProject ? '' : (data.project.title || '')),
      description: setup.description || data.researchQuestion || '',
      primary_topic: setup.primary_topic || '',
      domain: setup.domain || '',
      search_terms: setup.search_terms || data.keywords[0] || '',
      platforms: selectedPlatforms,
      max_results: fallbackMaxResults,
      source_limits: normalizeSourceLimits(setup.source_limits || {}, selectedPlatforms, fallbackMaxResults),
      date_start: setup.date_start || '',
      date_end: setup.date_end || '',
      keywords: data.keywords.length ? data.keywords.slice(0, 8) : [],
    };
  }

  function sourceLimitValue(sourceLimits, source, fallbackMaxResults) {
    if (sourceLimits && Object.prototype.hasOwnProperty.call(sourceLimits, source)) {
      return String(sourceLimits[source] || fallbackMaxResults || DEFAULT_MAX_RESULTS_PER_PLATFORM);
    }
    return String(fallbackMaxResults || DEFAULT_MAX_RESULTS_PER_PLATFORM);
  }

  function normalizeSourceLimits(sourceLimits, sources, fallbackMaxResults) {
    const limits = {};
    const sourceList = (sources && sources.length) ? sources : ['pubmed', 'openalex', 'arxiv'];
    sourceList.forEach((source) => {
      limits[source] = sourceLimitValue(sourceLimits || {}, source, fallbackMaxResults);
    });
    return limits;
  }

  function selectedSourceLimits() {
    return normalizeSourceLimits(state.setupDraft.source_limits, state.setupDraft.platforms, state.setupDraft.max_results);
  }

  function maxResultsFromSourceLimits(sourceLimits, fallbackMaxResults = DEFAULT_MAX_RESULTS_PER_PLATFORM) {
    const values = Object.values(sourceLimits || {})
      .map((value) => Number(value))
      .filter((value) => Number.isFinite(value) && value > 0);
    return String(values.length ? Math.max(...values) : (fallbackMaxResults || DEFAULT_MAX_RESULTS_PER_PLATFORM));
  }

  function setupPayloadFromDraft(overrides = {}) {
    const sourceLimits = selectedSourceLimits();
    const keywordText = state.setupDraft.keywords.map(unescapePayloadValue).join(' AND ');
    return {
      project_name: unescapePayloadValue(state.setupDraft.project_name || D.project.title || 'Untitled review'),
      description: unescapePayloadValue(state.setupDraft.description || D.researchQuestion || state.setupDraft.search_terms || keywordText || 'Review project'),
      primary_topic: unescapePayloadValue(state.setupDraft.primary_topic || ''),
      domain: unescapePayloadValue(state.setupDraft.domain || ''),
      search_terms: unescapePayloadValue(state.setupDraft.search_terms) || keywordText,
      platforms: state.setupDraft.platforms.join(', '),
      source_limits: sourceLimits,
      max_results: maxResultsFromSourceLimits(sourceLimits, state.setupDraft.max_results),
      date_start: unescapePayloadValue(state.setupDraft.date_start),
      date_end: unescapePayloadValue(state.setupDraft.date_end),
      ...overrides,
    };
  }

  function platformKey(label) {
    return String(label || '').toLowerCase().replace(/\s+/g, '').replace('openalex', 'openalex').replace('arxiv', 'arxiv');
  }

  function platformLabel(key) {
    const labels = { pubmed: 'PubMed', openalex: 'Openalex', arxiv: 'arXiv', ieee: 'IEEE', acm: 'ACM DL', semantic: 'Semantic Scholar' };
    return labels[key] || key.replace(/[_-]/g, ' ').replace(/\b\w/g, (ch) => ch.toUpperCase());
  }

  function setData(data, alreadyEscaped = false, { preserveView = false } = {}) {
    const previousProjectId = D.project.id || '';
    const previousStep = state.step;
    const previousTab = state.tab;
    const nextData = normalizeData(alreadyEscaped ? data : escapeData(data || {}));
    if (state.preservedChatMessages.length) {
      nextData.messages = mergeConversationMessages(state.preservedChatMessages, nextData.messages);
      state.preservedChatMessages = [];
    }
    D = nextData;
    MAX = maxPlatformValue(D.platforms);
    state.activeProjectId = D.project.id || '';
    const sameProject = !!previousProjectId && previousProjectId === D.project.id;
    const stepKeys = new Set(D.steps.map((step) => step.key));
    state.step = preserveView && sameProject && stepKeys.has(previousStep) ? previousStep : initialStep(D);
    state.tab = preserveView && sameProject && ['fields', 'preview'].includes(previousTab) ? previousTab : 'fields';
    state.actionError = '';
    state.setupDraft = setupDraftFromData(D);
    state.catDraft = categorizationDraftFromData(D);
    state.chatInputFocus = false;
    state.quickStartOpen = false;
  }

  function mergeConversationMessages(preservedMessages, incomingMessages) {
    const merged = [];
    const seen = new Set();
    const addMessage = (message) => {
      if (!message || !message.text) return;
      const key = `${message.step || 1}|${message.role || ''}|${message.text}`;
      if (seen.has(key)) return;
      seen.add(key);
      merged.push(message);
    };
    preservedMessages.forEach(addMessage);
    incomingMessages.forEach(addMessage);
    return merged;
  }

  async function fetchProjectState(projectId) {
    const res = await fetch(`/projects/${encodeURIComponent(projectId)}/state`);
    if (!res.ok) throw new Error(`State refresh failed: ${res.status}`);
    return res.json();
  }

  async function selectProject(projectId) {
    setData(await fetchProjectState(projectId));
  }

  async function postAction(action, payload = null) {
    const projectId = state.activeProjectId || D.project.id;
    if (!projectId || D.isNewProject) return;
    if (action === 'collect') await saveDraftSetup(projectId);
    const options = payload
      ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }
      : { method: 'POST' };
    const res = await fetch(`/projects/${encodeURIComponent(projectId)}/actions/${action}`, options);
    if (!res.ok) {
      const body = await res.json().catch(() => null);
      const detail = body && typeof body.detail === 'string' && body.detail.trim()
        ? body.detail : `Action failed: ${res.status}`;
      throw new Error(detail);
    }
    const task = await res.json();
    await waitForTask(task.task_id);
    setData(await fetchProjectState(projectId), false, { preserveView: true });
  }

  async function createProject(form) {
    if (form) updateDraftFromForm(form);
    await createProjectFromPayload(setupPayloadFromDraft());
    state.dialog = '';
  }

  async function createProjectFromChat(message) {
    const sourceLimits = selectedSourceLimits();
    await createProjectFromPayload(setupPayloadFromDraft({
      project_name: projectNameFromTopic(message),
      description: message,
      primary_topic: '',
      domain: '',
      source_limits: sourceLimits,
      max_results: maxResultsFromSourceLimits(sourceLimits, state.setupDraft.max_results),
      derive_search_terms: true,
    }));
  }

  async function createProjectFromPayload(payload) {
    const res = await fetch('/projects', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || `Project creation failed: ${res.status}`);
    }
    const project = await res.json();
    setData(await fetchProjectState(project.id));
  }

  async function sendProjectChat(message) {
    const projectId = state.activeProjectId || D.project.id;
    if (!projectId) throw new Error('Project chat requires an active project.');
    const res = await fetch(`/projects/${encodeURIComponent(projectId)}/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message, step: state.step }),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || `Project chat failed: ${res.status}`);
    }
    const payload = await res.json();
    setData(payload.state || await fetchProjectState(projectId), false, { preserveView: true });
  }

  async function updateProjectSetup(form) {
    const projectId = state.activeProjectId || D.project.id;
    if (!projectId) return;
    if (form) updateDraftFromForm(form);
    await saveDraftSetup(projectId);
    setData(await fetchProjectState(projectId));
    state.dialog = '';
  }

  async function saveDraftSetup(projectId) {
    const res = await fetch(`/projects/${encodeURIComponent(projectId)}/setup`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(setupPayloadFromDraft()),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || `Setup update failed: ${res.status}`);
    }
  }

  async function waitForTask(taskId) {
    for (let i = 0; i < TASK_MAX_POLLS; i += 1) {
      const res = await fetch(`/tasks/${encodeURIComponent(taskId)}`);
      if (!res.ok) throw new Error(`Task lookup failed: ${res.status}`);
      const task = await res.json();
      if (task.status === 'completed') return task;
      if (task.status === 'failed') throw new Error(task.error || 'Task failed');
      await new Promise((resolve) => setTimeout(resolve, TASK_POLL_INTERVAL_MS));
    }
    throw new Error('Task did not finish within 30 minutes. Refresh the project before retrying.');
  }

  function appendMessage(role, text) {
    const currentStep = D.steps.find((step) => step.key === state.step);
    D.messages = [...D.messages, { step: currentStep ? currentStep.n : 1, role, text: esc(text) }];
  }

  async function handleChatSubmit(text) {
    const message = String(text || '').trim();
    if (!message) return;
    appendMessage('u', message);
    state.preservedChatMessages = D.messages.slice();
    state.chatPending = true;
    try {
      if (D.isNewProject) {
        await createProjectFromChat(message);
        return;
      }
      await sendProjectChat(message);
    } finally {
      state.chatPending = false;
      state.preservedChatMessages = [];
    }
  }

  function projectNameFromTopic(topic) {
    const words = String(topic || '')
      .replace(/[^\w\s-]/g, ' ')
      .replace(/\b(i|we|want|to|do|a|an|the|survey|review|study|of|for|in|terms|project|give|me)\b/gi, ' ')
      .replace(/\s+/g, ' ')
      .trim()
      .split(' ')
      .filter(Boolean)
      .slice(0, 5);
    if (!words.length) return 'Untitled review';
    return words.map((word) => word.charAt(0).toUpperCase() + word.slice(1)).join(' ');
  }

  function computeVals() {
    const steps = D.steps;
    const step = state.step;
    const cur = steps.find((s) => s.key === step) || steps[0] || { n: 1, key: 'search', label: 'ReviewPilot' };
    const N = cur.n || 1;
    const progressIndex = workflowProgressIndex(steps);
    const allFields = D.fields.map((f, i) => ({
      idx: String(i + 1).padStart(2, '0'),
      name: f[0],
      type: f[1],
      desc: f[2],
      req: f[3],
      reqNot: !f[3],
    }));
    const platformIssueByLabel = new Map();
    (D.platformIssues || []).forEach((issue) => {
      if (issue.label) platformIssueByLabel.set(String(issue.label).toLowerCase(), issue);
      if (issue.platform) platformIssueByLabel.set(platformLabel(issue.platform).toLowerCase(), issue);
    });
    const platforms = D.platforms.map((p) => ({
      k: p[0],
      v: p[1],
      pct: Math.round(((Number(p[1]) || 0) / MAX) * 100),
      issue: platformIssueByLabel.get(String(p[0]).toLowerCase()) || null,
    }));
    const draftSources = ['pubmed', 'openalex', 'arxiv'].map((key) => ({
      key,
      label: platformLabel(key),
      selected: state.setupDraft.platforms.includes(key),
    }));
    const retrievalPct = D.retrievalSummary.total
      ? Math.max(0, Math.min(100, Math.round((D.retrievalSummary.retrieved / D.retrievalSummary.total) * 100)))
      : 0;
    const activeAction = D.quietActions[step] || '';
    const canvasActionPending = !!state.actionPending && state.actionPending === activeAction;
    const canvasActionElapsedLabel = canvasActionPending && state.actionStartedAt
      ? formatElapsed(Date.now() - state.actionStartedAt)
      : '';
    const activity = canvasActionPending
      ? [{ t: 'now', tag: 'running', msg: `${D.quietLabels[step] || 'Workflow action'} in progress${canvasActionElapsedLabel ? ` · ${canvasActionElapsedLabel}` : ''}` }, ...(D.activityByStep[step] || [])]
      : (D.activityByStep[step] || []);
    const activityMessages = activityMessagesForVisibleSteps(steps, progressIndex, step, activity);
    const chatMessages = conversationMessagesWithActivity(
      D.messages.filter((m) => m.step <= N),
      activityMessages,
      N
    );

    return {
      project: D.project,
      researchQuestion: D.researchQuestion,
      steps: steps.map((s, i, arr) => ({
        ...draftStep(s),
        canView: i <= progressIndex,
        active: s.key === step,
        notActive: s.key !== step,
        isDone: s.status === 'done',
        isActive: s.status === 'active',
        isTodo: s.status === 'todo',
        noLeft: i === 0,
        leftNavy: i > 0 && arr[i - 1].status === 'done',
        leftGray: i > 0 && arr[i - 1].status !== 'done',
        noRight: i === arr.length - 1,
        rightNavy: i < arr.length - 1 && s.status === 'done',
        rightGray: i < arr.length - 1 && s.status !== 'done',
      })),
      stepTitle: cur.label,
      isSearch: step === 'search',
      isScreening: step === 'screening',
      isRetrieval: step === 'retrieval',
      isExtraction: step === 'extraction',
      isCategorize: step === 'categorize',
      isNewProject: D.isNewProject,
      allFields,
      schemaWorkbench: D.schemaWorkbench,
      platforms,
      platformIssues: D.platformIssues,
      draftSources,
      keywords: step === 'search' ? state.setupDraft.keywords : D.keywords,
      groups: D.groups,
      retrieved: D.retrieved,
      previewFields: D.previewFields,
      previewPaper: D.previewPaper,
      chat: chatMessages,
      chatPending: state.chatPending,
      quickStartOpen: state.quickStartOpen,
      assistantContext: D.ctxLabels[step] || '',
      isFieldsTab: state.tab === 'fields',
      notFieldsTab: state.tab !== 'fields',
      isPreviewTab: state.tab === 'preview',
      notPreviewTab: state.tab !== 'preview',
      showCanvasAction: !D.isNewProject && step !== 'categorize' && step !== 'extraction' && !!D.quietLabels[step],
      canvasActionLabel: D.quietLabels[step] || '',
      canvasActionName: D.quietActions[step] || '',
      canvasActionPending,
      canvasActionElapsedLabel,
      historyGroups: historyGroupsForView(D.history),
      actionError: state.actionError,
      screeningMetrics: D.screeningMetrics,
      retrievalSummary: D.retrievalSummary,
      retrievalDashOffset: (144.5 - ((144.5 * retrievalPct) / 100)).toFixed(1),
      categorizationSummary: D.categorizationSummary,
      categorizationWorkflow: D.categorizationWorkflow,
      catDraft: state.catDraft,
      resultOverview: D.resultOverview,
      evidenceMatrix: D.evidenceMatrix,
      categorizationAnalysis: D.categorizationAnalysis,
      exportPackage: D.exportPackage,
      setupDraft: state.setupDraft,
      showSetupDialog: state.dialog === 'setup',
      showKeywordDialog: state.dialog === 'keyword',
    };
  }

  function draftStep(step) {
    if (!D.isNewProject) return step;
    if (step.key === 'search') return { ...step, sub: `${state.setupDraft.platforms.length} sources` };
    return step;
  }

  function activityMessagesForVisibleSteps(steps, progressIndex, activeStep, activeActivity) {
    return steps
      .map((step, index) => {
        if (index > progressIndex) return null;
        const lines = (step.key === activeStep ? activeActivity : (D.activityByStep[step.key] || []))
          .filter((line) => shouldRenderActivityLine(line, step.status));
        if (!lines.length) return null;
        return {
          step: step.n || index + 1,
          role: 'activity',
          activityTitle: step.label,
          activity: lines,
          isActivity: true,
        };
      })
      .filter(Boolean);
  }

  function shouldRenderActivityLine(line, stepStatus) {
    const tag = String(line && line.tag || '').toLowerCase();
    const msg = String(line && line.msg || '').toLowerCase();
    if (tag === 'running') return true;
    if (msg.includes('waiting for')) return false;
    if (stepStatus !== 'done' && /\b0\s+(records|included|pdfs|fields|groups|papers)\b/.test(msg)) return false;
    return !!String(line && line.msg || '').trim();
  }

  function conversationMessagesWithActivity(messages, activityMessages, currentStepNumber) {
    const activitiesByStep = new Map();
    activityMessages
      .filter((message) => message.step <= currentStepNumber)
      .forEach((message) => activitiesByStep.set(message.step, message));

    const combined = [];
    const emittedActivitySteps = new Set();
    const visibleMessages = messages.map((m) => ({ ...m, isAssistant: m.role === 'a', isUser: m.role === 'u' }));
    let highestSeenStep = 0;
    const emitActivitiesBefore = (step) => {
      activityMessages.forEach((message) => {
        if (message.step > currentStepNumber || message.step >= step || message.step <= highestSeenStep || emittedActivitySteps.has(message.step)) return;
        combined.push(message);
        emittedActivitySteps.add(message.step);
      });
    };
    visibleMessages.forEach((message, index) => {
      const step = message.step || 1;
      emitActivitiesBefore(step);
      if (step > 1 && activitiesByStep.has(step) && !emittedActivitySteps.has(step)) {
        combined.push(activitiesByStep.get(step));
        emittedActivitySteps.add(step);
      }
      combined.push(message);
      const nextStep = visibleMessages[index + 1] && (visibleMessages[index + 1].step || 1);
      if (step === 1 && nextStep !== step && activitiesByStep.has(step) && !emittedActivitySteps.has(step)) {
        combined.push(activitiesByStep.get(step));
        emittedActivitySteps.add(step);
      }
      if (nextStep !== step) highestSeenStep = Math.max(highestSeenStep, step);
    });

    activityMessages.forEach((message) => {
      if (message.step > currentStepNumber || emittedActivitySteps.has(message.step)) return;
      combined.push(message);
    });
    return combined;
  }

  const logo = (sz) =>
    `<svg viewBox="0 0 100 100" width="${sz}" height="${sz}" fill="none"><path d="M49 62 L15 52 L15 74 L49 82 Z" fill="#1a365d"></path><path d="M51 62 L85 52 L85 74 L51 82 Z" fill="#1a365d" opacity="0.5"></path><line x1="64" y1="50" x2="75" y2="61" stroke="#1a365d" stroke-width="8.5" stroke-linecap="round"></line><circle cx="50" cy="36" r="21" fill="#fffefc" stroke="#1a365d" stroke-width="8"></circle><path d="M50 22 L59 48 L50 42 L41 48 Z" fill="#1a365d"></path></svg>`;

  const sourceRow = (p) => {
    const issue = p.issue;
    return `<div style="padding:5px 0;">
      <div style="display:flex;align-items:center;gap:12px;"><span style="width:130px;flex:0 0 130px;font-size:12.5px;color:#1a1a1a;">${p.k}</span><span style="flex:1;height:5px;background:#eef0ee;border-radius:999px;overflow:hidden;"><span style="display:block;height:100%;width:${p.pct}%;background:${issue ? '#b45309' : '#1a365d'};"></span></span><span style="width:32px;text-align:right;font-family:'IBM Plex Mono',monospace;font-size:11.5px;color:#6b746c;">${p.v}</span></div>
      ${issue ? `<div data-ui="source-warning" style="margin:7px 0 0 130px;border:1px solid #f1d39b;background:#fff8e8;color:#7a4b00;border-radius:8px;padding:7px 9px;font-size:11.5px;line-height:1.35;"><strong>Platform issue</strong>: ${issue.message}</div>` : ''}
    </div>`;
  };

  const buttonStyle = 'border:1px solid #c8d8e8;background:#fffefc;color:#1a365d;border-radius:9px;padding:8px 11px;font:inherit;font-size:12px;cursor:pointer;display:inline-flex;align-items:center;gap:7px;';

  function historyGroupsForView(history) {
    const groups = (history.length ? history : [{ label: 'Historys', items: [] }]).map((gr) => ({
      ...gr,
      label: gr.label === 'Projects' ? 'Historys' : gr.label,
      items: gr.items.map((it) => ({ ...it, notActive: !it.active })),
    }));
    const first = groups[0] || { label: 'Historys', items: [] };
    if (first.items.some((item) => item.isNewProject)) return groups;
    const draft = { id: '', title: 'Untitled review', active: D.isNewProject, notActive: !D.isNewProject, isNewProject: true };
    return [{ ...first, label: 'Historys', items: [draft, ...first.items] }, ...groups.slice(1)];
  }

  function newProjectDataWithCurrentHistory() {
    return normalizeData(JSON.parse(JSON.stringify(NEW_PROJECT_TEMPLATE)));
  }

  function sourceChecklist(sources, sourceLimits, fallbackMaxResults) {
    return `<div data-ui="source-checklist" style="display:flex;flex-direction:column;gap:8px;">
      ${sources.map((source) => {
        const value = esc(sourceLimitValue(sourceLimits, source.key, fallbackMaxResults));
        return `<div data-ui="source-row" style="display:grid;grid-template-columns:minmax(0,1fr) auto;gap:8px;align-items:center;">
          <button type="button" data-act="toggle-source" data-source="${source.key}" role="checkbox" aria-checked="${source.selected ? 'true' : 'false'}" style="justify-self:start;display:inline-flex;align-items:center;gap:7px;border:1px solid ${source.selected ? '#1a365d' : '#c8d8e8'};background:${source.selected ? '#eef4fb' : '#fffefc'};color:#1a365d;border-radius:999px;padding:7px 11px;font:inherit;font-size:12.5px;cursor:pointer;">
            <span data-ui="source-check-circle" style="width:14px;height:14px;border-radius:999px;border:1px solid #1a365d;background:${source.selected ? '#eaf0f7' : '#fffefc'};display:inline-flex;align-items:center;justify-content:center;color:#1a365d;box-sizing:border-box;">${source.selected ? '<i class="ph ph-check" style="font-size:9px;"></i>' : ''}</span>
            <span>${source.label}</span>
          </button>
          <label style="display:flex;align-items:center;gap:8px;justify-content:flex-end;color:${source.selected ? '#6b746c' : '#b4bbb2'};font-size:11px;letter-spacing:-0.01em;">
            <span style="white-space:nowrap;">Max results/platform</span>
            <input data-source-limit="${source.key}" type="number" min="0" inputmode="numeric" value="${value}" ${source.selected ? '' : 'disabled'} style="width:58px;border:1px solid ${source.selected ? '#c8d8e8' : '#e0e4df'};border-radius:8px;background:${source.selected ? '#fffefc' : '#f7f8f6'};color:${source.selected ? '#1a365d' : '#aab1a9'};font:inherit;font-family:'IBM Plex Mono',monospace;font-size:11.5px;padding:5px 7px;box-sizing:border-box;">
          </label>
        </div>`;
      }).join('')}
    </div>`;
  }

  function dateRangeCard(setupDraft) {
    const start = esc(setupDraft.date_start || '2020-01-01');
    const end = esc(setupDraft.date_end || '');
    return `<div data-ui="date-range-card" style="border:1px solid #e5e7eb;border-radius:12px;padding:16px 18px;height:100%;box-sizing:border-box;display:flex;flex-direction:column;gap:11px;">
        <div style="display:flex;align-items:center;justify-content:space-between;gap:10px;">
          <div style="font-size:10px;letter-spacing:0.06em;text-transform:uppercase;color:#9aa39b;">Date range</div>
          <span data-ui="date-range-summary" style="display:inline-flex;align-items:center;max-width:180px;border:1px solid #c8d8e8;background:#eef4fb;color:#1a365d;border-radius:999px;padding:5px 9px;font-family:'IBM Plex Mono',monospace;font-size:10.5px;line-height:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${start} to ${end || 'present'}</span>
        </div>
        <div data-ui="date-range-inputs" style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;">
          <label data-ui="date-range-field" style="display:block;min-height:58px;box-sizing:border-box;border:1px solid #eef0ee;border-radius:10px;background:#fbfcfa;padding:9px 10px;font-size:10px;letter-spacing:0.06em;text-transform:uppercase;color:#9aa39b;">From
            <input data-draft-field="date_start" value="${start}" placeholder="2020-01-01" style="display:block;width:100%;box-sizing:border-box;margin-top:6px;border:none;background:transparent;padding:0;font:inherit;font-family:'IBM Plex Mono',monospace;font-size:12px;letter-spacing:0;color:#1a1a1a;outline:none;">
          </label>
          <label data-ui="date-range-field" style="display:block;min-height:58px;box-sizing:border-box;border:1px solid #eef0ee;border-radius:10px;background:#fbfcfa;padding:9px 10px;font-size:10px;letter-spacing:0.06em;text-transform:uppercase;color:#9aa39b;">To
            <input data-draft-field="date_end" value="${end}" placeholder="blank=now" style="display:block;width:100%;box-sizing:border-box;margin-top:6px;border:none;background:transparent;padding:0;font:inherit;font-family:'IBM Plex Mono',monospace;font-size:12px;letter-spacing:0;color:#1a1a1a;outline:none;">
          </label>
        </div>
        <div data-ui="date-range-note" style="margin-top:auto;border-top:1px solid #eef0ee;padding-top:8px;font-size:11px;color:#6b746c;line-height:1.35;letter-spacing:-0.01em;">Blank end date uses the current day.</div>
      </div>`;
  }

  function workspaceResponsiveStyle() {
    return `@keyframes rp-thinking-bounce { 0%, 80%, 100% { transform: translateY(0); opacity: .42; } 40% { transform: translateY(-3px); opacity: 1; } } @keyframes rp-action-spin { to { transform: rotate(360deg); } }
@media (max-width: 760px) {
  body { overflow:auto !important; }
  #app { height:auto !important; min-height:100vh !important; }
  .rp-shell { flex-direction:column !important; height:auto !important; min-height:100vh !important; overflow:visible !important; }
  .rp-sidebar { display:none !important; }
  .rp-main { width:100% !important; flex:0 0 auto !important; min-height:58vh !important; }
  .rp-main-scroll { overflow:visible !important; padding:14px 12px !important; }
  .rp-workspace-header > div:first-child { padding:12px 12px 0 !important; }
  .rp-stepper { padding:12px 10px 10px !important; overflow-x:auto !important; }
  .rp-stepper > div { min-width:132px !important; }
  .rp-final-overview { grid-template-columns:repeat(2,minmax(0,1fr)) !important; }
  .rp-category-grid { grid-template-columns:1fr !important; }
  .rp-evidence-head { display:none !important; }
  .rp-evidence-row { grid-template-columns:24px minmax(0,1fr) !important; gap:7px !important; }
  .rp-evidence-row > span:nth-child(n+3) { grid-column:2 !important; }
  .rp-assistant { width:100% !important; flex:0 0 auto !important; min-height:360px !important; height:42vh !important; border-left:none !important; border-top:1px solid #e5e7eb !important; }
}`;
  }

  function render(v) {
    return `
${v.showSetupDialog ? setupDialog(v) : ''}
${v.showKeywordDialog ? keywordDialog(v) : ''}
<style>${workspaceResponsiveStyle()}</style>
<div class="rp-shell" style="width:100vw;height:100vh;background:#fffefc;color:#1a1a1a;font-family:'Hanken Grotesk',system-ui,sans-serif;font-weight:400;letter-spacing:-0.01em;display:flex;overflow:hidden;border:none;border-radius:0;">
  <aside class="rp-sidebar" style="width:175px;flex:0 0 175px;border-right:1px solid #e5e7eb;display:flex;flex-direction:column;min-height:0;">
    <div style="display:flex;align-items:center;gap:8px;padding:13px 10px 8px 12px;">
      <div title="ReviewPilot · Workspace" style="display:flex;align-items:center;gap:8px;min-width:0;">
        ${logo(22)}
        <span style="font-weight:600;font-size:15px;letter-spacing:-0.03em;color:#1a365d;">ReviewPilot</span>
      </div>
    </div>
    <div style="padding:4px 10px 10px;">
      <button data-act="new-project" style="display:flex;align-items:center;justify-content:center;gap:6px;width:100%;background:#fffefc;border:1px solid #d8e2f0;border-radius:10px;padding:9px 8px;font-size:12.5px;font-family:inherit;color:#1a365d;letter-spacing:-0.02em;cursor:pointer;transition:background .15s ease;white-space:nowrap;" data-hover="background:#eef4fb;"><i class="ph ph-plus" style="font-size:14px;flex:0 0 auto;"></i>New Review</button>
    </div>
      <div class="rp-scroll" style="flex:1;min-height:0;overflow-y:auto;padding:2px 10px 12px;">
      ${v.historyGroups.map((g) => `
        <div data-ui="history-label" style="font-size:10px;letter-spacing:0.04em;color:#9aa39b;padding:11px 8px 5px;">${g.label}</div>
        ${g.items.map((h) => projectNavItem(h)).join('')}
      `).join('')}
    </div>
    <div style="flex:0 0 auto;border-top:1px solid #eef0ee;padding:7px 10px 9px;display:flex;flex-direction:column;gap:1px;">
      <div style="display:flex;align-items:center;gap:11px;padding:9px 10px;border-radius:9px;cursor:pointer;font-size:13px;color:#6b746c;transition:background .12s ease;" data-hover="background:#eef4fb;color:#1a365d;"><i class="ph ph-gear-six" style="font-size:16px;"></i>Settings</div>
      <div style="display:flex;align-items:center;gap:11px;padding:9px 10px;border-radius:9px;cursor:pointer;font-size:13px;color:#6b746c;transition:background .12s ease;" data-hover="background:#eef4fb;color:#1a365d;"><i class="ph ph-question" style="font-size:16px;"></i>Help &amp; support</div>
    </div>
  </aside>

  <main class="rp-main" style="flex:1;min-width:0;display:flex;flex-direction:column;">
    ${workspaceHeader(v)}
    <div class="rp-scroll rp-main-scroll" style="flex:1;min-height:0;overflow-y:auto;padding:20px 22px;">
      ${actionErrorBanner(v)}
      ${v.isSearch ? searchCanvas(v) : ''}
      ${v.isScreening ? screeningCanvas(v) : ''}
      ${v.isRetrieval ? retrievalCanvas(v) : ''}
      ${v.isExtraction ? extractionCanvas(v) : ''}
      ${v.isCategorize ? categorizeCanvas(v) : ''}
      ${canvasActionButton(v)}
    </div>
  </main>
  ${assistantPanel(v)}
</div>`;
  }

  function projectNavItem(h) {
    const activeStyle = h.active ? 'background:#eaf0f7;' : 'transition:background .12s ease;';
    const icon = h.active ? 'ph-fill ph-chat-circle' : 'ph ph-chat-circle';
    const color = h.active ? '#1a365d' : '#9aa39b';
    const actionAttrs = h.isNewProject ? `data-act="new-project"` : (h.id ? `data-act="project" data-project="${h.id}"` : '');
    const clickable = h.isNewProject || h.id;
    return `<div ${actionAttrs} style="display:flex;align-items:center;gap:9px;padding:8px 10px;border-radius:9px;cursor:${clickable ? 'pointer' : 'default'};${activeStyle}" data-hover="background:#eef4fb;">
      <i class="${icon}" style="font-size:15px;color:${color};flex:0 0 auto;"></i>
      <span style="font-size:13px;letter-spacing:-0.01em;color:${h.active ? '#1a365d' : '#1a1a1a'};white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${h.title}</span>
    </div>`;
  }

  function workspaceHeader(v) {
    return `<div class="rp-workspace-header" style="border-bottom:1px solid #e5e7eb;flex:0 0 auto;">
      <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:16px;padding:13px 22px 0;">
        <div style="display:flex;align-items:baseline;gap:13px;flex-wrap:wrap;min-width:0;">
          <div style="font-family:Newsreader,Georgia,serif;font-weight:400;font-size:19px;letter-spacing:-0.01em;color:#1a1a1a;line-height:1.15;white-space:nowrap;">${v.project.title}</div>
          <div style="display:flex;align-items:center;gap:9px;font-size:11.5px;color:#6b746c;letter-spacing:-0.01em;">
            <span style="display:inline-flex;align-items:center;gap:6px;"><span style="width:6px;height:6px;border-radius:999px;background:#1a365d;"></span>${v.project.status}</span>
            <span style="color:#cdd5e0;">·</span>
            <span style="display:inline-flex;align-items:center;gap:5px;font-family:'IBM Plex Mono',monospace;"><i class="ph ph-cpu" style="font-size:13px;color:#9aa39b;"></i>${v.project.model}</span>
            <span style="color:#cdd5e0;">·</span>
            <span style="display:inline-flex;align-items:center;gap:5px;"><i class="ph ph-calendar-blank" style="font-size:13px;color:#9aa39b;"></i>${v.project.date}</span>
          </div>
        </div>
      </div>
      <div class="rp-stepper" style="display:flex;align-items:flex-start;padding:14px 20px 12px;">
        ${v.steps.map((s) => stepItem(s)).join('')}
      </div>
    </div>`;
  }

  function stepItem(s) {
    const actionAttrs = s.canView ? `data-act="step" data-step="${s.key}"` : `data-step="${s.key}" data-disabled="true" aria-disabled="true" title="${s.label} is not available yet"`;
    const stepStyle = `position:relative;flex:1;display:flex;flex-direction:column;cursor:${s.canView ? 'pointer' : 'default'};padding:0 6px 12px;transition:opacity .12s ease;opacity:${s.canView ? '1' : '.48'};`;
    const hoverAttr = s.canView ? 'data-hover="opacity:0.74;"' : '';
    return `<div ${actionAttrs} style="${stepStyle}" ${hoverAttr}>
      <div style="display:flex;align-items:center;height:40px;">
        ${s.noLeft ? '<span style="flex:1;"></span>' : ''}
        ${s.leftNavy ? '<span style="flex:1;height:2px;background:#1a365d;"></span>' : ''}
        ${s.leftGray ? '<span style="flex:1;height:2px;background:#e3e8ef;"></span>' : ''}
        <span style="flex:0 0 auto;display:flex;align-items:center;justify-content:center;margin:0 7px;">
          ${s.isDone ? '<i class="ph-fill ph-check-circle" style="font-size:19px;color:#1a365d;"></i>' : ''}
          ${s.isActive ? '<span style="width:18px;height:18px;border-radius:999px;border:2px solid #1a365d;display:flex;align-items:center;justify-content:center;background:#fffefc;"><span style="width:7px;height:7px;border-radius:999px;background:#1a365d;"></span></span>' : ''}
          ${s.isTodo ? '<span style="width:16px;height:16px;border-radius:999px;border:1.5px solid #cdd5e0;background:#fffefc;"></span>' : ''}
        </span>
        ${s.noRight ? '<span style="flex:1;"></span>' : ''}
        ${s.rightNavy ? '<span style="flex:1;height:2px;background:#1a365d;"></span>' : ''}
        ${s.rightGray ? '<span style="flex:1;height:2px;background:#e3e8ef;"></span>' : ''}
      </div>
      <div style="text-align:center;margin-top:2px;">
        <div style="font-size:12px;font-weight:${s.active ? '500' : '400'};letter-spacing:-0.02em;color:${s.active ? '#1a365d' : '#3a4252'};line-height:1.2;white-space:nowrap;">${s.label}</div>
      </div>
      ${s.active ? '<span style="position:absolute;left:8px;right:8px;bottom:-1px;height:2px;border-radius:2px;background:#1a365d;"></span>' : ''}
    </div>`;
  }

  function searchCanvas(v) {
    if (v.isNewProject && !v.setupDraft.description) {
      return `<div style="border:1px dashed #c8d8e8;border-radius:12px;padding:28px;text-align:center;margin-bottom:16px;background:#f8fbff;">
        <span style="display:inline-flex;align-items:center;justify-content:center;margin-bottom:12px;">${logo(56)}</span>
        <div style="font-family:Newsreader,Georgia,serif;font-size:22px;color:#1a1a1a;line-height:1.15;">Welcome to ReviewPilot</div>
        <div style="font-size:12.5px;color:#6b746c;margin-top:8px;max-width:520px;margin-left:auto;margin-right:auto;line-height:1.5;">ReviewPilot helps you turn a research topic into a literature review.</div>
        <div style="font-size:12.5px;color:#1a365d;margin-top:14px;line-height:1.45;">To get started, describe your research topic in the chat.</div>
        ${starterTopicButtons()}
      </div>`;
    }
    return `<div style="border:1px solid #e5e7eb;border-radius:12px;padding:16px 18px;margin-bottom:16px;">
        <div style="font-size:10px;letter-spacing:0.06em;text-transform:uppercase;color:#9aa39b;margin-bottom:7px;">Research question</div>
        <div style="font-size:15px;color:#1a1a1a;line-height:1.45;letter-spacing:-0.01em;">${v.researchQuestion || 'Review project'}</div>
      </div>
      <div style="border:1px solid #e5e7eb;border-radius:12px;padding:16px 18px;margin-bottom:16px;">
        <div style="font-size:10px;letter-spacing:0.06em;text-transform:uppercase;color:#9aa39b;margin-bottom:11px;">Keywords</div>
        ${keywordGrid(v.keywords, true)}
      </div>
      <div data-ui="search-setup-controls" style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;align-items:stretch;">
        <div data-ui="sources-card" style="border:1px solid #e5e7eb;border-radius:12px;padding:16px 18px;height:100%;box-sizing:border-box;">
          <div style="font-size:10px;letter-spacing:0.06em;text-transform:uppercase;color:#9aa39b;margin-bottom:12px;">Sources</div>
          ${sourceChecklist(v.draftSources, v.setupDraft.source_limits, v.setupDraft.max_results)}
        </div>
        ${dateRangeCard(v.setupDraft)}
      </div>`;
  }

  function keywordGrid(keywords, editable) {
    const items = keywords.map((kw) => keywordCard(kw, editable)).join('');
    const add = editable ? `<button data-ui="keyword-add-card" data-act="add-keyword" title="Add keyword" style="min-height:26px;border:1px dashed #cfe0f5;background:#fffefc;color:#1a365d;border-radius:6px;padding:4px 9px;font:inherit;font-family:'IBM Plex Mono',monospace;font-size:11.5px;line-height:1.2;cursor:pointer;display:inline-flex;align-items:center;justify-content:center;gap:6px;"><i class="ph ph-plus" style="font-size:11px;"></i><span>Add keyword</span></button>` : '';
    if (!items && !editable) return emptyHint('No keywords yet');
    return `<div data-ui="keyword-card-grid" style="display:flex;flex-wrap:wrap;gap:7px;align-items:flex-start;">${items}${add}</div>`;
  }

  function starterTopicButtons() {
    return `<div data-ui="starter-topic-buttons" style="display:grid;grid-template-columns:minmax(0,1fr);gap:7px;margin-top:12px;max-width:620px;margin-left:auto;margin-right:auto;">
      ${STARTER_TOPICS.map((topic) => `<button type="button" data-act="starter-topic" data-topic="${esc(topic)}" style="border:1px solid #c8d8e8;background:#fffefc;color:#1a365d;border-radius:10px;padding:9px 10px;font:inherit;font-size:11.5px;letter-spacing:-0.01em;cursor:pointer;display:flex;align-items:flex-start;gap:7px;text-align:left;line-height:1.35;min-width:0;transition:background .15s ease,border-color .15s ease;" data-hover="background:#eef4fb;border-color:#9bb8d8;"><i class="ph ph-arrow-right" style="font-size:12px;flex:0 0 auto;margin-top:2px;"></i><span style="min-width:0;">${topic}</span></button>`).join('')}
    </div>`;
  }

  function keywordCard(kw, removable) {
    return `<div data-ui="keyword-card" style="position:relative;min-height:26px;max-width:260px;border:1px solid #cfe0f5;background:#eaf0f7;color:#1a365d;border-radius:6px;padding:4px 24px 4px 9px;display:inline-flex;align-items:center;justify-content:center;min-width:0;box-sizing:border-box;"><span style="font-family:'IBM Plex Mono',monospace;font-size:11.5px;line-height:1.2;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${kw}</span>${removable ? `<button data-ui="keyword-card-remove" data-act="remove-keyword" data-keyword="${kw}" title="Remove keyword" aria-label="Remove keyword ${kw}" style="position:absolute;top:2px;right:3px;width:14px;height:14px;border:none;background:transparent;color:#1a365d;border-radius:999px;padding:0;cursor:pointer;display:inline-flex;align-items:center;justify-content:center;opacity:.78;"><i class="ph ph-x" style="font-size:9px;"></i></button>` : ''}</div>`;
  }

  function emptyHint(text) {
    return `<span style="font-size:12.5px;color:#9aa39b;">${text}</span>`;
  }

  function gate(title, text, icon) {
    return `<div style="border:1px dashed #c8d8e8;border-radius:12px;padding:24px;text-align:center;margin-bottom:16px;background:#f8fbff;">
      <span style="width:42px;height:42px;border-radius:11px;background:#eaf0f7;color:#1a365d;display:inline-flex;align-items:center;justify-content:center;margin-bottom:10px;"><i class="ph ${icon}" style="font-size:21px;"></i></span>
      <div style="font-size:16px;color:#1a365d;letter-spacing:-0.01em;">${title}</div>
      <div style="font-size:12.5px;color:#6b746c;margin-top:6px;max-width:420px;margin-left:auto;margin-right:auto;line-height:1.5;">${text}</div>
    </div>`;
  }

  function canvasActionButton(v) {
    if (!v.showCanvasAction) return '';
    const pendingLabel = v.canvasActionLabel.replace(/^Run\s+/i, '');
    const label = v.canvasActionPending ? `Running ${pendingLabel.toLowerCase()}...${v.canvasActionElapsedLabel ? ` ${v.canvasActionElapsedLabel}` : ''}` : v.canvasActionLabel;
    const disabled = v.canvasActionPending ? 'disabled' : '';
    const icon = v.canvasActionPending
      ? '<span data-ui="canvas-action-spinner" style="width:13px;height:13px;border:2px solid #c8d8e8;border-top-color:#1a365d;border-radius:999px;display:inline-block;animation:rp-action-spin .7s linear infinite;"></span>'
      : '<i class="ph ph-arrow-bend-down-right" style="font-size:13px;"></i>';
    return `<div data-ui="canvas-action-row" style="display:flex;justify-content:flex-end;margin:16px 0;">
      <button data-ui="canvas-action-button" data-act="action" data-action="${v.canvasActionName}" ${disabled} style="${buttonStyle}${v.canvasActionPending ? ';opacity:.72;cursor:wait;' : ''}">${icon}${label}</button>
    </div>`;
  }

  function actionErrorBanner(v) {
    if (!v.actionError) return '';
    return `<div data-ui="canvas-action-error" style="border:1px solid #f4b4b4;background:#fff5f5;color:#8a1f1f;border-radius:8px;padding:9px 11px;font-size:12.5px;margin-bottom:14px;line-height:1.45;">${esc(v.actionError)}</div>`;
  }

  function screeningCanvas(v) {
    return `${v.isNewProject ? gate('Paper Screening starts after collection', 'Create the Search Setup first, then run collection before screening records.', 'ph-funnel') : ''}
      <div style="display:flex;border:1px solid #e5e7eb;border-radius:12px;overflow:hidden;margin-bottom:16px;">
        <div style="flex:1;padding:18px;text-align:center;border-right:1px solid #eef0ee;"><div style="font-family:'IBM Plex Mono',monospace;font-size:28px;color:#1a1a1a;">${v.screeningMetrics.identified}</div><div style="font-size:11px;color:#8a938b;margin-top:4px;">identified</div></div>
        <div style="flex:1;padding:18px;text-align:center;border-right:1px solid #eef0ee;"><div style="font-family:'IBM Plex Mono',monospace;font-size:28px;color:#1a1a1a;">${v.screeningMetrics.afterDedup}</div><div style="font-size:11px;color:#8a938b;margin-top:4px;">after de-dup</div></div>
        <div style="flex:1;padding:18px;text-align:center;"><div style="font-family:'IBM Plex Mono',monospace;font-size:28px;color:#1a365d;">${v.screeningMetrics.included}</div><div style="font-size:11px;color:#1a365d;margin-top:4px;">included</div></div>
      </div>
      <div style="border:1px solid #e5e7eb;border-radius:12px;padding:16px 18px;"><div style="font-size:10px;letter-spacing:0.06em;text-transform:uppercase;color:#9aa39b;margin-bottom:12px;">Records by source</div>${v.platforms.map(sourceRow).join('')}</div>`;
  }

  function retrievalCanvas(v) {
    return `${v.isNewProject ? gate('Full-Text Retrieval starts after screening', 'Download PDFs only after included papers are known.', 'ph-file-arrow-down') : ''}
      <div style="display:flex;gap:14px;margin-bottom:16px;">
        <div style="flex:0 0 180px;border:1px solid #e5e7eb;border-radius:12px;padding:18px;display:flex;align-items:center;gap:14px;"><svg width="50" height="50" viewBox="0 0 56 56"><circle cx="28" cy="28" r="23" fill="none" stroke="#e8ebe7" stroke-width="4"></circle><circle cx="28" cy="28" r="23" fill="none" stroke="#1a365d" stroke-width="4" stroke-linecap="round" stroke-dasharray="144.5" stroke-dashoffset="${v.retrievalDashOffset}" transform="rotate(-90 28 28)"></circle></svg><div><div style="font-family:'IBM Plex Mono',monospace;font-size:20px;color:#1a365d;">${v.retrievalSummary.retrieved}/${v.retrievalSummary.total}</div><div style="font-size:11px;color:#6b746c;margin-top:3px;">retrieved</div></div></div>
        <div style="flex:1;border:1px solid #e5e7eb;border-radius:12px;padding:14px 18px;display:flex;flex-direction:column;justify-content:center;gap:9px;">
          <div style="display:flex;justify-content:space-between;font-size:12.5px;"><span style="color:#1a1a1a;">Open access</span><span style="font-family:'IBM Plex Mono',monospace;color:#6b746c;">${v.retrievalSummary.openAccess}</span></div>
          <div style="height:1px;background:#f2f4f1;"></div>
          <div style="display:flex;justify-content:space-between;font-size:12.5px;"><span style="color:#1a1a1a;">Via institution</span><span style="font-family:'IBM Plex Mono',monospace;color:#6b746c;">${v.retrievalSummary.viaInstitution}</span></div>
          <div style="height:1px;background:#f2f4f1;"></div>
          <div style="display:flex;justify-content:space-between;font-size:12.5px;"><span style="color:#1a1a1a;">Unavailable</span><span style="font-family:'IBM Plex Mono',monospace;color:#6b746c;">${v.retrievalSummary.unavailable}</span></div>
        </div>
      </div>
      <div style="border:1px solid #e5e7eb;border-radius:12px;padding:16px 18px;"><div style="font-size:10px;letter-spacing:0.06em;text-transform:uppercase;color:#9aa39b;margin-bottom:10px;">${v.retrievalSummary.retrieved > 0 ? 'Recently retrieved' : 'Included papers queued for retrieval'}</div>${v.retrieved.map((r) => `<div style="display:flex;align-items:center;gap:11px;padding:8px 0;border-bottom:1px solid #f4f6f3;"><i class="ph ph-file-text" style="font-size:16px;color:#1a365d;"></i><div style="flex:1;min-width:0;"><div style="font-size:12.5px;color:#1a1a1a;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${r.t}</div><div style="font-family:'IBM Plex Mono',monospace;font-size:10.5px;color:#9aa39b;">${r.v}</div></div><i class="ph-fill ph-check-circle" style="font-size:15px;color:#1a365d;"></i></div>`).join('') || emptyHint('No included papers queued yet')}</div>`;
  }

  function extractionCanvas(v) {
    const wb = v.schemaWorkbench || { status: 'missing' };
    const statusLabel = wb.status === 'finalized' ? 'Finalized schema' : (wb.status === 'draft' ? 'Draft schema' : 'No schema');
    const statusColor = wb.status === 'finalized' ? '#1a365d' : (wb.status === 'draft' ? '#6b746c' : '#9aa39b');
    const schemaActionDisabled = state.actionPending ? 'disabled' : '';
    const schemaActionPendingStyle = state.actionPending ? ';opacity:.72;cursor:wait;' : '';
    const schemaActions = v.isNewProject ? '' : (
      wb.status === 'finalized'
        ? `<button data-act="action" data-action="edit-schema" ${schemaActionDisabled} style="${buttonStyle}${schemaActionPendingStyle}"><i class="ph ph-pencil-simple" style="font-size:14px;"></i>Edit Schema</button><button data-act="action" data-action="run-extraction" ${schemaActionDisabled} style="${buttonStyle}${schemaActionPendingStyle}"><i class="ph ph-play-circle" style="font-size:13px;"></i>Run Extraction</button>`
        : `<button data-act="action" data-action="generate-schema" ${schemaActionDisabled} style="${buttonStyle}${schemaActionPendingStyle}"><i class="ph ph-arrows-clockwise" style="font-size:13px;"></i>Regenerate</button><button data-act="action" data-action="finalize-schema" ${schemaActionDisabled} style="${buttonStyle}${schemaActionPendingStyle}"><i class="ph ph-check-circle" style="font-size:13px;"></i>Finalize Schema</button>`
    );
    return `<div style="display:flex;align-items:center;gap:4px;border-bottom:1px solid #eef0ee;margin-bottom:2px;">
        ${v.isFieldsTab ? `<span style="font-size:13px;color:#1a365d;padding:9px 12px;border-bottom:2px solid #1a365d;margin-bottom:-1px;cursor:pointer;">Schema fields <span style="font-family:'IBM Plex Mono',monospace;font-size:11px;color:#9aa39b;">${v.allFields.length}</span></span>` : ''}
        ${v.notFieldsTab ? `<span data-act="tab" data-tab="fields" style="font-size:13px;color:#6b746c;padding:9px 12px;cursor:pointer;transition:color .12s ease;" data-hover="color:#1a365d;">Schema fields <span style="font-family:'IBM Plex Mono',monospace;font-size:11px;color:#9aa39b;">${v.allFields.length}</span></span>` : ''}
        ${v.isPreviewTab ? '<span style="font-size:13px;color:#1a365d;padding:9px 12px;border-bottom:2px solid #1a365d;margin-bottom:-1px;cursor:pointer;">Preview on paper</span>' : ''}
        ${v.notPreviewTab ? '<span data-act="tab" data-tab="preview" style="font-size:13px;color:#6b746c;padding:9px 12px;cursor:pointer;transition:color .12s ease;" data-hover="color:#1a365d;">Preview on paper</span>' : ''}
        ${!v.isNewProject ? `<span style="margin-left:auto;font-family:'IBM Plex Mono',monospace;font-size:10.5px;color:${statusColor};background:#f4f6f3;border:1px solid #e5e7eb;border-radius:999px;padding:5px 8px;">${statusLabel}</span><span style="display:flex;gap:8px;margin-left:8px;">${schemaActions}</span>` : ''}
      </div>
      ${v.isNewProject ? gate('Information Extraction waits for full texts', 'Create setup, screen papers, and retrieve PDFs before defining extraction fields.', 'ph-table') : ''}
      ${!v.isNewProject && wb.status === 'draft' ? `<div style="border:1px solid #d8e2f0;background:#f8fbff;border-radius:9px;padding:10px 11px;margin:12px 0;color:#1a365d;font-size:12px;line-height:1.45;">Refine this draft through chat, then finalize the schema before running extraction.</div>` : ''}
      ${v.isFieldsTab ? fieldsTable(v) : previewTable(v)}`;
  }

  function fieldsTable(v) {
    return `<div style="display:grid;grid-template-columns:26px 1.5fr 1.1fr 2fr 54px;gap:12px;padding:9px 12px;font-size:10px;letter-spacing:0.05em;text-transform:uppercase;color:#9aa39b;border-bottom:1px solid #eef0ee;"><span>#</span><span>Field</span><span>Type</span><span>Description</span><span style="text-align:center;">Req</span></div>
      ${v.allFields.map((f) => `<div style="display:grid;grid-template-columns:26px 1.5fr 1.1fr 2fr 54px;gap:12px;padding:8px 12px;border-bottom:1px solid #f4f6f3;align-items:center;font-size:12.5px;letter-spacing:-0.01em;transition:background .1s ease;" data-hover="background:#f4f8fd;"><span style="font-family:'IBM Plex Mono',monospace;font-size:11px;color:#bcc4bb;">${f.idx}</span><span style="color:#1a1a1a;">${f.name}</span><span style="font-family:'IBM Plex Mono',monospace;font-size:11px;color:#2c5282;background:#eaf0f7;padding:2px 7px;border-radius:5px;justify-self:start;">${f.type}</span><span style="color:#8a938b;">${f.desc}</span><span style="display:flex;justify-content:center;">${f.req ? '<i class="ph-fill ph-check-circle" style="font-size:16px;color:#1a365d;"></i>' : '<i class="ph ph-minus" style="font-size:14px;color:#c8cfc7;"></i>'}</span></div>`).join('') || emptyHint('No schema fields yet')}`;
  }

  function previewTable(v) {
    return `<div style="display:flex;align-items:center;justify-content:space-between;padding:14px 2px 12px;"><div style="min-width:0;"><div style="font-size:13.5px;color:#1a1a1a;letter-spacing:-0.01em;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:440px;">${v.previewPaper.title}</div><div style="font-family:'IBM Plex Mono',monospace;font-size:11px;color:#9aa39b;margin-top:2px;">${v.previewPaper.ref}</div></div><div style="font-family:'IBM Plex Mono',monospace;font-size:11px;color:#6b746c;">${v.previewPaper.countLabel}</div></div>
      <div style="border:1px solid #e5e7eb;border-radius:12px;overflow:hidden;">${v.previewFields.map((p) => `<div style="display:grid;grid-template-columns:160px 1fr;gap:14px;padding:10px 16px;border-bottom:1px solid #f4f6f3;align-items:start;"><span style="font-size:12px;color:#8a938b;letter-spacing:-0.01em;">${p.k}</span><span style="font-family:'IBM Plex Mono',monospace;font-size:12px;color:#1a1a1a;line-height:1.5;">${p.v}</span></div>`).join('') || emptyHint('No preview yet')}</div>`;
  }

  function categorizeCanvas(v) {
    const workflow = v.categorizationWorkflow || emptyCategorizationWorkflow();
    if (v.isNewProject || !workflow.metrics.papersExtracted) {
      return gate('Categorization starts after extraction', 'Choose extracted fields, generate groups, and apply labels after extraction completes.', 'ph-stack');
    }
    return `<div style="display:flex;align-items:center;justify-content:space-between;gap:14px;margin-bottom:14px;">
        <div>
          <div style="font-size:10px;letter-spacing:0.06em;text-transform:uppercase;color:#9aa39b;margin-bottom:5px;">Step 5</div>
          <div style="font-family:Newsreader,Georgia,serif;font-size:21px;color:#1a1a1a;line-height:1.15;">Categorization & Analysis</div>
        </div>
        <div style="font-family:'IBM Plex Mono',monospace;font-size:11px;color:#1a365d;background:#eaf0f7;border:1px solid #d8e2f0;border-radius:999px;padding:5px 9px;">${workflow.done ? `${v.categorizationSummary.papers} papers · ${v.categorizationSummary.groups} categories` : `${workflow.metrics.papersExtracted} papers · ${workflow.metrics.fields} fields`}</div>
      </div>
      ${categorizationMetrics(workflow)}
      ${(workflow.done || v.catDraft.skipped) ? categorizationAnalysisSummary(v, workflow) : categorizationSetupPanel(v, workflow)}`;
  }

  function categorizationMetrics(workflow) {
    return `<div style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin-bottom:16px;">
      <div style="border:1px solid #e5e7eb;border-radius:10px;padding:13px 14px;"><div style="font-size:10px;color:#8a938b;letter-spacing:-0.01em;">Papers Extracted</div><div style="font-family:'IBM Plex Mono',monospace;font-size:20px;color:#1a365d;margin-top:4px;">${workflow.metrics.papersExtracted}</div></div>
      <div style="border:1px solid #e5e7eb;border-radius:10px;padding:13px 14px;"><div style="font-size:10px;color:#8a938b;letter-spacing:-0.01em;">Fields</div><div style="font-family:'IBM Plex Mono',monospace;font-size:20px;color:#1a365d;margin-top:4px;">${workflow.metrics.fields}</div></div>
    </div>`;
  }

  function categorizationSetupPanel(v, workflow) {
    const draft = v.catDraft;
    const profile = workflow.fieldProfiles[draft.field] || { papersWithValue: 0, uniqueValuesCount: 0, sampleValues: [], distribution: [] };
    const hasCategories = categorizationCategoriesFromDraft().length > 0;
    const suggesting = state.actionPending === 'suggest-categories';
    const applying = state.actionPending === 'categorize';
    return `<section style="border:1px solid #e5e7eb;border-radius:12px;padding:16px 18px;margin-bottom:16px;">
      <div style="font-size:10px;letter-spacing:0.06em;text-transform:uppercase;color:#9aa39b;margin-bottom:12px;">Select Field to Categorize</div>
      <label style="display:block;font-size:11.5px;color:#6b746c;margin-bottom:6px;">Choose a field with varied values to group into categories:</label>
      <select data-cat-field="1" style="width:100%;border:1px solid #d8ddd6;border-radius:9px;background:#fffefc;padding:9px 10px;font:inherit;font-size:13px;color:#1a1a1a;margin-bottom:12px;">
        ${workflow.fieldNames.map((field) => `<option value="${field}" ${field === draft.field ? 'selected' : ''}>${evidenceFieldLabel(field)}${field === workflow.recommendedField ? ' · recommended' : ''}</option>`).join('')}
      </select>
      <div style="font-size:12.5px;color:#3a4252;margin-bottom:10px;"><strong>${profile.papersWithValue} papers</strong> have values for this field</div>
      <details open style="border:1px solid #eef0ee;border-radius:10px;padding:10px 12px;margin-bottom:14px;background:#fbfcfa;">
        <summary style="cursor:pointer;color:#1a365d;font-size:12.5px;">Sample Values</summary>
        <div style="display:flex;flex-direction:column;gap:6px;margin-top:9px;">${profile.sampleValues.map((value) => `<div style="font-family:'IBM Plex Mono',monospace;font-size:11px;color:#3a4252;line-height:1.45;">• ${value}</div>`).join('') || emptyHint('No values found for this field')}</div>
        ${profile.uniqueValuesCount > profile.sampleValues.length ? `<div style="font-size:11px;color:#8a938b;margin-top:7px;">...and ${profile.uniqueValuesCount - profile.sampleValues.length} more unique values</div>` : ''}
      </details>
      <div style="font-size:10px;letter-spacing:0.06em;text-transform:uppercase;color:#9aa39b;margin-bottom:8px;">Categorization Mode</div>
      <div style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;margin-bottom:14px;">
        ${categoryModeButton('single', 'Exactly one category per paper', draft.mode === 'single')}
        ${categoryModeButton('multiple', 'One or more categories per paper', draft.mode !== 'single')}
      </div>
      <button data-act="action" data-action="suggest-categories" ${suggesting ? 'disabled' : ''} style="${buttonStyle}${suggesting ? ';opacity:.72;cursor:wait;' : ''}"><i class="ph ph-sparkle" style="font-size:13px;"></i>${suggesting ? 'Analyzing field values semantically...' : 'Generate Categories with AI'}</button>
      ${hasCategories ? generatedCategoriesPanel(v, workflow, applying) : ''}
      <div style="border-top:1px solid #eef0ee;margin-top:16px;padding-top:12px;"><button data-act="skip-categorization" style="${buttonStyle}">Skip Categorization</button></div>
    </section>`;
  }

  function categoryModeButton(mode, label, active) {
    return `<button data-act="cat-mode" data-mode="${mode}" style="border:1px solid ${active ? '#1a365d' : '#d8e2f0'};background:${active ? '#eef4fb' : '#fffefc'};color:#1a365d;border-radius:9px;padding:9px 10px;font:inherit;font-size:12px;cursor:pointer;text-align:left;">${label}</button>`;
  }

  function generatedCategoriesPanel(v, workflow, applying) {
    const categories = categorizationCategoriesFromDraft();
    const confirmed = v.catDraft.confirmed;
    return `<div style="border-top:1px solid #eef0ee;margin-top:16px;padding-top:14px;">
      <div style="font-size:10px;letter-spacing:0.06em;text-transform:uppercase;color:#9aa39b;margin-bottom:8px;">Generated Categories</div>
      <textarea data-cat-categories="1" rows="7" style="width:100%;box-sizing:border-box;border:1px solid #d8ddd6;border-radius:9px;background:#fffefc;padding:10px 11px;font:inherit;font-size:12.5px;color:#1a1a1a;resize:vertical;">${v.catDraft.categoriesText}</textarea>
      ${categoryDescriptions(workflow.categoryDescriptions)}
      <div style="margin-top:12px;">
        <div style="font-size:12.5px;color:#1a1a1a;margin-bottom:8px;">Review your categories:</div>
        <div style="display:flex;flex-direction:column;gap:5px;">${categories.map((category, index) => `<div style="font-size:12px;color:#3a4252;">${index + 1}. ${category}</div>`).join('')}</div>
      </div>
      ${confirmed ? `<div style="margin-top:12px;border:1px solid #d8e2f0;background:#f8fbff;border-radius:9px;padding:10px 11px;color:#1a365d;font-size:12px;">Categories confirmed: ${categories.length} categories</div><div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:10px;"><button data-act="edit-categories" style="${buttonStyle}"><i class="ph ph-pencil-simple" style="font-size:13px;"></i><span>Edit Categories</span></button><button data-act="action" data-action="categorize" ${applying ? 'disabled' : ''} style="${buttonStyle}${applying ? ';opacity:.72;cursor:wait;' : ''}"><i class="ph ph-check-circle" style="font-size:13px;"></i>${applying ? 'Categorizing papers...' : 'Apply Categorization'}</button></div>` : `<div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:12px;"><button data-act="confirm-categories" style="${buttonStyle}"><i class="ph ph-check-circle" style="font-size:13px;"></i>Confirm Categories</button><button data-act="action" data-action="suggest-categories" style="${buttonStyle}"><i class="ph ph-arrows-clockwise" style="font-size:13px;"></i>Regenerate</button></div><div style="font-size:11px;color:#8a938b;margin-top:8px;">Review the categories above. Click Confirm when ready, or Regenerate for new suggestions.</div>`}
    </div>`;
  }

  function categoryDescriptions(descriptions) {
    const rows = Object.entries(descriptions || {});
    if (!rows.length) return '';
    return `<details style="border:1px solid #eef0ee;border-radius:9px;padding:9px 10px;margin-top:10px;"><summary style="cursor:pointer;color:#1a365d;font-size:12px;">Category Descriptions (reference)</summary><div style="display:flex;flex-direction:column;gap:6px;margin-top:8px;">${rows.map(([category, desc]) => `<div style="font-size:11.5px;color:#6b746c;line-height:1.4;"><strong style="color:#1a365d;">${category}</strong>: ${desc}</div>`).join('')}</div></details>`;
  }

  function categorizationAnalysisSummary(v, workflow) {
    const skipped = v.catDraft.skipped && !workflow.done;
    return `<section>
      <div style="font-size:10px;letter-spacing:0.06em;text-transform:uppercase;color:#9aa39b;margin:4px 0 10px;">Analysis Summary</div>
      ${skipped ? `<div style="border:1px solid #d8e2f0;background:#f8fbff;border-radius:10px;padding:11px 12px;margin-bottom:14px;font-size:12.5px;color:#1a365d;">Categorization skipped. You can still review extracted field distributions and full results.</div>` : ''}
      <div style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin-bottom:16px;">${workflow.analysisDistributions.map((item) => `<details style="border:1px solid #e5e7eb;border-radius:10px;padding:11px 12px;background:#fffefc;"><summary style="cursor:pointer;color:#1a365d;font-size:12.5px;">${evidenceFieldLabel(item.field)} Distribution</summary>${distributionChart(item.values)}</details>`).join('')}</div>
      ${workflow.done ? `<div style="margin-bottom:16px;"><div style="font-size:12.5px;color:#1a1a1a;margin-bottom:8px;"><strong>${evidenceFieldLabel(workflow.selectedField)} Categories:</strong></div>${distributionChart(workflow.categorizedDistribution.values)}</div>` : ''}
      ${workflow.done ? categoryBriefsPanel(v.categorizationAnalysis) : ''}
      <div style="font-size:10px;letter-spacing:0.06em;text-transform:uppercase;color:#9aa39b;margin-bottom:10px;">Full Results</div>
      ${fullResultsTable(workflow.fullResults)}
      <div style="display:flex;justify-content:flex-end;margin-top:14px;"><button data-act="finalize-project" style="${buttonStyle}"><i class="ph ph-flag-checkered" style="font-size:13px;"></i>Finalize Project</button></div>
      ${v.catDraft.finalized ? `<div style="margin-top:10px;font-size:12px;color:#1a365d;text-align:right;">Project Complete!</div>` : ''}
    </section>`;
  }

  function categoryBriefsPanel(analysis) {
    const categories = (analysis && analysis.categories) || [];
    if (!categories.length) return '';
    return `<div style="margin-bottom:18px;">
      <div style="font-size:10px;letter-spacing:0.06em;text-transform:uppercase;color:#9aa39b;margin-bottom:10px;">Category Briefs</div>
      <div style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;">${categories.map(categoryBriefCard).join('')}</div>
    </div>`;
  }

  function categoryBriefCard(category) {
    const papers = (category.papers || []).slice(0, 3);
    return `<article style="border:1px solid #e5e7eb;border-radius:10px;background:#fffefc;padding:12px 13px;min-width:0;">
      <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:10px;margin-bottom:7px;"><div style="font-size:13px;color:#1a365d;line-height:1.25;font-weight:600;">${category.name}</div><span style="font-family:'IBM Plex Mono',monospace;font-size:11px;color:#1a365d;background:#eef4fb;border:1px solid #d8e2f0;border-radius:999px;padding:3px 7px;">${category.count || 0}</span></div>
      ${category.description ? `<div style="font-size:11.5px;color:#6b746c;line-height:1.4;margin-bottom:9px;">${category.description}</div>` : ''}
      <div style="font-size:10px;letter-spacing:0.06em;text-transform:uppercase;color:#9aa39b;margin-bottom:7px;">Representative papers</div>
      <div style="display:flex;flex-direction:column;gap:7px;">${papers.map(categoryBriefPaper).join('') || emptyHint('No representative papers')}</div>
    </article>`;
  }

  function categoryBriefPaper(paper) {
    return `<details data-ui="category-brief-paper" style="border-top:1px solid #f2f4f1;padding-top:7px;">
      <summary style="cursor:pointer;list-style:none;display:flex;align-items:flex-start;justify-content:space-between;gap:10px;color:#1a1a1a;">
        <span style="font-size:12px;line-height:1.35;min-width:0;">${paper.title}</span>
        ${paper.evidence ? `<span style="font-size:10px;letter-spacing:0.04em;text-transform:uppercase;color:#1a365d;background:#eef4fb;border:1px solid #d8e2f0;border-radius:999px;padding:2px 6px;white-space:nowrap;">Evidence</span>` : ''}
      </summary>
      ${paper.evidence ? `<div style="font-size:11.5px;color:#4b5563;line-height:1.45;margin-top:6px;padding:8px 9px;background:#f8fbff;border:1px solid #eef2f7;border-radius:8px;">${paper.evidence}</div>` : ''}
    </details>`;
  }

  function distributionChart(values) {
    if (!values || !values.length) return emptyHint('No values to chart');
    const max = Math.max(1, ...values.map((item) => Number(item.count) || 0));
    return `<div style="display:flex;flex-direction:column;gap:8px;margin-top:10px;">${values.map((item) => `<div style="display:grid;grid-template-columns:minmax(0,1fr) 46px;gap:9px;align-items:center;"><div style="min-width:0;"><div style="font-size:11.5px;color:#3a4252;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${item.label}</div><div style="height:5px;background:#eef0ee;border-radius:999px;overflow:hidden;margin-top:4px;"><span style="display:block;height:100%;width:${Math.max(4, Math.round(((Number(item.count) || 0) / max) * 100))}%;background:#1a365d;"></span></div></div><span style="font-family:'IBM Plex Mono',monospace;font-size:11px;color:#6b746c;text-align:right;">${item.count}</span></div>`).join('')}</div>`;
  }

  function fullResultsTable(table) {
    const columns = (table && table.columns) || [];
    const rows = (table && table.rows) || [];
    if (!columns.length || !rows.length) return emptyHint('No results available');
    return `<div class="rp-scroll" style="border:1px solid #e5e7eb;border-radius:10px;overflow:auto;max-height:300px;"><table style="width:100%;border-collapse:collapse;font-size:12px;"><thead><tr>${columns.map((column) => `<th style="text-align:left;padding:8px 10px;background:#f8fbff;color:#9aa39b;font-size:10px;letter-spacing:0.05em;text-transform:uppercase;border-bottom:1px solid #eef0ee;white-space:nowrap;">${evidenceFieldLabel(column)}</th>`).join('')}</tr></thead><tbody>${rows.map((row) => `<tr>${columns.map((column) => `<td style="vertical-align:top;padding:8px 10px;border-bottom:1px solid #f4f6f3;color:#3a4252;line-height:1.4;min-width:120px;">${row[column] || ''}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
  }

  function evidenceFieldLabel(name) {
    const labels = {
      title: 'Paper',
      key_findings: 'Findings',
      datasets_used: 'Datasets',
      dataset: 'Dataset',
      methods: 'Methods',
      methodology: 'Methodology',
      evaluation_metrics: 'Evaluation',
      limitations: 'Limitations',
      biomedical_domain: 'Domain',
      task: 'Task',
      task_or_application: 'Task / Application',
      model: 'Model',
    };
    const key = String(name || '').trim();
    return labels[key] || key.replace(/_/g, ' ').replace(/\b\w/g, (ch) => ch.toUpperCase());
  }

  function activityMessage(m) {
    return `<div data-ui="assistant-activity-message" style="display:flex;gap:8px;align-items:flex-start;"><span style="flex:0 0 20px;margin-top:1px;">${logo(20)}</span><div data-ui="assistant-activity-card" style="flex:1;min-width:0;border:1px solid #e5e7eb;border-radius:12px;padding:12px 13px;background:#fffefc;">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:4px;"><div style="display:flex;align-items:center;gap:8px;min-width:0;"><i class="ph ph-pulse" style="font-size:15px;color:#1a365d;flex:0 0 auto;"></i><span style="font-size:10px;letter-spacing:0.08em;text-transform:uppercase;color:#9aa39b;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">Activity · ${m.activityTitle}</span></div><span style="display:inline-flex;align-items:center;gap:5px;font-size:10px;color:#1a365d;flex:0 0 auto;"><span style="width:6px;height:6px;border-radius:999px;background:#1a365d;"></span>live</span></div>
      ${m.activity.map((l) => `<div style="display:grid;grid-template-columns:54px 72px minmax(0,1fr);gap:8px;padding:7px 0;border-top:1px solid #f4f6f3;align-items:baseline;"><span style="font-family:'IBM Plex Mono',monospace;font-size:10.5px;color:#bcc4bb;">${l.t}</span><span style="font-family:'IBM Plex Mono',monospace;font-size:11px;color:#1a365d;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${l.tag}</span><span style="font-size:12px;color:#6b746c;letter-spacing:-0.01em;line-height:1.35;min-width:0;">${l.msg}</span></div>`).join('')}
    </div></div>`;
  }

  function chatMessage(m) {
    if (m.isActivity) return activityMessage(m);
    if (m.isAssistant) {
      return `<div style="display:flex;gap:8px;align-items:flex-start;"><span style="flex:0 0 20px;margin-top:1px;">${logo(20)}</span><div style="background:#eef4fb;border:1px solid #e1e8f2;border-radius:3px 13px 13px 13px;padding:10px 12px;font-size:12.5px;color:#1f2937;line-height:1.5;letter-spacing:-0.01em;white-space:pre-line;">${assistantMessageHtml(m.text)}</div></div>`;
    }
    return `<div style="display:flex;justify-content:flex-end;"><div style="background:#1a365d;color:#eaf0f7;border-radius:13px 13px 3px 13px;padding:10px 12px;font-size:12.5px;line-height:1.5;letter-spacing:-0.01em;max-width:230px;">${m.text}</div></div>`;
  }

  function assistantMessageHtml(text) {
    const value = String(text ?? '');
    if (value.includes('| Step | Description |')) return prototypeWelcomeHtml(value);
    return value.replace(/\n/g, '<br>');
  }

  function prototypeWelcomeHtml(text) {
    const rows = String(text ?? '').split('\n')
      .map((line) => line.match(/^\| \*\*(\d+)\. ([^*]+)\*\* \| (.+) \|$/))
      .filter(Boolean)
      .map((match) => ({ n: match[1], label: match[2], desc: match[3] }));
    const fallbackRows = [
      { n: '1', label: 'Search Setup', desc: 'Define your research topic and search parameters' },
      { n: '2', label: 'Paper Screening', desc: 'Collect papers, filter, and check relevance' },
      { n: '3', label: 'Full-Text Retrieval', desc: 'Download full-text PDFs for included papers' },
      { n: '4', label: 'Information Extraction', desc: 'Extract structured information from papers' },
      { n: '5', label: 'Categorization & Analysis', desc: 'Generate the final Categorization & Analysis report' },
    ];
    const mergedRows = fallbackRows.map((fallback) => rows.find((row) => row.n === fallback.n) || fallback);
    const steps = mergedRows
      .map((row) => `<li style="display:grid;grid-template-columns:18px minmax(0,1fr);gap:7px;padding:5px 0;border-top:1px solid rgba(26,54,93,.10);"><span style="font-family:'IBM Plex Mono',monospace;color:#1a365d;">${row.n}</span><span><strong style="color:#1a365d;">${row.label}</strong><br><span style="color:#4b5563;">${row.desc}</span></span></li>`)
      .join('');
    return `<div data-ui="prototype-welcome-message" style="display:flex;flex-direction:column;gap:9px;"><div>Welcome to <strong>ReviewPilot</strong>!</div><div>ReviewPilot helps you turn a research topic into a literature review:</div><ol data-ui="prototype-welcome-steps" style="list-style:none;margin:0;padding:0;">${steps}</ol><div><strong>Choose a starter topic on the canvas, or describe your own topic in the chat.</strong></div></div>`;
  }

  function thinkingBubble() {
    const dots = [0, 1, 2]
      .map((i) => `<span style="width:5px;height:5px;border-radius:999px;background:#1a365d;display:inline-block;animation:rp-thinking-bounce 1.05s ${i * 0.14}s infinite;"></span>`)
      .join('');
    return `<div data-ui="thinking-bubble" style="display:flex;gap:8px;align-items:flex-start;"><span style="flex:0 0 20px;margin-top:1px;">${logo(20)}</span><div style="background:#eef4fb;border:1px solid #e1e8f2;border-radius:3px 13px 13px 13px;padding:10px 12px;font-size:12.5px;color:#1f2937;line-height:1.5;letter-spacing:-0.01em;display:inline-flex;align-items:center;gap:9px;"><span>Thinking</span><span style="display:inline-flex;align-items:center;gap:3px;">${dots}</span></div></div>`;
  }

  function chatQuickStartPopover() {
    const rows = STARTER_TOPICS
      .map((topic) => `<button type="button" data-act="chat-quick-start" data-topic="${esc(topic)}" style="width:100%;border:none;border-top:1px solid #eef0ee;background:#fffefc;color:#1a365d;padding:9px 10px;font:inherit;font-size:12px;line-height:1.35;letter-spacing:-0.01em;text-align:left;cursor:pointer;display:grid;grid-template-columns:16px minmax(0,1fr);gap:7px;align-items:start;" data-hover="background:#eef4fb;"><i class="ph ph-arrow-right" style="font-size:12px;margin-top:2px;color:#1a365d;"></i><span>${topic}</span></button>`)
      .join('');
    return `<div data-ui="chat-quick-start" style="position:absolute;left:14px;right:14px;bottom:74px;z-index:5;background:#fffefc;border:1px solid #d8e2f0;border-radius:12px;box-shadow:0 14px 36px rgba(26,54,93,.12);overflow:hidden;">
      <div style="display:flex;align-items:center;gap:7px;padding:10px 11px;font-size:11px;color:#6b746c;letter-spacing:.02em;text-transform:uppercase;"><i class="ph ph-sparkle" style="font-size:13px;color:#1a365d;"></i><span>Quick Start</span></div>
      ${rows}
    </div>`;
  }

  function assistantPanel(v) {
    return `<aside class="rp-assistant" style="width:407px;flex:0 0 407px;border-left:1px solid #e5e7eb;display:flex;flex-direction:column;min-height:0;">
      <div style="display:flex;align-items:center;gap:9px;padding:13px 15px;border-bottom:1px solid #eef0ee;flex:0 0 auto;">${logo(24)}<div style="flex:1;min-width:0;"><div style="font-size:13.5px;color:#1a1a1a;letter-spacing:-0.01em;">ReviewPilot</div><div style="font-size:10.5px;color:#9aa39b;letter-spacing:-0.01em;">${v.assistantContext}</div></div><button data-act="open-setup" style="width:28px;height:28px;border-radius:8px;border:1px solid #e0e4df;background:none;color:#6b746c;cursor:pointer;display:flex;align-items:center;justify-content:center;transition:all .15s ease;" data-hover="background:#eef4fb;color:#1a365d;"><i class="ph ph-plus" style="font-size:15px;"></i></button></div>
      <div class="rp-scroll" id="rp-conv" style="flex:1;min-height:0;overflow-y:auto;padding:16px 15px;display:flex;flex-direction:column;gap:13px;">
        ${v.chat.map(chatMessage).join('')}
        ${v.chatPending ? thinkingBubble() : ''}
      </div>
      <div data-ui="assistant-chat-input-area" style="position:relative;flex:0 0 auto;padding:12px 14px;border-top:1px solid #eef0ee;">${v.quickStartOpen ? chatQuickStartPopover() : ''}<form id="rp-chat-form" style="display:flex;align-items:center;gap:9px;background:#fffefc;border:1px solid #d8ddd6;border-radius:14px;padding:8px 8px 8px 12px;transition:border-color .15s ease;" data-hover="border-color:#b9c3b6;"><span style="flex:0 0 auto;display:flex;align-items:center;">${logo(20)}</span><input data-ui="research-topic-input" aria-label="Describe your research topic" name="message" placeholder="${v.isNewProject && !v.setupDraft.description ? 'Describe your research topic...' : 'Reply to ReviewPilot...'}" autocomplete="off" style="flex:1;border:none;background:none;outline:none;font-size:13px;font-family:inherit;color:#1a1a1a;letter-spacing:-0.01em;"><button type="submit" style="width:30px;height:30px;flex:0 0 30px;border-radius:9px;border:none;background:#1a365d;color:#fffefc;display:flex;align-items:center;justify-content:center;cursor:pointer;"><i class="ph ph-arrow-up" style="font-size:15px;"></i></button></form><div style="font-size:10px;color:#aab1a9;margin-top:7px;text-align:center;letter-spacing:-0.01em;">ReviewPilot can make mistakes. Verify important results.</div></div>
    </aside>`;
  }

  function setupDialog(v) {
    const d = v.setupDraft;
    return `<div style="position:fixed;inset:0;background:rgba(17,24,39,.34);display:flex;align-items:center;justify-content:center;z-index:50;">
      <form id="rp-setup-dialog-form" style="width:560px;background:#fffefc;border:1px solid #d8e2f0;border-radius:12px;box-shadow:0 24px 70px rgba(26,54,93,.20);padding:18px 20px 16px;font-family:'Hanken Grotesk',system-ui,sans-serif;">
        <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:14px;"><div style="display:flex;align-items:center;gap:9px;min-width:0;">${logo(24)}<span style="font-family:Newsreader,Georgia,serif;font-size:20px;color:#1a1a1a;">Search Setup</span></div><button type="button" data-act="close-dialog" style="width:30px;height:30px;border-radius:8px;border:1px solid #e0e4df;background:none;color:#6b746c;display:flex;align-items:center;justify-content:center;cursor:pointer;"><i class="ph ph-x" style="font-size:15px;"></i></button></div>
        ${v.actionError ? `<div style="border:1px solid #f4b4b4;background:#fff5f5;color:#8a1f1f;border-radius:8px;padding:8px 10px;font-size:12px;margin-bottom:12px;">${v.actionError}</div>` : ''}
        ${dialogInput('Project name', 'project_name', d.project_name, 'AI surgery review', 'required')}
        <label style="display:block;font-size:11px;color:#6b746c;margin:10px 0 5px;">Research question</label>
        <textarea name="description" required rows="3" style="width:100%;box-sizing:border-box;border:1px solid #d8ddd6;border-radius:9px;background:#fffefc;padding:10px 11px;font:inherit;font-size:13px;margin-bottom:10px;color:#1a1a1a;resize:vertical;" placeholder="Review evidence for AI tools in surgical decision support">${d.description}</textarea>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;">${dialogInput('Primary topic', 'primary_topic', d.primary_topic, 'AI tools')}${dialogInput('Domain', 'domain', d.domain, 'Surgery')}</div>
        ${dialogInput('Boolean query / search terms', 'search_terms', d.search_terms || d.keywords.join(' AND '), 'AI AND surgery')}
        <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;">${dialogInput('Max/source', 'max_results', d.max_results, DEFAULT_MAX_RESULTS_PER_PLATFORM, 'inputmode="numeric"')}${dialogInput('Start date', 'date_start', d.date_start, '2020-01-01')}${dialogInput('End date', 'date_end', d.date_end, '2026-12-31')}</div>
        <div style="font-size:11px;color:#6b746c;margin-top:10px;">Sources are selected on the canvas: ${d.platforms.map(platformLabel).join(', ')}</div>
        <div style="display:flex;align-items:center;justify-content:flex-end;gap:9px;margin-top:16px;"><button type="button" data-act="close-dialog" style="${buttonStyle}">Cancel</button><button type="submit" style="border:none;background:#1a365d;color:#fffefc;border-radius:9px;padding:10px 14px;font:inherit;font-size:13px;cursor:pointer;display:inline-flex;align-items:center;gap:7px;"><i class="ph ph-check-circle" style="font-size:15px;"></i>${v.isNewProject ? 'Create project' : 'Save setup'}</button></div>
      </form>
    </div>`;
  }

  function dialogInput(label, name, value, placeholder, extra = '') {
    return `<div><label style="display:block;font-size:11px;color:#6b746c;margin:10px 0 5px;">${label}</label><input name="${name}" value="${esc(value)}" ${extra} style="width:100%;box-sizing:border-box;border:1px solid #d8ddd6;border-radius:9px;background:#fffefc;padding:9px 10px;font:inherit;font-size:13px;color:#1a1a1a;" placeholder="${placeholder}"></div>`;
  }

  function keywordDialog() {
    return `<div style="position:fixed;inset:0;background:rgba(17,24,39,.34);display:flex;align-items:center;justify-content:center;z-index:55;"><form id="rp-keyword-dialog-form" style="width:360px;background:#fffefc;border:1px solid #d8e2f0;border-radius:12px;box-shadow:0 24px 70px rgba(26,54,93,.20);padding:18px 20px 16px;font-family:'Hanken Grotesk',system-ui,sans-serif;"><div style="font-family:Newsreader,Georgia,serif;font-size:20px;color:#1a1a1a;margin-bottom:12px;">Add keyword</div><input name="keyword" required autofocus value="${esc(state.keywordDraft)}" style="width:100%;box-sizing:border-box;border:1px solid #d8ddd6;border-radius:9px;background:#fffefc;padding:10px 11px;font:inherit;font-size:13px;color:#1a1a1a;" placeholder="clinical NLP"><div style="display:flex;justify-content:flex-end;gap:9px;margin-top:14px;"><button type="button" data-act="close-dialog" style="${buttonStyle}">Cancel</button><button type="submit" style="border:none;background:#1a365d;color:#fffefc;border-radius:9px;padding:10px 14px;font:inherit;font-size:13px;cursor:pointer;">Add</button></div></form></div>`;
  }

  function updateDraftFromForm(form) {
    const payload = Object.fromEntries(new FormData(form).entries());
    state.setupDraft = { ...state.setupDraft, ...payload };
    if (payload.search_terms && !state.setupDraft.keywords.includes(payload.search_terms)) {
      state.setupDraft.keywords = [payload.search_terms, ...state.setupDraft.keywords].slice(0, 8);
    }
  }

  function updateSetupDraftField(field, value) {
    if (!['date_start', 'date_end'].includes(field)) return;
    state.setupDraft[field] = esc(value);
  }

  function updateSourceLimit(source, value) {
    state.setupDraft.source_limits = {
      ...(state.setupDraft.source_limits || {}),
      [source]: esc(value),
    };
    state.setupDraft.max_results = maxResultsFromSourceLimits(selectedSourceLimits(), state.setupDraft.max_results);
  }

  function toggleSource(source) {
    const current = state.setupDraft.platforms;
    state.setupDraft.platforms = current.includes(source)
      ? current.filter((item) => item !== source)
      : [...current, source];
    if (!state.setupDraft.platforms.length) state.setupDraft.platforms = [source];
    if (!state.setupDraft.source_limits || !Object.prototype.hasOwnProperty.call(state.setupDraft.source_limits, source)) {
      state.setupDraft.source_limits = {
        ...(state.setupDraft.source_limits || {}),
        [source]: state.setupDraft.max_results || DEFAULT_MAX_RESULTS_PER_PLATFORM,
      };
    }
    state.setupDraft.max_results = maxResultsFromSourceLimits(selectedSourceLimits(), state.setupDraft.max_results);
  }

  function categorizationCategoriesFromDraft() {
    return String(state.catDraft.categoriesText || '')
      .split('\n')
      .map((item) => item.trim())
      .filter(Boolean);
  }

  function categorizationActionPayload() {
    return {
      field: state.catDraft.field,
      mode: state.catDraft.mode,
      categories: categorizationCategoriesFromDraft(),
      category_descriptions: D.categorizationWorkflow.categoryDescriptions || {},
    };
  }

  function updateCategorizationField(field) {
    state.catDraft.field = esc(field);
    state.catDraft.confirmed = false;
    state.catDraft.skipped = false;
  }

  function updateCategorizationMode(mode) {
    state.catDraft.mode = mode === 'single' ? 'single' : 'multiple';
    state.catDraft.confirmed = false;
  }

  function wireHover(root) {
    root.querySelectorAll('[data-hover]').forEach((el) => {
      const hov = el.getAttribute('data-hover');
      const base = el.getAttribute('style') || '';
      el.addEventListener('mouseenter', () => el.setAttribute('style', base + ';' + hov));
      el.addEventListener('mouseleave', () => el.setAttribute('style', base));
    });
  }

  function focusResearchTopicInput(root) {
    const input = root.querySelector('[data-ui="research-topic-input"]');
    if (!input) return;
    input.focus({ preventScroll: true });
  }

  function mount() {
    const root = document.getElementById('app');
    function paint() {
      root.innerHTML = render(computeVals());
      const conv = document.getElementById('rp-conv');
      if (conv) conv.scrollTop = conv.scrollHeight;
      wireHover(root);
      if (state.chatInputFocus) {
        state.chatInputFocus = false;
        focusResearchTopicInput(root);
      }
      writeWorkspaceSnapshot();
      if (state.actionPending && !actionTicker) {
        actionTicker = setInterval(paint, 1000);
      } else if (!state.actionPending && actionTicker) {
        clearInterval(actionTicker);
        actionTicker = null;
      }
    }

    async function resumeActiveTask() {
      if (!D.activeTask || !D.activeTask.task_id) return;
      const projectId = D.activeTask.project_id || state.activeProjectId || D.project.id;
      try {
        await waitForTask(D.activeTask.task_id);
        setData(await fetchProjectState(projectId), false, { preserveView: true });
      } catch (err) {
        state.actionError = err.message || String(err);
      } finally {
        state.actionPending = '';
        state.actionStartedAt = 0;
        D.activeTask = null;
        paint();
      }
    }

    async function submitChatForm(form) {
      const input = form.querySelector('input[name="message"]');
      const pending = handleChatSubmit(input ? input.value : '');
      state.quickStartOpen = false;
      form.reset();
      paint();
      await pending;
    }

    root.addEventListener('click', (e) => {
      const insideChatInputArea = e.target.closest('[data-ui="assistant-chat-input-area"]');
      if (state.quickStartOpen && !insideChatInputArea) state.quickStartOpen = false;
      const t = e.target.closest('[data-act]');
      if (!t) {
        if (!insideChatInputArea) paint();
        return;
      }
      const act = t.getAttribute('data-act');
      if (act === 'step') {
        if (t.getAttribute('data-disabled') === 'true') return;
        state.step = t.getAttribute('data-step');
      }
      else if (act === 'tab') state.tab = t.getAttribute('data-tab');
      else if (act === 'new-project') {
        setData(newProjectDataWithCurrentHistory(), true);
        state.dialog = '';
        state.chatInputFocus = true;
      }
      else if (act === 'starter-topic') {
        const topic = t.getAttribute('data-topic') || '';
        const pending = handleChatSubmit(topic);
        paint();
        pending.then(paint).catch((err) => { state.actionError = err.message || String(err); paint(); });
        return;
      }
      else if (act === 'chat-quick-start') {
        const topic = t.getAttribute('data-topic') || '';
        state.quickStartOpen = false;
        const pending = handleChatSubmit(topic);
        paint();
        pending.then(paint).catch((err) => { state.actionError = err.message || String(err); paint(); });
        return;
      }
      else if (act === 'project') {
        const projectId = t.getAttribute('data-project');
        selectProject(projectId).then(paint).catch((err) => { state.actionError = err.message || String(err); paint(); });
        return;
      }
      else if (act === 'open-setup') state.dialog = 'setup';
      else if (act === 'close-dialog') {
        state.dialog = '';
        state.actionError = '';
      }
      else if (act === 'toggle-source') toggleSource(t.getAttribute('data-source'));
      else if (act === 'add-keyword') state.dialog = 'keyword';
      else if (act === 'remove-keyword') {
        const keyword = t.getAttribute('data-keyword');
        state.setupDraft.keywords = state.setupDraft.keywords.filter((kw) => kw !== keyword);
      }
      else if (act === 'cat-mode') updateCategorizationMode(t.getAttribute('data-mode'));
      else if (act === 'confirm-categories') {
        state.catDraft.confirmed = true;
      }
      else if (act === 'edit-categories') {
        state.catDraft.confirmed = false;
      }
      else if (act === 'skip-categorization') {
        state.catDraft.skipped = true;
      }
      else if (act === 'finalize-project') {
        state.catDraft.finalized = true;
      }
      else if (act === 'action') {
        const actionName = t.getAttribute('data-action');
        const payload = ['suggest-categories', 'categorize'].includes(actionName) ? categorizationActionPayload() : null;
        if (actionName === 'categorize' && (!payload.categories || !payload.categories.length)) {
          state.actionError = 'Confirm at least one category before applying categorization.';
          paint();
          return;
        }
        state.actionPending = actionName;
        state.actionStartedAt = Date.now();
        state.actionError = '';
        paint();
        postAction(actionName, payload)
          .catch((err) => { state.actionError = err.message || String(err); })
          .finally(() => {
            state.actionPending = '';
            state.actionStartedAt = 0;
            paint();
          });
        return;
      }
      paint();
    });

    root.addEventListener('focusin', (e) => {
      if (!e.target.matches || !e.target.matches('[data-ui="research-topic-input"]')) return;
      if (state.quickStartOpen) return;
      state.quickStartOpen = true;
      state.chatInputFocus = true;
      setTimeout(paint, 0);
    });

    root.addEventListener('input', (e) => {
      if (e.target.matches && e.target.matches('[data-ui="research-topic-input"]')) {
        if (!state.quickStartOpen) {
          state.quickStartOpen = true;
          writeWorkspaceSnapshot();
        }
        return;
      }
      const sourceLimit = e.target.getAttribute('data-source-limit');
      if (sourceLimit) {
        updateSourceLimit(sourceLimit, e.target.value);
        writeWorkspaceSnapshot();
        return;
      }
      const draftField = e.target.getAttribute('data-draft-field');
      const catCategories = e.target.getAttribute('data-cat-categories');
      if (catCategories) {
        state.catDraft.categoriesText = e.target.value;
        state.catDraft.confirmed = false;
        writeWorkspaceSnapshot();
        return;
      }
      if (!draftField) return;
      updateSetupDraftField(draftField, e.target.value);
      writeWorkspaceSnapshot();
    });

    root.addEventListener('change', (e) => {
      const sourceLimit = e.target.getAttribute('data-source-limit');
      if (sourceLimit) {
        updateSourceLimit(sourceLimit, e.target.value);
        writeWorkspaceSnapshot();
        return;
      }
      const draftField = e.target.getAttribute('data-draft-field');
      const catField = e.target.getAttribute('data-cat-field');
      if (catField) {
        updateCategorizationField(e.target.value);
        writeWorkspaceSnapshot();
        paint();
        return;
      }
      if (!draftField) return;
      updateSetupDraftField(draftField, e.target.value);
      paint();
    });

    root.addEventListener('keydown', (e) => {
      if (e.key !== 'Enter' || e.shiftKey || e.isComposing) return;
      const form = e.target.closest ? e.target.closest('#rp-chat-form') : null;
      if (!form) return;
      e.preventDefault();
      submitChatForm(form).then(paint).catch((err) => { state.actionError = err.message || String(err); paint(); });
    });

    root.addEventListener('submit', (e) => {
      if (e.target.id === 'rp-chat-form') {
        e.preventDefault();
        submitChatForm(e.target).then(paint).catch((err) => { state.actionError = err.message || String(err); paint(); });
        return;
      }
      if (e.target.id === 'rp-keyword-dialog-form') {
        e.preventDefault();
        const keyword = String(new FormData(e.target).get('keyword') || '').trim();
        if (keyword && !state.setupDraft.keywords.includes(keyword)) state.setupDraft.keywords.push(esc(keyword));
        state.dialog = '';
        paint();
        return;
      }
      if (e.target.id !== 'rp-setup-dialog-form') return;
      e.preventDefault();
      updateDraftFromForm(e.target);
      const submit = e.target.querySelector('button[type="submit"]');
      if (submit) submit.disabled = true;
      const save = D.isNewProject ? createProject : updateProjectSetup;
      save(e.target).then(paint).catch((err) => {
        state.actionError = err.message || String(err);
        paint();
      });
    });

    paint();
    resumeActiveTask();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount);
  else mount();
})();
