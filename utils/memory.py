"""
Memory System for ReviewPilot.

Provides both short-term (project-level) and long-term (user-level) memory
to improve suggestions for screening criteria and information extraction.

LEGACY/UNWIRED: production Agent Memory is implemented in
``reviewpilot_core.agent_memory``. This prototype is retained only for
backward compatibility and must not be imported by the Web App runtime.
"""

LEGACY_UNWIRED = True

import json
import sqlite3
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime
from dataclasses import dataclass, asdict, field
import hashlib


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class ExtractionField:
    """Definition of a field to extract from papers."""
    name: str
    description: str
    field_type: str = "text"  # text, number, list, boolean
    examples: List[str] = field(default_factory=list)
    required: bool = True

    def to_dict(self) -> Dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict) -> 'ExtractionField':
        return cls(**data)


@dataclass
class ExtractionSchema:
    """Schema for extracting information from papers."""
    id: str
    name: str
    domain: str
    fields: List[ExtractionField]
    extraction_prompt: str = ""
    created: str = field(default_factory=lambda: datetime.now().isoformat())
    times_used: int = 0
    user_rating: float = 0.0
    success_rate: float = 0.0

    def to_dict(self) -> Dict:
        d = asdict(self)
        d['fields'] = [f.to_dict() if isinstance(f, ExtractionField) else f for f in self.fields]
        return d

    @classmethod
    def from_dict(cls, data: Dict) -> 'ExtractionSchema':
        fields = [ExtractionField.from_dict(f) if isinstance(f, dict) else f
                  for f in data.get('fields', [])]
        data['fields'] = fields
        return cls(**data)


@dataclass
class ScreeningCriteria:
    """Criteria for screening papers."""
    inclusion: List[str] = field(default_factory=list)
    exclusion: List[str] = field(default_factory=list)
    quality: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict) -> 'ScreeningCriteria':
        return cls(**data)


@dataclass
class Interaction:
    """Record of a user interaction."""
    timestamp: str
    step: str
    user_input: str
    system_response: str
    feedback: Optional[str] = None  # approved, revised, rejected

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class Correction:
    """Record of a user correction."""
    timestamp: str
    field: str
    original: str
    revised: str
    reason: Optional[str] = None

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class LearnedPattern:
    """A pattern learned from user behavior."""
    pattern_type: str  # extraction, screening, preference
    description: str
    domain: Optional[str] = None
    example: Optional[str] = None
    confidence: float = 0.5
    times_observed: int = 1

    def to_dict(self) -> Dict:
        return asdict(self)


# =============================================================================
# SHORT-TERM MEMORY (Project-Level)
# =============================================================================

