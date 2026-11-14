import importlib.util
from pathlib import Path
import unittest

script = Path(__file__).resolve().parents[1] / "check-memory-limits.py"
spec = importlib.util.spec_from_file_location("memory_limits", script)
memory_limits = importlib.util.module_from_spec(spec)
spec.loader.exec_module(memory_limits)


class MemoryLimitsTest(unittest.TestCase):
    def test_accepts_explicit_no_swap_and_bounded_swap(self):
        self.assertEqual(memory_limits.validate({"services": {
            "site": {"mem_limit": 67108864, "memswap_limit": 67108864},
            "api": {"mem_limit": "134217728", "memswap_limit": "167772160"},
        }}), [])

    def test_rejects_missing_and_unlimited_budgets(self):
        for value in (None, 0, -1, True, 1.5, "1g", "-1", "Infinity"):
            with self.subTest(value=value):
                self.assertTrue(memory_limits.validate({"services": {
                    "api": {"mem_limit": value, "memswap_limit": value},
                }}))

    def test_total_budget_cannot_be_less_than_ram(self):
        self.assertTrue(memory_limits.validate({"services": {
            "api": {"mem_limit": 1024, "memswap_limit": 512},
        }}))

    def test_checks_every_service_and_rejects_empty_input(self):
        self.assertTrue(memory_limits.validate({"services": {
            "bounded": {"mem_limit": 1024, "memswap_limit": 1024},
            "unbounded": {},
        }}))
        for value in ({}, {"services": {}}, [], None):
            with self.subTest(value=value):
                self.assertTrue(memory_limits.validate(value))
