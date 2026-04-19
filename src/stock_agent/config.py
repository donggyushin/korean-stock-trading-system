from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """프로젝트 환경설정. `.env` 에서 로드한다.

    민감 필드는 `SecretStr` 로 감싸 로그·repr 노출을 막는다.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    kis_env: Literal["paper", "live"] = Field(
        default="paper",
        description="실행 도메인. Phase 0~3 은 paper 고정.",
    )

    kis_hts_id: str = Field(min_length=1)
    kis_app_key: SecretStr = Field(min_length=36, max_length=36)
    kis_app_secret: SecretStr = Field(min_length=180, max_length=180)
    kis_account_no: str = Field(
        pattern=r"^\d{8}-\d{2}$",
        description="모의 계좌번호 'XXXXXXXX-XX' 형식.",
    )

    telegram_bot_token: SecretStr = Field(min_length=1)
    telegram_chat_id: int


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
