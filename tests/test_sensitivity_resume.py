"""sensitivity resume 기능 단위 테스트 (RED — 미구현 API).

검증 대상:
- load_completed_combos: 기존 sensitivity CSV → 완료된 params key set 반환
- filter_remaining_combos: 완료 조합 제외 후 미완료 list 반환
- merge_sensitivity_rows: existing·new rows 를 grid 순서로 병합

외부 I/O: tmp_path 파일 쓰기만. 실 네트워크·KIS 접촉 없음.
"""

from __future__ import annotations

import csv as csv_mod
from datetime import time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from stock_agent.backtest import (
    BacktestMetrics,
    ParameterAxis,
    SensitivityGrid,
    SensitivityRow,
    default_grid,
    filter_remaining_combos,
    load_completed_combos,
    merge_sensitivity_rows,
    write_csv,
)

# ---------------------------------------------------------------------------
# 상수 / 헬퍼 (기존 test_sensitivity.py 컨벤션 따름)
# ---------------------------------------------------------------------------

KST = timezone(timedelta(hours=9))


def _make_metrics(net_pnl_krw: int = 0) -> BacktestMetrics:
    total_return = Decimal(net_pnl_krw) / Decimal(1_000_000)
    return BacktestMetrics(
        total_return_pct=total_return,
        max_drawdown_pct=Decimal("0"),
        sharpe_ratio=Decimal("0"),
        win_rate=Decimal("1") if net_pnl_krw > 0 else Decimal("0"),
        avg_pnl_ratio=Decimal("0"),
        trades_per_day=Decimal("0"),
        net_pnl_krw=net_pnl_krw,
    )


def _make_row(
    params: dict[str, Any],
    net_pnl_krw: int = 0,
    trade_count: int = 0,
) -> SensitivityRow:
    return SensitivityRow(
        params=tuple(params.items()),
        metrics=_make_metrics(net_pnl_krw),
        trade_count=trade_count,
        rejected_total=0,
        post_slippage_rejections=0,
    )


def _small_grid() -> SensitivityGrid:
    """strategy.stop_loss_pct × strategy.take_profit_pct — 2×2 = 4 조합."""
    return SensitivityGrid(
        axes=(
            ParameterAxis(
                name="strategy.stop_loss_pct",
                values=(Decimal("0.010"), Decimal("0.015")),
            ),
            ParameterAxis(
                name="strategy.take_profit_pct",
                values=(Decimal("0.020"), Decimal("0.030")),
            ),
        )
    )


def _all_rows_for_grid(grid: SensitivityGrid) -> tuple[SensitivityRow, ...]:
    """grid 의 모든 조합에 대한 SensitivityRow 튜플 (순서 동일)."""
    rows = []
    for combo in grid.iter_combinations():
        rows.append(_make_row(combo, net_pnl_krw=1000, trade_count=1))
    return tuple(rows)


def _write_csv_for_grid(path: Path, grid: SensitivityGrid) -> None:
    """grid 의 모든 조합을 write_csv 로 파일에 저장."""
    rows = _all_rows_for_grid(grid)
    write_csv(rows, path)


def _combo_key(
    combo: dict[str, Any], axes: tuple[ParameterAxis, ...]
) -> tuple[tuple[str, Any], ...]:
    """grid 축 순서 기준 params key 생성 헬퍼."""
    return tuple((ax.name, combo[ax.name]) for ax in axes)


# ---------------------------------------------------------------------------
# A. TestLoadCompletedCombos
# ---------------------------------------------------------------------------


