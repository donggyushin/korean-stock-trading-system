"""Phase 0 healthcheck — KIS 모의 잔고 조회 + 텔레그램 hello 알림.

실주문은 절대 수행하지 않는다. 조회 API와 메시지 전송만 호출한다.
"""

from __future__ import annotations

import asyncio
import sys

from loguru import logger
from pykis import PyKis
from telegram import Bot
from telegram.error import TelegramError

from stock_agent.config import Settings, get_settings


def _build_kis(settings: Settings) -> PyKis:
    """모의투자 전용 PyKis 인스턴스 생성.

    python-kis 2.x 는 init 단계에서 실전(live) APP_KEY/SECRET 슬롯을 형식적으로
    요구한다. paper-only 환경에서는 모의 키를 양쪽 슬롯에 동일 입력해 길이 검증을
    통과시킨 뒤, `virtual_appkey` 가 설정되어 자동으로 모의 도메인으로만
    라우팅되도록 한다 (`PyKis.virtual` 프로퍼티가 True 가 되어
    `request()` 가 VIRTUAL_DOMAIN 만 호출). 실전 도메인은 한 번도 호출되지 않는다.
    """
    if settings.kis_env != "paper":
        raise RuntimeError(f"healthcheck 는 paper 모드 전용. 현재 KIS_ENV={settings.kis_env}")

    appkey = settings.kis_app_key.get_secret_value()
    secret = settings.kis_app_secret.get_secret_value()
    return PyKis(
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


def check_kis_balance(settings: Settings) -> str:
    kis = _build_kis(settings)
    try:
        balance = kis.account().balance()
    finally:
        kis.close()

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
        logger.error(f".env 로드 실패: {e}")
        return 1

    try:
        summary = check_kis_balance(settings)
        logger.info(f"KIS 잔고 조회 OK — {summary}")
    except Exception as e:
        logger.exception(f"KIS 잔고 조회 실패: {e}")
        return 1

    try:
        send_telegram_hello(settings, summary)
        logger.info("Telegram 메시지 전송 OK")
    except TelegramError as e:
        logger.exception(f"Telegram 전송 실패: {e}")
        return 1

    logger.info("healthcheck 통과")
    return 0


if __name__ == "__main__":
    sys.exit(main())
