export const meta = {
  name: 'agent-skill-redo-row51-100',
  description: 'Independently label L-U for row51-row100 from PDF text using supervisor coding scheme',
  phases: [
    { title: 'Label', detail: 'one agent per row reads PDF text and drafts L-U' },
    { title: 'Verify', detail: 'adversarial re-read adjudicates U + title match + column quality' },
  ],
}

const ROWS = [{"rid":"row51","title":"Black-Box Skill Stealing Attack from Proprietary LLM Agents: An Empirical Study","doi":"10.48550/arxiv.2604.21829","url":"https://openalex.org/W7155533524","textPath":"/Users/zhaoqianxue/Desktop/UA/ReviewPilot/tmp/redo_extract/row51.txt","flag":"normal","risk":false},{"rid":"row52","title":"OpenSkill: Open-World Self-Evolution for LLM Agents","doi":null,"url":"https://openalex.org/W7164090336","textPath":"/Users/zhaoqianxue/Desktop/UA/ReviewPilot/tmp/redo_extract/row52.txt","flag":"normal","risk":false},{"rid":"row53","title":"From Human Guidance to Autonomy: Agent Skill System for End-to-End LLM Deployment on Spatial NPUs","doi":"10.48550/arxiv.2606.07586","url":"https://openalex.org/W7163996892","textPath":"/Users/zhaoqianxue/Desktop/UA/ReviewPilot/tmp/redo_extract/row53.txt","flag":"normal","risk":false},{"rid":"row54","title":"Skill Is Not Document: A Query-Conditional Benchmark and Two-Stage Retriever for LLM Agent Skill Routing","doi":"10.48550/arxiv.2606.03565","url":"https://openalex.org/W7163374377","textPath":"/Users/zhaoqianxue/Desktop/UA/ReviewPilot/tmp/redo_extract/row54.txt","flag":"normal","risk":false},{"rid":"row55","title":"ShardMemo: Masked MoE Routing for Sharded Agentic LLM Memory","doi":"10.48550/arxiv.2601.21545","url":"https://openalex.org/W7126267767","textPath":"/Users/zhaoqianxue/Desktop/UA/ReviewPilot/tmp/redo_extract/row55.txt","flag":"normal","risk":false},{"rid":"row56","title":"Do Self-Evolving Agents Forget? Capability Degradation and Preservation in Lifelong LLM Agent Adaptation","doi":null,"url":"https://openalex.org/W7161091951","textPath":"/Users/zhaoqianxue/Desktop/UA/ReviewPilot/tmp/redo_extract/row56.txt","flag":"normal","risk":false},{"rid":"row57","title":"SkillDAG: Self-Evolving Typed Skill Graphs for LLM Skill Selection at Scale","doi":"10.48550/arxiv.2606.03056","url":"https://openalex.org/W7163317726","textPath":"/Users/zhaoqianxue/Desktop/UA/ReviewPilot/tmp/redo_extract/row57.txt","flag":"normal","risk":false},{"rid":"row58","title":"Lessons Learned: A Multi-Agent Framework for Code LLMs to Learn and Improve","doi":"10.48550/arxiv.2505.23946","url":"https://openalex.org/W4414855616","textPath":"/Users/zhaoqianxue/Desktop/UA/ReviewPilot/tmp/redo_extract/row58.txt","flag":"normal","risk":false},{"rid":"row59","title":"Procedural Skill Memory for LLM Agent Systems: Architecture, Benchmark, and Honest Limits of a First Implementation","doi":"10.5281/zenodo.19227514","url":"https://openalex.org/W7140276500","textPath":"/Users/zhaoqianxue/Desktop/UA/ReviewPilot/tmp/redo_extract/row59.txt","flag":"external_md","risk":false},{"rid":"row60","title":"CraftUtopia: A LLM-based Multi-Agent System for Collaborative Construction in Minecraft","doi":"10.65109/puij7087","url":"https://openalex.org/W7162246218","textPath":"/Users/zhaoqianxue/Desktop/UA/ReviewPilot/tmp/redo_extract/row60.txt","flag":"normal","risk":false},{"rid":"row61","title":"Skill-Pro: Learning Reusable Skills from Experience via Non-Parametric PPO for LLM Agents","doi":"10.48550/arxiv.2602.01869","url":"https://openalex.org/W7127324322","textPath":"/Users/zhaoqianxue/Desktop/UA/ReviewPilot/tmp/redo_extract/row61.txt","flag":"normal","risk":false},{"rid":"row62","title":"LEGO: An LLM Skill-Based Front-End Design Generation Platform","doi":null,"url":"https://openalex.org/W7158423607","textPath":"/Users/zhaoqianxue/Desktop/UA/ReviewPilot/tmp/redo_extract/row62.txt","flag":"normal","risk":false},{"rid":"row63","title":"SKILLC: Learning Autonomous Skill Internalization in LLM Agents via Contrastive Credit Assignment","doi":"10.48550/arxiv.2605.27899","url":"https://openalex.org/W7162652987","textPath":"/Users/zhaoqianxue/Desktop/UA/ReviewPilot/tmp/redo_extract/row63.txt","flag":"normal","risk":false},{"rid":"row64","title":"AutoRefine: From Trajectories to Reusable Expertise for Continual LLM Agent Refinement","doi":"10.48550/arxiv.2601.22758","url":"https://openalex.org/W7127071411","textPath":"/Users/zhaoqianxue/Desktop/UA/ReviewPilot/tmp/redo_extract/row64.txt","flag":"normal","risk":false},{"rid":"row65","title":"ExpGraph: Model-Agnostic Experience Learning with Graph-Structured Memory for LLM Agents","doi":"10.48550/arxiv.2605.30712","url":"https://openalex.org/W7163012325","textPath":"/Users/zhaoqianxue/Desktop/UA/ReviewPilot/tmp/redo_extract/row65.txt","flag":"normal","risk":false},{"rid":"row66","title":"SKILLS: Structured Knowledge Injection for LLM-Driven Telecommunications Operations","doi":"10.48550/arxiv.2603.15372","url":"https://openalex.org/W7138185929","textPath":"/Users/zhaoqianxue/Desktop/UA/ReviewPilot/tmp/redo_extract/row66.txt","flag":"normal","risk":false},{"rid":"row67","title":"EvoTrainer: Co-Evolving LLM Policies and Training Harnesses for Autonomous Agentic Reinforcement Learning","doi":"10.48550/arxiv.2606.03108","url":"https://openalex.org/W7163428483","textPath":"/Users/zhaoqianxue/Desktop/UA/ReviewPilot/tmp/redo_extract/row67.txt","flag":"normal","risk":false},{"rid":"row68","title":"SkVM: Revisiting Language VM for Skills across Heterogenous LLMs and Harnesses","doi":"10.48550/arxiv.2604.03088","url":"https://openalex.org/W7151028516","textPath":"/Users/zhaoqianxue/Desktop/UA/ReviewPilot/tmp/redo_extract/row68.txt","flag":"normal","risk":false},{"rid":"row69","title":"CaveAgent: Transforming LLMs into Stateful Runtime Operators","doi":"10.48550/arxiv.2601.01569","url":"https://openalex.org/W7118618871","textPath":"/Users/zhaoqianxue/Desktop/UA/ReviewPilot/tmp/redo_extract/row69.txt","flag":"normal","risk":false},{"rid":"row70","title":"SCALAR: Learning and Composing Skills through LLM Guided Symbolic Planning and Deep RL Grounding","doi":"10.48550/arxiv.2603.09036","url":"https://openalex.org/W7134946402","textPath":"/Users/zhaoqianxue/Desktop/UA/ReviewPilot/tmp/redo_extract/row70.txt","flag":"normal","risk":false},{"rid":"row71","title":"Library Drift: Diagnosing and Fixing a Silent Failure Mode in Self-Evolving LLM Skill Libraries","doi":"10.48550/arxiv.2605.19576","url":"https://openalex.org/W7161850881","textPath":"/Users/zhaoqianxue/Desktop/UA/ReviewPilot/tmp/redo_extract/row71.txt","flag":"normal","risk":false},{"rid":"row72","title":"Empowering Large Language Model Agents through Action Learning","doi":"10.48550/arxiv.2402.15809","url":"https://openalex.org/W4392223772","textPath":"/Users/zhaoqianxue/Desktop/UA/ReviewPilot/tmp/redo_extract/row72.txt","flag":"normal","risk":false},{"rid":"row73","title":"SkillOpt: Executive Strategy for Self-Evolving Agent Skills","doi":"10.48550/arxiv.2605.23904","url":"https://openalex.org/W7162340582","textPath":"/Users/zhaoqianxue/Desktop/UA/ReviewPilot/tmp/redo_extract/row73.txt","flag":"normal","risk":false},{"rid":"row74","title":"Automatically Learning Skills for Coding Agents","doi":"10.1145/3786335.3813196","url":"https://openalex.org/W7162114202","textPath":"/Users/zhaoqianxue/Desktop/UA/ReviewPilot/tmp/redo_extract/row74.txt","flag":"normal","risk":false},{"rid":"row75","title":"Knowledgeable Agents by Offline Reinforcement Learning from Large Language Model Rollouts","doi":"10.48550/arxiv.2404.09248","url":"https://openalex.org/W4394867908","textPath":"/Users/zhaoqianxue/Desktop/UA/ReviewPilot/tmp/redo_extract/row75.txt","flag":"normal","risk":false},{"rid":"row76","title":"AgenticGenomics: pharmacogenomics benchmark dataset for \"Trustworthy agentic genomics through versioned skill libraries\"","doi":"10.5281/zenodo.20567743","url":"https://openalex.org/W7163738419","textPath":"/Users/zhaoqianxue/Desktop/UA/ReviewPilot/tmp/redo_extract/row76.txt","flag":"normal","risk":false},{"rid":"row77","title":"CoEvoSkills: Self-Evolving Agent Skills via Co-Evolutionary Verification","doi":null,"url":"https://openalex.org/W7149873831","textPath":"/Users/zhaoqianxue/Desktop/UA/ReviewPilot/tmp/redo_extract/row77.txt","flag":"normal","risk":false},{"rid":"row78","title":"Behavioral Integrity Verification for AI Agent Skills","doi":"10.48550/arxiv.2605.11770","url":"https://openalex.org/W7161066883","textPath":"/Users/zhaoqianxue/Desktop/UA/ReviewPilot/tmp/redo_extract/row78.txt","flag":"normal","risk":false},{"rid":"row79","title":"VideoWeaver: Evaluating and Evolving Skills for Agentic Long Video Generation","doi":null,"url":"https://openalex.org/W7164235099","textPath":"/Users/zhaoqianxue/Desktop/UA/ReviewPilot/tmp/redo_extract/row79.txt","flag":"normal","risk":false},{"rid":"row80","title":"Skill as Memory, Not Document: A Database-Native Substrate for Agent Skill Catalogs","doi":"10.5281/zenodo.20128887","url":"https://openalex.org/W7160862948","textPath":"/Users/zhaoqianxue/Desktop/UA/ReviewPilot/tmp/redo_extract/row80.txt","flag":"normal","risk":false},{"rid":"row81","title":"Kimi-Dev: Agentless Training as Skill Prior for SWE-Agents","doi":"10.48550/arxiv.2509.23045","url":"https://openalex.org/W4415332089","textPath":"/Users/zhaoqianxue/Desktop/UA/ReviewPilot/tmp/redo_extract/row81.txt","flag":"normal","risk":false},{"rid":"row82","title":"MalSkillBench: A Runtime-Verified Benchmark of Malicious Agent Skills","doi":"10.48550/arxiv.2606.07131","url":"https://openalex.org/W7163907095","textPath":"/Users/zhaoqianxue/Desktop/UA/ReviewPilot/tmp/redo_extract/row82.txt","flag":"normal","risk":false},{"rid":"row83","title":"Skill-R1: Agent Skill Evolution via Reinforcement Learning","doi":"10.48550/arxiv.2605.09359","url":"https://openalex.org/W7160957554","textPath":"/Users/zhaoqianxue/Desktop/UA/ReviewPilot/tmp/redo_extract/row83.txt","flag":"normal","risk":false},{"rid":"row84","title":"Context Matters: Repository-Aware Security Analysis of the Agent Skill Ecosystem","doi":null,"url":"https://openalex.org/W7139147085","textPath":"/Users/zhaoqianxue/Desktop/UA/ReviewPilot/tmp/redo_extract/row84.txt","flag":"normal","risk":false},{"rid":"row85","title":"Organizing, Orchestrating, and Benchmarking Agent Skills at Ecosystem Scale","doi":null,"url":"https://openalex.org/W7133365140","textPath":"/Users/zhaoqianxue/Desktop/UA/ReviewPilot/tmp/redo_extract/row85.txt","flag":"normal","risk":false},{"rid":"row86","title":"SkillGuard: A Permission Framework for Agent Skills","doi":"10.48550/arxiv.2606.03024","url":"https://openalex.org/W7163319953","textPath":"/Users/zhaoqianxue/Desktop/UA/ReviewPilot/tmp/redo_extract/row86.txt","flag":"normal","risk":false},{"rid":"row87","title":"Kabab: A Hybrid Rust-TypeScript Architecture for Autonomous AI Agents with Universal Skill Loading and Multi-Provider Model Routing","doi":"10.5281/zenodo.19647937","url":"https://openalex.org/W7154909817","textPath":"/Users/zhaoqianxue/Desktop/UA/ReviewPilot/tmp/redo_extract/row87.txt","flag":"normal","risk":false},{"rid":"row88","title":"WebXSkill: Skill Learning for Autonomous Web Agents","doi":"10.48550/arxiv.2604.13318","url":"https://openalex.org/W7154615954","textPath":"/Users/zhaoqianxue/Desktop/UA/ReviewPilot/tmp/redo_extract/row88.txt","flag":"normal","risk":false},{"rid":"row89","title":"Defenses & Enablers For Skill Injection Attacks on Terminal Based Agents","doi":null,"url":"https://openalex.org/W7163595862","textPath":"/Users/zhaoqianxue/Desktop/UA/ReviewPilot/tmp/redo_extract/row89.txt","flag":"normal","risk":false},{"rid":"row90","title":"SciVisAgentSkills: Design and Evaluation of Agent Skills for Scientific Data Analysis and Visualization","doi":"10.48550/arxiv.2606.05525","url":"https://openalex.org/W7163642110","textPath":"/Users/zhaoqianxue/Desktop/UA/ReviewPilot/tmp/redo_extract/row90.txt","flag":"normal","risk":false},{"rid":"row91","title":"Reinforcement Learning for Self-Improving Agent with Skill Library","doi":"10.48550/arxiv.2512.17102","url":"https://openalex.org/W7116881161","textPath":"/Users/zhaoqianxue/Desktop/UA/ReviewPilot/tmp/redo_extract/row91.txt","flag":"normal","risk":false},{"rid":"row92","title":"SkillGrad: Optimizing Agent Skills Like Gradient Descent","doi":"10.48550/arxiv.2605.27760","url":"https://openalex.org/W7162673730","textPath":"/Users/zhaoqianxue/Desktop/UA/ReviewPilot/tmp/redo_extract/row92.txt","flag":"normal","risk":false},{"rid":"row93","title":"Terminal-World: Scaling Terminal-Agent Environments via Agent Skills","doi":"10.48550/arxiv.2605.20876","url":"https://openalex.org/W7161983523","textPath":"/Users/zhaoqianxue/Desktop/UA/ReviewPilot/tmp/redo_extract/row93.txt","flag":"normal","risk":false},{"rid":"row94","title":"FederatedSkill: Federated Learning for Agentic Skill Evolution","doi":null,"url":"https://openalex.org/W7163596986","textPath":"/Users/zhaoqianxue/Desktop/UA/ReviewPilot/tmp/redo_extract/row94.txt","flag":"normal","risk":false},{"rid":"row95","title":"Skill Reuse as Compression in Agentic RL","doi":null,"url":"https://openalex.org/W7163287260","textPath":"/Users/zhaoqianxue/Desktop/UA/ReviewPilot/tmp/redo_extract/row95.txt","flag":"normal","risk":false},{"rid":"row96","title":"Scaling Coding Agents via Atomic Skills","doi":"10.48550/arxiv.2604.05013","url":"https://openalex.org/W7152025511","textPath":"/Users/zhaoqianxue/Desktop/UA/ReviewPilot/tmp/redo_extract/row96.txt","flag":"abstract_only_no_pdf","risk":false},{"rid":"row97","title":"MemSkill: Learning and Evolving Memory Skills for Self-Evolving Agents","doi":"10.48550/arxiv.2602.02474","url":"https://openalex.org/W7127393332","textPath":"/Users/zhaoqianxue/Desktop/UA/ReviewPilot/tmp/redo_extract/row97.txt","flag":"normal","risk":false},{"rid":"row98","title":"HarmfulSkillBench: How Do Harmful Skills Weaponize Your Agents?","doi":null,"url":"https://openalex.org/W7155246914","textPath":"/Users/zhaoqianxue/Desktop/UA/ReviewPilot/tmp/redo_extract/row98.txt","flag":"normal","risk":false},{"rid":"row99","title":"Socratic-SWE: Self-Evolving Coding Agents via Trace-Derived Agent Skills","doi":"10.48550/arxiv.2606.07412","url":"https://openalex.org/W7163899414","textPath":"/Users/zhaoqianxue/Desktop/UA/ReviewPilot/tmp/redo_extract/row99.txt","flag":"normal","risk":false},{"rid":"row100","title":"CodeClinic: Evaluating Automation of Coding Skills for Clinical Reasoning Agents","doi":"10.48550/arxiv.2605.09675","url":"https://openalex.org/W7160940018","textPath":"/Users/zhaoqianxue/Desktop/UA/ReviewPilot/tmp/redo_extract/row100.txt","flag":"normal","risk":false}];