class TestLoadCompletedCombos:
    """load_completed_combos(path, grid) → set[tuple[tuple[str, Any], ...]]"""

    def test_파일_없음_FileNotFoundError(self, tmp_path: Path):
        """존재하지 않는 파일 → FileNotFoundError."""
        path = tmp_path / "nonexistent.csv"
        grid = _small_grid()
        with pytest.raises(FileNotFoundError):
            load_completed_combos(path, grid)

    def test_빈_CSV_헤더만_빈_set(self, tmp_path: Path):
        """헤더만 있는 CSV (데이터 행 0) → 빈 set 반환."""
        path = tmp_path / "empty.csv"
        grid = _small_grid()
        # write_csv with empty rows → 헤더만 쓴다
        write_csv((), path)
        result = load_completed_combos(path, grid)
        assert isinstance(result, set)
        assert len(result) == 0

    def test_기본_grid_모든_조합_읽기(self, tmp_path: Path):
        """write_csv 로 생성한 4조합 CSV → set 크기 4 반환."""
        path = tmp_path / "full.csv"
        grid = _small_grid()
        _write_csv_for_grid(path, grid)
        result = load_completed_combos(path, grid)
        assert len(result) == 4

    def test_반환값_각_원소가_tuple_of_tuples(self, tmp_path: Path):
        """반환 set 의 각 원소는 tuple[tuple[str, Any], ...] 타입이어야 한다."""
        path = tmp_path / "full.csv"
        grid = _small_grid()
        _write_csv_for_grid(path, grid)
        result = load_completed_combos(path, grid)
        for key in result:
            assert isinstance(key, tuple), f"원소가 tuple 이 아님: {type(key)}"
            for item in key:
                assert isinstance(item, tuple) and len(item) == 2, f"내부 원소 불일치: {item}"

    def test_Decimal_파싱_정확도(self, tmp_path: Path):
        """CSV 의 "0.01500" 같은 표기도 Decimal("0.015") 와 동일 조합으로 인식."""
        path = tmp_path / "decimal.csv"
        grid = _small_grid()
        _write_csv_for_grid(path, grid)
        result = load_completed_combos(path, grid)
        # Decimal("0.015") 와 Decimal("0.01500") 는 == 비교 상 같아야 함
        # grid 에 있는 Decimal("0.015") 조합이 set 에 포함되는지 확인
        target_key = (
            ("strategy.stop_loss_pct", Decimal("0.015")),
            ("strategy.take_profit_pct", Decimal("0.020")),
        )
        assert target_key in result, f"Decimal 파싱 불일치. result={result}"

    def test_time_파싱_정확도(self, tmp_path: Path):
        """CSV 의 "09:15:00" 문자열 → time(9, 15) 복원 후 조합 set 에 포함."""
        path = tmp_path / "time_grid.csv"
        # strategy.or_end 축을 포함하는 그리드
        grid = SensitivityGrid(
            axes=(
                ParameterAxis(
                    name="strategy.or_end",
                    values=(time(9, 15), time(9, 30)),
                ),
                ParameterAxis(
                    name="strategy.stop_loss_pct",
                    values=(Decimal("0.015"),),
                ),
            )
        )
        _write_csv_for_grid(path, grid)
        result = load_completed_combos(path, grid)
        target_key = (
            ("strategy.or_end", time(9, 15)),
            ("strategy.stop_loss_pct", Decimal("0.015")),
        )
        assert target_key in result, f"time 파싱 불일치. result={result}"

    def test_헤더에_축_이름_누락_RuntimeError(self, tmp_path: Path):
        """CSV 헤더에 grid 의 축 이름이 없으면 RuntimeError."""
        path = tmp_path / "bad_header.csv"
        # 축 이름이 없는 헤더로 CSV 작성
        with path.open("w", newline="", encoding="utf-8") as fp:
            writer = csv_mod.writer(fp)
            writer.writerow(["wrong_col", "net_pnl_krw"])
            writer.writerow(["val1", "0"])
        grid = _small_grid()
        with pytest.raises(RuntimeError, match="헤더"):
            load_completed_combos(path, grid)

    def test_미지원_축_타입_RuntimeError(self, tmp_path: Path):
        """파싱 규칙이 없는 축 이름이면 RuntimeError ('파싱 규칙 없는 축')."""
        path = tmp_path / "unknown_axis.csv"
        # 헤더에 파싱 규칙이 없는 축 이름 포함
        with path.open("w", newline="", encoding="utf-8") as fp:
            writer = csv_mod.writer(fp)
            writer.writerow(["strategy.unknown_field", "net_pnl_krw"])
            writer.writerow(["42", "0"])
        grid = SensitivityGrid(
            axes=(
                ParameterAxis(
                    name="strategy.unknown_field",
                    values=(1,),
                ),
            )
        )
        with pytest.raises(RuntimeError, match="파싱 규칙"):
            load_completed_combos(path, grid)

    def test_default_grid_32조합_전체_읽기(self, tmp_path: Path):
        """default_grid() 32조합 write_csv → load 시 set 크기 32."""
        path = tmp_path / "default_grid.csv"
        grid = default_grid()
        _write_csv_for_grid(path, grid)
        result = load_completed_combos(path, grid)
        assert len(result) == 32


