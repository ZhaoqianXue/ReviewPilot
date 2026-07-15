"""
Two-layer memory system for ReviewPilot.

LEGACY/UNWIRED: production Agent Memory is implemented in
``reviewpilot_core.agent_memory``. This prototype is retained only for
backward compatibility and must not be imported by the Web App runtime.

memory/
├── runs/                        # Short-term: per-run state
│   └── {project_name}/
│       ├── config.json          # Run configuration
│       ├── search_log.jsonl     # Search queries + results
│       ├── screening_log.jsonl  # Screening decisions
│       └── extraction_log.jsonl # Extraction results
├── search/
│   └── queries.jsonl            # Long-term: reusable query patterns
├── screening/
│   └── prompts.jsonl            # Long-term: reusable screening prompts
├── extraction/
│   └── schemas.jsonl            # Long-term: reusable extraction schemas
├── papers/
│   └── papers.jsonl             # Long-term: per-paper knowledge
└── preferences.json             # Global user preferences
"""

LEGACY_UNWIRED = True

import json
from pathlib import Path
from datetime import datetime
from typing import List, Any


class AgentMemory:
    """Two-layer memory: short-term (per run) + long-term (across runs)."""

    def __init__(self, base_dir: str = None):
        if base_dir is None:
            base_dir = Path(__file__).parent.parent / "memory"
        self.base = Path(base_dir)
        self._ensure_dirs()

    def _ensure_dirs(self):
        for d in ["runs", "search", "screening", "extraction"]:
            (self.base / d).mkdir(parents=True, exist_ok=True)

    # =========================================================================
    # Layer 1: Short-term run memory
    # =========================================================================

    def get_run_dir(self, project_name: str) -> Path:
        run_dir = self.base / "runs" / project_name
        run_dir.mkdir(parents=True, exist_ok=True)
        return run_dir

    def save_run_config(self, project_name: str, config: dict):
        """Save the run configuration."""
        path = self.get_run_dir(project_name) / "config.json"
        path.write_text(json.dumps(config, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

    def load_run_config(self, project_name: str) -> dict:
        path = self.get_run_dir(project_name) / "config.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        return {}

    def append_run_log(self, project_name: str, log_type: str, record: dict):
        """Append a record to a run log (search_log, screening_log, extraction_log)."""
        path = self.get_run_dir(project_name) / f"{log_type}.jsonl"
        record["timestamp"] = datetime.now().isoformat()
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

    def read_run_log(self, project_name: str, log_type: str) -> list:
        path = self.get_run_dir(project_name) / f"{log_type}.jsonl"
        if not path.exists():
            return []
        records = []
        for line in path.read_text(encoding="utf-8").strip().split("\n"):
            if line.strip():
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return records

    def list_runs(self) -> List[str]:
        runs_dir = self.base / "runs"
        if not runs_dir.exists():
            return []
        return [d.name for d in runs_dir.iterdir() if d.is_dir()]

    # =========================================================================
    # Layer 2: Long-term reusable memory
    # =========================================================================

    def _append_long_term(self, category: str, filename: str, record: dict):
        path = self.base / category / filename
        record["saved_at"] = datetime.now().isoformat()
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

    def _read_long_term(self, category: str, filename: str) -> list:
        path = self.base / category / filename
        if not path.exists():
            return []
        records = []
        for line in path.read_text(encoding="utf-8").strip().split("\n"):
            if line.strip():
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return records

    # ---- Search Memory ----

    def save_search_query(self, project_name: str, topic: str, domain: str,
                          query: str, platforms: list, result_count: int):
        """Save a successful search query for future reuse."""
        self._append_long_term("search", "queries.jsonl", {
            "project": project_name,
            "topic": topic,
            "domain": domain,
            "query": query,
            "platforms": platforms,
            "result_count": result_count,
        })

    def find_similar_queries(self, topic: str, domain: str = "", top_k: int = 3) -> list:
        """Find past search queries for similar topics."""
        records = self._read_long_term("search", "queries.jsonl")
        return self._rank_by_similarity(records, topic, domain, top_k)

    # ---- Screening Memory ----

    def save_screening_prompt(self, project_name: str, topic: str, domain: str,
                              prompt: str, total_screened: int = 0,
                              included_count: int = 0):
        """Save a screening prompt for future reuse."""
        self._append_long_term("screening", "prompts.jsonl", {
            "project": project_name,
            "topic": topic,
            "domain": domain,
            "prompt": prompt,
            "total_screened": total_screened,
            "included_count": included_count,
            "success_rate": included_count / total_screened if total_screened > 0 else 0,
        })

    def find_similar_prompts(self, topic: str, domain: str = "", top_k: int = 3) -> list:
        """Find past screening prompts for similar topics."""
        records = self._read_long_term("screening", "prompts.jsonl")
        return self._rank_by_similarity(records, topic, domain, top_k)

    # ---- Extraction Memory ----

    def save_extraction_schema(self, project_name: str, topic: str, domain: str,
                               schema: dict):
        """Save an extraction schema for future reuse."""
        self._append_long_term("extraction", "schemas.jsonl", {
            "project": project_name,
            "topic": topic,
            "domain": domain,
            "schema": schema,
        })

    def find_similar_schemas(self, topic: str, domain: str = "", top_k: int = 3) -> list:
        """Find past extraction schemas for similar topics."""
        records = self._read_long_term("extraction", "schemas.jsonl")
        return self._rank_by_similarity(records, topic, domain, top_k)

    # ---- Preferences ----

    def save_preference(self, key: str, value: Any):
        prefs = self._load_preferences()
        prefs[key] = value
        self._save_preferences(prefs)

    def get_preference(self, key: str, default=None):
        prefs = self._load_preferences()
        return prefs.get(key, default)

    def _load_preferences(self) -> dict:
        path = self.base / "preferences.json"
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return {}
        return {}

    def _save_preferences(self, prefs: dict):
        path = self.base / "preferences.json"
        path.write_text(json.dumps(prefs, indent=2, ensure_ascii=False), encoding="utf-8")

    # ---- Similarity Search ----

    def _rank_by_similarity(self, records: list, topic: str, domain: str = "",
                            top_k: int = 3) -> list:
        """Rank records by keyword overlap with topic/domain."""
        query_words = set(topic.lower().split())
        if domain:
            query_words |= set(domain.lower().split())
        # Remove common stop words
        stop = {"the", "a", "an", "of", "for", "and", "or", "in", "to", "on", "with", "is", "are"}
        query_words -= stop

        scored = []
        for record in records:
            record_words = set()
            for field in ["topic", "domain", "description", "project"]:
                record_words |= set(record.get(field, "").lower().split())
            record_words -= stop

            overlap = len(query_words & record_words)
            if overlap > 0:
                scored.append({"score": overlap, "record": record})

        scored.sort(key=lambda x: -x["score"])
        return [s["record"] for s in scored[:top_k]]

    def get_context_for_step(self, step: str, topic: str, domain: str = "") -> str:
        """Get relevant memory context for a workflow step as a prompt string."""
        parts = []

        if step == "search":
            similar = self.find_similar_queries(topic, domain)
            for rec in similar[:2]:
                parts.append(f"- Project '{rec.get('project', '')}' ({rec.get('topic', '')}): query = {rec.get('query', '')[:200]}")

        elif step == "screening":
            similar = self.find_similar_prompts(topic, domain)
            for rec in similar[:2]:
                parts.append(f"- Project '{rec.get('project', '')}' ({rec.get('topic', '')}): prompt snippet = {rec.get('prompt', '')[:200]}...")

        elif step == "extraction":
            similar = self.find_similar_schemas(topic, domain)
            for rec in similar[:2]:
                schema = rec.get("schema", {})
                fields = [f["name"] for f in schema.get("fields", [])[:6]]
                parts.append(f"- Project '{rec.get('project', '')}' ({rec.get('topic', '')}): fields = {', '.join(fields)}")

        if parts:
            return "\n**Similar past projects (from memory):**\n" + "\n".join(parts)
        return ""