class ShortTermMemory:
    """
    Project-level memory that tracks interactions within a single project.
    Stored in the project's .memory directory.
    """

    def __init__(self, project_path: Path):
        self.project_path = Path(project_path)
        self.memory_dir = self.project_path / ".memory"
        self.memory_dir.mkdir(parents=True, exist_ok=True)

        self.session_file = self.memory_dir / "session.json"
        self.interactions_file = self.memory_dir / "interactions.jsonl"
        self.corrections_file = self.memory_dir / "corrections.json"

        self._load()

    def _load(self):
        """Load existing session data."""
        if self.session_file.exists():
            with open(self.session_file, 'r') as f:
                self.session = json.load(f)
        else:
            self.session = {
                "project_id": self.project_path.name,
                "session_start": datetime.now().isoformat(),
                "research_context": {},
                "evolved_criteria": {},
                "current_schema": None
            }

        if self.corrections_file.exists():
            with open(self.corrections_file, 'r') as f:
                self.corrections = json.load(f)
        else:
            self.corrections = []

    def _save(self):
        """Save session data."""
        with open(self.session_file, 'w') as f:
            json.dump(self.session, f, indent=2)

        with open(self.corrections_file, 'w') as f:
            json.dump(self.corrections, f, indent=2)

    def set_research_context(self, topic: str, domain: str, review_type: str = "systematic"):
        """Set the research context for this project."""
        self.session["research_context"] = {
            "topic": topic,
            "domain": domain,
            "review_type": review_type
        }
        self._save()

    def get_research_context(self) -> Dict:
        """Get the current research context."""
        return self.session.get("research_context", {})

    def record_interaction(self, step: str, user_input: str, response: str,
                          feedback: Optional[str] = None):
        """Record a user interaction."""
        interaction = Interaction(
            timestamp=datetime.now().isoformat(),
            step=step,
            user_input=user_input,
            system_response=response,
            feedback=feedback
        )

        # Append to interactions file
        with open(self.interactions_file, 'a') as f:
            f.write(json.dumps(interaction.to_dict()) + '\n')

    def record_correction(self, field: str, original: str, revised: str,
                         reason: Optional[str] = None):
        """Record a user correction (learning opportunity)."""
        correction = Correction(
            timestamp=datetime.now().isoformat(),
            field=field,
            original=original,
            revised=revised,
            reason=reason
        )
        self.corrections.append(correction.to_dict())
        self._save()

    def get_corrections(self) -> List[Dict]:
        """Get all corrections from this session."""
        return self.corrections

    def update_criteria(self, criteria: ScreeningCriteria):
        """Update the evolved screening criteria."""
        self.session["evolved_criteria"] = criteria.to_dict()
        self._save()

    def get_criteria(self) -> Optional[ScreeningCriteria]:
        """Get the current screening criteria."""
        if self.session.get("evolved_criteria"):
            return ScreeningCriteria.from_dict(self.session["evolved_criteria"])
        return None

    def set_extraction_schema(self, schema: ExtractionSchema):
        """Set the current extraction schema."""
        self.session["current_schema"] = schema.to_dict()
        self._save()

    def get_extraction_schema(self) -> Optional[ExtractionSchema]:
        """Get the current extraction schema."""
        if self.session.get("current_schema"):
            return ExtractionSchema.from_dict(self.session["current_schema"])
        return None

    def get_recent_interactions(self, step: Optional[str] = None, limit: int = 10) -> List[Dict]:
        """Get recent interactions, optionally filtered by step."""
        interactions = []
        if self.interactions_file.exists():
            with open(self.interactions_file, 'r') as f:
                for line in f:
                    if line.strip():
                        interaction = json.loads(line)
                        if step is None or interaction.get('step') == step:
                            interactions.append(interaction)

        return interactions[-limit:]


# =============================================================================
# LONG-TERM MEMORY (User-Level)
# =============================================================================

