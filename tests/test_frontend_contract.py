from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class FrontendContractTests(unittest.TestCase):
    def test_retrieval_recovery_panel_is_accessible_index_bound_and_hides_full_download(self):
        source = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
        panel = source[source.index("function retrievalRecoveryPanel") : source.index("function extractionCanvas")]
        compute = source[source.index("function computeVals") : source.index("function draftStep")]

        self.assertIn("retrievalRecovery: normalizeRetrievalRecovery", source)
        self.assertIn("<fieldset", panel)
        self.assertIn("<legend", panel)
        self.assertIn('type="checkbox"', panel)
        self.assertIn('data-retry-index="${index}"', panel)
        self.assertNotIn("data-retry-id", panel)
        self.assertIn('data-act="retry-select-all"', panel)
        self.assertIn('data-act="retry-clear"', panel)
        self.assertIn('data-act="retry-submit"', panel)
        self.assertIn('role="status" aria-live="polite"', panel)
        self.assertIn("retryRecoveryRunning", compute)
        self.assertIn("showCanvasAction", compute)
        self.assertIn("!retryRecoveryVisible", compute)

    def test_retry_click_routing_canonicalizes_selection_and_prevents_double_submit(self):
        source = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
        router = source[source.index("root.addEventListener('click'") : source.index("root.addEventListener('focusin'")]

        self.assertIn("act === 'retry-toggle'", router)
        self.assertIn("act === 'retry-select-all'", router)
        self.assertIn("act === 'retry-clear'", router)
        self.assertIn("act === 'retry-submit'", router)
        self.assertIn("if (state.actionPending) return;", router)
        self.assertIn("orderedRetryIds", router)
        self.assertIn("retry-failed-downloads", router)

    def test_setup_update_handles_impact_preview_and_revision_confirmation(self):
        source = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
        self.assertIn("confirmationRequired", source)
        self.assertIn("expected_revision", source)
        self.assertIn("affectedStages", source)

    def test_normalized_refresh_state_retains_server_owned_stage_state(self):
        source = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")

        self.assertIn("stageState: data.stageState || {},", source)

    def test_new_review_fallbacks_and_visible_sources_use_frozen_platform_order(self):
        source = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
        setup_draft = source[source.index("function setupDraftFromData") : source.index("function sourceLimitValue")]
        normalize_limits = source[source.index("function normalizeSourceLimits") : source.index("function selectedSourceLimits")]
        compute_vals = source[source.index("function computeVals") : source.index("const retrievalPct", source.index("function computeVals"))]

        frozen_order = "['pubmed', 'arxiv', 'openalex']"
        self.assertIn(frozen_order, setup_draft)
        self.assertNotIn("['pubmed', 'openalex', 'arxiv']", setup_draft)
        self.assertIn(frozen_order, normalize_limits)
        self.assertNotIn("['pubmed', 'openalex', 'arxiv']", normalize_limits)
        self.assertIn(f"const draftSources = {frozen_order}.map", compute_vals)

    def test_search_setup_dialog_propagates_changed_maximum_after_capturing_previous_value(self):
        source = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
        update = source[source.index("function updateDraftFromForm") : source.index("function updateSetupDraftField")]

        previous_capture = "const previousMaxResults = state.setupDraft.max_results;"
        payload_merge = "const mergedDraft = { ...state.setupDraft, ...payload };"
        helper_call = "state.setupDraft = applySubmittedMaxToSourceLimits(mergedDraft, previousMaxResults, payload.max_results);"
        self.assertIn(previous_capture, update)
        self.assertIn(payload_merge, update)
        self.assertIn(helper_call, update)
        self.assertLess(update.index(previous_capture), update.index(payload_merge))
        self.assertLess(update.index(payload_merge), update.index(helper_call))

    def test_unbound_click_branch_uses_form_safe_paint_decision(self):
        source = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
        click_router = source[source.index("root.addEventListener('click'") : source.index("root.addEventListener('focusin'")]
        unbound_branch = click_router[click_router.index("if (!t)") : click_router.index("const act =")]

        self.assertIn("const quickStartWasOpen = state.quickStartOpen;", click_router)
        self.assertIn("e.target.closest('form')", click_router)
        self.assertIn("const shouldCloseQuickStart = quickStartWasOpen && !insideChatInputArea;", click_router)
        self.assertIn("shouldPaintUnboundClick({ insideForm, insideChatInputArea, shouldCloseQuickStart })", unbound_branch)
        self.assertNotIn("if (!insideChatInputArea) paint()", unbound_branch)

    def test_workspace_interactions_do_not_navigate_to_project_pages(self):
        source = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")

        self.assertNotIn("window.location.href = '/projects/new'", source)
        self.assertNotIn("window.location.href = `/projects/${encodeURIComponent", source)
        self.assertIn("fetchProjectState", source)

    def test_workspace_header_does_not_expose_search_setup_button(self):
        source = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
        workspace_header = source[source.index("function workspaceHeader(v)") : source.index("function stepItem(s)")]

        self.assertNotIn('data-act="open-setup"', workspace_header)
        self.assertNotIn('title="Edit search setup"', workspace_header)

    def test_search_setup_typing_is_dialog_based_not_canvas_based(self):
        source = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")

        self.assertNotIn('id="rp-search-setup-form" style="border:1px solid #e5e7eb', source)
        self.assertIn('id="rp-setup-dialog-form"', source)
        self.assertIn('data-act="add-keyword"', source)

    def test_new_review_opens_search_setup_canvas_without_auto_dialog(self):
        source = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")

        self.assertNotIn("dialog: D.isNewProject ? 'setup' : ''", source)
        self.assertIn("dialog: ''", source)
        self.assertNotIn("setData(NEW_PROJECT_TEMPLATE);\n        state.dialog = 'setup';", source)
        self.assertIn("setData(newProjectDataWithCurrentHistory(), true);\n        state.dialog = '';", source)

    def test_new_review_uses_review_language_and_focuses_chat_topic_input(self):
        source = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")

        self.assertIn(">New Review</button>", source)
        self.assertNotIn(">New review</button>", source)
        self.assertNotIn(">New conversation</button>", source)
        self.assertIn("chatInputFocus", source)
        self.assertIn("state.chatInputFocus = true", source)
        self.assertIn("focusResearchTopicInput(root)", source)
        self.assertIn('data-ui="research-topic-input"', source)
        self.assertIn("Describe your research topic...", source)

    def test_sidebar_uses_historys_and_keeps_new_review_in_history(self):
        source = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
        project_nav_item = source[source.index("function projectNavItem") : source.index("function workspaceHeader")]
        click_router = source[source.index("root.addEventListener('click'") : source.index("root.addEventListener('focusin'")]
        restore_snapshot = source[source.index("function restoreWorkspaceSnapshot") : source.index("function shouldRestoreSnapshotDataForRoute")]

        self.assertIn("label: 'Historys'", source)
        self.assertIn('data-ui="history-label"', source)
        self.assertNotIn('data-ui="history-label" style="font-size:10px;letter-spacing:0.08em;text-transform:uppercase', source)
        self.assertNotIn(">PROJECTS</div>", source)
        self.assertIn("historyGroupsForView(D.history)", source)
        self.assertIn('? `data-act="new-project"`', project_nav_item)
        self.assertIn("h.isNewProject", project_nav_item)
        self.assertIn("title: 'Untitled review'", source)
        self.assertIn("const icon = h.active ? 'ph-fill ph-chat-circle' : 'ph ph-chat-circle';", project_nav_item)
        self.assertIn('data-act="history-quick-start"', project_nav_item)
        self.assertIn("h.starterTopic", project_nav_item)
        self.assertIn("act === 'history-quick-start'", click_router)
        self.assertIn("setData(newProjectDataWithCurrentHistory(), true);", click_router)
        self.assertIn("const pending = handleChatSubmit(topic);", click_router)
        self.assertIn("const authoritativeHistory = D.history;", restore_snapshot)
        self.assertIn("D.history = authoritativeHistory;", restore_snapshot)
        self.assertNotIn("ph-plus-circle", project_nav_item)
        self.assertNotIn("title: D.project.title || 'Untitled review'", source)

    def test_untitled_review_starts_from_chat_topic_input(self):
        source = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")

        self.assertIn('id="rp-chat-form"', source)
        self.assertIn('name="message"', source)
        self.assertIn("handleChatSubmit", source)
        self.assertIn("createProjectFromChat", source)
        self.assertIn("Welcome to ReviewPilot", source)
        self.assertIn("ReviewPilot helps you turn a research topic into a literature review.", source)
        self.assertIn("${logo(56)}", source)
        self.assertNotIn("rp-welcome-step-pills", source)
        self.assertIn("STARTER_TOPICS", source)
        self.assertIn('data-ui="starter-topic-buttons"', source)
        self.assertIn("grid-template-columns:minmax(0,1fr)", source)
        self.assertNotIn("grid-template-columns:repeat(3,minmax(0,1fr))", source)
        self.assertIn('data-act="starter-topic"', source)
        self.assertIn("I want to review how LLMs are used in biomedical research and clinical care.", source)
        self.assertIn("I want to review how LLMs are changing human-computer interaction.", source)
        self.assertIn("I want to review how LLMs support urban planning and smart cities.", source)
        starter_topics = source[source.index("const STARTER_TOPICS") : source.index("let D = normalizeData")]
        self.assertNotIn("'LLM for Biomedical'", starter_topics)
        self.assertNotIn("'LLM for HCI'", starter_topics)
        self.assertNotIn("'LLM for Urban'", starter_topics)
        self.assertIn("const pending = handleChatSubmit(topic);", source)
        self.assertIn("Categorization & Analysis", source)
        self.assertIn("final Categorization & Analysis report", source)
        self.assertIn("To get started, describe your research topic in the chat.", source)
        self.assertIn("white-space:pre-line", source)
        self.assertNotIn("ReviewPilot turns your topic into a five-step review, from search setup to final Categorization & Analysis.", source)
        self.assertNotIn("lead agent", source.lower())
        self.assertNotIn("specialist agents", source)
        self.assertNotIn('Example: "Survey of using LLM for rare disease diagnosis"', source)
        self.assertNotIn("I'll guide you through a systematic literature review in 5 steps, ending with a final Categorization & Analysis report.", source)
        self.assertNotIn("Final output: grouped evidence and exportable results.", source)
        self.assertNotIn("Project question still needs to be entered in the setup dialog.", source)

    def test_new_review_chat_creates_project_through_backend_agent(self):
        source = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
        chat_submit = source[source.index("function handleChatSubmit") : source.index("function computeVals")]

        self.assertIn("async function createProjectFromChat(message)", source)
        self.assertIn("async function sendProjectChat(message)", source)
        self.assertIn("derive_search_terms: true", source)
        self.assertIn("await createProjectFromChat(message)", chat_submit)
        self.assertIn("await sendProjectChat(message)", chat_submit)
        self.assertNotIn("draftSearchSetupFromTopic(message)", chat_submit)
        self.assertNotIn("I drafted a Search Setup from your topic.", source)
        self.assertNotIn("live project chat actions are not wired", source)
        self.assertNotIn("I captured that.", source)

    def test_chat_drafted_search_setup_reuses_the_existing_canvas(self):
        source = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")

        self.assertNotIn("Drafted from your chat topic", source)
        self.assertNotIn("Use canvas clicks for sources and keywords", source)
        self.assertNotIn("Project details", source)
        self.assertNotIn("function setupCanvasControls", source)
        self.assertIn(">Research question</div>", source)
        self.assertIn(">Keywords</div>", source)
        self.assertIn(">Sources</div>", source)

    def test_chat_input_submits_with_enter_like_assistant_clients(self):
        source = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")

        self.assertIn("root.addEventListener('keydown'", source)
        self.assertIn("e.key !== 'Enter'", source)
        self.assertIn("handleChatSubmit(input ? input.value : '')", source)

    def test_chat_input_quick_start_uses_the_three_review_topics(self):
        source = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
        assistant_panel = source[source.index("function assistantPanel") : source.index("function setupDialog")]
        click_router = source[source.index("root.addEventListener('click'") : source.index("root.addEventListener('focusin'")]
        focus_router = source[source.index("root.addEventListener('focusin'") : source.index("root.addEventListener('input'")]

        self.assertIn("quickStartOpen: false", source)
        self.assertIn("quickStartOpen: state.quickStartOpen", source)
        self.assertIn("function chatQuickStartPopover", source)
        self.assertIn('data-ui="chat-quick-start"', source)
        self.assertIn('data-ui="assistant-chat-input-area"', assistant_panel)
        self.assertIn('data-act="chat-quick-start"', source)
        self.assertIn("STARTER_TOPICS", source)
        self.assertIn("root.addEventListener('focusin'", source)
        self.assertIn("[data-ui=\"research-topic-input\"]", focus_router)
        self.assertIn("state.quickStartOpen = true", focus_router)
        self.assertIn("setTimeout(paint, 0)", focus_router)
        self.assertIn("act === 'chat-quick-start'", click_router)
        self.assertIn("const topic = t.getAttribute('data-topic') || '';", click_router)
        self.assertIn("const pending = handleChatSubmit(topic);", click_router)
        self.assertIn("state.quickStartOpen = false", click_router)
        self.assertIn("I want to review how LLMs are used in biomedical research and clinical care.", source)
        self.assertIn("I want to review how LLMs are changing human-computer interaction.", source)
        self.assertIn("I want to review how LLMs support urban planning and smart cities.", source)

    def test_chat_waiting_state_renders_thinking_bubble_and_preserves_history(self):
        source = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")

        self.assertIn("chatPending: false", source)
        self.assertIn("state.chatPending = true", source)
        self.assertIn("state.chatPending = false", source)
        self.assertIn("function thinkingBubble", source)
        self.assertIn('data-ui="thinking-bubble"', source)
        self.assertIn(">Thinking</span>", source)
        self.assertIn("@keyframes rp-thinking-bounce", source)
        self.assertIn("preservedChatMessages", source)
        self.assertIn("function mergeConversationMessages", source)
        self.assertIn("state.preservedChatMessages = D.messages.slice();", source)

    def test_workflow_action_buttons_render_on_canvas_not_chat_thread(self):
        source = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
        assistant_panel = source[source.index("function assistantPanel") : source.index("function setupDialog")]
        render_main = source[source.index('<main class="rp-main"') : source.index("${assistantPanel(v)}")]

        self.assertNotIn('data-act="action"', assistant_panel)
        self.assertNotIn("showQuietAction", assistant_panel)
        self.assertIn("function canvasActionButton", source)
        self.assertIn("${canvasActionButton(v)}", render_main)

    def test_stage_activity_renders_as_conversation_messages_not_fixed_panel(self):
        source = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
        assistant_panel = source[source.index("function assistantPanel") : source.index("function setupDialog")]
        render_main = source[source.index('<main class="rp-main"') : source.index("${assistantPanel(v)}")]
        activity_message = source[source.index("function activityMessage(m)") : source.index("function assistantMessageHtml")]

        self.assertIn("function activityMessagesForVisibleSteps", source)
        self.assertIn("function conversationMessagesWithActivity", source)
        self.assertIn("role: 'activity'", source)
        self.assertIn("isActivity: true", source)
        self.assertIn("emittedActivitySteps.has(step)", source)
        self.assertIn("const emitActivitiesBefore = (step) =>", source)
        self.assertIn("message.step >= step", source)
        self.assertIn("if (step > 1 && activitiesByStep.has(step)", source)
        self.assertIn("if (step === 1 && nextStep !== step", source)
        self.assertIn("highestSeenStep = Math.max(highestSeenStep, step)", source)
        self.assertIn("${v.chat.map(chatMessage).join('')}", assistant_panel)
        self.assertNotIn("${activityPanel(v)}", assistant_panel)
        self.assertNotIn("${activityPanel(v)}", render_main)
        self.assertIn('data-ui="assistant-activity-message"', activity_message)
        self.assertIn('data-ui="assistant-activity-card"', activity_message)
        self.assertIn("Activity · ${m.activityTitle}", activity_message)
        self.assertIn("msg.includes('waiting for')", source)
        self.assertNotIn("data-act=", activity_message)

    def test_workspace_refresh_restores_last_tab_state(self):
        source = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")

        self.assertIn("WORKSPACE_SNAPSHOT_KEY", source)
        self.assertIn("sessionStorage.getItem(WORKSPACE_SNAPSHOT_KEY)", source)
        self.assertIn("sessionStorage.setItem(WORKSPACE_SNAPSHOT_KEY", source)
        self.assertIn("restoreWorkspaceSnapshot();", source)
        self.assertIn("writeWorkspaceSnapshot();", source)
        self.assertIn("data: snapshotData(D)", source)
        self.assertIn("step: state.step", source)
        self.assertIn("tab: state.tab", source)
        self.assertIn("activeProjectId: state.activeProjectId", source)
        self.assertIn("setupDraft: state.setupDraft", source)

    def test_refresh_migrates_stale_new_review_welcome_without_dropping_state(self):
        source = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")

        self.assertIn("OLD_NEW_REVIEW_WELCOME", source)
        self.assertIn("DOUBLE_ESCAPED_NEW_REVIEW_WELCOME_FRAGMENT", source)
        self.assertIn("function migrateWorkspaceSnapshotData(data)", source)
        self.assertIn("const shouldRestoreSnapshotData = shouldRestoreSnapshotDataForRoute(snapshot);", source)
        self.assertIn("if (shouldRestoreSnapshotData) {", source)
        self.assertIn("D = migrateWorkspaceSnapshotData(snapshot.data);", source)
        self.assertIn("firstText.includes(DOUBLE_ESCAPED_NEW_REVIEW_WELCOME_FRAGMENT)", source)
        self.assertIn("NEW_PROJECT_TEMPLATE.messages[0].text", source)

    def test_workspace_refresh_restores_current_snapshot_but_deep_links_stay_authoritative(self):
        source = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
        restore = source[source.index("function restoreWorkspaceSnapshot") : source.index("function writeWorkspaceSnapshot")]
        route_guard = source[source.index("function shouldRestoreSnapshotDataForRoute") : source.index("function writeWorkspaceSnapshot")]

        self.assertIn("const shouldRestoreSnapshotData = shouldRestoreSnapshotDataForRoute(snapshot);", restore)
        self.assertIn("const isWorkspaceRoute = path === '' || path === '/' || path === '/workspace';", route_guard)
        self.assertIn("if (isWorkspaceRoute) return true;", route_guard)
        self.assertIn("return D.isNewProject && !!snapshot.data?.isNewProject;", route_guard)
        self.assertIn("state.step = shouldRestoreSnapshotData && stepKeys.has(ui.step) ? ui.step : initialStep(D);", restore)
        self.assertIn("state.activeProjectId = D.project.id || (shouldRestoreSnapshotData ? ui.activeProjectId : '') || '';", restore)
        self.assertIn("if ((shouldRestoreSnapshotData || sameProject) && ui.setupDraft)", restore)

    def test_refresh_migrates_stale_four_step_snapshot_to_current_five_step_workflow(self):
        source = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")

        self.assertIn("const templateSteps = NEW_PROJECT_TEMPLATE.steps || [];", source)
        self.assertIn("templateSteps.length > migrated.steps.length", source)
        self.assertIn("const existingStepsByKey = new Map(migrated.steps.map((step) => [step.key, step]));", source)
        self.assertIn("migrated.steps = templateSteps.map((step) => ({", source)
        self.assertIn("migrated.activityByStep = { ...NEW_PROJECT_TEMPLATE.activityByStep, ...migrated.activityByStep };", source)
        self.assertIn("migrated.ctxLabels = { ...NEW_PROJECT_TEMPLATE.ctxLabels, ...migrated.ctxLabels };", source)

    def test_welcome_renderer_pads_stale_four_row_markdown_with_final_report_step(self):
        source = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")

        self.assertIn("const mergedRows = fallbackRows.map((fallback) => rows.find((row) => row.n === fallback.n) || fallback);", source)
        self.assertIn("const steps = mergedRows", source)
        self.assertIn("Generate the final Categorization & Analysis report", source)
        self.assertNotIn("Generate the final Categorization & Analysis report with semantic groups", source)

    def test_welcome_canvas_does_not_duplicate_workflow_chips(self):
        source = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
        welcome_canvas = source[source.index("function searchCanvas(v)") : source.index("return `<div style=\"border:1px solid #e5e7eb")]

        self.assertNotIn("rp-welcome-step-pills", source)
        self.assertNotIn("flex-wrap:nowrap", welcome_canvas)
        self.assertNotIn("max-width:760px", welcome_canvas)
        self.assertNotIn("white-space:nowrap", welcome_canvas)

    def test_new_review_template_is_not_double_escaped(self):
        source = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")

        self.assertIn("function setData(data, alreadyEscaped = false, { preserveView = false } = {})", source)
        self.assertIn("const nextData = normalizeData(alreadyEscaped ? data : escapeData(data || {}));", source)
        self.assertIn("D = nextData;", source)
        self.assertIn("setData(newProjectDataWithCurrentHistory(), true);", source)

    def test_new_review_uses_fixed_demo_history_template(self):
        source = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
        new_project_click = source[source.index("else if (act === 'new-project')") : source.index("else if (act === 'project')")]
        new_project_data = source[source.index("function newProjectDataWithCurrentHistory") : source.index("function sourceChecklist")]

        self.assertIn("function newProjectDataWithCurrentHistory()", source)
        self.assertIn("return normalizeData(JSON.parse(JSON.stringify(NEW_PROJECT_TEMPLATE)));", new_project_data)
        self.assertNotIn("function inactiveProjectItemsFromHistory", source)
        self.assertNotIn("if (!D.isNewProject && D.project.id)", source)
        self.assertIn("setData(newProjectDataWithCurrentHistory(), true);", new_project_click)
        self.assertNotIn("setData(NEW_PROJECT_TEMPLATE, true);", new_project_click)

    def test_assistant_welcome_renders_as_native_ui_not_raw_markdown(self):
        source = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")

        self.assertIn("function assistantMessageHtml(text)", source)
        self.assertIn("function prototypeWelcomeHtml(text)", source)
        self.assertIn("| Step | Description |", source)
        self.assertIn("prototype-welcome-steps", source)
        self.assertIn("${assistantMessageHtml(m.text)}", source)
        self.assertNotIn("Categorization queued", source)

    def test_workspace_shell_uses_full_browser_viewport(self):
        app_source = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
        html_source = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")

        self.assertNotIn("width:1280px", app_source)
        self.assertNotIn("height:860px", app_source)
        self.assertIn("width:100vw;height:100vh", app_source)
        self.assertNotIn("padding: 24px", html_source)
        self.assertIn("#app { width: 100vw; height: 100vh; }", html_source)

    def test_search_setup_keywords_are_card_grid_controls(self):
        source = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")

        self.assertIn('data-ui="keyword-card-grid"', source)
        self.assertIn('<div data-ui="keyword-card"', source)
        self.assertNotIn('<span data-ui="keyword-card"', source)
        self.assertNotIn('data-ui="keyword-card-plus"', source)
        self.assertIn('data-ui="keyword-card-remove"', source)
        self.assertIn('data-act="remove-keyword"', source)
        self.assertIn("ph ph-x", source)
        self.assertIn('data-ui="keyword-add-card"', source)
        self.assertIn("gap:7px", source)
        self.assertIn("border:1px dashed #cfe0f5", source)
        self.assertIn("border:1px solid #cfe0f5", source)
        self.assertIn("background:#eaf0f7", source)
        self.assertIn("font-size:11.5px", source)
        self.assertIn("Add keyword", source)
        self.assertNotIn('<i class="ph ph-pencil-simple" style="font-size:13px;"></i>Edit', source)

    def test_search_setup_sources_are_checkbox_controls_not_counts(self):
        source = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")

        self.assertIn("function sourceChecklist(sources, sourceLimits, fallbackMaxResults)", source)
        self.assertIn('data-ui="source-checklist"', source)
        self.assertIn('data-ui="source-row"', source)
        self.assertIn("grid-template-columns:minmax(0,1fr) auto", source)
        self.assertIn('role="checkbox"', source)
        self.assertIn('aria-checked="${source.selected ? \'true\' : \'false\'}"', source)
        self.assertIn('data-act="toggle-source"', source)
        self.assertIn('data-ui="source-check-circle"', source)
        self.assertIn("border-radius:999px;padding:7px 11px", source)
        self.assertIn("width:14px;height:14px;border-radius:999px", source)
        self.assertIn('data-source-limit="${source.key}"', source)
        self.assertNotIn('data-draft-field="max_results" type="number"', source)
        self.assertIn("Max results/platform", source)
        self.assertIn("${source.selected ? '' : 'disabled'}", source)
        self.assertIn("${sourceChecklist(v.draftSources, v.setupDraft.source_limits, v.setupDraft.max_results)}", source)
        self.assertNotIn("width:14px;height:14px;border-radius:4px", source)
        self.assertNotIn("${v.platforms.map(sourceRow).join('')}\n      </div>`;\n  }\n\n  function keywordGrid", source)

    def test_search_setup_date_range_is_a_standalone_canvas_block(self):
        source = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")

        self.assertIn("function dateRangeCard(setupDraft)", source)
        self.assertIn('data-ui="date-range-card"', source)
        self.assertIn(">Date range</div>", source)
        self.assertIn('data-ui="date-range-summary"', source)
        self.assertIn('data-ui="date-range-inputs"', source)
        self.assertIn("grid-template-columns:repeat(2,minmax(0,1fr))", source)
        self.assertIn('data-ui="date-range-field"', source)
        self.assertIn('data-ui="date-range-note"', source)
        self.assertIn('data-draft-field="date_start"', source)
        self.assertIn('data-draft-field="date_end"', source)
        self.assertIn("${dateRangeCard(v.setupDraft)}", source)

    def test_search_setup_sources_and_date_range_are_side_by_side(self):
        source = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
        search_canvas = source[source.index("function searchCanvas(v)") : source.index("function keywordGrid")]

        self.assertIn('data-ui="search-setup-controls"', search_canvas)
        self.assertIn("grid-template-columns:repeat(2,minmax(0,1fr))", search_canvas)
        self.assertIn("align-items:stretch", search_canvas)
        self.assertIn('data-ui="sources-card"', search_canvas)
        self.assertIn("height:100%;box-sizing:border-box", search_canvas)
        self.assertLess(search_canvas.index('data-ui="sources-card"'), search_canvas.index("${dateRangeCard(v.setupDraft)}"))

    def test_search_setup_canvas_inputs_update_the_setup_draft(self):
        source = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")

        self.assertIn("function updateSetupDraftField(field, value)", source)
        self.assertIn("function updateSourceLimit(source, value)", source)
        self.assertIn("const sourceLimit = e.target.getAttribute('data-source-limit');", source)
        self.assertIn("updateSourceLimit(sourceLimit, e.target.value);", source)
        self.assertIn("root.addEventListener('input'", source)
        self.assertIn("const draftField = e.target.getAttribute('data-draft-field');", source)
        self.assertIn("updateSetupDraftField(draftField, e.target.value);", source)
        self.assertNotIn("function syncMaxResultsInputs", source)

    def test_run_collection_persists_canvas_setup_draft_before_action(self):
        source = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")

        self.assertIn("function setupPayloadFromDraft(overrides = {})", source)
        self.assertIn("async function saveDraftSetup(projectId)", source)
        self.assertIn("if (action === 'collect') await saveDraftSetup(projectId);", source)
        self.assertIn("source_limits: sourceLimits", source)
        self.assertIn("date_start: unescapePayloadValue(state.setupDraft.date_start)", source)
        self.assertIn("date_end: unescapePayloadValue(state.setupDraft.date_end)", source)
        self.assertIn("function unescapePayloadValue(value)", source)
        self.assertIn("unescapePayloadValue(state.setupDraft.search_terms)", source)
        self.assertLess(
            source.index("if (action === 'collect') await saveDraftSetup(projectId);"),
            source.index("const res = await fetch(`/projects/${encodeURIComponent(projectId)}/actions/${action}`"),
        )

    def test_action_failure_uses_server_detail_when_available(self):
        source = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
        post_action = source[source.index("async function postAction") : source.index("async function createProject")]

        self.assertIn("const body = await res.json().catch(() => null);", post_action)
        self.assertIn("body && typeof body.detail === 'string' && body.detail.trim()", post_action)
        self.assertIn("? body.detail : `Action failed: ${res.status}`", post_action)
        self.assertIn("throw new Error(detail);", post_action)

    def test_max_results_per_platform_defaults_to_ten_in_frontend_payload(self):
        source = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")

        self.assertIn("const DEFAULT_MAX_RESULTS_PER_PLATFORM = '10';", source)
        self.assertIn("const fallbackMaxResults = String(setup.max_results || DEFAULT_MAX_RESULTS_PER_PLATFORM);", source)
        self.assertIn("return String(sourceLimits[source] || fallbackMaxResults || DEFAULT_MAX_RESULTS_PER_PLATFORM);", source)
        self.assertIn("function maxResultsFromSourceLimits(sourceLimits, fallbackMaxResults = DEFAULT_MAX_RESULTS_PER_PLATFORM)", source)
        self.assertIn("[source]: state.setupDraft.max_results || DEFAULT_MAX_RESULTS_PER_PLATFORM", source)
        self.assertIn("dialogInput('Max/source', 'max_results', d.max_results, DEFAULT_MAX_RESULTS_PER_PLATFORM", source)
        self.assertNotIn("setup.max_results || '50'", source)
        self.assertNotIn("fallbackMaxResults = '50'", source)

    def test_workflow_actions_render_immediate_running_state(self):
        source = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
        action_branch = source[source.index("else if (act === 'action')") : source.index("root.addEventListener('input'")]
        canvas_action = source[source.index("function canvasActionButton(v)") : source.index("function screeningCanvas(v)")]

        self.assertIn("actionPending: D.activeTask?.action || ''", source)
        self.assertIn("state.actionPending = actionName;", action_branch)
        self.assertIn("paint();", action_branch)
        self.assertNotIn("state.actionPending = '';", action_branch)
        self.assertIn("syncActionState(null);", source)
        self.assertIn("data-ui=\"canvas-action-spinner\"", canvas_action)
        self.assertIn("data-ui=\"canvas-action-button\"", canvas_action)
        self.assertIn("Running", canvas_action)
        self.assertIn("disabled", canvas_action)

    def test_refresh_resumes_server_active_task_without_submitting_again(self):
        source = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
        restore = source[source.index("function restoreWorkspaceSnapshot") : source.index("function shouldRestoreSnapshotDataForRoute")]
        snapshot = source[source.index("function snapshotData") : source.index("function migrateWorkspaceSnapshotData")]
        set_data = source[source.index("function setData") : source.index("function mergeConversationMessages")]
        monitor = source[source.index("function monitorActiveTask") : source.index("async function postAction")]
        post_action = source[source.index("async function postAction") : source.index("async function createProject")]
        send_chat = source[source.index("async function sendProjectChat") : source.index("async function updateProjectSetup")]
        mount_tail = source[source.index("    paintWorkspace = paint;") : source.index("  if (document.readyState")]

        # Interleaving 1: server-null beats a stale snapshot task.
        self.assertIn("activeTask: null", snapshot)
        self.assertIn("data: snapshotData(D)", snapshot)
        self.assertIn("const authoritativeActiveTask = D.activeTask;", restore)
        self.assertIn("D.activeTask = authoritativeActiveTask;", restore)
        self.assertIn("const authoritativeRetrievalRecovery = D.retrievalRecovery;", restore)
        self.assertIn("D.retrievalRecovery = authoritativeRetrievalRecovery;", restore)

        # Interleaving 2: selecting a project adopts and monitors its active task.
        self.assertIn("function syncActionState(activeTask)", source)
        self.assertIn("syncActionState(D.activeTask);", set_data)
        self.assertIn("monitorActiveTask();", set_data)
        self.assertIn("setData(await fetchProjectState(projectId));", source)

        # Interleaving 3: navigation A->B invalidates resume and submit continuations.
        self.assertIn("const generation = ++activeTaskMonitor.generation;", monitor)
        self.assertIn("ownsProjectGeneration(state, D, activeTaskMonitor, projectId, generation, taskId)", monitor)
        self.assertIn("const projectId = state.activeProjectId || D.project.id;", post_action)
        self.assertIn("if (!isCurrentProject()) return;", post_action)
        self.assertNotIn("await waitForTask", post_action)
        self.assertNotIn("setData(await fetchProjectState", post_action)
        self.assertIn("const ownership = projectNavigation.capture(projectId);", send_chat)
        self.assertIn("if (!projectNavigation.owns(ownership)) return;", send_chat)
        self.assertNotIn("activeTaskMonitor.generation", send_chat)
        self.assertIn("projectNavigation.adoptProject(D.project.id || '');", set_data)
        self.assertIn("restoreWorkspaceSnapshot();\n  projectNavigation.adoptProject(D.project.id || '');", source)

        # Interleaving 4: task-id replacement supersedes the old monitor and dedupes the new one.
        self.assertIn("const key = `${projectId}:${taskId}`;", monitor)
        self.assertIn("syncActionState(activeTask);", monitor)
        self.assertIn("if (activeTaskMonitor.key === key) return;", monitor)
        self.assertIn("activeTaskMonitor.key === key", monitor)
        self.assertIn("const activeTaskPolls = createTaskPollRegistry(waitForTask);", source)
        self.assertIn("return activeTaskPolls.waitOnce(taskId, key);", monitor)
        self.assertIn("resolveTaskAndRefresh(waitForActiveTaskOnce(taskId, key), projectId, fetchProjectState)", monitor)
        self.assertIn("state.actionError = outcome.error;", monitor)
        self.assertEqual(monitor.count("waitForTask(taskId)"), 0)
        self.assertIn("monitorActiveTask();", post_action)
        self.assertEqual(source.count("function monitorActiveTask()"), 1)
        self.assertNotIn("resumeActiveTask", source)
        self.assertLess(mount_tail.index("paint();"), mount_tail.index("monitorActiveTask();"))
        self.assertIn("typeof window !== 'undefined' && typeof document !== 'undefined'", source)

    def test_retrieval_canvas_does_not_label_queued_papers_as_retrieved(self):
        source = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
        retrieval_canvas = source[source.index("function retrievalCanvas(v)") : source.index("function extractionCanvas(v)")]

        self.assertIn("Included papers queued for retrieval", retrieval_canvas)
        self.assertIn("v.retrievalSummary.retrieved > 0", retrieval_canvas)

    def test_extraction_canvas_and_assistant_match_prototype_decision_flow(self):
        source = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
        extraction_ui = source[source.index("function extractionCanvas(v)") : source.index("function categorizeCanvas(v)")]
        assistant = source[source.index("function extractionDecisionCard(v)") : source.index("function setupDialog(v)")]

        self.assertIn("schemaWorkbench", source)
        self.assertIn('role="tab"', extraction_ui)
        self.assertIn('aria-selected=', extraction_ui)
        self.assertIn('aria-label="Previous paper"', extraction_ui)
        self.assertIn('aria-label="Next paper"', extraction_ui)
        self.assertIn("extractionPreview", extraction_ui)
        self.assertIn("Decision needed", assistant)
        self.assertIn("Finalize Schema", assistant)
        self.assertIn("Preview", assistant)
        self.assertIn("JSON", assistant)
        self.assertIn("Regenerate schema", assistant)
        self.assertIn('data-action="finalize-and-run-extraction"', assistant)
        self.assertIn('data-act="open-preview"', assistant)
        self.assertIn('data-act="schema-json"', assistant)
        self.assertNotIn('data-action="edit-schema"', extraction_ui + assistant)
        self.assertNotIn('data-action="run-extraction"', extraction_ui + assistant)

    def test_same_project_chat_and_actions_preserve_visible_step_and_tab(self):
        source = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
        set_data = source[source.index("function setData") : source.index("function mergeConversationMessages")]
        monitor = source[source.index("function monitorActiveTask") : source.index("async function postAction")]
        send_chat = source[source.index("async function sendProjectChat") : source.index("async function updateProjectSetup")]

        self.assertIn("preserveView", set_data)
        self.assertIn("previousStep", set_data)
        self.assertIn("previousTab", set_data)
        self.assertIn("{ preserveView: true }", monitor)
        self.assertIn("{ preserveView: true }", send_chat)
        self.assertIn("JSON.stringify({ message, step: state.step })", send_chat)

    def test_optimistic_chat_message_uses_visible_workflow_step(self):
        source = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
        append_message = source[source.index("function appendMessage") : source.index("async function handleChatSubmit")]

        self.assertIn("state.step", append_message)
        self.assertIn("currentStep", append_message)
        self.assertNotIn("{ step: 1, role", append_message)

    def test_canvas_action_errors_render_on_main_workspace(self):
        source = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
        render_main = source[source.index('<main class="rp-main"') : source.index("${assistantPanel(v)}")]

        self.assertIn("function actionErrorBanner(v)", source)
        self.assertIn("${actionErrorBanner(v)}", render_main)
        self.assertIn('data-ui="canvas-action-error"', source)

    def test_workflow_task_polling_allows_long_running_external_actions(self):
        source = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
        wait_for_task = source[source.index("async function waitForTask") : source.index("function appendMessage")]

        self.assertIn("const TASK_POLL_INTERVAL_MS = 1000;", source)
        self.assertIn("const TASK_MAX_POLLS = 1800;", source)
        self.assertIn("i < TASK_MAX_POLLS", wait_for_task)
        self.assertIn("TASK_POLL_INTERVAL_MS", wait_for_task)
        self.assertIn("Task did not finish within 30 minutes", wait_for_task)
        self.assertNotIn("i < 120", wait_for_task)
        self.assertNotIn("Task timed out", wait_for_task)

    def test_future_workflow_steps_are_not_clickable(self):
        source = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")

        self.assertIn("function workflowProgressIndex(steps)", source)
        self.assertIn("canView: i <= progressIndex", source)
        self.assertIn('data-disabled="true" aria-disabled="true"', source)
        self.assertIn('const actionAttrs = s.canView ? `data-act="step"', source)
        self.assertIn("if (t.getAttribute('data-disabled') === 'true') return;", source)

    def test_workflow_stepper_hides_stage_sub_counts(self):
        source = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
        step_item = source[source.index("function stepItem(s)") : source.index("function searchCanvas(v)")]

        self.assertIn("${s.label}", step_item)
        self.assertNotIn("${s.sub}", step_item)
        self.assertNotIn("font-size:10px;color:#9aa39b;margin-top:3px", step_item)

    def test_categorization_canvas_replicates_prototype_step_five_flow(self):
        source = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
        categorize_canvas = source[source.index("function categorizeCanvas(v)") : source.index("function activityMessage(m)")]

        self.assertIn("Papers Extracted", categorize_canvas)
        self.assertIn("Select Field to Categorize", categorize_canvas)
        self.assertIn("Sample Values", categorize_canvas)
        self.assertIn("Categorization Mode", categorize_canvas)
        self.assertIn("Generate Categories with AI", categorize_canvas)
        self.assertIn("Generated Categories", categorize_canvas)
        self.assertIn("Confirm Categories", categorize_canvas)
        self.assertIn("Apply Categorization", categorize_canvas)
        self.assertIn("Skip Categorization", categorize_canvas)
        self.assertIn("Analysis Summary", categorize_canvas)
        self.assertIn("Category Briefs", categorize_canvas)
        self.assertIn("Representative papers", categorize_canvas)
        self.assertIn("Full Results", categorize_canvas)
        self.assertIn("Finalize Project", categorize_canvas)
        self.assertIn("function categorizationSetupPanel", source)
        self.assertIn("function categorizationAnalysisSummary", source)
        self.assertIn("function categoryBriefsPanel", source)
        self.assertIn("function categoryBriefPaper", source)
        self.assertIn("<details", source)
        self.assertIn("Evidence", source)
        self.assertIn("function distributionChart", source)
        self.assertIn("function fullResultsTable", source)
        self.assertIn("v.categorizationWorkflow", source)
        self.assertIn("v.categorizationAnalysis", source)
        self.assertNotIn("Evidence Matrix", categorize_canvas)
        self.assertIn("Export Package", categorize_canvas)

    def test_platform_collection_errors_render_as_source_warnings(self):
        source = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
        source_row = source[source.index("const sourceRow") : source.index("const buttonStyle")]

        self.assertIn("platformIssues", source)
        self.assertIn("platformIssueByLabel", source)
        self.assertIn('data-ui="source-warning"', source)
        self.assertIn("Platform issue", source_row)
        self.assertIn("${issue.message}", source_row)
        self.assertIn("v.platforms.map(sourceRow)", source)

    def test_categorization_flow_uses_readable_labels_and_responsive_layout(self):
        source = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")

        self.assertIn("function workspaceResponsiveStyle()", source)
        self.assertIn("@media (max-width: 760px)", source)
        self.assertIn('class="rp-shell"', source)
        self.assertIn('class="rp-sidebar"', source)
        self.assertIn('class="rp-main"', source)
        self.assertIn('class="rp-assistant"', source)
        self.assertIn(".rp-sidebar { display:none !important; }", source)
        self.assertIn(".rp-assistant { width:100% !important;", source)
        self.assertIn("function evidenceFieldLabel(name)", source)
        self.assertIn("function categorizationMetrics(workflow)", source)
        self.assertIn("function fullResultsTable(table)", source)
        self.assertIn("max-height:300px", source)
        self.assertIn("${evidenceFieldLabel(column)}", source)
        self.assertIn("Methodology", source)
        self.assertIn("Task / Application", source)

    def test_completed_final_workflow_step_does_not_render_a_right_tail(self):
        source = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
        compute_vals = source[source.index("steps: steps.map") : source.index("stepTitle: cur.label")]

        self.assertIn("noRight: i === arr.length - 1", compute_vals)
        self.assertIn("rightNavy: i < arr.length - 1 && ['done', 'partial'].includes(s.status)", compute_vals)
        self.assertNotIn("rightNavy: ['done', 'partial'].includes(s.status)", compute_vals)

    def test_completed_analysis_renders_accessible_existing_export_links_without_paths(self):
        source = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
        summary = source[source.index("function categorizationAnalysisSummary") : source.index("function categoryBriefsPanel")]
        export_section = source[source.index("function exportPackageSection") : source.index("function categoryBriefsPanel")]

        self.assertIn("${exportPackageSection(v.exportPackage)}", summary)
        self.assertIn("Export Package", export_section)
        self.assertIn('aria-label="Export Package"', export_section)
        self.assertIn(".filter((item) => item.exists)", export_section)
        self.assertIn('href="${item.downloadUrl}"', export_section)
        self.assertIn("download", export_section)
        self.assertNotIn("item.path", export_section)

    def test_canvas_actions_show_elapsed_running_state(self):
        source = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")

        self.assertIn("actionStartedAt: D.activeTask ?", source)
        self.assertIn("function formatElapsed(ms)", source)
        self.assertIn("let actionTicker = null;", source)
        self.assertIn("state.actionStartedAt = Date.now();", source)
        self.assertIn("setInterval(paint, 1000)", source)
        self.assertIn("clearInterval(actionTicker)", source)
        self.assertIn("v.canvasActionElapsedLabel", source)

    def test_development_command_uses_reload(self):
        for path in ["README.md", "QUICKSTART.md", "frontend/README.md"]:
            source = (ROOT / path).read_text(encoding="utf-8")
            self.assertIn("uvicorn web_app:app --host 127.0.0.1 --port 5602 --reload", source)


if __name__ == "__main__":
    unittest.main()
