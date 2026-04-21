"""storage — SQLite 원장 (주문·체결·일일 PnL).

공개 심볼:
- `TradingRecorder` (Protocol, @runtime_checkable)
- `SqliteTradingRecorder` — 기본 구현
- `NullTradingRecorder` — no-op 폴백
- `StorageError` — 초기화 실패 래퍼

모듈 세부는 [CLAUDE.md](./CLAUDE.md) 참조.
"""

from stock_agent.storage.db import (
    NullTradingRecorder,
    SqliteTradingRecorder,
    StorageError,
    TradingRecorder,
)

__all__ = [
    "NullTradingRecorder",
    "SqliteTradingRecorder",
    "StorageError",
    "TradingRecorder",
]
