"""CustomerSupport Agent: 接收程式碼需求，用 LLM 產出程式碼。

AgentCore runtime entrypoint。透過 AgentCore HTTP protocol 接收請求，
用 Bedrock Claude 生成程式碼並回傳。
"""
from __future__ import annotations
import json
import os

try:
    from strands import Agent, tool
    STRANDS_AVAILABLE = True
except ImportError:
    STRANDS_AVAILABLE = False


def create_agent():
    """Fail closed until Strands can receive the audited shared runtime gate."""
    raise RuntimeError(
        "CustomerSupport model access is disabled: unmanaged Bedrock clients are forbidden"
    )

    # Unreachable until a gated Strands adapter is implemented. Keeping the
    # schema here avoids silently changing this legacy demo's public contract.
    if not STRANDS_AVAILABLE:
        raise RuntimeError("strands-agents not installed")
    
    @tool
    def generate_code(request: str, language: str = "python") -> dict:
        """Generate code based on user request.
        
        Args:
            request: Description of what code to generate
            language: Programming language (default: python)
        
        Returns:
            dict with code, language, and explanation
        """
        # The agent itself will handle this through its LLM
        return {
            "status": "generated",
            "language": language,
            "request": request,
        }
    
    model_id = os.getenv("AGENTCORE_MODEL_ID", "us.anthropic.claude-sonnet-4-6")
    
    agent = Agent(
        model=model_id,
        tools=[generate_code],
        system_prompt="""You are a professional code assistant. 
        When asked to write code:
        1. Understand the requirement clearly
        2. Write clean, well-documented code
        3. Include error handling
        4. Explain your approach briefly
        
        Always respond with working code. Use the generate_code tool to structure your output."""
    )
    return agent


# AgentCore HTTP handler
def handler(event, context):
    """AgentCore runtime HTTP handler."""
    body = json.loads(event.get("body", "{}"))
    prompt = body.get("prompt", "")
    language = body.get("language", "python")
    
    if not prompt:
        return {
            "statusCode": 400,
            "body": json.dumps({"error": "prompt is required"})
        }
    
    try:
        agent = create_agent()
        result = agent(f"Write {language} code: {prompt}")
        return {
            "statusCode": 200,
            "body": json.dumps({
                "code": str(result),
                "language": language,
                "model": os.getenv("AGENTCORE_MODEL_ID", "us.anthropic.claude-sonnet-4-6"),
            })
        }
    except Exception as e:
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)})
        }


if __name__ == "__main__":
    # Local testing
    import sys
    prompt = sys.argv[1] if len(sys.argv) > 1 else "Write a hello world function"
    result = handler({"body": json.dumps({"prompt": prompt})}, {})
    print(json.dumps(json.loads(result["body"]), indent=2, ensure_ascii=False))
