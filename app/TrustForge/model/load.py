"""Model loader for TrustForge AgentCore runtime.

此處的 BedrockModel 用於 Strands Agent 的對話編排（system prompt + tool selection）。
pipeline 內部的 LLM 呼叫（claim extraction / narrative / stance）仍走 bedrock.py 的
boto3 bedrock-runtime 直連，不經過這裡。
"""
from strands.models.bedrock import BedrockModel


def load_model() -> BedrockModel:
    """Get Bedrock model client using IAM credentials (auto-injected by AgentCore Runtime)."""
    return BedrockModel(model_id="us.anthropic.claude-sonnet-4-5-20250929-v1:0")