# ---------------------------------------------------------------------------
# B. TestFilterRemainingCombos
# ---------------------------------------------------------------------------


class TestFilterRemainingCombos:
    """filter_remaining_combos(grid, completed) → list[dict[str, Any]]"""

    def test_완료_0건이면_grid_전체_반환(self):
        """completed 가 빈 set → grid 전체 조합 반환."""
        grid = _small_grid()
        result = filter_remaining_combos(grid, set())
        assert len(result) == grid.size

    def test_완료_일부면_나머지_반환(self):
        """completed 에 첫 조합이 있으면 나머지 3개만 반환."""
        grid = _small_grid()
        combos = list(grid.iter_combinations())
        # 첫 조합만 완료된 것으로 표시
        first_key = tuple(combos[0].items())
        result = filter_remaining_combos(grid, {first_key})
        assert len(result) == grid.size - 1

    def test_완료_전체면_빈_리스트(self):
        """completed 에 모든 조합이 있으면 빈 list 반환."""
        grid = _small_grid()
        completed = {tuple(combo.items()) for combo in grid.iter_combinations()}
        result = filter_remaining_combos(grid, completed)
        assert result == []

    def test_반환_순서는_grid_순서_유지(self):
        """반환된 list 의 순서는 grid.iter_combinations() 순서와 동일."""
        grid = _small_grid()
        combos = list(grid.iter_combinations())
        # 두 번째 조합만 완료로 표시 → [0번, 2번, 3번] 순서여야 함
        completed = {tuple(combos[1].items())}
        result = filter_remaining_combos(grid, completed)
        expected = [combos[0], combos[2], combos[3]]
        assert result == expected

    def test_completed_에_grid_없는_조합_섞여도_무시(self):
        """completed 에 grid 에 없는 조합이 있어도 오류 없이 무시."""
        grid = _small_grid()
        # grid 에 없는 임의 조합 추가
        orphan = (("strategy.stop_loss_pct", Decimal("0.999")),)
        result = filter_remaining_combos(grid, {orphan})
        # orphan 은 무시되고 grid 전체가 반환돼야 함
        assert len(result) == grid.size

    def test_반환값이_dict_리스트(self):
        """반환 타입은 list[dict[str, Any]]."""
        grid = _small_grid()
        result = filter_remaining_combos(grid, set())
        assert isinstance(result, list)
        for item in result:
            assert isinstance(item, dict)


# ---------------------------------------------------------------------------
# C. TestMergeSensitivityRows
# ---------------------------------------------------------------------------