class LongTermMemory:
    """
    User-level memory that persists across projects.
    Uses SQLite for efficient storage and retrieval.
    """

    def __init__(self, user_id: str = "default", base_dir: Optional[Path] = None):
        self.user_id = user_id

        if base_dir:
            self.base_dir = Path(base_dir)
        else:
            self.base_dir = Path.home() / ".reviewpilot"

        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.base_dir / "user_memory.db"
        self.schemas_dir = self.base_dir / "extraction_schemas"
        self.schemas_dir.mkdir(exist_ok=True)
        self.field_libraries_dir = self.base_dir / "field_libraries"
        self.field_libraries_dir.mkdir(exist_ok=True)

        self._init_db()

    def _init_db(self):
        """Initialize the SQLite database."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Extraction schemas table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS extraction_schemas (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                domain TEXT,
                schema_json TEXT NOT NULL,
                created TEXT,
                times_used INTEGER DEFAULT 0,
                user_rating REAL DEFAULT 0,
                success_rate REAL DEFAULT 0
            )
        ''')

        # Screening templates table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS screening_templates (
                id TEXT PRIMARY KEY,
                domain TEXT,
                criteria_json TEXT NOT NULL,
                created TEXT,
                times_used INTEGER DEFAULT 0,
                success_rate REAL DEFAULT 0
            )
        ''')

        # Successful prompts table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS prompts (
                id TEXT PRIMARY KEY,
                category TEXT,
                domain TEXT,
                task TEXT,
                prompt_text TEXT NOT NULL,
                times_used INTEGER DEFAULT 0,
                success_rate REAL DEFAULT 0,
                created TEXT
            )
        ''')

        # Learned patterns table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS learned_patterns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pattern_type TEXT,
                description TEXT,
                domain TEXT,
                example TEXT,
                confidence REAL DEFAULT 0.5,
                times_observed INTEGER DEFAULT 1,
                created TEXT
            )
        ''')

        # User preferences table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS preferences (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated TEXT
            )
        ''')

        # Normalization rules table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS normalization_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                field_type TEXT,
                canonical TEXT,
                variant TEXT
            )
        ''')

        conn.commit()
        conn.close()

    def _get_conn(self) -> sqlite3.Connection:
        """Get a database connection."""
        return sqlite3.connect(self.db_path)

    # -------------------------------------------------------------------------
    # Extraction Schemas
    # -------------------------------------------------------------------------

    def save_extraction_schema(self, schema: ExtractionSchema):
        """Save an extraction schema."""
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute('''
            INSERT OR REPLACE INTO extraction_schemas
            (id, name, domain, schema_json, created, times_used, user_rating, success_rate)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            schema.id, schema.name, schema.domain,
            json.dumps(schema.to_dict()),
            schema.created, schema.times_used,
            schema.user_rating, schema.success_rate
        ))

        conn.commit()
        conn.close()

        # Also save as JSON file for easy inspection
        schema_file = self.schemas_dir / f"{schema.id}.json"
        with open(schema_file, 'w') as f:
            json.dump(schema.to_dict(), f, indent=2)

    def get_extraction_schema(self, schema_id: str) -> Optional[ExtractionSchema]:
        """Get an extraction schema by ID."""
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute('SELECT schema_json FROM extraction_schemas WHERE id = ?', (schema_id,))
        row = cursor.fetchone()
        conn.close()

        if row:
            return ExtractionSchema.from_dict(json.loads(row[0]))
        return None

    def get_schemas_by_domain(self, domain: str, limit: int = 5) -> List[ExtractionSchema]:
        """Get extraction schemas for a domain, ordered by success rate."""
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT schema_json FROM extraction_schemas
            WHERE domain LIKE ?
            ORDER BY success_rate DESC, times_used DESC
            LIMIT ?
        ''', (f'%{domain}%', limit))

        rows = cursor.fetchall()
        conn.close()

        return [ExtractionSchema.from_dict(json.loads(row[0])) for row in rows]

    def get_all_schemas(self) -> List[ExtractionSchema]:
        """Get all extraction schemas."""
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute('SELECT schema_json FROM extraction_schemas ORDER BY times_used DESC')
        rows = cursor.fetchall()
        conn.close()

        return [ExtractionSchema.from_dict(json.loads(row[0])) for row in rows]

    def increment_schema_usage(self, schema_id: str):
        """Increment usage count for a schema."""
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute('''
            UPDATE extraction_schemas
            SET times_used = times_used + 1
            WHERE id = ?
        ''', (schema_id,))

        conn.commit()
        conn.close()

    def update_schema_rating(self, schema_id: str, rating: float, success_rate: float):
        """Update rating and success rate for a schema."""
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute('''
            UPDATE extraction_schemas
            SET user_rating = ?, success_rate = ?
            WHERE id = ?
        ''', (rating, success_rate, schema_id))

        conn.commit()
        conn.close()

    # -------------------------------------------------------------------------
    # Screening Templates
    # -------------------------------------------------------------------------

    def save_screening_template(self, domain: str, criteria: ScreeningCriteria,
                                 success_rate: float = 0.0):
        """Save a screening template."""
        template_id = hashlib.md5(f"{domain}_{datetime.now().isoformat()}".encode()).hexdigest()[:12]

        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute('''
            INSERT OR REPLACE INTO screening_templates
            (id, domain, criteria_json, created, times_used, success_rate)
            VALUES (?, ?, ?, ?, 0, ?)
        ''', (
            template_id, domain,
            json.dumps(criteria.to_dict()),
            datetime.now().isoformat(),
            success_rate
        ))

        conn.commit()
        conn.close()

        return template_id

    def get_screening_templates(self, domain: str, limit: int = 3) -> List[Tuple[str, ScreeningCriteria]]:
        """Get screening templates for a domain."""
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT id, criteria_json FROM screening_templates
            WHERE domain LIKE ?
            ORDER BY success_rate DESC, times_used DESC
            LIMIT ?
        ''', (f'%{domain}%', limit))

        rows = cursor.fetchall()
        conn.close()

        return [(row[0], ScreeningCriteria.from_dict(json.loads(row[1]))) for row in rows]

    # -------------------------------------------------------------------------
    # Prompts
    # -------------------------------------------------------------------------

    def save_prompt(self, category: str, domain: str, task: str, prompt_text: str):
        """Save a successful prompt."""
        prompt_id = hashlib.md5(f"{category}_{domain}_{task}".encode()).hexdigest()[:12]

        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute('''
            INSERT OR REPLACE INTO prompts
            (id, category, domain, task, prompt_text, times_used, success_rate, created)
            VALUES (?, ?, ?, ?, ?, 0, 0, ?)
        ''', (
            prompt_id, category, domain, task, prompt_text,
            datetime.now().isoformat()
        ))

        conn.commit()
        conn.close()

        return prompt_id

    def get_prompts(self, category: str, domain: Optional[str] = None,
                    limit: int = 5) -> List[Dict]:
        """Get prompts for a category and optional domain."""
        conn = self._get_conn()
        cursor = conn.cursor()

        if domain:
            cursor.execute('''
                SELECT id, category, domain, task, prompt_text, times_used, success_rate
                FROM prompts
                WHERE category = ? AND domain LIKE ?
                ORDER BY success_rate DESC, times_used DESC
                LIMIT ?
            ''', (category, f'%{domain}%', limit))
        else:
            cursor.execute('''
                SELECT id, category, domain, task, prompt_text, times_used, success_rate
                FROM prompts
                WHERE category = ?
                ORDER BY success_rate DESC, times_used DESC
                LIMIT ?
            ''', (category, limit))

        rows = cursor.fetchall()
        conn.close()

        return [{
            'id': row[0], 'category': row[1], 'domain': row[2],
            'task': row[3], 'prompt_text': row[4],
            'times_used': row[5], 'success_rate': row[6]
        } for row in rows]

    def increment_prompt_usage(self, prompt_id: str, success: bool = True):
        """Increment usage and update success rate for a prompt."""
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute('SELECT times_used, success_rate FROM prompts WHERE id = ?', (prompt_id,))
        row = cursor.fetchone()

        if row:
            times_used = row[0] + 1
            # Update success rate as running average
            old_rate = row[1]
            new_rate = ((old_rate * row[0]) + (1.0 if success else 0.0)) / times_used

            cursor.execute('''
                UPDATE prompts
                SET times_used = ?, success_rate = ?
                WHERE id = ?
            ''', (times_used, new_rate, prompt_id))

        conn.commit()
        conn.close()

    # -------------------------------------------------------------------------
    # Learned Patterns
    # -------------------------------------------------------------------------

    def add_learned_pattern(self, pattern: LearnedPattern):
        """Add or update a learned pattern."""
        conn = self._get_conn()
        cursor = conn.cursor()

        # Check if similar pattern exists
        cursor.execute('''
            SELECT id, times_observed, confidence FROM learned_patterns
            WHERE pattern_type = ? AND description = ?
        ''', (pattern.pattern_type, pattern.description))

        row = cursor.fetchone()

        if row:
            # Update existing pattern
            new_times = row[1] + 1
            new_confidence = min(0.95, row[2] + 0.05)  # Increase confidence
            cursor.execute('''
                UPDATE learned_patterns
                SET times_observed = ?, confidence = ?
                WHERE id = ?
            ''', (new_times, new_confidence, row[0]))
        else:
            # Insert new pattern
            cursor.execute('''
                INSERT INTO learned_patterns
                (pattern_type, description, domain, example, confidence, times_observed, created)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (
                pattern.pattern_type, pattern.description, pattern.domain,
                pattern.example, pattern.confidence, pattern.times_observed,
                datetime.now().isoformat()
            ))

        conn.commit()
        conn.close()

    def get_patterns(self, pattern_type: Optional[str] = None,
                     domain: Optional[str] = None,
                     min_confidence: float = 0.5) -> List[LearnedPattern]:
        """Get learned patterns filtered by type, domain, and confidence."""
        conn = self._get_conn()
        cursor = conn.cursor()

        query = 'SELECT pattern_type, description, domain, example, confidence, times_observed FROM learned_patterns WHERE confidence >= ?'
        params = [min_confidence]

        if pattern_type:
            query += ' AND pattern_type = ?'
            params.append(pattern_type)

        if domain:
            query += ' AND (domain LIKE ? OR domain IS NULL)'
            params.append(f'%{domain}%')

        query += ' ORDER BY confidence DESC, times_observed DESC'

        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()

        return [LearnedPattern(
            pattern_type=row[0], description=row[1], domain=row[2],
            example=row[3], confidence=row[4], times_observed=row[5]
        ) for row in rows]

    # -------------------------------------------------------------------------
    # Preferences
    # -------------------------------------------------------------------------

    def set_preference(self, key: str, value: Any):
        """Set a user preference."""
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute('''
            INSERT OR REPLACE INTO preferences (key, value, updated)
            VALUES (?, ?, ?)
        ''', (key, json.dumps(value), datetime.now().isoformat()))

        conn.commit()
        conn.close()

    def get_preference(self, key: str, default: Any = None) -> Any:
        """Get a user preference."""
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute('SELECT value FROM preferences WHERE key = ?', (key,))
        row = cursor.fetchone()
        conn.close()

        if row:
            return json.loads(row[0])
        return default

    def get_all_preferences(self) -> Dict:
        """Get all user preferences."""
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute('SELECT key, value FROM preferences')
        rows = cursor.fetchall()
        conn.close()

        return {row[0]: json.loads(row[1]) for row in rows}

    # -------------------------------------------------------------------------
    # Field Libraries
    # -------------------------------------------------------------------------

    def get_common_fields(self, domain: str) -> List[ExtractionField]:
        """Get common fields for a domain."""
        library_file = self.field_libraries_dir / f"{domain.replace('/', '_')}.json"

        if library_file.exists():
            with open(library_file, 'r') as f:
                data = json.load(f)
                return [ExtractionField.from_dict(f) for f in data.get('fields', [])]

        return []

    def save_field_library(self, domain: str, fields: List[ExtractionField]):
        """Save a field library for a domain."""
        library_file = self.field_libraries_dir / f"{domain.replace('/', '_')}.json"

        with open(library_file, 'w') as f:
            json.dump({
                'domain': domain,
                'fields': [f.to_dict() for f in fields],
                'updated': datetime.now().isoformat()
            }, f, indent=2)

    # -------------------------------------------------------------------------
    # Normalization Rules
    # -------------------------------------------------------------------------

    def add_normalization_rule(self, field_type: str, canonical: str, variant: str):
        """Add a normalization rule."""
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute('''
            INSERT OR IGNORE INTO normalization_rules (field_type, canonical, variant)
            VALUES (?, ?, ?)
        ''', (field_type, canonical, variant))

        conn.commit()
        conn.close()

    def normalize_value(self, field_type: str, value: str) -> str:
        """Normalize a value using stored rules."""
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT canonical FROM normalization_rules
            WHERE field_type = ? AND LOWER(variant) = LOWER(?)
        ''', (field_type, value))

        row = cursor.fetchone()
        conn.close()

        return row[0] if row else value


# =============================================================================
# MEMORY MANAGER (Combines Short-term and Long-term)
# =============================================================================

class MemoryManager:
    """
    High-level interface for the memory system.
    Combines short-term (project) and long-term (user) memory.
    """

    def __init__(self, project_path: Optional[Path] = None, user_id: str = "default"):
        self.user_id = user_id
        self.long_term = LongTermMemory(user_id)

        if project_path:
            self.short_term = ShortTermMemory(project_path)
        else:
            self.short_term = None

    def set_project(self, project_path: Path):
        """Set or change the current project."""
        self.short_term = ShortTermMemory(project_path)

    # -------------------------------------------------------------------------
    # Suggestion Methods
    # -------------------------------------------------------------------------

    def suggest_criteria(self, topic: str, domain: str) -> Dict:
        """Suggest screening criteria based on memory."""
        suggestions = {
            "inclusion": [],
            "exclusion": [],
            "quality": [],
            "source": "default"
        }

        # Check long-term memory for similar domains
        templates = self.long_term.get_screening_templates(domain)
        if templates:
            best_template = templates[0][1]
            suggestions["inclusion"] = best_template.inclusion
            suggestions["exclusion"] = best_template.exclusion
            suggestions["quality"] = best_template.quality
            suggestions["source"] = "long_term_memory"

        # Get learned patterns for this domain
        patterns = self.long_term.get_patterns(
            pattern_type="screening",
            domain=domain,
            min_confidence=0.6
        )

        if patterns:
            suggestions["learned_patterns"] = [p.description for p in patterns]

        return suggestions

    def suggest_extraction_schema(self, topic: str, domain: str) -> Optional[ExtractionSchema]:
        """Suggest an extraction schema based on memory."""
        # First check for exact domain match
        schemas = self.long_term.get_schemas_by_domain(domain)

        if schemas:
            return schemas[0]

        # Fall back to common fields for domain
        common_fields = self.long_term.get_common_fields(domain)

        if common_fields:
            # Create a new schema from common fields
            schema = ExtractionSchema(
                id=f"suggested_{domain.replace('/', '_')}",
                name=f"Suggested for {domain}",
                domain=domain,
                fields=common_fields
            )
            return schema

        return None

    def suggest_fields(self, domain: str, existing_fields: List[str] = None) -> List[ExtractionField]:
        """Suggest additional fields based on domain and what's already included."""
        existing = set(existing_fields or [])

        # Get common fields for domain
        all_fields = self.long_term.get_common_fields(domain)

        # Filter out already included fields
        suggestions = [f for f in all_fields if f.name not in existing]

        return suggestions

    def get_best_extraction_prompt(self, domain: str, task: str) -> Optional[str]:
        """Get the best extraction prompt for a domain and task."""
        prompts = self.long_term.get_prompts(
            category="extraction",
            domain=domain,
            limit=1
        )

        if prompts:
            return prompts[0]['prompt_text']

        return None

    # -------------------------------------------------------------------------
    # Learning Methods
    # -------------------------------------------------------------------------

    def learn_from_correction(self, field: str, original: str, revised: str,
                              domain: Optional[str] = None):
        """Learn from a user correction."""
        # Record in short-term memory
        if self.short_term:
            self.short_term.record_correction(field, original, revised)

        # Add normalization rule if it looks like a standardization
        if len(original) < 50 and len(revised) < 50:
            self.long_term.add_normalization_rule(field, revised, original)

        # Create a learned pattern
        pattern = LearnedPattern(
            pattern_type="extraction",
            description=f"For field '{field}': prefer '{revised}' over '{original}'",
            domain=domain,
            example=f"{original} -> {revised}",
            confidence=0.6
        )
        self.long_term.add_learned_pattern(pattern)

    def learn_from_successful_extraction(self, schema: ExtractionSchema,
                                         success_rate: float, rating: float = None):
        """Record a successful extraction schema usage."""
        schema.times_used += 1
        schema.success_rate = (
            (schema.success_rate * (schema.times_used - 1) + success_rate)
            / schema.times_used
        )

        if rating:
            schema.user_rating = rating

        self.long_term.save_extraction_schema(schema)

    def learn_from_successful_screening(self, criteria: ScreeningCriteria,
                                         domain: str, success_rate: float):
        """Record successful screening criteria."""
        self.long_term.save_screening_template(domain, criteria, success_rate)

    # -------------------------------------------------------------------------
    # Context Methods
    # -------------------------------------------------------------------------

    def get_context_for_step(self, step: str) -> Dict:
        """Get relevant context for a workflow step."""
        context = {
            "preferences": self.long_term.get_all_preferences(),
            "patterns": []
        }

        # Get patterns relevant to this step
        step_to_pattern_type = {
            "screening": "screening",
            "criteria": "screening",
            "extraction": "extraction",
            "quality": "screening"
        }

        pattern_type = step_to_pattern_type.get(step)
        if pattern_type:
            patterns = self.long_term.get_patterns(pattern_type=pattern_type)
            context["patterns"] = [p.to_dict() for p in patterns]

        # Add short-term context if available
        if self.short_term:
            context["research_context"] = self.short_term.get_research_context()
            context["corrections"] = self.short_term.get_corrections()
            context["recent_interactions"] = self.short_term.get_recent_interactions(step)

        return context

    def get_memory_summary(self) -> Dict:
        """Get a summary of memory contents for debugging/display."""
        summary = {
            "long_term": {
                "schemas": len(self.long_term.get_all_schemas()),
                "preferences": len(self.long_term.get_all_preferences()),
                "patterns": len(self.long_term.get_patterns())
            }
        }

        if self.short_term:
            summary["short_term"] = {
                "project": self.short_term.session.get("project_id"),
                "context": self.short_term.get_research_context(),
                "corrections": len(self.short_term.get_corrections())
            }

        return summary

    def get_prompt_context(self, step: str, topic: str = "", domain: str = "") -> str:
        """
        Generate a prompt-ready context string for LLM agents.

        This provides agents with relevant memory context to improve their responses.

        Args:
            step: Current workflow step (search, screening, extraction, chat)
            topic: Current research topic
            domain: Current research domain

        Returns:
            A formatted string to include in LLM prompts
        """
        context_parts = []

        # Add learned patterns for this step
        patterns = self.long_term.get_patterns(pattern_type=step)
        if patterns:
            pattern_strs = []
            for p in patterns[:5]:  # Top 5 patterns by confidence
                pattern_strs.append(f"- {p.description} (confidence: {p.confidence:.0%})")
            if pattern_strs:
                context_parts.append(f"**Learned Patterns:**\n" + "\n".join(pattern_strs))

        # Add domain-specific successful templates
        if step == "screening" and domain:
            templates = self.long_term.get_screening_templates(domain)
            if templates:
                _, best_criteria = templates[0]  # Unpack tuple (domain, ScreeningCriteria)
                inclusion = best_criteria.inclusion
                exclusion = best_criteria.exclusion
                if inclusion or exclusion:
                    template_str = f"**Previous Successful Criteria for {domain}:**\n"
                    if inclusion:
                        template_str += "Inclusion: " + ", ".join(inclusion[:3]) + "\n"
                    if exclusion:
                        template_str += "Exclusion: " + ", ".join(exclusion[:3])
                    context_parts.append(template_str)

        if step == "extraction" and domain:
            schemas = self.long_term.get_schemas_by_domain(domain)
            if schemas:
                best = schemas[0]
                field_names = [f.name for f in best.fields[:8]]
                context_parts.append(
                    f"**Previously Successful Fields for {domain}:**\n" +
                    ", ".join(field_names)
                )

        # Add recent corrections (learning from mistakes)
        if self.short_term:
            corrections = self.short_term.get_corrections()
            if corrections:
                correction_strs = []
                for c in corrections[-3:]:  # Last 3 corrections
                    correction_strs.append(
                        f"- Changed '{c.get('field', 'unknown')}': "
                        f"'{c.get('original', '')[:30]}...' → '{c.get('revised', '')[:30]}...'"
                    )
                if correction_strs:
                    context_parts.append(
                        "**Recent User Corrections (learn from these):**\n" +
                        "\n".join(correction_strs)
                    )

            # Add recent interactions for chat context
            if step == "chat":
                interactions = self.short_term.get_recent_interactions(limit=5)
                if interactions:
                    interaction_strs = []
                    for i in interactions:
                        interaction_strs.append(f"- User: {i.get('user_input', '')[:50]}...")
                    context_parts.append(
                        "**Recent Interactions:**\n" + "\n".join(interaction_strs)
                    )

        # Add user preferences
        preferences = self.long_term.get_all_preferences()
        if preferences:
            pref_strs = [f"- {k}: {v}" for k, v in list(preferences.items())[:5]]
            context_parts.append("**User Preferences:**\n" + "\n".join(pref_strs))

        if not context_parts:
            return ""

        return "\n\n".join(context_parts)


