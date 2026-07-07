/* Demo-only fallback.
 *
 * The migrated app does not load this file through web_app.py. Starlette injects
 * real state as window.RP_DATA and serves /static/app.js instead.
 */
window.RP_DATA = {
  isNewProject: false,
  project: { title: 'ReviewPilot', status: 'No project', model: '', date: '' },
  researchQuestion: '',
  steps: [],
  fields: [],
  platforms: [],
  keywords: [],
  groups: [],
  retrieved: [],
  previewFields: [],
  previewPaper: { title: 'No paper preview available', ref: '', countLabel: '0 / 0' },
  messages: [{ step: 1, role: 'a', text: 'Run web_app.py to load real ReviewPilot projects.' }],
  activityByStep: {},
  quietLabels: {},
  quietActions: {},
  ctxLabels: {},
  history: [],
  screeningMetrics: { identified: 0, afterDedup: 0, included: 0 },
  retrievalSummary: { retrieved: 0, total: 0, openAccess: 0, viaInstitution: 0, unavailable: 0 },
  categorizationSummary: { papers: 0, groups: 0 },
};