class TestMergeSensitivityRows:
    """merge_sensitivity_rows(existing, new, grid) → tuple[SensitivityRow, ...]"""

    def test_existing_만_있으면_grid_순서로_반환(self):
        """new=() → existing 만으로 grid 순서 정렬 후 반환."""
        grid = _small_grid()
        existing = _all_rows_for_grid(grid)
        result = merge_sensitivity_rows(existing, (), grid)
        assert len(result) == grid.size
        # 순서가 grid 순서와 일치
        for result_row, combo in zip(result, grid.iter_combinations(), strict=True):
            assert result_row.params_dict() == combo

    def test_new_만_있으면_grid_순서로_반환(self):
        """existing=() → new 만으로 grid 순서 정렬 후 반환."""
        grid = _small_grid()
        new = _all_rows_for_grid(grid)
        result = merge_sensitivity_rows((), new, grid)
        assert len(result) == grid.size
        for result_row, combo in zip(result, grid.iter_combinations(), strict=True):
            assert result_row.params_dict() == combo

    def test_병합_grid_순서로_정렬(self):
        """existing·new 를 섞어 넣어도 결과는 grid 순서와 일치."""
        grid = _small_grid()
        combos = list(grid.iter_combinations())
        # existing: 0번, 2번 / new: 1번, 3번
        existing = tuple(_make_row(combos[i], net_pnl_krw=100) for i in [0, 2])
        new = tuple(_make_row(combos[i], net_pnl_krw=200) for i in [1, 3])
        result = merge_sensitivity_rows(existing, new, grid)
        assert len(result) == 4
        for result_row, combo in zip(result, combos, strict=True):
            assert result_row.params_dict() == combo

    def test_existing_new_중복_조합은_new_우선(self):
        """동일 params 가 existing · new 양쪽에 있으면 new 가 채택된다."""
        grid = _small_grid()
        combos = list(grid.iter_combinations())
        # 0번 조합이 중복 — existing: net_pnl=100, new: net_pnl=999
        existing = tuple(_make_row(c, net_pnl_krw=100) for c in combos)
        new = (_make_row(combos[0], net_pnl_krw=999),)
        result = merge_sensitivity_rows(existing, new, grid)
        # 결과 순서 0번 = new 값 (999)
        assert result[0].metrics.net_pnl_krw == 999

    def test_grid_누락_조합_있으면_RuntimeError(self):
        """existing + new 를 합쳐도 grid 의 일부 조합이 없으면 RuntimeError."""
        grid = _small_grid()
        combos = list(grid.iter_combinations())
        # 첫 조합만 있고 나머지는 없음
        only_one = (_make_row(combos[0], net_pnl_krw=100),)
        with pytest.raises(RuntimeError, match="누락"):
            merge_sensitivity_rows(only_one, (), grid)

    def test_orphan_조합_무시(self):
        """existing/new 에 grid 에 없는 조합이 있어도 무시하고 grid 조합만 포함."""
        grid = _small_grid()
        all_rows = _all_rows_for_grid(grid)
        # grid 에 없는 orphan row 추가
        orphan = _make_row(
            {
                "strategy.stop_loss_pct": Decimal("0.999"),
                "strategy.take_profit_pct": Decimal("0.999"),
            },
            net_pnl_krw=0,
        )
        existing_with_orphan = all_rows + (orphan,)
        result = merge_sensitivity_rows(existing_with_orphan, (), grid)
        # orphan 은 포함되지 않아야 함 → 정확히 grid.size 개
        assert len(result) == grid.size

    def test_반환값_tuple_타입(self):
        """반환 타입은 tuple[SensitivityRow, ...]."""
        grid = _small_grid()
        existing = _all_rows_for_grid(grid)
        result = merge_sensitivity_rows(existing, (), grid)
        assert isinstance(result, tuple)
        for row in result:
            assert isinstance(row, SensitivityRow)

    def test_빈_existing_빈_new_grid_전체_없으면_RuntimeError(self):
        """existing=(), new=() → grid 의 모든 조합이 누락 → RuntimeError."""
        grid = _small_grid()
        with pytest.raises(RuntimeError, match="누락"):
            merge_sensitivity_rows((), (), grid)
