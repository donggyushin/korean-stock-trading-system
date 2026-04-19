"""Settings 모델 검증 테스트 (kis_env ↔ kis_key_origin 정합성)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from pydantic import ValidationError

from stock_agent.config import Settings, reset_settings_cache

_VALID_BASE_ENV: dict[str, str] = {
    "KIS_HTS_ID": "tester",
    "KIS_APP_KEY": "A" * 36,
    "KIS_APP_SECRET": "B" * 180,
    "KIS_ACCOUNT_NO": "12345678-01",
    "TELEGRAM_BOT_TOKEN": "tg-token",
    "TELEGRAM_CHAT_ID": "1",
}


@pytest.fixture(autouse=True)
def _clear_settings_cache_and_env(  # pyright: ignore[reportUnusedFunction]
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    """매 테스트 시작·종료 시 lru_cache 와 .env 영향 제거."""
    # .env 파일이 자동 로드되지 않도록 model_config 의 env_file 을 무력화
    monkeypatch.setenv("PYDANTIC_SETTINGS_DOTENV_DISABLED", "1")
    for k in (
        "KIS_ENV",
        "KIS_KEY_ORIGIN",
        *_VALID_BASE_ENV.keys(),
    ):
        monkeypatch.delenv(k, raising=False)
    reset_settings_cache()
    yield
    reset_settings_cache()


def _set_env(monkeypatch: pytest.MonkeyPatch, **overrides: str) -> None:
    for k, v in {**_VALID_BASE_ENV, **overrides}.items():
        monkeypatch.setenv(k, v)


def test_kis_env_paper_와_key_origin_paper는_통과한다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_env(monkeypatch, KIS_ENV="paper", KIS_KEY_ORIGIN="paper")
    settings = Settings()  # type: ignore[call-arg]
    assert settings.kis_env == "paper"
    assert settings.kis_key_origin == "paper"


def test_kis_env_live_와_key_origin_paper는_거부된다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_env(monkeypatch, KIS_ENV="live", KIS_KEY_ORIGIN="paper")
    with pytest.raises(ValidationError, match="KIS_ENV=live"):
        Settings()  # type: ignore[call-arg]


def test_kis_env_paper_와_key_origin_live는_거부된다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_env(monkeypatch, KIS_ENV="paper", KIS_KEY_ORIGIN="live")
    with pytest.raises(ValidationError, match="KIS_KEY_ORIGIN=live"):
        Settings()  # type: ignore[call-arg]


def test_app_key_길이가_36자가_아니면_거부된다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_env(monkeypatch, KIS_APP_KEY="short")
    with pytest.raises(ValidationError):
        Settings()  # type: ignore[call-arg]


def test_account_no_형식이_틀리면_거부된다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_env(monkeypatch, KIS_ACCOUNT_NO="00000000")  # -01 누락
    with pytest.raises(ValidationError):
        Settings()  # type: ignore[call-arg]