const SCHEME = `You are coding a literature row for an "agent skill" survey. Fill ten columns L-U, each a SHORT phrase or single short sentence (NOT a paragraph). Match the supervisor sample style exactly.

COLUMN DEFINITIONS:
- L purpose/application: what the work is for (one short phrase).
- M type: the category (e.g. "Security benchmark", "Skill evolution framework", "Domain agent; spreadsheet", "Memory-based online RL", "Dataset/fine-tuning benchmark").
- N functionality: the core skill mechanism / what it does.
- O Creation: how the skill artifact is created OR invoked — pick from human-authored / model-generated / retrieval-based invocation / trained / failure-driven / experience-distilled, phrased concretely for this paper.
- P workflow: a short arrow chain "A -> B -> C" of the pipeline.
- Q execution: how skills are executed (e.g. "inference-time skill invocation", "training-time RL", "runtime pipeline", "offline dataset analysis").
- R Metric(index): the evaluation metrics used.
- S methods: benchmark / baselines / ablation used (e.g. "Benchmark with baselines and ablations").
- T lifecycle governance: audit/monitor/update/security mechanisms AND the gap (e.g. "Evaluator gate; no full lifecycle/security governance"). If no PDF, state the source limitation here.
- U human judgment status: exactly one of "check", "No", "PDF mismatch", or for no-PDF cases "No local PDF" (see rule).

U DECISION RULE (decisive, non-obvious):
- "check" = the paper PRODUCES or MANAGES a reusable skill ARTIFACT or skill SYSTEM: skill generation/synthesis, skill library/catalog/repository, skill retrieval/routing, skill evolution/composition, skill invocation control, skill security/governance/permissions, or a skill benchmark/eval. The skill must persist as a reusable, inspectable artifact or be the managed object of the system.
- "No" = ordinary agent training / trajectory fine-tuning / generic RL policy improvement / generic memory adaptation / capability learning where the DELIVERABLE is a model/policy with NO persisting reusable skill artifact or skill system. The word "skill" appearing as a generic capability is NOT enough.
- CRITICAL TRAP — skill INTERNALIZATION / skill PRIOR papers: if the method takes skills (external or discovered) and DISSOLVES them into model weights via training/distillation/RL so the final deliverable is a skill-free policy (no standalone reusable skill files/library at inference), label "No" even if the title says "skill". Examples of this trap: a paper that internalizes skills via contrastive credit assignment; agentless training used only as a "skill prior" to train a policy; trajectory tuning across "skill dimensions"; offline RL distillation into a policy.
- "PDF mismatch" = the PDF content does not match the Excel title (different paper/topic). Then set L-T to N/A or a one-line note of what the PDF actually is.

SUPERVISOR SAMPLE ROWS (study the exact style and the check/No boundary):

[check — security auditing system] L: Pre-load security auditing for untrusted Agent Skills. | M: Security auditing framework. | N: Three-way classification with role-aware evidence and semantic verification. | O: Evidence extraction + verification + adjudication pipeline. | P: Skill package -> extract evidence -> verify semantics -> adjudicate verdict. | Q: Pre-load audit before agent skill use. | R: Exact accuracy; malicious recall; attack consistency. | S: SkillGuardBench and public-extension evaluations. | T: Strong pre-load audit; no post-load monitoring/update loop. | U: check

[check — retrieval skill] L: Retrieval strategy orchestration for heterogeneous RAG tasks. | M: Pluggable retrieval/orchestration skill. | N: Selects retrieval strategy using scene analysis and experience memory. | O: Human-designed skill with experience-memory routing. | P: Analyze query -> consult memory -> choose retriever -> return structured evidence. | Q: Agent invokes skill between agent and retriever pool. | R: nDCG@10. | S: BeIR NQ/HotpotQA/SciFact with fixed-retriever baselines. | T: Structured evidence output; no lifecycle governance. | U: check

[check — autonomous skill discovery] L: Autonomous discovery and practice of internet/foundation-agent skills. | M: Autonomous skill discovery system. | N: Proposer generates tasks; agent practices; evaluator validates skill acquisition. | O: Context-aware task proposal and evaluator-gated practice. | P: Propose task -> agent attempts -> evaluator scores -> update skill repertoire. | Q: Foundation-model agents execute internet/interactive tasks. | R: Skill success; diversity; generalization. | S: PAE evaluation with baselines in open-ended tasks. | T: Evaluator gate; no full lifecycle/security governance. | U: check

[No — memory adaptation, no skill artifact] L: Continual adaptation of LLM agents without parameter fine-tuning. | M: Memory-based online RL; agent adaptation. | N: Episodic memory cases function as reusable behavior guidance. | O: Memory writing/rewriting from environment feedback; retrieval policy. | P: Store experience -> rewrite memory -> retrieve cases -> guide actions -> update policy. | Q: Memory-augmented agent policy at inference. | R: Benchmark task success and adaptation performance. | S: M-MDP formulation; online RL experiments with baselines. | T: Memory update mechanism; no explicit skill audit/governance. | U: No

[No — fine-tuning data method] L: Improve agent tuning by using failed expert trajectories. | M: Agent fine-tuning/data method; tangential to skill artifacts. | N: Failed trajectories provide plans/actions for agentic skill acquisition. | O: Trajectory mining plus fine-tuning, not reusable skill-library creation. | P: Collect expert failures -> extract guidance -> train/explore -> improve agent. | Q: Fine-tuned LLM agent policy. | R: Agent performance; exploration efficiency; OOD subtask success. | S: RFT comparison and benchmark evaluation. | T: No skill lifecycle governance. | U: No

[No — trajectory tuning across "skill dimensions"] L: Generalized LLM agent capability learning from interaction trajectories. | M: Dataset/fine-tuning benchmark; agent skill dimensions. | N: 50k+ trajectories across 16 tasks and five skill dimensions. | O: Annotated trajectory collection and fine-tuning. | P: Collect trajectories -> annotate -> fine-tune model -> evaluate agent skills. | Q: Fine-tuned agent models, not modular skills. | R: Task success across 16 tasks/five dimensions. | S: Large-scale trajectory tuning and comparative experiments. | T: Dataset quality/difficulty-bias control; no skill lifecycle governance. | U: No

[PDF mismatch] L: PDF mismatch: content is PEEK rolling-contact/tribology, not agent skills. | M: Not relevant / wrong PDF. | N: N/A. | O: N/A. | P: N/A. | Q: N/A. | R: N/A. | S: N/A. | T: N/A. | U: PDF mismatch
`;

