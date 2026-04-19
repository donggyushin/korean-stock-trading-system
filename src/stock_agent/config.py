from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, model_validator
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
        description=(
            "실행 도메인. Phase 0~3 은 paper 고정. live 전환은 plan.md Phase 4 체크리스트 통과 후."
        ),
    )
    kis_key_origin: Literal["paper", "live"] = Field(
        default="paper",
        description=(
            "현재 .env 에 채워둔 KIS_APP_KEY/SECRET 의 발급 출처. "
            "kis_env 와 다르면 ValidationError. "
            "모의/실전 키 모두 36자라 길이로 식별 불가하므로 사용자가 명시한다."
        ),
    )

    kis_hts_id: str = Field(min_length=1)
    kis_app_key: SecretStr = Field(min_length=36, max_length=36)
    kis_app_secret: SecretStr = Field(min_length=180, max_length=180)
    kis_account_no: str = Field(
        pattern=r"^\d{8}-\d{2}$",
        description=(
            "모의 계좌번호 'XXXXXXXX-XX' 형식 (현재는 모의 한정, 실전 전환 시 패턴 재확인)."
        ),
    )

    telegram_bot_token: SecretStr = Field(min_length=1)
    telegram_chat_id: int

    @model_validator(mode="after")
    def _check_env_matches_key_origin(self) -> "Settings":
        if self.kis_env != self.kis_key_origin:
            raise ValueError(
                f"KIS_ENV={self.kis_env} 인데 KIS_KEY_ORIGIN={self.kis_key_origin}. "
                "환경 모드와 채워둔 키의 출처가 일치해야 한다 "
                "(실전 도메인을 모의 키로 호출하거나 그 반대를 막는 가드)."
            )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


def reset_settings_cache() -> None:
    """테스트에서 환경변수 변경 후 Settings 를 새로 읽고 싶을 때 호출."""
    get_settings.cache_clear()
