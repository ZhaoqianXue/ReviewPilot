/* app.js — ReviewPilot UI (faithful reproduction of the "Direction C · Ledger" design).
 *
 * No build step, no framework. Pure DOM + template strings.
 * - computeVals(state): derives view-model from window.RP_DATA (ported from the design's renderVals()).
 * - render(v): returns the full HTML for the 3-column shell (the design's markup, DSL expanded).
 * - State is { step, tab }; clicks on the stepper / tabs re-render. Hover effects mirror the original.
 */
(function () {
  'use strict';
  const D = window.RP_DATA;
  const MAX = Math.max.apply(null, D.platforms.map((p) => p[1])); // bar baseline = largest source

  // ---- view-model -----------------------------------------------------------
  function computeVals(state) {
    const steps = D.steps;
    const allFields = D.fields.map((f, i) => ({
      idx: String(i + 1).padStart(2, '0'), name: f[0], type: f[1], desc: f[2], req: f[3], reqNot: !f[3],
    }));
    const platforms = D.platforms.map((p) => ({ k: p[0], v: p[1], pct: Math.round((p[1] / MAX) * 100) }));
    const historyGroups = D.history.map((gr) => ({
      ...gr, items: gr.items.map((it) => ({ ...it, notActive: !it.active })),
    }));

    const step = state.step;
    const cur = steps.find((s) => s.key === step);
    const N = cur.n;
    const chat = D.messages.filter((m) => m.step <= N).map((m) => ({ ...m, isAssistant: m.role === 'a', isUser: m.role === 'u' }));

    return {
      project: D.project,
      steps: steps.map((s, i, arr) => ({
        ...s,
        active: s.key === step, notActive: s.key !== step,
        isDone: s.status === 'done', isActive: s.status === 'active', isTodo: s.status === 'todo',
        noLeft: i === 0,
        leftNavy: i > 0 && arr[i - 1].status === 'done',
        leftGray: i > 0 && arr[i - 1].status !== 'done',
        noRight: i === arr.length - 1,
        rightNavy: s.status === 'done',
        rightGray: i < arr.length - 1 && s.status !== 'done',
      })),
      stepTitle: cur.label,
      isSearch: step === 'search', isScreening: step === 'screening', isRetrieval: step === 'retrieval',
      isExtraction: step === 'extraction', isCategorize: step === 'categorize',
      allFields, platforms,
      keywords: D.keywords, groups: D.groups, retrieved: D.retrieved, previewFields: D.previewFields,
      activity: D.activityByStep[step],
      isFieldsTab: state.tab === 'fields', notFieldsTab: state.tab !== 'fields',
      isPreviewTab: state.tab === 'preview', notPreviewTab: state.tab !== 'preview',
      chat,
      assistantContext: D.ctxLabels[step],
      showDecision: step === 'extraction',
      showLocked: step === 'categorize',
      showQuietAction: !!D.quietLabels[step],
      quietActionLabel: D.quietLabels[step] || '',
      historyGroups,
    };
  }

  // ---- the ReviewPilot mark (used at several sizes) -------------------------
  const logo = (sz) =>
    `<svg viewBox="0 0 100 100" width="${sz}" height="${sz}" fill="none"><path d="M49 62 L15 52 L15 74 L49 82 Z" fill="#1a365d"></path><path d="M51 62 L85 52 L85 74 L51 82 Z" fill="#1a365d" opacity="0.5"></path><line x1="64" y1="50" x2="75" y2="61" stroke="#1a365d" stroke-width="8.5" stroke-linecap="round"></line><circle cx="50" cy="36" r="21" fill="#fffefc" stroke="#1a365d" stroke-width="8"></circle><path d="M50 22 L59 48 L50 42 L41 48 Z" fill="#1a365d"></path></svg>`;

  const sourceRow = (p) =>
    `<div style="display:flex;align-items:center;gap:12px;padding:5px 0;"><span style="width:130px;flex:0 0 130px;font-size:12.5px;color:#1a1a1a;">${p.k}</span><span style="flex:1;height:5px;background:#eef0ee;border-radius:999px;overflow:hidden;"><span style="display:block;height:100%;width:${p.pct}%;background:#1a365d;"></span></span><span style="width:32px;text-align:right;font-family:'IBM Plex Mono',monospace;font-size:11.5px;color:#6b746c;">${p.v}</span></div>`;

  // ---- template -------------------------------------------------------------
  function render(v) {
    return `
<div style="width:1280px;height:860px;background:#fffefc;color:#1a1a1a;font-family:'Hanken Grotesk',system-ui,sans-serif;font-weight:400;letter-spacing:-0.01em;display:flex;overflow:hidden;border:1px solid #e5e7eb;border-radius:16px;">

  <aside style="width:175px;flex:0 0 175px;border-right:1px solid #e5e7eb;display:flex;flex-direction:column;min-height:0;">
    <div style="display:flex;align-items:center;gap:8px;padding:13px 10px 8px 12px;">
      <div title="ReviewPilot · Home" style="display:flex;align-items:center;gap:8px;cursor:pointer;min-width:0;">
        ${logo(22)}
        <span style="font-weight:600;font-size:15px;letter-spacing:-0.03em;color:#1a365d;">ReviewPilot</span>
      </div>
    </div>
    <div style="padding:4px 10px 10px;">
      <button style="display:flex;align-items:center;justify-content:center;gap:6px;width:100%;background:#fffefc;border:1px solid #d8e2f0;border-radius:10px;padding:9px 8px;font-size:12.5px;font-family:inherit;color:#1a365d;letter-spacing:-0.02em;cursor:pointer;transition:background .15s ease;white-space:nowrap;" data-hover="background:#eef4fb;"><i class="ph ph-plus" style="font-size:14px;flex:0 0 auto;"></i>New conversation</button>
    </div>
    <div class="rp-scroll" style="flex:1;min-height:0;overflow-y:auto;padding:2px 10px 12px;">
      ${v.historyGroups.map((g) => `
        <div style="font-size:10px;letter-spacing:0.08em;text-transform:uppercase;color:#9aa39b;padding:11px 8px 5px;">${g.label}</div>
        ${g.items.map((h) => `
          ${h.active ? `
            <div style="display:flex;align-items:center;gap:9px;padding:8px 10px;border-radius:9px;background:#eaf0f7;cursor:pointer;">
              <i class="ph-fill ph-chat-circle" style="font-size:15px;color:#1a365d;flex:0 0 auto;"></i>
              <span style="font-size:13px;letter-spacing:-0.01em;color:#1a365d;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${h.title}</span>
            </div>` : ''}
          ${h.notActive ? `
            <div style="display:flex;align-items:center;gap:9px;padding:8px 10px;border-radius:9px;cursor:pointer;transition:background .12s ease;" data-hover="background:#eef4fb;">
              <i class="ph ph-chat-circle" style="font-size:15px;color:#9aa39b;flex:0 0 auto;"></i>
              <span style="font-size:13px;letter-spacing:-0.01em;color:#1a1a1a;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${h.title}</span>
            </div>` : ''}
        `).join('')}
      `).join('')}
    </div>
    <div style="flex:0 0 auto;border-top:1px solid #eef0ee;padding:7px 10px 9px;display:flex;flex-direction:column;gap:1px;">
      <div style="display:flex;align-items:center;gap:11px;padding:9px 10px;border-radius:9px;cursor:pointer;font-size:13px;color:#6b746c;transition:background .12s ease;" data-hover="background:#eef4fb;color:#1a365d;"><i class="ph ph-gear-six" style="font-size:16px;"></i>Settings</div>
      <div style="display:flex;align-items:center;gap:11px;padding:9px 10px;border-radius:9px;cursor:pointer;font-size:13px;color:#6b746c;transition:background .12s ease;" data-hover="background:#eef4fb;color:#1a365d;"><i class="ph ph-question" style="font-size:16px;"></i>Help &amp; support</div>
    </div>
  </aside>

  <main style="flex:1;min-width:0;display:flex;flex-direction:column;">

    <div style="border-bottom:1px solid #e5e7eb;flex:0 0 auto;">
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
        <div style="display:flex;align-items:center;gap:11px;flex:0 0 auto;padding-top:2px;">
          <button style="width:30px;height:30px;border-radius:8px;border:1px solid #e0e4df;background:none;color:#6b746c;cursor:pointer;display:flex;align-items:center;justify-content:center;transition:all .15s ease;" data-hover="background:#eef4fb;color:#1a365d;"><i class="ph ph-dots-three-vertical" style="font-size:16px;"></i></button>
        </div>
      </div>
      <div style="display:flex;align-items:flex-start;padding:14px 20px 12px;">
        ${v.steps.map((s) => `
          <div data-act="step" data-step="${s.key}" style="position:relative;flex:1;display:flex;flex-direction:column;cursor:pointer;padding:0 6px 12px;transition:opacity .12s ease;" data-hover="opacity:0.74;">
            <div style="display:flex;align-items:center;height:40px;">
              ${s.noLeft ? `<span style="flex:1;"></span>` : ''}
              ${s.leftNavy ? `<span style="flex:1;height:2px;background:#1a365d;"></span>` : ''}
              ${s.leftGray ? `<span style="flex:1;height:2px;background:#e3e8ef;"></span>` : ''}
              <span style="flex:0 0 auto;display:flex;align-items:center;justify-content:center;margin:0 7px;">
                ${s.isDone ? `<i class="ph-fill ph-check-circle" style="font-size:19px;color:#1a365d;"></i>` : ''}
                ${s.isActive ? `<span style="width:18px;height:18px;border-radius:999px;border:2px solid #1a365d;display:flex;align-items:center;justify-content:center;background:#fffefc;"><span style="width:7px;height:7px;border-radius:999px;background:#1a365d;"></span></span>` : ''}
                ${s.isTodo ? `<span style="width:16px;height:16px;border-radius:999px;border:1.5px solid #cdd5e0;background:#fffefc;"></span>` : ''}
              </span>
              ${s.noRight ? `<span style="flex:1;"></span>` : ''}
              ${s.rightNavy ? `<span style="flex:1;height:2px;background:#1a365d;"></span>` : ''}
              ${s.rightGray ? `<span style="flex:1;height:2px;background:#e3e8ef;"></span>` : ''}
            </div>
            <div style="text-align:center;margin-top:2px;">
              ${s.active ? `<div style="font-size:12px;font-weight:500;letter-spacing:-0.02em;color:#1a365d;line-height:1.2;white-space:nowrap;">${s.label}</div>` : ''}
              ${s.notActive ? `<div style="font-size:12px;letter-spacing:-0.02em;color:#3a4252;line-height:1.2;white-space:nowrap;">${s.label}</div>` : ''}
              <div style="font-family:'IBM Plex Mono',monospace;font-size:10px;color:#9aa39b;margin-top:3px;">${s.sub}</div>
            </div>
            ${s.active ? `<span style="position:absolute;left:8px;right:8px;bottom:-1px;height:2px;border-radius:2px;background:#1a365d;"></span>` : ''}
          </div>
        `).join('')}
      </div>
    </div>

    <div class="rp-scroll" style="flex:1;min-height:0;overflow-y:auto;padding:20px 22px;">

      ${v.isExtraction ? `
        <div style="display:flex;align-items:center;gap:4px;border-bottom:1px solid #eef0ee;margin-bottom:2px;">
          ${v.isFieldsTab ? `<span style="font-size:13px;color:#1a365d;padding:9px 12px;border-bottom:2px solid #1a365d;margin-bottom:-1px;cursor:pointer;">Schema fields <span style="font-family:'IBM Plex Mono',monospace;font-size:11px;color:#9aa39b;">16</span></span>` : ''}
          ${v.notFieldsTab ? `<span data-act="tab" data-tab="fields" style="font-size:13px;color:#6b746c;padding:9px 12px;cursor:pointer;transition:color .12s ease;" data-hover="color:#1a365d;">Schema fields <span style="font-family:'IBM Plex Mono',monospace;font-size:11px;color:#9aa39b;">16</span></span>` : ''}
          ${v.isPreviewTab ? `<span style="font-size:13px;color:#1a365d;padding:9px 12px;border-bottom:2px solid #1a365d;margin-bottom:-1px;cursor:pointer;">Preview on paper</span>` : ''}
          ${v.notPreviewTab ? `<span data-act="tab" data-tab="preview" style="font-size:13px;color:#6b746c;padding:9px 12px;cursor:pointer;transition:color .12s ease;" data-hover="color:#1a365d;">Preview on paper</span>` : ''}
          <button data-act="rerender" style="margin-left:auto;display:inline-flex;align-items:center;gap:6px;font-size:12px;color:#1a365d;background:none;border:1px solid #c8d8e8;border-radius:8px;padding:6px 11px;cursor:pointer;font-family:inherit;transition:background .15s ease;" data-hover="background:#eef4fb;"><i class="ph ph-arrows-clockwise" style="font-size:13px;"></i>Regenerate</button>
        </div>

        ${v.isFieldsTab ? `
          <div style="display:grid;grid-template-columns:26px 1.5fr 1.1fr 2fr 54px;gap:12px;padding:9px 12px;font-size:10px;letter-spacing:0.05em;text-transform:uppercase;color:#9aa39b;border-bottom:1px solid #eef0ee;">
            <span>#</span><span>Field</span><span>Type</span><span>Description</span><span style="text-align:center;">Req</span>
          </div>
          ${v.allFields.map((f) => `
            <div style="display:grid;grid-template-columns:26px 1.5fr 1.1fr 2fr 54px;gap:12px;padding:8px 12px;border-bottom:1px solid #f4f6f3;align-items:center;font-size:12.5px;letter-spacing:-0.01em;transition:background .1s ease;" data-hover="background:#f4f8fd;">
              <span style="font-family:'IBM Plex Mono',monospace;font-size:11px;color:#bcc4bb;">${f.idx}</span>
              <span style="color:#1a1a1a;">${f.name}</span>
              <span style="font-family:'IBM Plex Mono',monospace;font-size:11px;color:#2c5282;background:#eaf0f7;padding:2px 7px;border-radius:5px;justify-self:start;">${f.type}</span>
              <span style="color:#8a938b;">${f.desc}</span>
              <span style="display:flex;justify-content:center;">
                ${f.req ? `<i class="ph-fill ph-check-circle" style="font-size:16px;color:#1a365d;"></i>` : ''}
                ${f.reqNot ? `<i class="ph ph-minus" style="font-size:14px;color:#c8cfc7;"></i>` : ''}
              </span>
            </div>
          `).join('')}
        ` : ''}

        ${v.isPreviewTab ? `
          <div style="display:flex;align-items:center;justify-content:space-between;padding:14px 2px 12px;">
            <div style="min-width:0;"><div style="font-size:13.5px;color:#1a1a1a;letter-spacing:-0.01em;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:440px;">Clinical reasoning with large language models: a systematic evaluation</div><div style="font-family:'IBM Plex Mono',monospace;font-size:11px;color:#9aa39b;margin-top:2px;">row1_pubmed_2025 · Nature Medicine</div></div>
            <div style="display:flex;align-items:center;gap:6px;flex:0 0 auto;"><button style="width:28px;height:28px;border-radius:7px;border:1px solid #e0e4df;background:none;color:#6b746c;cursor:pointer;display:flex;align-items:center;justify-content:center;"><i class="ph ph-caret-left" style="font-size:13px;"></i></button><span style="font-family:'IBM Plex Mono',monospace;font-size:11px;color:#6b746c;">1 / 70</span><button style="width:28px;height:28px;border-radius:7px;border:1px solid #e0e4df;background:none;color:#6b746c;cursor:pointer;display:flex;align-items:center;justify-content:center;"><i class="ph ph-caret-right" style="font-size:13px;"></i></button></div>
          </div>
          <div style="border:1px solid #e5e7eb;border-radius:12px;overflow:hidden;">
            ${v.previewFields.map((p) => `
              <div style="display:grid;grid-template-columns:160px 1fr;gap:14px;padding:10px 16px;border-bottom:1px solid #f4f6f3;align-items:start;">
                <span style="font-size:12px;color:#8a938b;letter-spacing:-0.01em;">${p.k}</span>
                <span style="font-family:'IBM Plex Mono',monospace;font-size:12px;color:#1a1a1a;line-height:1.5;">${p.v}</span>
              </div>
            `).join('')}
          </div>
        ` : ''}
      ` : ''}

      ${v.isSearch ? `
        <div style="border:1px solid #e5e7eb;border-radius:12px;padding:16px 18px;margin-bottom:16px;">
          <div style="font-size:10px;letter-spacing:0.06em;text-transform:uppercase;color:#9aa39b;margin-bottom:7px;">Research question</div>
          <div style="font-size:15px;color:#1a1a1a;line-height:1.45;letter-spacing:-0.01em;">How are large language models applied to biomedical and clinical informatics tasks, and how is their performance evaluated?</div>
        </div>
        <div style="border:1px solid #e5e7eb;border-radius:12px;padding:16px 18px;margin-bottom:16px;">
          <div style="font-size:10px;letter-spacing:0.06em;text-transform:uppercase;color:#9aa39b;margin-bottom:11px;">Keywords</div>
          <div style="display:flex;flex-wrap:wrap;gap:7px;">${v.keywords.map((kw) => `<span style="font-family:'IBM Plex Mono',monospace;font-size:11.5px;color:#1a365d;background:#eaf0f7;border:1px solid #cfe0f5;padding:4px 9px;border-radius:6px;">${kw}</span>`).join('')}</div>
        </div>
        <div style="border:1px solid #e5e7eb;border-radius:12px;padding:16px 18px;">
          <div style="font-size:10px;letter-spacing:0.06em;text-transform:uppercase;color:#9aa39b;margin-bottom:12px;">Sources</div>
          ${v.platforms.map(sourceRow).join('')}
        </div>
      ` : ''}

      ${v.isScreening ? `
        <div style="display:flex;border:1px solid #e5e7eb;border-radius:12px;overflow:hidden;margin-bottom:16px;">
          <div style="flex:1;padding:18px;text-align:center;border-right:1px solid #eef0ee;"><div style="font-family:'IBM Plex Mono',monospace;font-size:28px;color:#1a1a1a;">312</div><div style="font-size:11px;color:#8a938b;margin-top:4px;">identified</div></div>
          <div style="flex:1;padding:18px;text-align:center;border-right:1px solid #eef0ee;"><div style="font-family:'IBM Plex Mono',monospace;font-size:28px;color:#1a1a1a;">248</div><div style="font-size:11px;color:#8a938b;margin-top:4px;">after de-dup</div></div>
          <div style="flex:1;padding:18px;text-align:center;"><div style="font-family:'IBM Plex Mono',monospace;font-size:28px;color:#1a365d;">70</div><div style="font-size:11px;color:#1a365d;margin-top:4px;">included</div></div>
        </div>
        <div style="border:1px solid #e5e7eb;border-radius:12px;padding:16px 18px;">
          <div style="font-size:10px;letter-spacing:0.06em;text-transform:uppercase;color:#9aa39b;margin-bottom:12px;">Records by source</div>
          ${v.platforms.map(sourceRow).join('')}
        </div>
      ` : ''}

      ${v.isRetrieval ? `
        <div style="display:flex;gap:14px;margin-bottom:16px;">
          <div style="flex:0 0 180px;border:1px solid #e5e7eb;border-radius:12px;padding:18px;display:flex;align-items:center;gap:14px;"><svg width="50" height="50" viewBox="0 0 56 56"><circle cx="28" cy="28" r="23" fill="none" stroke="#e8ebe7" stroke-width="4"></circle><circle cx="28" cy="28" r="23" fill="none" stroke="#1a365d" stroke-width="4" stroke-linecap="round" stroke-dasharray="144.5" stroke-dashoffset="16.5" transform="rotate(-90 28 28)"></circle></svg><div><div style="font-family:'IBM Plex Mono',monospace;font-size:20px;color:#1a365d;">62/70</div><div style="font-size:11px;color:#6b746c;margin-top:3px;">retrieved</div></div></div>
          <div style="flex:1;border:1px solid #e5e7eb;border-radius:12px;padding:14px 18px;display:flex;flex-direction:column;justify-content:center;gap:9px;">
            <div style="display:flex;justify-content:space-between;font-size:12.5px;"><span style="color:#1a1a1a;">Open access</span><span style="font-family:'IBM Plex Mono',monospace;color:#6b746c;">41</span></div>
            <div style="height:1px;background:#f2f4f1;"></div>
            <div style="display:flex;justify-content:space-between;font-size:12.5px;"><span style="color:#1a1a1a;">Via institution</span><span style="font-family:'IBM Plex Mono',monospace;color:#6b746c;">21</span></div>
            <div style="height:1px;background:#f2f4f1;"></div>
            <div style="display:flex;justify-content:space-between;font-size:12.5px;"><span style="color:#1a1a1a;">Unavailable</span><span style="font-family:'IBM Plex Mono',monospace;color:#6b746c;">08</span></div>
          </div>
        </div>
        <div style="border:1px solid #e5e7eb;border-radius:12px;padding:16px 18px;">
          <div style="font-size:10px;letter-spacing:0.06em;text-transform:uppercase;color:#9aa39b;margin-bottom:10px;">Recently retrieved</div>
          ${v.retrieved.map((r) => `<div style="display:flex;align-items:center;gap:11px;padding:8px 0;border-bottom:1px solid #f4f6f3;"><i class="ph ph-file-text" style="font-size:16px;color:#1a365d;"></i><div style="flex:1;min-width:0;"><div style="font-size:12.5px;color:#1a1a1a;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${r.t}</div><div style="font-family:'IBM Plex Mono',monospace;font-size:10.5px;color:#9aa39b;">${r.v}</div></div><i class="ph-fill ph-check-circle" style="font-size:15px;color:#1a365d;"></i></div>`).join('')}
        </div>
      ` : ''}

      ${v.isCategorize ? `
        <div style="border:1px dashed #c8d8e8;border-radius:12px;padding:28px;text-align:center;margin-bottom:16px;background:#f8fbff;">
          <span style="width:46px;height:46px;border-radius:12px;background:#eaf0f7;color:#1a365d;display:inline-flex;align-items:center;justify-content:center;margin-bottom:12px;"><i class="ph ph-stack" style="font-size:22px;"></i></span>
          <div style="font-size:17px;color:#1a365d;letter-spacing:-0.01em;">Categorization queued</div>
          <div style="font-size:12.5px;color:#6b746c;margin-top:6px;max-width:380px;margin-left:auto;margin-right:auto;line-height:1.5;">Runs automatically after the schema is finalized — 70 papers into 2 preferred groups.</div>
        </div>
        <div style="font-size:10px;letter-spacing:0.06em;text-transform:uppercase;color:#9aa39b;margin-bottom:10px;">Suggested grouping (preview)</div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px;opacity:0.62;">${v.groups.map((g) => `<div style="border:1px solid #e5e7eb;border-radius:12px;padding:15px;"><div style="display:flex;align-items:baseline;justify-content:space-between;"><span style="font-size:13.5px;color:#1a1a1a;">${g.name}</span><span style="font-family:'IBM Plex Mono',monospace;font-size:18px;color:#1a365d;">${g.n}</span></div><div style="font-size:11.5px;color:#8a938b;margin-top:5px;line-height:1.4;">${g.desc}</div></div>`).join('')}</div>
      ` : ''}

      <div style="margin-top:18px;border:1px solid #e5e7eb;border-radius:12px;padding:13px 16px;">
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:4px;">
          <div style="display:flex;align-items:center;gap:8px;"><i class="ph ph-pulse" style="font-size:15px;color:#1a365d;"></i><span style="font-size:10px;letter-spacing:0.08em;text-transform:uppercase;color:#9aa39b;">Activity · ${v.stepTitle}</span></div>
          <span style="display:inline-flex;align-items:center;gap:5px;font-size:10px;color:#1a365d;"><span style="width:6px;height:6px;border-radius:999px;background:#1a365d;"></span>live</span>
        </div>
        ${v.activity.map((l) => `
          <div style="display:flex;gap:12px;padding:7px 0;border-top:1px solid #f4f6f3;align-items:baseline;">
            <span style="font-family:'IBM Plex Mono',monospace;font-size:10.5px;color:#bcc4bb;flex:0 0 56px;">${l.t}</span>
            <span style="font-family:'IBM Plex Mono',monospace;font-size:11px;color:#1a365d;flex:0 0 84px;">${l.tag}</span>
            <span style="font-size:12px;color:#6b746c;letter-spacing:-0.01em;">${l.msg}</span>
          </div>
        `).join('')}
      </div>

    </div>
  </main>

  <aside style="width:407px;flex:0 0 407px;border-left:1px solid #e5e7eb;display:flex;flex-direction:column;min-height:0;">
    <div style="display:flex;align-items:center;gap:9px;padding:13px 15px;border-bottom:1px solid #eef0ee;flex:0 0 auto;">
      ${logo(24)}
      <div style="flex:1;min-width:0;">
        <div style="font-size:13.5px;color:#1a1a1a;letter-spacing:-0.01em;">ReviewPilot</div>
        <div style="font-size:10.5px;color:#9aa39b;letter-spacing:-0.01em;">${v.assistantContext}</div>
      </div>
      <button style="width:28px;height:28px;border-radius:8px;border:1px solid #e0e4df;background:none;color:#6b746c;cursor:pointer;display:flex;align-items:center;justify-content:center;transition:all .15s ease;" data-hover="background:#eef4fb;color:#1a365d;"><i class="ph ph-plus" style="font-size:15px;"></i></button>
    </div>

    <div class="rp-scroll" id="rp-conv" style="flex:1;min-height:0;overflow-y:auto;padding:16px 15px;display:flex;flex-direction:column;gap:13px;">
      ${v.chat.map((m) => `
        ${m.isAssistant ? `
          <div style="display:flex;gap:8px;align-items:flex-start;">
            <span style="flex:0 0 20px;margin-top:1px;">${logo(20)}</span>
            <div style="background:#eef4fb;border:1px solid #e1e8f2;border-radius:3px 13px 13px 13px;padding:10px 12px;font-size:12.5px;color:#1f2937;line-height:1.5;letter-spacing:-0.01em;">${m.text}</div>
          </div>` : ''}
        ${m.isUser ? `
          <div style="display:flex;justify-content:flex-end;">
            <div style="background:#1a365d;color:#eaf0f7;border-radius:13px 13px 3px 13px;padding:10px 12px;font-size:12.5px;line-height:1.5;letter-spacing:-0.01em;max-width:230px;">${m.text}</div>
          </div>` : ''}
      `).join('')}

      ${v.showDecision ? `
        <div style="margin-left:26px;border:1px solid #c8d8e8;border-radius:13px;padding:13px;background:#fffefc;">
          <div style="display:flex;align-items:center;gap:6px;margin-bottom:8px;"><span style="width:6px;height:6px;border-radius:999px;background:#1a365d;"></span><span style="font-size:9.5px;letter-spacing:0.08em;text-transform:uppercase;color:#1a365d;">Decision needed</span></div>
          <div style="font-size:12.5px;color:#1a1a1a;line-height:1.45;margin-bottom:12px;letter-spacing:-0.01em;">Finalize the 16-field schema, or preview it on a paper first.</div>
          <button style="display:flex;align-items:center;justify-content:center;gap:8px;width:100%;background:#1a365d;color:#fffefc;border:none;border-radius:10px;padding:11px;font-size:13.5px;font-family:inherit;letter-spacing:-0.01em;cursor:pointer;transition:background .15s ease;" data-hover="background:#142948;"><i class="ph ph-check-circle" style="font-size:16px;"></i>Finalize Schema</button>
          <div style="display:flex;gap:8px;margin-top:8px;">
            <button data-act="tab" data-tab="preview" style="flex:1;display:flex;align-items:center;justify-content:center;gap:6px;background:none;color:#1a365d;border:1px solid #c8d8e8;border-radius:10px;padding:9px;font-size:12px;font-family:inherit;cursor:pointer;transition:background .15s ease;" data-hover="background:#eef4fb;"><i class="ph ph-files" style="font-size:14px;"></i>Preview</button>
            <button style="flex:1;display:flex;align-items:center;justify-content:center;gap:6px;background:none;color:#1a365d;border:1px solid #c8d8e8;border-radius:10px;padding:9px;font-size:12px;font-family:inherit;cursor:pointer;transition:background .15s ease;" data-hover="background:#eef4fb;"><i class="ph ph-code" style="font-size:14px;"></i>JSON</button>
          </div>
          <button style="width:100%;display:flex;align-items:center;justify-content:center;gap:6px;background:none;color:#6b746c;border:none;padding:10px 0 1px;font-size:12px;font-family:inherit;cursor:pointer;transition:color .15s ease;" data-hover="color:#1a365d;"><i class="ph ph-arrows-clockwise" style="font-size:13px;"></i>Regenerate schema</button>
        </div>` : ''}

      ${v.showLocked ? `
        <div style="margin-left:26px;display:flex;align-items:center;gap:9px;border:1px dashed #d3d9d1;border-radius:11px;padding:11px 13px;color:#9aa39b;">
          <i class="ph ph-lock-simple" style="font-size:16px;"></i><span style="font-size:12px;letter-spacing:-0.01em;">Waiting on schema finalization</span>
        </div>` : ''}

      ${v.showQuietAction ? `
        <button style="margin-left:26px;align-self:flex-start;display:inline-flex;align-items:center;gap:7px;background:none;color:#1a365d;border:1px solid #c8d8e8;border-radius:999px;padding:7px 13px;font-size:12px;font-family:inherit;cursor:pointer;transition:background .15s ease;" data-hover="background:#eef4fb;"><i class="ph ph-arrow-bend-down-right" style="font-size:13px;"></i>${v.quietActionLabel}</button>` : ''}
    </div>

    <div style="flex:0 0 auto;padding:12px 14px;border-top:1px solid #eef0ee;">
      <div style="display:flex;align-items:center;gap:9px;background:#fffefc;border:1px solid #d8ddd6;border-radius:14px;padding:8px 8px 8px 12px;transition:border-color .15s ease;" data-hover="border-color:#b9c3b6;">
        <span style="flex:0 0 auto;display:flex;align-items:center;">${logo(20)}</span>
        <input placeholder="Reply to ReviewPilot…" style="flex:1;border:none;background:none;outline:none;font-size:13px;font-family:inherit;color:#1a1a1a;letter-spacing:-0.01em;">
        <button style="width:30px;height:30px;flex:0 0 30px;border-radius:9px;border:none;background:#1a365d;color:#fffefc;display:flex;align-items:center;justify-content:center;cursor:pointer;transition:background .15s ease;" data-hover="background:#142948;"><i class="ph ph-arrow-up" style="font-size:15px;"></i></button>
      </div>
      <div style="font-size:10px;color:#aab1a9;margin-top:7px;text-align:center;letter-spacing:-0.01em;">ReviewPilot can make mistakes. Verify important results.</div>
    </div>
  </aside>

</div>`;
  }

  // ---- state + wiring -------------------------------------------------------
  const state = { step: 'extraction', tab: 'fields' };

  function wireHover(root) {
    root.querySelectorAll('[data-hover]').forEach((el) => {
      const hov = el.getAttribute('data-hover');
      const base = el.getAttribute('style') || '';
      el.addEventListener('mouseenter', () => el.setAttribute('style', base + ';' + hov));
      el.addEventListener('mouseleave', () => el.setAttribute('style', base));
    });
  }

  function mount() {
    const root = document.getElementById('app');
    function paint() {
      root.innerHTML = render(computeVals(state));
      const conv = document.getElementById('rp-conv');
      if (conv) conv.scrollTop = conv.scrollHeight; // mirror componentDidMount/Update auto-scroll
      wireHover(root);
    }
    root.addEventListener('click', (e) => {
      const t = e.target.closest('[data-act]');
      if (!t) return;
      const act = t.getAttribute('data-act');
      if (act === 'step') state.step = t.getAttribute('data-step');
      else if (act === 'tab') state.tab = t.getAttribute('data-tab');
      // 'rerender' just repaints
      paint();
    });
    paint();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount);
  else mount();
})();