const LABEL_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['L','M','N','O','P','Q','R','S','T','U','title_match','u_reasoning','confidence'],
  properties: {
    L: {type:'string'}, M:{type:'string'}, N:{type:'string'}, O:{type:'string'},
    P:{type:'string'}, Q:{type:'string'}, R:{type:'string'}, S:{type:'string'}, T:{type:'string'},
    U:{type:'string', enum:['check','No','PDF mismatch','No local PDF']},
    title_match:{type:'boolean', description:'Does the PDF/source first-page title match the Excel title (same paper)?'},
    u_reasoning:{type:'string', description:'1-2 sentences: why this U, citing the artifact-vs-internalization test.'},
    confidence:{type:'string', enum:['high','moderate','low']},
  },
};

const VERIFY_SCHEMA = {
  type:'object', additionalProperties:false,
  required:['L','M','N','O','P','Q','R','S','T','U','changed_u','changed_cols','title_match','notes','confidence'],
  properties:{
    L:{type:'string'}, M:{type:'string'}, N:{type:'string'}, O:{type:'string'},
    P:{type:'string'}, Q:{type:'string'}, R:{type:'string'}, S:{type:'string'}, T:{type:'string'},
    U:{type:'string', enum:['check','No','PDF mismatch','No local PDF']},
    changed_u:{type:'boolean'}, changed_cols:{type:'boolean'},
    title_match:{type:'boolean'},
    notes:{type:'string', description:'Adjudication note: what you confirmed or changed and why.'},
    confidence:{type:'string', enum:['high','moderate','low']},
  },
};

