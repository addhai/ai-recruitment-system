# -*- coding: utf-8 -*-
"""LLM 预算计账口径的测试。

**这里守的是什么**：预算墙读到的数字必须等于"窗口内真实花掉的钱"，
既不能多算（提前触顶，用户以为有 ¥5 上限、实际只花了 ¥2.5 就被拦），
也不能少算（漏掉历史与其它进程，保护形同虚设）。

**为什么要有这个测试**：原实现的 `spent_in_window()` 在回读分支返回
`库内总额 + 进程内累计`——而每次调用都会立刻落库，库里的数字已经包含
本进程那部分，于是同一笔钱被算两次。实测跑全量评测时，预算 ¥5 在
真实花费 ¥2.84 处就触顶了（读出来的是 ¥5.67）。
更糟的是非回读分支直接返回进程内累计，把历史与其它进程全漏了。

这些都不是"看起来像"的问题，是能拿数字对上的问题，所以要有测试钉住。
"""
import asyncio

import pytest

from src.models.database import SessionLocal, LLMCallLog
from src.services import budget


def _write_log(cost: float, currency: str = "CNY") -> None:
    """真写一行 LLM 调用日志（模拟一次已落库的调用）。"""
    with SessionLocal() as db:
        db.add(LLMCallLog(
            call_site="budget_accounting_test",
            model="test-model",
            input_tokens=100, output_tokens=200,
            cost=cost, currency=currency,
            latency_ms=10, status="ok", degraded=False,
        ))
        db.commit()


def _clear_window() -> None:
    """清掉窗口内的历史行，保证测试只看到自己造的数据。"""
    with SessionLocal() as db:
        db.query(LLMCallLog).filter(
            LLMCallLog.call_site == "budget_accounting_test").delete()
        db.commit()


@pytest.fixture(autouse=True)
def _reset_budget():
    asyncio.run(budget.reset_cache_for_test())
    _clear_window()
    yield
    asyncio.run(budget.reset_cache_for_test())
    _clear_window()


def _spent_in_window() -> float:
    return asyncio.run(budget.spent_in_window())


class TestBudgetAccounting:
    def test_same_call_not_counted_twice(self):
        """同一笔成本既在库里、也在进程内累计里 → 只能算一次。

        这是本轮修掉的真 bug：原来读出来的是两倍，
        于是 ¥5 的上限在真实花掉 ¥2.5 时就触顶。
        """
        cost = 2.5
        _write_log(cost)
        # 模拟"这次调用已经 add_spend 过、也已经落库"
        asyncio.run(budget.add_spend(cost))

        spent = _spent_in_window()
        assert spent == pytest.approx(cost), (
            f"同一笔钱被算了两遍：真实花费 {cost}，预算读到 {spent}")

    def test_cold_start_reads_history(self):
        """冷启动（进程内计数为 0）必须回读库里的历史花费。

        不回读就等于"今天已经花超上限也照样放行"，保护直接失效。
        """
        _write_log(3.0)
        spent = _spent_in_window()
        assert spent == pytest.approx(3.0), (
            f"冷启动没回读到历史花费：读到 {spent}")

    def test_non_refresh_path_keeps_history_baseline(self):
        """不回读的那几次检查也必须带上历史底数。

        默认每 20 次调用才回读一次，也就是说 20 次里有 19 次走这条路径。
        如果这里只返回进程内累计，历史与其它进程的花费就全漏了。
        """
        _write_log(1.0)
        first = _spent_in_window()          # 触发回读
        assert first == pytest.approx(1.0)

        # 再花一点，但还没到回读阈值
        asyncio.run(budget.add_spend(0.5))
        second = _spent_in_window()
        assert second == pytest.approx(1.5), (
            f"不回读时漏算了历史：第一次读到 {first}，第二次应为 1.5，实际 {second}")

    def test_refresh_does_not_accumulate_forever(self):
        """进程跑得越久，重复计账不能越滚越大。

        原实现从不在回读时把进程内累计归零，于是重复的部分只增不减。
        """
        _write_log(1.0)
        for _ in range(3):
            asyncio.run(budget.add_spend(1.0))
            _write_log(1.0)
            # 强制走回读分支
            asyncio.run(budget.add_spend(0.0))
            budget._calls_since_refresh = budget.settings.LLM_BUDGET_REFRESH_EVERY
            spent = _spent_in_window()
        assert spent == pytest.approx(4.0), (
            "回读没有把重叠部分扣掉，窗口总额被重复累加：读到 %s" % spent)

    def test_foreign_currency_excluded(self):
        """币种不一致的行不能计入当前币种的上限（¥ 与 $ 不能直接相加）。"""
        _write_log(2.0, currency="CNY")
        _write_log(7.0, currency="USD")
        spent = _spent_in_window()
        assert spent == pytest.approx(2.0), (
            f"其它币种的花费被算进来了：读到 {spent}")
