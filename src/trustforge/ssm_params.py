"""SSM Parameter Store 常駐 token 讀取模組。

本模組在 TrustForge 啟動期從 AWS SSM Parameter Store 讀取 SecureString 型態的
常駐 token（admin-token / live-token），取代過去將 token 放入環境變數的作法
（計劃：runtime Parameter Store token 讀取 PR-A）。

opt-in 機制：
    透過環境變數 TRUSTFORGE_TOKEN_SSM_PREFIX 啟用。若該變數未設定（None 或空
    字串），`get_runtime_token` 會立即回傳 None，完全不匯入 boto3、不建立任何
    client、不發出任何 AWS 呼叫——確保零設定的離線 demo 不受影響（web.py/
    admin_config.py 既有的零設定零 AWS 呼叫不變式）。

boto3 慣例（與 admin_config.py / bedrock.py 一致）：
    - 延遲匯入：boto3 / botocore 僅在實際需要存取 SSM 時才於函式內部 import，
      離線模式不需要安裝 AWS SDK。
    - 顯式 timeout：使用 botocore.config.Config 設定 connect_timeout、
      read_timeout 與重試策略，不依賴 boto3 預設的無限期等待（bedrock.py 血淚
      註解：boto3 預設等於無限期等待）。
    - lazy client + threading.Lock 雙重檢查：client 於首次呼叫時建立並快取於
      模組級變數，以 threading.Lock 保護並發初次建立（比照
      admin_config.AdminConfigStore._get_table 慣例）。

失敗語意（呼叫端 web.py 自行處理落回 env 的 fallback，本模組只管「這一層讀不
讀得到」）：
    - 讀到非空字串 → 回傳該字串。
    - 讀到空字串 → 視同「此層未提供」，回傳 None。
    - ParameterNotFound → WARNING（僅記參數名稱，絕不記值）→ 回傳 None。
    - 其他任何例外（網路/憑證/throttle/重試耗盡等）→ ERROR（僅記參數名稱 +
      exc_info 堆疊，絕不記值）→ 回傳 None，不 raise。
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

_CLIENT: Any = None
_CLIENT_LOCK = threading.Lock()


def _get_or_create_client() -> Any:
    """以雙重檢查鎖 lazy 建立 SSM client，建立後快取於模組級 _CLIENT。"""
    global _CLIENT
    if _CLIENT is not None:
        return _CLIENT
    with _CLIENT_LOCK:
        if _CLIENT is not None:  # 拿到鎖後可能已被別的執行緒建好
            return _CLIENT
        import boto3  # 延遲匯入：離線模式不需安裝/設定 AWS
        from botocore.config import Config

        region = os.getenv("AWS_REGION", "us-east-1")
        config = Config(
            connect_timeout=3,
            read_timeout=5,
            retries={"mode": "standard", "max_attempts": 3},
        )
        _CLIENT = boto3.client("ssm", region_name=region, config=config)
        return _CLIENT


def set_client_for_tests(client: Any) -> None:
    """測試輔助：注入 mock client（傳 None 等同重置，下次呼叫重新 lazy 建立）。"""
    global _CLIENT
    with _CLIENT_LOCK:
        _CLIENT = client


def get_runtime_token(
    name: str,
    *,
    max_attempts: int = 5,
    backoff_base: float = 0.2,
    backoff_cap: float = 2.0,
) -> str | None:
    """從 SSM Parameter Store 讀取常駐 token。

    參數：
        name: token 邏輯名稱，例如 "admin-token" 或 "live-token"。
        max_attempts / backoff_base / backoff_cap：#121 follow-up 的 IAM 傳播
            重試——實例角色 / SSM / KMS 權限剛建立後，短時間內 `get_parameter`
            可能拿到暫時性的 `AccessDeniedException` / `ThrottlingException`
            （IAM 最終一致傳播尚未收斂）。這類錯誤指數退避重試
            （`backoff_base * 2**i`，上限 `backoff_cap`，預設最多 5 次、總退避
            約 3.8s），待傳播完成後即可讀到；重試耗盡才視同失敗回 None。非
            暫時性錯誤（ParameterNotFound / 一般例外）不重試，直接回 None。

    回傳：
        token 字串；若此層未提供（未設定 TRUSTFORGE_TOKEN_SSM_PREFIX、參數不
        存在、值為空字串、或任何讀取錯誤）則回傳 None。本函式絕不 raise，讓
        呼叫端自行 fallback（見模組 docstring 失敗語意）。token 值**絕不**出
        現在任何 log / 例外訊息中。
    """
    prefix = os.getenv("TRUSTFORGE_TOKEN_SSM_PREFIX")
    if not prefix:
        return None

    full_name = f"{prefix}/{name}"

    client = _get_or_create_client()

    for attempt in range(max_attempts):
        try:
            resp = client.get_parameter(Name=full_name, WithDecryption=True)
            value = resp.get("Parameter", {}).get("Value")
        except Exception as exc:
            from botocore.exceptions import ClientError

            code = (
                exc.response.get("Error", {}).get("Code")
                if isinstance(exc, ClientError)
                else ""
            )
            # #121：暫時性錯誤（IAM 傳播 / throttle）→ 退避重試。
            if code in (
                "ThrottlingException",
                "Throttling",
                "AccessDeniedException",
                "AccessDenied",
            ):
                if attempt < max_attempts - 1:
                    time.sleep(min(backoff_base * (2 ** attempt), backoff_cap))
                    continue
                logger.warning(
                    "SSM 讀取重試耗盡（IAM 傳播/Throttle）：%s", full_name, exc_info=True
                )
                return None
            if code == "ParameterNotFound":
                logger.warning("SSM parameter not found: %s", full_name)
                return None
            logger.error("Failed to read SSM parameter: %s", full_name, exc_info=True)
            return None
        if not value:
            return None
        return value
    return None


# ---------------------------------------------------------------------------
# #121 follow-up：部署期臨時參數的時間窗 sweep
# ---------------------------------------------------------------------------
def sweep_deploy_parameters(
    prefix: str = "/trustforge/deploy",
    *,
    max_age_seconds: float = 3600.0,
    now_fn: Any = time.time,
) -> list[str]:
    """清理「部署期臨時參數」（`/trustforge/deploy/*`）中超過時間窗、早該被
    trap 清掉卻因異常中斷而殘留的項目（#121 sweep 時間窗優化）。

    常駐參數（`/trustforge/runtime/*`）**不**在此函式清掃範圍（見
    `put_runtime_tokens.sh` 的禁止事項說明）——那類參數若被自動刪除會讓線上
    服務啟動時讀不到 token 而整個掛掉。

    回傳被刪除的參數全名清單（供呼叫端記 log / 斷言）。SSM 讀寫失敗只記
    warning、不 raise（sweep 是維運收尾動作，不該因為 SSM 暫時不可用而炸）。
    """
    try:
        client = _get_or_create_client()
        # Use the path-scoped API instead of DescribeParameters. The latter
        # requires account-wide metadata permission and cannot be restricted
        # to TrustForge's deploy prefix.
        # NextToken 分頁，否則後面幾頁的殘留參數會被漏清（多頁部署期參數
        # 存在時尤其危險——舊 token 殘留到下次部署）。這裡迴圈收斂到沒有
        # NextToken 為止。
        params: list[dict] = []
        next_token: str | None = None
        while True:
            kwargs: dict = {"Path": prefix, "Recursive": True, "WithDecryption": False}
            if next_token:
                kwargs["NextToken"] = next_token
            resp = client.get_parameters_by_path(**kwargs)
            params.extend(resp.get("Parameters", []) or [])
            next_token = resp.get("NextToken")
            if not next_token:
                break
    except Exception as exc:
        logger.warning("SSM sweep 列舉失敗（跳過）：%s", exc, exc_info=True)
        return []
    now = now_fn()
    deleted: list[str] = []
    for p in params:
        name = p.get("Name")
        if not name:
            continue
        lm = p.get("LastModifiedDate")
        if lm is None:
            continue
        ts = lm.timestamp() if isinstance(lm, datetime) else float(lm)
        if now - ts > max_age_seconds:
            try:
                client.delete_parameter(Name=name)
                deleted.append(name)
            except Exception as exc:
                logger.warning("SSM sweep 刪除失敗：%s", name, exc_info=True)
    return deleted
