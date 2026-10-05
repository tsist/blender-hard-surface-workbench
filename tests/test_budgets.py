import math
import unittest
from unittest import mock
from hardsurface.budgets import BudgetExceeded, BudgetLimits, JobBudget, checked_product, estimate_arc_segments


class BudgetTests(unittest.TestCase):
    def test_wall_includes_caller_preflight_and_queue(self):
        clock = lambda: 12.0
        budget = JobBudget(BudgetLimits(wall_seconds=3), started_at=8.0, clock=clock)
        with self.assertRaises(BudgetExceeded) as caught:
            budget.check()
        self.assertEqual(caught.exception.resource, "wall_seconds")

    def test_budget_not_reset_between_stages(self):
        now = [10.0]
        budget = JobBudget(BudgetLimits(wall_seconds=10), started_at=10, clock=lambda: now[0])
        now[0] = 17
        budget.check()
        self.assertEqual(budget.remaining_seconds, 3)
        now[0] = 20
        with self.assertRaises(BudgetExceeded):
            budget.check()

    def test_future_start_and_nonfinite_rejected(self):
        for value in (11, float("inf"), float("nan"), True):
            with self.assertRaises(ValueError):
                JobBudget(BudgetLimits(), started_at=value, clock=lambda: 10)
        for value in (float("inf"), -1, True):
            with self.assertRaises(ValueError):
                BudgetLimits(wall_seconds=value)

    def test_expand_before_allocation(self):
        self.assertEqual(checked_product([2, 3, 4], 24), 24)
        with self.assertRaises(BudgetExceeded):
            checked_product([10**30, 10**30], 1024)
        with self.assertRaises(ValueError):
            checked_product([True], 12)

    def test_arc_error_and_tiny_tolerance(self):
        count = estimate_arc_segments(10, math.pi, 0.01)
        self.assertLessEqual(10 * (1 - math.cos(math.pi / count / 2)), 0.01)
        with self.assertRaises(BudgetExceeded):
            estimate_arc_segments(1e100, math.tau, 1e-250)
        with self.assertRaises(BudgetExceeded):
            estimate_arc_segments(1, math.tau, 1e-25, 1000)
        with self.assertRaises(ValueError):
            estimate_arc_segments(1, 7, .1)

    def test_plan_counts_all_cases_attempts_diagnosis(self):
        budget = JobBudget(BudgetLimits(max_steps=12), started_at=0, clock=lambda: 0)
        with self.assertRaises(BudgetExceeded):
            budget.preflight({"steps": checked_product([4, 3, 2], 100)})
        with self.assertRaises(BudgetExceeded):
            budget.preflight({"steps_per_unit": [65]})
        with self.assertRaises(ValueError):
            budget.preflight({"unknown": 1})

    def test_disk_space_reserve_before_work(self):
        budget = JobBudget(BudgetLimits(max_disk_bytes=100, disk_reserve_bytes=10), started_at=0, clock=lambda: 0)
        with mock.patch("hardsurface.budgets.shutil.disk_usage", return_value=type("D", (), {"free": 50})()):
            with self.assertRaises(BudgetExceeded):
                budget.preflight({"input_bytes": 20, "output_bytes": 20, "checkpoint_bytes": 20}, disk_root="/tmp")
        with self.assertRaises(BudgetExceeded):
            budget.preflight({"input_bytes": 101})

    def test_memory_threshold_is_observation_not_hard_limit(self):
        budget = JobBudget(BudgetLimits(max_tree_rss_bytes=100), started_at=0, clock=lambda: 0)
        budget.check(tree_rss_bytes=80, disk_bytes=30)
        with self.assertRaises(BudgetExceeded):
            budget.check(tree_rss_bytes=101)
        report = budget.report()
        self.assertFalse(report["hard_memory_enforcement"])
        self.assertTrue(report["rss_shared_pages_may_be_counted_more_than_once"])
        self.assertTrue(report["sampling_may_miss_transient_peaks"])
        self.assertEqual(report["peak_tree_rss_bytes_observed"], 101)

    def test_available_ram_floor(self):
        budget = JobBudget(BudgetLimits(minimum_available_ram_bytes=100), started_at=0, clock=lambda: 0)
        with self.assertRaises(BudgetExceeded):
            budget.check(available_ram=90)


if __name__ == "__main__":
    unittest.main()
