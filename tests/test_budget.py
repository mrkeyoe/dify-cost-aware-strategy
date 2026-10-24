"""预算账本测试。"""

from __future__ import annotations

import unittest

from core.budget import BudgetExhaustedError, BudgetLedger


class TestBudgetLedger(unittest.TestCase):
    def test_initial_state(self):
        ledger = BudgetLedger(total_budget=100)
        self.assertEqual(ledger.remaining, 100)
        self.assertEqual(ledger.usage_ratio, 0)
        self.assertEqual(ledger.consumed, 0)

    def test_can_afford_boundary_is_inclusive(self):
        ledger = BudgetLedger(total_budget=100, consumed=80)
        self.assertTrue(ledger.can_afford(20))  # 刚好花完
        self.assertFalse(ledger.can_afford(20.01))
        self.assertTrue(ledger.can_afford(0))

    def test_charge_and_remaining(self):
        ledger = BudgetLedger(total_budget=100)
        ledger.charge(30)
        ledger.charge(20.5)
        self.assertEqual(ledger.consumed, 50.5)
        self.assertAlmostEqual(ledger.remaining, 49.5)
        self.assertAlmostEqual(ledger.usage_ratio, 0.505)

    def test_remaining_is_floored_at_zero(self):
        ledger = BudgetLedger(total_budget=100)
        ledger.charge(150)  # 允许超记（例如宿主导入的历史消耗）
        self.assertEqual(ledger.remaining, 0)

    def test_refund(self):
        ledger = BudgetLedger(total_budget=100)
        ledger.charge(40)
        ledger.refund(40)
        self.assertEqual(ledger.consumed, 0)

    def test_refund_more_than_consumed_raises(self):
        ledger = BudgetLedger(total_budget=100)
        ledger.charge(10)
        with self.assertRaises(BudgetExhaustedError):
            ledger.refund(20)

    def test_invalid_values(self):
        with self.assertRaises(ValueError):
            BudgetLedger(total_budget=0)
        with self.assertRaises(ValueError):
            BudgetLedger(total_budget=100, consumed=-1)
        ledger = BudgetLedger(total_budget=100)
        with self.assertRaises(ValueError):
            ledger.charge(-5)

    def test_snapshot(self):
        ledger = BudgetLedger(total_budget=100, consumed=25)
        snapshot = ledger.snapshot()
        self.assertEqual(snapshot["consumed"], 25)
        self.assertEqual(snapshot["remaining"], 75)
        self.assertEqual(snapshot["usage_ratio"], 0.25)


if __name__ == "__main__":
    unittest.main()