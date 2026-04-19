"""Phase 0 healthcheck — KIS 모의 잔고 조회 + 텔레그램 hello 알림.

실주문 API(매수/매도/취소) 는 호출하지 않는다. paper 모드 외에서는
`_build_kis` 가 RuntimeError 로 즉시 실패하며, 추가로 PyKis 인스턴스에
`install_paper_mode_guard` 를 설치해 `request(domain="real")` 호출도 차단한다.
"""

from __future__ import annotations

import asyncio
import sys

from loguru import logger
from pykis import PyKis
from telegram import Bot
from telegram.error import TelegramError

from stock_agent.config import Settings, get_settings
from stock_agent.safety import install_paper_mode_guard


def _build_kis(settings: Settings) -> PyKis:
    """모의투자 전용 PyKis 인스턴스 생성.

    python-kis 2.x 의 PyKis 는 init 단계에서 실전(live) APP_KEY/SECRET 슬롯을
    형식적으로 요구한다 (`KisKey` 가 36/180자 길이 검증). paper-only 환경에서는
    실전 슬롯에도 같은 36/180자 문자열이 필요하므로 모의 키를 동일하게 채운다.

    런타임 라우팅을 결정하는 것은 **`virtual_appkey` 키워드 인자 그 자체** 이다
    (`pykis/kis.py` `virtual` 프로퍼티: `return self.virtual_appkey is not None`).
    `virtual_appkey` 가 세팅되어 있으면 `request()` 가 명시적 `domain` 없이
    호출될 때 자동으로 `domain="virtual"` 로 라우팅된다 (`pykis/kis.py:530-531`).

    단, 라이브러리 내부에는 `request(domain="real")` 을 명시 호출하는 경로가
    9곳 (시세·주문·차트 등) 존재하므로, 본 함수가 반환하는 인스턴스에는
    추가로 `install_paper_mode_guard` 가 설치되어 그런 호출을 즉시 차단한다.
    healthcheck 가 사용하는 `kis.account().balance()` 는 `domain` 미지정 경로라
    이 가드의 영향을 받지 않는다.

    `keep_token=True` 는 토큰을 사용자 홈의 `~/.pykis/` 아래 평문 JSON 으로
    캐시한다 (라이브러리 기본 동작). healthcheck 는 1회성이지만 첫 발급 시
    레이트리밋 회피용으로 유지한다.
    """
    if settings.kis_env != "paper":
        raise RuntimeError(f"healthcheck 는 paper 모드 전용. 현재 KIS_ENV={settings.kis_env}")

    appkey = settings.kis_app_key.get_secret_value()
    secret = settings.kis_app_secret.get_secret_value()
    kis = PyKis(
        id=settings.kis_hts_id,
        account=settings.kis_account_no,
        appkey=appkey,
        secretkey=secret,
        virtual_id=settings.kis_hts_id,
        virtual_appkey=appkey,
        virtual_secretkey=secret,
        keep_token=True,
        use_websocket=False,
    )
    install_paper_mode_guard(kis)
    return kis


def check_kis_balance(settings: Settings) -> str:
    kis = _build_kis(settings)
    try:
        balance = kis.account().balance()
    finally:
        # close 가 던지는 예외가 원본 잔고 조회 예외를 가리지 않도록 별도 처리.
        try:
            kis.close()
        except Exception as close_err:  # noqa: BLE001 — close 실패는 부수 정보로만 기록
            logger.warning(f"PyKis close 중 예외 발생 (원본 예외 보존, close 메시지: {close_err})")

    deposit = int(balance.withdrawable_amount)
    total = int(balance.total)
    holdings = len(list(balance.stocks))
    return f"예수금 {deposit:,}원 / 평가총액 {total:,}원 / 보유종목 {holdings}건"


async def _send_telegram(token: str, chat_id: int, text: str) -> None:
    bot = Bot(token=token)
    async with bot:
        await bot.send_message(chat_id=chat_id, text=text)


def send_telegram_hello(settings: Settings, body: str) -> None:
    text = f"[stock-agent] healthcheck OK\n{body}"
    asyncio.run(
        _send_telegram(
            token=settings.telegram_bot_token.get_secret_value(),
            chat_id=settings.telegram_chat_id,
            text=text,
        )
    )


def main() -> int:
    try:
        settings = get_settings()
    except Exception as e:
        logger.exception(f".env 로드 실패: {e}")
        return 1

    try:
        summary = check_kis_balance(settings)
        logger.info(f"KIS 잔고 조회 OK — {summary}")
    except Exception as e:
        logger.exception(f"KIS 잔고 조회 단계 실패: {e}")
        return 1

    try:
        send_telegram_hello(settings, summary)
        logger.info("Telegram 메시지 전송 OK")
    except TelegramError as e:
        logger.exception(f"Telegram 전송 실패 (Telegram API 오류): {e}")
        return 1
    except Exception as e:
        # 네트워크 단절 / asyncio 타임아웃 / SSL 등 비-TelegramError 도 모두 1로.
        logger.exception(f"Telegram 전송 실패 (네트워크/런타임 오류): {e}")
        return 1

    logger.info("healthcheck 통과")
    return 0


if __name__ == "__main__":
    sys.exit(main())
