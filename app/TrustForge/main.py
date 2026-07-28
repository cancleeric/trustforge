"""AWS Bedrock AgentCore deployment entrypoint for TrustForge."""

from __future__ import annotations

from bedrock_agentcore.runtime import BedrockAgentCoreApp

from trustforge.agent.agentcore_runtime import invoke_payload

app = BedrockAgentCoreApp()


@app.entrypoint
async def invoke(payload, context):
    del context
    return invoke_payload(payload)


if __name__ == "__main__":
    app.run()
