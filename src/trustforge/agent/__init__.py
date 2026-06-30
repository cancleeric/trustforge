"""Agent 編排層：把信任加權後的 brief 交給 Bedrock 生成帶溯源的市場分析。"""
from .orchestrator import Analysis, analyze

__all__ = ["Analysis", "analyze"]
