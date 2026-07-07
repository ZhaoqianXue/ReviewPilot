"""Adapter between Lead Agent intents and ReviewPilot workflow actions."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .sub_agent_contracts import SubAgentContract, default_sub_agent_contracts


class WorkflowActionAdapter:
    """Runs workflow actions through explicit Sub Agent contracts."""

    def __init__(
        self,
        contracts: list[SubAgentContract] | None = None,
    ):
        self.contracts = {contract.action: contract for contract in (contracts or default_sub_agent_contracts())}

    def contract_for(self, action: str) -> SubAgentContract:
        contract = self.contracts.get(action)
        if contract is None:
            raise ValueError(f"Unsupported action: {action}")
        return contract

    def run(
        self,
        action: str,
        output_root: Path | str,
        project_id: str,
        llm_query=None,
        input_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.contract_for(action).run(Path(output_root), project_id, llm_query=llm_query, input_data=input_data)
