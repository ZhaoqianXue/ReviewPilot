"""
Base Agent class for the multi-agent academic paper search system.
Provides common functionality for all agents including state management,
logging, and standard interfaces.
"""

import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, Any, Optional
from datetime import datetime

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from utils.jsonl_handler import save_json, load_json


class BaseAgent(ABC):
    """
    Base class for all agents in the pipeline.

    Provides:
    - Project path management
    - State persistence for resume capability
    - Logging integration
    - Standard run interface
    """

    def __init__(self, project_path: Path, agent_name: str):
        """
        Initialize the base agent.

        Args:
            project_path: Path to the project output directory
            agent_name: Name of this agent (used for logging and state)
        """
        self.project_path = Path(project_path)
        self.agent_name = agent_name
        self.state: Dict[str, Any] = {}
        self.logger = self._setup_logger()

    def _setup_logger(self) -> logging.Logger:
        """Set up logging for this agent."""
        logger = logging.getLogger(f"agent.{self.agent_name}")

        # Only add handler if not already configured
        if not logger.handlers:
            logger.setLevel(logging.DEBUG)

            # Create logs directory
            log_dir = self.project_path / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)

            # File handler for detailed logs
            file_handler = logging.FileHandler(
                log_dir / "pipeline.log",
                encoding='utf-8'
            )
            file_handler.setLevel(logging.DEBUG)
            file_formatter = logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
            )
            file_handler.setFormatter(file_formatter)
            logger.addHandler(file_handler)

            # Console handler for important messages
            console_handler = logging.StreamHandler()
            console_handler.setLevel(logging.INFO)
            console_formatter = logging.Formatter('%(message)s')
            console_handler.setFormatter(console_formatter)
            logger.addHandler(console_handler)

        return logger

    @abstractmethod
    def run(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute the agent's main task.

        Args:
            input_data: Input data from previous agent or coordinator

        Returns:
            Output data for next agent or coordinator
        """
        pass

    def save_state(self, state: Optional[Dict[str, Any]] = None):
        """
        Save agent state for potential resume.

        Args:
            state: State to save (uses self.state if not provided)
        """
        if state is not None:
            self.state = state

        state_file = self.project_path / "logs" / "agent_states.json"

        # Load existing states
        all_states = {}
        if state_file.exists():
            try:
                all_states = load_json(str(state_file))
            except Exception:
                all_states = {}

        # Update with this agent's state
        all_states[self.agent_name] = {
            "state": self.state,
            "updated_at": datetime.now().isoformat()
        }

        save_json(str(state_file), all_states)
        self.logger.debug(f"Saved state for {self.agent_name}")

    def load_state(self) -> Optional[Dict[str, Any]]:
        """
        Load previous agent state.

        Returns:
            Previous state dictionary, or None if not found
        """
        state_file = self.project_path / "logs" / "agent_states.json"

        if not state_file.exists():
            return None

        try:
            all_states = load_json(str(state_file))
            if self.agent_name in all_states:
                self.state = all_states[self.agent_name].get("state", {})
                self.logger.debug(f"Loaded state for {self.agent_name}")
                return self.state
        except Exception as e:
            self.logger.warning(f"Failed to load state: {e}")

        return None

    def clear_state(self):
        """Clear this agent's saved state."""
        state_file = self.project_path / "logs" / "agent_states.json"

        if state_file.exists():
            try:
                all_states = load_json(str(state_file))
                if self.agent_name in all_states:
                    del all_states[self.agent_name]
                    save_json(str(state_file), all_states)
                    self.logger.debug(f"Cleared state for {self.agent_name}")
            except Exception as e:
                self.logger.warning(f"Failed to clear state: {e}")

        self.state = {}

    def log(self, message: str, level: str = "info"):
        """
        Log a message.

        Args:
            message: Message to log
            level: Log level (debug, info, warning, error)
        """
        log_func = getattr(self.logger, level.lower(), self.logger.info)
        log_func(message)

    def ensure_directory(self, subpath: str) -> Path:
        """
        Ensure a subdirectory exists within the project path.

        Args:
            subpath: Subdirectory path relative to project

        Returns:
            Full path to the directory
        """
        path = self.project_path / subpath
        path.mkdir(parents=True, exist_ok=True)
        return path

    def get_output_path(self, filename: str, subdir: Optional[str] = None) -> Path:
        """
        Get the full path for an output file.

        Args:
            filename: Name of the output file
            subdir: Optional subdirectory

        Returns:
            Full path to the file
        """
        if subdir:
            return self.ensure_directory(subdir) / filename
        return self.project_path / filename
