"""TrustForge AgentCore Frontend — Flask BFF。

提供：
- Cognito hosted UI login（authorization code flow）
- Chat 介面代理 AgentCore Runtime API
- 自動從 deployed-state.json 取得 agent ARN
"""
import json
import os
import uuid
from pathlib import Path
from urllib.parse import urlencode

import boto3
import requests
from flask import Flask, redirect, render_template, request, session, jsonify, url_for

app = Flask(__name__)
app.secret_key = os.urandom(32)

REGION = os.environ.get("AWS_DEFAULT_REGION", "us-west-2")

# --- Config ---

_config = {}


def get_config():
    if not _config:
        # 嘗試從 SSM 取 Cognito 參數（部署後）；本機 dev 可用環境變數覆蓋
        try:
            ssm = boto3.client("ssm", region_name=REGION)
            _config["pool_id"] = os.environ.get("COGNITO_POOL_ID") or ssm.get_parameter(
                Name="/app/trustforge/agentcore/pool_id")["Parameter"]["Value"]
            _config["web_client_id"] = os.environ.get("COGNITO_WEB_CLIENT_ID") or ssm.get_parameter(
                Name="/app/trustforge/agentcore/web_client_id")["Parameter"]["Value"]
            # 取 Cognito domain
            cognito = boto3.client("cognito-idp", region_name=REGION)
            pool_info = cognito.describe_user_pool(UserPoolId=_config["pool_id"])
            domain_prefix = pool_info["UserPool"]["Domain"]
            _config["cognito_domain"] = f"https://{domain_prefix}.auth.{REGION}.amazoncognito.com"
        except Exception as e:
            # Fallback：本機 dev 不需要 Cognito（直接跳過登入）
            _config["pool_id"] = os.environ.get("COGNITO_POOL_ID", "")
            _config["web_client_id"] = os.environ.get("COGNITO_WEB_CLIENT_ID", "")
            _config["cognito_domain"] = os.environ.get("COGNITO_DOMAIN", "")
            print(f"[WARN] Cognito not configured: {e}. Running in dev mode (no auth).")

        _config["redirect_uri"] = "http://localhost:8501/callback"
        _config["runtime_arn"] = get_runtime_arn()
    return _config


def get_runtime_arn() -> str:
    """Read runtime ARN from deployed-state.json."""
    possible_paths = [
        Path(__file__).resolve().parents[2] / "agentcore" / ".cli" / "deployed-state.json",
        Path(__file__).resolve().parents[3] / "agentcore" / ".cli" / "deployed-state.json",
        Path(__file__).resolve().parents[4] / "agentcore" / ".cli" / "deployed-state.json",
    ]
    for p in possible_paths:
        if p.exists():
            with open(p) as f:
                state = json.load(f)
            targets = state.get("targets", {})
            for target_data in targets.values():
                resources = target_data.get("resources", {})
                runtimes = resources.get("runtimes", {})
                for runtime_info in runtimes.values():
                    if "runtimeArn" in runtime_info:
                        return runtime_info["runtimeArn"]
    return os.environ.get("AGENTCORE_RUNTIME_ARN", "NOT_DEPLOYED")


# --- Routes ---

@app.route("/")
def index():
    config = get_config()
    # Dev mode: 跳過 login
    if not config.get("cognito_domain"):
        session.setdefault("access_token", "dev-mode")
        session.setdefault("username", "dev-user")
    elif "access_token" not in session:
        return redirect(url_for("login"))

    return render_template(
        "index.html",
        runtime_arn=config["runtime_arn"],
        username=session.get("username", "User"),
    )


@app.route("/login")
def login():
    config = get_config()
    if not config.get("cognito_domain"):
        return redirect(url_for("index"))
    params = {
        "client_id": config["web_client_id"],
        "response_type": "code",
        "scope": "openid email",
        "redirect_uri": config["redirect_uri"],
    }
    login_url = f"{config['cognito_domain']}/login?{urlencode(params)}"
    return render_template("login.html", login_url=login_url)


@app.route("/callback")
def callback():
    code = request.args.get("code")
    if not code:
        return redirect(url_for("login"))
    config = get_config()
    token_url = f"{config['cognito_domain']}/oauth2/token"
    resp = requests.post(token_url, data={
        "grant_type": "authorization_code",
        "client_id": config["web_client_id"],
        "code": code,
        "redirect_uri": config["redirect_uri"],
    }, headers={"Content-Type": "application/x-www-form-urlencoded"})
    if resp.status_code != 200:
        return f"Token exchange failed: {resp.text}", 400
    tokens = resp.json()
    session["access_token"] = tokens["access_token"]
    try:
        import jwt
        claims = jwt.decode(tokens["access_token"], options={"verify_signature": False})
        session["username"] = claims.get("username", claims.get("email", "User"))
    except Exception:
        session["username"] = "User"
    return redirect(url_for("index"))


@app.route("/logout")
def logout():
    config = get_config()
    session.clear()
    if config.get("cognito_domain"):
        params = {"client_id": config["web_client_id"], "logout_uri": "http://localhost:8501/login"}
        return redirect(f"{config['cognito_domain']}/logout?{urlencode(params)}")
    return redirect(url_for("login"))


@app.route("/chat", methods=["POST"])
def chat():
    """Proxy chat to AgentCore Runtime."""
    data = request.json
    prompt = data.get("prompt", "")
    session_id = data.get("session_id", str(uuid.uuid4()))
    config = get_config()
    access_token = session.get("access_token", "")

    runtime_arn = config["runtime_arn"]
    if runtime_arn == "NOT_DEPLOYED":
        # Dev mode: 直接呼叫本地 pipeline
        try:
            import sys
            sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
            from trustforge.pipeline import run
            from trustforge.schema import QuestionType
            # 簡單解析 coin
            coin = "BTC"
            for c in ["BTC", "ETH", "SOL", "DOGE", "XRP", "ADA"]:
                if c.lower() in prompt.lower():
                    coin = c
                    break
            report, evidence, _ = run(coin, prompt, QuestionType.MULTI_SOURCE, data_mode="sample", llm_mode="off")
            return jsonify({"response": str(report)})
        except Exception as e:
            return jsonify({"response": f"[Dev mode] Error: {e}"})

    # Production: call AgentCore REST API
    encoded_arn = requests.utils.quote(runtime_arn, safe="")
    invoke_url = f"https://bedrock-agentcore.{REGION}.amazonaws.com/runtimes/{encoded_arn}/invocations"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {access_token}" if access_token != "dev-mode" else "",
        "x-amzn-bedrock-agentcore-session-id": session_id,
    }
    try:
        resp = requests.post(invoke_url, json={"prompt": prompt}, headers=headers, timeout=120)
        if resp.status_code != 200:
            return jsonify({"error": f"Agent error ({resp.status_code}): {resp.text}"}), resp.status_code
        response_text = ""
        for line in resp.text.strip().split("\n"):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
                if isinstance(event, dict) and "data" in event:
                    response_text += event["data"]
            except json.JSONDecodeError:
                response_text += line
        return jsonify({"response": response_text or resp.text})
    except requests.Timeout:
        return jsonify({"error": "Request timed out"}), 504
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    config = get_config()
    print(f"Runtime ARN: {config['runtime_arn']}")
    print(f"Mode: {'Production' if config.get('cognito_domain') else 'Dev (no auth)'}")
    app.run(host="127.0.0.1", port=8501, debug=True)
