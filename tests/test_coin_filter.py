"""離線 coin 過濾回歸測試 — 鎖住 codex 復審發現的三個 bug 修正。"""
from trustforge.ingestion.base import _matches_coin, _coins_mentioned
from trustforge.ingestion.base import Document


def _d(id_, text, meta=None):
    return Document(id=id_, kind="news", source="s", text=text, ts=1.0, meta=meta or {})


def test_cross_mention_excluded():
    """High: 跨幣內容（同時提 BTC 與 ETH）過濾單幣時應排除，避免他幣訊號污染。"""
    assert _matches_coin(_d("n1", "BTC 與 ETH 連動上漲"), "ETH") is False
    assert _matches_coin(_d("n2", "ETH 質押率突破 28%"), "ETH") is True


def test_ascii_alias_word_boundary():
    """Med: ASCII 別名須詞界，'SOL' 不可誤命中 'solana'/'solution'/'console'。"""
    # solana 是 SOL 的合法別名 → 命中
    assert "SOL" in _coins_mentioned("solana 鏈上活躍")
    # solution / console 不含獨立 sol → 不命中任何幣
    assert _coins_mentioned("a solution on the console") == set()


def test_multi_coin_filter():
    """Med: comparison 的多幣 'BTC,ETH' 須正確拆解，非當單一 token。"""
    assert _matches_coin(_d("n4", "BTC 大額流入交易所"), "BTC,ETH") is True
    assert _matches_coin(_d("n5", "ETH 質押需求上升"), "BTC,ETH") is True
    assert _matches_coin(_d("n6", "SOL 生態 TVL 成長"), "BTC,ETH") is False


def test_market_wide_included():
    """無任何幣別提及的全市場資料（如恐懼貪婪指數）對所有幣都納入。"""
    assert _matches_coin(_d("n7", "市場 恐懼 貪婪 指數 中性"), "BTC") is True
    assert _matches_coin(_d("n8", "市場 恐懼 貪婪 指數 中性"), "ETH") is True


def test_explicit_meta_coin_priority():
    """meta['coin'] 顯式標記優先，且須屬目標集合。"""
    assert _matches_coin(_d("n9", "任意內容", meta={"coin": "BTC"}), "BTC") is True
    assert _matches_coin(_d("n10", "任意內容", meta={"coin": "BTC"}), "ETH") is False
