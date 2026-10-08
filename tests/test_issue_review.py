"""Two-tier issue review rules — pure logic."""

import unittest
import uuid
from datetime import UTC, datetime, timedelta

from domain.issue_review import (
    APPROVAL_VALIDITY,
    ReviewDecision,
    check_review_input,
    is_approval_current,
)
from models.enums import Role
from services.issue_review_service import ReviewTier, tier_for_role

APPROVE, REJECT = ReviewDecision.APPROVE, ReviewDecision.REJECT
NOW = datetime(2026, 10, 8, 9, 0, tzinfo=UTC)


class TierTests(unittest.TestCase):
    def test_owner_and_platform_admin_decide_finally(self):
        self.assertIs(tier_for_role(Role.OWNER), ReviewTier.FINAL)
        self.assertIs(tier_for_role(Role.PLATFORM_ADMIN), ReviewTier.FINAL)

    def test_accountant_only_recommends(self):
        self.assertIs(tier_for_role(Role.ACCOUNTANT), ReviewTier.RECOMMENDATION)


class InputTests(unittest.TestCase):
    def test_remark_is_always_required(self):
        self.assertIsNotNone(check_review_input("HAND_SET_VALUE", APPROVE, None, None))
        self.assertIsNotNone(check_review_input("HAND_SET_VALUE", REJECT, None, "  "))

    def test_pricing_approval_needs_a_reason_code(self):
        self.assertIsNotNone(check_review_input("MARGIN_BUFFER_ERODED", APPROVE, None, "fine"))
        self.assertIsNone(check_review_input("MARGIN_BUFFER_ERODED", APPROVE, "COST_CONFIRMED", "fine"))

    def test_pricing_rejection_does_not_need_a_reason_code(self):
        self.assertIsNone(check_review_input("NEGATIVE_MARGIN", REJECT, None, "re-check cost"))

    def test_non_pricing_approval_needs_only_a_remark(self):
        self.assertIsNone(check_review_input("HAND_SET_VALUE", APPROVE, None, "known override"))

    def test_unknown_reason_code_is_refused(self):
        self.assertIsNotNone(check_review_input("HAND_SET_VALUE", APPROVE, "BECAUSE", "ok ok"))


class CarryForwardTests(unittest.TestCase):
    model_a, model_b = uuid.uuid4(), uuid.uuid4()

    def test_valid_within_window_under_same_model(self):
        self.assertTrue(is_approval_current(NOW - timedelta(days=3), self.model_a, self.model_a, NOW))

    def test_lapses_after_the_window(self):
        self.assertFalse(is_approval_current(NOW - APPROVAL_VALIDITY, self.model_a, self.model_a, NOW))

    def test_lapses_when_the_model_changes(self):
        self.assertFalse(is_approval_current(NOW - timedelta(days=1), self.model_a, self.model_b, NOW))

    def test_unknown_model_is_judged_on_the_window_alone(self):
        self.assertTrue(is_approval_current(NOW - timedelta(days=1), None, self.model_b, NOW))


if __name__ == "__main__":
    unittest.main()
