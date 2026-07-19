"""Memory session manager for TrustForge.

記住使用者偏好：關注的幣種、風險偏好、分析歷史摘要。
SEMANTIC 策略自動抽取事實，SUMMARIZATION 壓縮對話歷史。
"""
import os
from typing import Optional
from bedrock_agentcore.memory.integrations.strands.config import AgentCoreMemoryConfig, RetrievalConfig
from bedrock_agentcore.memory.integrations.strands.session_manager import AgentCoreMemorySessionManager

MEMORY_ID = os.getenv("MEMORY_USERPREFERENCES_ID")
REGION = os.getenv("AWS_REGION")


def get_memory_session_manager(session_id: str, actor_id: str) -> Optional[AgentCoreMemorySessionManager]:
    """建立 memory session manager，若 MEMORY_ID 未設定則回傳 None（本機 dev 不需要 memory）。"""
    if not MEMORY_ID:
        return None

    retrieval_config = {
        f"/users/{actor_id}/preferences": RetrievalConfig(top_k=5, relevance_score=0.3),
        f"/summaries/{actor_id}/{session_id}": RetrievalConfig(top_k=3, relevance_score=0.3),
    }

    return AgentCoreMemorySessionManager(
        AgentCoreMemoryConfig(
            memory_id=MEMORY_ID,
            session_id=session_id,
            actor_id=actor_id,
            retrieval_config=retrieval_config,
        ),
        REGION,
    )
