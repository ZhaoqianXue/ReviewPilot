/* data.js — ReviewPilot demo data stub.
 *
 * This is the ONLY file you replace to wire the real backend.
 * Swap the literals below for values fetched from your Python API
 * (e.g. `fetch('/api/state').then(r => r.json())`), keeping the same shapes,
 * and app.js renders the live data unchanged.
 *
 * Ported verbatim from the original design's data model.
 */
window.RP_DATA = {
  // Top-bar project meta
  project: {
    title: 'LLM in Biomedicine Survey',
    status: 'Active · 2 min ago',
    model: 'gpt-5-mini',
    date: 'Jun 18, 2026',
  },

  // Workflow stepper (status: 'done' | 'active' | 'todo')
  steps: [
    { n: 1, key: 'search',     label: 'Search Setup',         status: 'done',   sub: '5 sources', desc: 'Research question, keywords and sources for this review.' },
    { n: 2, key: 'screening',  label: 'Paper Screening',      status: 'done',   sub: '70 / 312',  desc: 'De-duplication and relevance assessment of collected records.' },
    { n: 3, key: 'retrieval',  label: 'Full-Text Retrieval',  status: 'done',   sub: '62 / 70',   desc: 'Full-text PDFs gathered for the included papers.' },
    { n: 4, key: 'extraction', label: 'Information Extraction',status: 'active', sub: '16 fields', desc: 'Define and review the data to extract from included papers.' },
    { n: 5, key: 'categorize', label: 'Categorization',       status: 'todo',   sub: '2 groups',  desc: 'Group the extracted papers into thematic categories.' },
  ],

  // Extraction schema — [name, type, description, required]
  fields: [
    ['Study ID',             'Text',        'Short identifier or citation key',        true],
    ['Title',                'Text',        'Full title of the paper',                 true],
    ['Authors',              'Text (list)', 'All authors as listed',                   true],
    ['Year',                 'Number',      'Publication year',                        true],
    ['Venue / Journal',      'Text',        'Conference or journal name',              false],
    ['Study Type',           'Select',      'Experiment, Survey, Case Study, Theory',  true],
    ['Domain / Application',  'Text',        'Clinical application area or use case',    false],
    ['LLM / Model Used',      'Text (list)', 'Models or approaches evaluated',          false],
    ['Dataset(s)',           'Text (list)', 'Datasets used for evaluation',            false],
    ['Sample Size',          'Number',      'Records, patients or samples',            false],
    ['Task / Objective',     'Text',        'Primary task or research objective',      true],
    ['Evaluation Metrics',   'Text (list)', 'Metrics reported (F1, AUROC…)',           false],
    ['Key Findings',         'Long text',   'Main results and conclusions',            true],
    ['Limitations',          'Long text',   'Stated limitations or risks',             false],
    ['Clinical Relevance',   'Select',      'None / Indirect / Direct',                false],
    ['Citation Count',       'Number',      'Citations to date',                       false],
  ],

  // Sources — [name, count] (bar width is computed against the max in app.js)
  platforms: [['PubMed', 138], ['IEEE Xplore', 64], ['arXiv', 52], ['ACM DL', 33], ['Semantic Scholar', 25]],

  keywords: ['large language model', 'clinical NLP', 'biomedical', 'EHR', 'medical informatics', 'decision support'],

  groups: [
    { name: 'Clinical Decision Support', n: 38, desc: 'Diagnosis, triage, risk prediction, EHR reasoning.' },
    { name: 'Biomedical NLP & IE',       n: 32, desc: 'Entity extraction, literature mining, summarization.' },
  ],

  retrieved: [
    { t: 'Clinical reasoning with large language models: a systematic evaluation', v: 'row1_pubmed_2025' },
    { t: 'Retrieval-augmented generation over electronic health records',           v: 'row7_acm_2024' },
    { t: 'Benchmarking LLMs on biomedical entity extraction',                       v: 'row14_arxiv_2025' },
  ],

  previewFields: [
    { k: 'Study ID',             v: 'chen2025clinical' },
    { k: 'Year',                 v: '2025' },
    { k: 'Venue / Journal',      v: 'Nature Medicine' },
    { k: 'Study Type',           v: 'Evaluation' },
    { k: 'Domain / Application',  v: 'Clinical decision support' },
    { k: 'LLM / Model Used',      v: 'GPT-4, Med-PaLM 2, Llama-3-70B' },
    { k: 'Task / Objective',     v: 'Diagnostic reasoning from case vignettes' },
    { k: 'Evaluation Metrics',   v: 'Accuracy 0.82 · F1 0.79' },
    { k: 'Key Findings',         v: 'LLMs approach specialist accuracy on common presentations; lag on rare conditions.' },
    { k: 'Clinical Relevance',   v: 'Direct' },
  ],

  // Assistant chat — shown cumulatively up to the current step number
  messages: [
    { step: 1, role: 'u', text: 'How are large language models applied to biomedical and clinical informatics tasks, and how is their performance evaluated?' },
    { step: 1, role: 'a', text: 'Good question — I’ll run a full systematic review on it. I expanded it into 6 keyword groups and searched 5 databases (PubMed, IEEE, arXiv, ACM, Semantic Scholar) across 2020–2026.' },
    { step: 1, role: 'a', text: '312 records identified.' },
    { step: 2, role: 'a', text: 'Screening those 312: I removed 64 duplicates, then excluded 178 as off-topic. 70 papers made it through.' },
    { step: 3, role: 'a', text: 'Pulling full text for the 70 included papers — 62 retrieved, 8 are paywalled or missing. I can retry those anytime.' },
    { step: 4, role: 'a', text: 'All 62 PDFs are in. So we capture the same details from every paper, I drafted a shared extraction schema.' },
    { step: 4, role: 'u', text: 'Looks good — what did you include?' },
    { step: 4, role: 'a', text: '16 fields spanning identity, methods and findings; 7 are required. They’re listed on the left — tell me how to proceed.' },
    { step: 5, role: 'a', text: 'Once the schema is finalized, I’ll sort all 70 papers into your 2 preferred groups — Clinical Decision Support and Biomedical NLP & IE.' },
  ],

  activityByStep: {
    search: [
      { t: '10:30:02', tag: 'search',     msg: 'conditions confirmed' },
      { t: '10:30:48', tag: 'query',      msg: 'expanded to 6 keyword groups' },
      { t: '10:31:48', tag: 'collection', msg: '312 records · 5 sources' },
    ],
    screening: [
      { t: '10:33:10', tag: 'dedup',     msg: '312 → 248 (64 removed)' },
      { t: '10:38:12', tag: 'relevance', msg: '70 included · 178 excluded' },
    ],
    retrieval: [
      { t: '10:39:05', tag: 'download',  msg: 'started · 70 targets' },
      { t: '10:41:55', tag: 'retrieval', msg: '62 / 70 PDFs fetched' },
      { t: '10:42:01', tag: 'retrieval', msg: '8 unavailable · paywalled' },
    ],
    extraction: [
      { t: '10:42:07', tag: 'schema', msg: 'generated · 16 fields' },
      { t: '10:42:09', tag: 'schema', msg: '7 required · awaiting review' },
    ],
    categorize: [
      { t: '—:—:—', tag: 'categorize', msg: 'queued · waiting on schema' },
    ],
  },

  // Secondary "quiet" action button per step (omitted where absent)
  quietLabels: { search: 'Adjust search criteria', screening: 'View 242 excluded', retrieval: 'Retry 8 unavailable' },

  // Assistant header sub-line per step
  ctxLabels: { search: 'Step complete', screening: 'Step complete', retrieval: 'Step complete', extraction: 'Schema ready for review', categorize: 'Queued · waiting' },

  history: [
    { label: 'Today', items: [
      { title: 'LLM in Biomedicine Survey',          active: true },
      { title: 'Agent Skills: a systematic survey',  active: false },
    ]},
    { label: 'Previous 7 days', items: [
      { title: 'RAG over electronic health records',  active: false },
      { title: 'Benchmarking clinical LLMs',          active: false },
      { title: 'Multimodal medical imaging review',   active: false },
      { title: 'Privacy-preserving NLP in health',    active: false },
      { title: 'Synthetic data for medical ML',       active: false },
    ]},
  ],
};