function labelPrompt(r){
  const noPdf = r.flag === 'abstract_only_no_pdf';
  const extMd = r.flag === 'external_md';
  let srcNote = '';
  if (noPdf) srcNote = `\nIMPORTANT: NO PDF is available for this row (arXiv ID 404s, no open-access PDF). The source file contains ONLY the abstract. Judge from the abstract, keep confidence "low" or "moderate", and set U per the rule but note in T that no full text was available. Do NOT label PDF mismatch (there is simply no PDF). If you cannot confirm a reusable skill artifact from the abstract, lean toward the strict rule.`;
  if (extMd) srcNote = `\nNOTE: No local/publisher PDF; the source is the authors' own technical markdown from the Zenodo record (authoritative). Treat it as the paper text.`;
  return `${SCHEME}

=== YOUR TASK ===
Read the source text file with the Read tool: ${r.textPath}
Excel base info for this row:
- Excel TITLE: ${r.title}
- DOI: ${r.doi || 'none'} | URL: ${r.url}
${srcNote}

Steps:
1. Read the full source text file.
2. Check whether the source's own title matches the Excel TITLE (same paper). Set title_match.
3. Read abstract, intro, method/framework, experiments/eval, and conclusion/limitations.
4. Independently fill L-U as SHORT phrases in the supervisor style. Each cell one short sentence/phrase.
5. Apply the U rule strictly — especially the skill-internalization/skill-prior trap (training that dissolves skills into weights -> "No").
Return the structured object. Do NOT write any files. Your structured output IS the deliverable.`;
}

