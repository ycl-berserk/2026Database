from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 测试永远不继承本机真实 Key，避免回归时误调外部模型。
for _name in (
    "LLM_API_KEY",
    "DEEPSEEK_API_KEY",
    "OPENAI_API_KEY",
    "MOONSHOT_API_KEY",
    "EMBEDDING_API_KEY",
):
    os.environ.pop(_name, None)
