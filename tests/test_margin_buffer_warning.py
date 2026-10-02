"""MARGIN_BUFFER_ERODED fires only when DNBP strictly exceeds Peter's cost.
Run: `.venv/bin/python -m unittest discover -s tests -t .`"""

import unittest
from decimal import Decimal

from domain.engine import issues as codes
from domain.engine.config import EverhealthConfig
from domain.engine.workings import OrderLineInput, compute_order_workings

# buffer 0 and factor 1 make DNBP equal the sell price, so cost is the only variable.
CONFIG = EverhealthConfig.from_values(cif_buffer_per_kg="0", dnbp_factor_by_species={"VEAL": "1"})


def issue_codes(cost: str | None) -> list[str]:
    line = OrderLineInput(
        species="VEAL",
        avg_price_aud=Decimal("3.25"),
        expected_livestock_cost_per_kg=Decimal(cost) if cost is not None else None,
    )
    _, found = compute_order_workings(line, CONFIG)
    return [i.code for i in found]


class MarginBufferErodedTests(unittest.TestCase):
    def test_dnbp_below_cost_does_not_warn(self):
        self.assertNotIn(codes.MARGIN_BUFFER_ERODED, issue_codes("3.50"))

    def test_dnbp_equal_to_cost_does_not_warn(self):
        self.assertNotIn(codes.MARGIN_BUFFER_ERODED, issue_codes("3.25"))

    def test_dnbp_above_cost_warns(self):
        self.assertIn(codes.MARGIN_BUFFER_ERODED, issue_codes("3.00"))

    def test_tiny_excess_still_warns(self):
        self.assertIn(codes.MARGIN_BUFFER_ERODED, issue_codes("3.2499999999"))

    def test_missing_cost_does_not_warn(self):
        self.assertNotIn(codes.MARGIN_BUFFER_ERODED, issue_codes(None))


if __name__ == "__main__":
    unittest.main()
