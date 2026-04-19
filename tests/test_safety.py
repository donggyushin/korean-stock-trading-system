"""install_paper_mode_guard 단위 테스트."""

from __future__ import annotations

from typing import Any

import pytest

from stock_agent.safety import install_paper_mode_guard


class _FakeKis:
    """PyKis 의 request 메서드만 흉내내는 최소 더블."""

    def __init__(self) -> None:
        self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def request(self, *args: Any, **kwargs: Any) -> str:
        self.calls.append((args, kwargs))
        return "ok"


def test_real_도메인_호출은_RuntimeError로_차단된다() -> None:
    kis = _FakeKis()
    install_paper_mode_guard(kis)

    with pytest.raises(RuntimeError, match="paper 모드에서 실전 도메인"):
        kis.request("/uapi/foo", method="GET", domain="real")

    assert kis.calls == [], "차단된 호출은 원본 request 에 도달하지 않아야 한다"


def test_virtual_도메인_호출은_원본에_그대로_위임된다() -> None:
    kis = _FakeKis()
    install_paper_mode_guard(kis)

    result = kis.request("/uapi/foo", method="GET", domain="virtual")

    assert result == "ok"
    assert kis.calls == [(("/uapi/foo",), {"method": "GET", "domain": "virtual"})]


def test_domain_미지정_호출은_원본에_그대로_위임된다() -> None:
    """PyKis 의 기본 라우팅(`domain=None` -> virtual) 경로는 가드가 건드리지 않는다."""
    kis = _FakeKis()
    install_paper_mode_guard(kis)

    result = kis.request("/uapi/bar", method="GET")

    assert result == "ok"
    assert kis.calls == [(("/uapi/bar",), {"method": "GET"})]


def test_가드는_path_정보를_에러메시지에_포함한다() -> None:
    kis = _FakeKis()
    install_paper_mode_guard(kis)

    with pytest.raises(RuntimeError, match="/uapi/forbidden"):
        kis.request("/uapi/forbidden", domain="real")


def test_path를_kwarg로_받는_경우에도_에러메시지에_포함한다() -> None:
    kis = _FakeKis()
    install_paper_mode_guard(kis)

    with pytest.raises(RuntimeError, match="/uapi/kw-path"):
        kis.request(path="/uapi/kw-path", domain="real")
