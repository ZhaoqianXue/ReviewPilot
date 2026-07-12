"""
JSONL file handler utilities.
Provides read, write, and append operations for JSONL files with real-time flushing.
"""

import json
from pathlib import Path
from typing import Dict, List, Any, Iterator, Optional
from datetime import datetime

from reviewpilot_core.atomic_files import atomic_write_json, atomic_write_jsonl


def read_jsonl(file_path: str) -> List[Dict[str, Any]]:
    """
    Read all records from a JSONL file.

    Args:
        file_path: Path to the JSONL file

    Returns:
        List of dictionaries, one per line
    """
    records = []
    path = Path(file_path)

    if not path.exists():
        return records

    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError as e:
                    print(f"Warning: Skipping invalid JSON line: {e}")

    return records


def read_jsonl_iter(file_path: str) -> Iterator[Dict[str, Any]]:
    """
    Read records from a JSONL file as an iterator (memory efficient).

    Args:
        file_path: Path to the JSONL file

    Yields:
        Dictionary for each line
    """
    path = Path(file_path)

    if not path.exists():
        return

    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    yield json.loads(line)
                except json.JSONDecodeError as e:
                    print(f"Warning: Skipping invalid JSON line: {e}")


def write_jsonl(file_path: str, records: List[Dict[str, Any]], overwrite: bool = True):
    """
    Write records to a JSONL file.

    Args:
        file_path: Path to the JSONL file
        records: List of dictionaries to write
        overwrite: If True, overwrite existing file; if False, raise error if exists
    """
    path = Path(file_path)

    if not overwrite and path.exists():
        raise FileExistsError(f"File already exists: {file_path}")
    atomic_write_jsonl(path, records)


def append_jsonl(file_path: str, record: Dict[str, Any], flush: bool = True):
    """
    Append a single record to a JSONL file with optional immediate flush.

    Args:
        file_path: Path to the JSONL file
        record: Dictionary to append
        flush: If True, flush to disk immediately (for real-time writing)
    """
    path = Path(file_path)

    # Create parent directories if needed
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, 'a', encoding='utf-8') as f:
        f.write(json.dumps(record, ensure_ascii=False) + '\n')
        if flush:
            f.flush()


class JSONLWriter:
    """
    Context manager for writing JSONL files with real-time flushing.
    Keeps file open for efficient multiple writes.
    """

    def __init__(self, file_path: str, mode: str = 'w'):
        """
        Initialize JSONL writer.

        Args:
            file_path: Path to the JSONL file
            mode: 'w' for write (overwrite), 'a' for append
        """
        self.file_path = Path(file_path)
        self.mode = mode
        self.file = None
        self.count = 0

    def __enter__(self):
        # Create parent directories if needed
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self.file = open(self.file_path, self.mode, encoding='utf-8')
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.file:
            self.file.close()
        return False

    def write(self, record: Dict[str, Any], flush: bool = True):
        """
        Write a record to the file.

        Args:
            record: Dictionary to write
            flush: If True, flush to disk immediately
        """
        if self.file:
            self.file.write(json.dumps(record, ensure_ascii=False) + '\n')
            if flush:
                self.file.flush()
            self.count += 1

    def write_many(self, records: List[Dict[str, Any]], flush: bool = True):
        """
        Write multiple records to the file.

        Args:
            records: List of dictionaries to write
            flush: If True, flush to disk after all writes
        """
        if self.file:
            for record in records:
                self.file.write(json.dumps(record, ensure_ascii=False) + '\n')
                self.count += 1
            if flush:
                self.file.flush()


def save_json(file_path: str, data: Any, indent: int = 2):
    """
    Save data to a JSON file (not JSONL).

    Args:
        file_path: Path to the JSON file
        data: Data to save (dict, list, etc.)
        indent: Indentation level for pretty printing
    """
    atomic_write_json(file_path, data, indent=indent)


def load_json(file_path: str) -> Any:
    """
    Load data from a JSON file.

    Args:
        file_path: Path to the JSON file

    Returns:
        Loaded data
    """
    with open(file_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def count_jsonl(file_path: str) -> int:
    """
    Count the number of records in a JSONL file without loading all into memory.

    Args:
        file_path: Path to the JSONL file

    Returns:
        Number of valid JSON records
    """
    count = 0
    path = Path(file_path)

    if not path.exists():
        return 0

    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            if line.strip():
                count += 1

    return count


def write_xlsx(file_path: str, records: List[Dict[str, Any]], columns: List[str] = None):
    """
    Write records to an Excel xlsx file.

    Args:
        file_path: Path to the xlsx file
        records: List of dictionaries to write
        columns: Optional list of column names to include (in order). If None, uses all keys.
    """
    try:
        import pandas as pd
    except ImportError:
        print("Warning: pandas not installed. Cannot export to xlsx.")
        return

    path = Path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    if not records:
        # Create empty file with headers if no records
        df = pd.DataFrame(columns=columns or [])
    else:
        df = pd.DataFrame(records)

        # Reorder columns if specified
        if columns:
            # Only include columns that exist in data
            existing_cols = [c for c in columns if c in df.columns]
            # Add any remaining columns not in the specified list
            remaining_cols = [c for c in df.columns if c not in columns]
            df = df[existing_cols + remaining_cols]

    # Write to xlsx
    df.to_excel(path, index=False, engine='openpyxl')


def jsonl_to_xlsx(jsonl_path: str, xlsx_path: str = None, columns: List[str] = None):
    """
    Convert a JSONL file to xlsx format.

    Args:
        jsonl_path: Path to the input JSONL file
        xlsx_path: Path to the output xlsx file (defaults to same name with .xlsx extension)
        columns: Optional list of column names to include
    """
    if xlsx_path is None:
        xlsx_path = str(Path(jsonl_path).with_suffix('.xlsx'))

    records = read_jsonl(jsonl_path)
    write_xlsx(xlsx_path, records, columns)


def save_papers_with_xlsx(file_path: str, papers: List[Dict[str, Any]],
                          xlsx_columns: List[str] = None):
    """
    Save papers to both JSONL and xlsx formats.

    Args:
        file_path: Path to save (without extension, will create both .jsonl and .xlsx)
        papers: List of paper dictionaries
        xlsx_columns: Columns to include in xlsx (in order)
    """
    path = Path(file_path)
    base_path = path.parent / path.stem  # Remove extension if present

    # Default columns for papers
    if xlsx_columns is None:
        xlsx_columns = [
            'paper_id', 'title', 'authors', 'year', 'source',
            'doi', 'abstract', 'url', 'pdf_downloaded', 'pdf_path',
            'screening_decision', 'exclusion_reasons'
        ]

    # Write JSONL
    write_jsonl(str(base_path) + '.jsonl', papers)

    # Write xlsx
    write_xlsx(str(base_path) + '.xlsx', papers, xlsx_columns)
