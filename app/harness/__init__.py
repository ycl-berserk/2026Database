from __future__ import annotations

from pathlib import Path

from .agent_transcript import AgentTranscriptConnector
from .base import HarnessConnector, HarnessSession
from .codex import CodexConnector
from .opencode import OpencodeConnector

CONNECTORS: dict[str, HarnessConnector] = {
    "codex": CodexConnector(),
    "opencode": OpencodeConnector(),
    "qoder": AgentTranscriptConnector(
        key="qoder",
        label="Qoder CLI",
        platform="qoder",
        account_label="qoder-cli",
        env_name="LS_HARNESS_QODER_DIR",
        defaults=(Path("~/.qoder-cn/projects"), Path("~/.qoder/projects")),
    ),
}


def available_connectors() -> list[HarnessConnector]:
    return [connector for connector in CONNECTORS.values() if connector.root() is not None]


__all__ = ["CONNECTORS", "HarnessConnector", "HarnessSession", "available_connectors"]
