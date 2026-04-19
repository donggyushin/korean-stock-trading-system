"""런타임 안전 가드 모음.

paper 모드에서 실수로 실전 도메인을 호출하지 않도록 PyKis 인스턴스에
방어 가드를 설치한다. 라이브러리(`python-kis 2.x`) 내부에는
`request(domain="real")` 을 명시적으로 호출하는 경로가 9곳 (시세·주문·차트 등)
존재하므로, 모의 키만 채워둔 환경에서 그 경로를 무심코 타면 모의 키가
실전 도메인(`openapi.koreainvestment.com`) 으로 전송될 위험이 있다.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol


class _RequestableKis(Protocol):
    """가드 설치에 필요한 최소 인터페이스. PyKis 와 테스트용 더블 모두 만족."""

    def request(self, *args: Any, **kwargs: Any) -> Any: ...


def install_paper_mode_guard(kis: _RequestableKis) -> None:
    """`request(domain="real")` 호출을 즉시 차단하도록 인스턴스에 wrapper 를 설치.

    PyKis 인스턴스의 `request` 메서드를 같은 시그니처의 가드로 교체한다.
    가드는 `kwargs.get("domain") == "real"` 인 호출만 거부하고, 그 외
    (`domain` 미지정 또는 `"virtual"`)는 원본 메서드로 그대로 위임한다.

    Args:
        kis: `request` 를 가진 PyKis (또는 테스트 더블) 인스턴스.

    Raises:
        설치 자체는 예외를 던지지 않음. 실제 차단은 추후 가드된 `request`
        호출이 일어났을 때 `RuntimeError` 로 발생.
    """
    original: Callable[..., Any] = kis.request

    def guarded(*args: Any, **kwargs: Any) -> Any:
        if kwargs.get("domain") == "real":
            path = args[0] if args else kwargs.get("path", "<unknown>")
            raise RuntimeError(
                f"paper 모드에서 실전 도메인 호출 차단됨: path={path!r}. "
                "Phase 4 실전 전환 전에는 domain='real' 호출을 허용하지 않는다."
            )
        return original(*args, **kwargs)

    kis.request = guarded  # type: ignore[method-assign]