function verifyPrompt(r, draft){
  return `${SCHEME}

=== ADVERSARIAL VERIFICATION TASK ===
A first annotator produced a DRAFT coding for this row. Independently RE-READ the source and adjudicate. Be skeptical: challenge the U label and the title match especially.

Excel TITLE: ${r.title}
Source text file (RE-READ it with Read): ${r.textPath}

DRAFT to scrutinize:
L: ${draft.L}
M: ${draft.M}
N: ${draft.N}
O: ${draft.O}
P: ${draft.P}
Q: ${draft.Q}
R: ${draft.R}
S: ${draft.S}
T: ${draft.T}
U: ${draft.U}  (draft reasoning: ${draft.u_reasoning})

Your job:
1. Re-read the source. Confirm title_match (PDF vs Excel title).
2. Re-apply the U rule with maximum rigor. The single most common error is calling a skill-INTERNALIZATION / skill-PRIOR / trajectory-tuning / generic-RL-policy paper "check" when its deliverable is a skill-free trained policy — those are "No". The opposite error is calling a genuine skill library/generation/retrieval/security/benchmark system "No". Decide the correct U yourself.
3. Fix any L-T cell that is wrong, mislabeled, too long/verbose, or off-style. Otherwise keep the draft wording.
4. Return the FINAL, corrected full L-U (this is the authoritative output), plus changed_u/changed_cols flags, title_match, a short note, and your confidence.
Return the structured object only. Do NOT write files.`;
}

phase('Label');
const results = await pipeline(
  ROWS,
  (r) => agent(labelPrompt(r), {label:`label:${r.rid}`, phase:'Label', schema:LABEL_SCHEMA, effort:'medium'})
            .then(draft => ({r, draft})),
  (prev) => {
    if (!prev || !prev.draft) return {rid: '?', final:null};
    const {r, draft} = prev;
    return agent(verifyPrompt(r, draft), {label:`verify:${r.rid}`, phase:'Verify', schema:VERIFY_SCHEMA, effort:'high'})
      .then(v => ({rid:r.rid, flag:r.flag, draft, final:v}))
      .catch(() => ({rid:r.rid, flag:r.flag, draft, final:null}));
  }
);

const out = {};
for (const item of results){
  if (!item) continue;
  out[item.rid] = item;
}
log(`Completed ${Object.keys(out).length}/50 rows`);
return out;