# =============================================================================
# DEFAULT FIELD LIBRARIES
# =============================================================================

DEFAULT_FIELD_LIBRARIES = {
    "healthcare": [
        ExtractionField("study_design", "Study design (RCT, cohort, case-control, etc.)", "text", ["RCT", "Prospective cohort", "Retrospective cohort"]),
        ExtractionField("sample_size", "Number of participants/patients", "number", ["100", "500", "1000"]),
        ExtractionField("population", "Patient population characteristics", "text", ["Adults with diabetes", "ICU patients"]),
        ExtractionField("intervention", "Treatment or intervention tested", "text", ["Drug A vs placebo", "AI-assisted diagnosis"]),
        ExtractionField("comparator", "Control or comparison group", "text", ["Standard care", "Placebo"]),
        ExtractionField("outcomes", "Primary and secondary outcomes", "text", ["Mortality", "Length of stay"]),
        ExtractionField("results", "Key findings with numbers", "text", ["HR 0.85 (95% CI 0.72-0.99)"]),
        ExtractionField("limitations", "Study limitations", "text", ["Small sample size", "Single center"]),
    ],
    "AI_ML": [
        ExtractionField("model", "Model architecture used", "text", ["BERT", "GPT-4", "ResNet"]),
        ExtractionField("dataset", "Training/evaluation dataset", "text", ["ImageNet", "MIMIC-III"]),
        ExtractionField("task", "Task being performed", "text", ["Classification", "Segmentation"]),
        ExtractionField("metrics", "Evaluation metrics used", "text", ["Accuracy", "F1-score", "AUC"]),
        ExtractionField("performance", "Results with numbers", "text", ["92% accuracy", "0.85 AUC"]),
        ExtractionField("baseline", "Comparison baselines", "text", ["Previous SOTA", "Human performance"]),
        ExtractionField("code_available", "Is code/data available", "boolean", ["Yes", "No"]),
    ],
    "LLM_as_judge": [
        ExtractionField("dataset", "Dataset name(s) used", "text", ["MIMIC-IV", "PubMedQA"]),
        ExtractionField("llm_judge_model", "LLM model(s) used as judge", "text", ["GPT-4o", "Claude-3.5"]),
        ExtractionField("judge_content", "What the LLM judge evaluates", "text", ["Response quality", "Factual accuracy"]),
        ExtractionField("evaluation_metrics", "Metrics to evaluate the judge", "text", ["Cohen's kappa", "Accuracy"]),
        ExtractionField("judge_performance", "Performance vs ground truth", "text", ["0.78 kappa", "85% agreement"]),
        ExtractionField("prompt_strategy", "Prompting technique used", "text", ["Zero-shot", "Few-shot CoT"]),
        ExtractionField("limitations", "Limitations discussed", "text", ["Hallucination", "Inconsistency"]),
        ExtractionField("potential_direction", "Future directions", "text", ["Fine-tuning", "Multi-agent"]),
    ]
}


def initialize_default_libraries(memory: LongTermMemory):
    """Initialize default field libraries."""
    for domain, fields in DEFAULT_FIELD_LIBRARIES.items():
        memory.save_field_library(domain, fields)
