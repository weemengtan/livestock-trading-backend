"""Pure-logic tests for which DNBP model is live and each model's status.
Run: `.venv/bin/python -m unittest discover -s tests -t .`"""

import unittest
import uuid
from datetime import UTC, datetime, timedelta

from domain.engine.model_schedule import ModelStatus, ScheduledModel, can_still_change, live_model_id, status_of

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def model(offset_days: float, *, approved: bool = True, cancelled: bool = False) -> ScheduledModel:
    return ScheduledModel(
        id=uuid.uuid4(),
        activation_at=NOW + timedelta(days=offset_days),
        approved=approved,
        cancelled=cancelled,
    )


class LiveModelTests(unittest.TestCase):
    def test_nothing_due_means_no_live_model(self):
        self.assertIsNone(live_model_id([model(+1)], NOW))
        self.assertIsNone(live_model_id([], NOW))

    def test_latest_due_model_wins(self):
        old, newer, future = model(-30), model(-2), model(+5)
        self.assertEqual(live_model_id([old, future, newer], NOW), newer.id)

    def test_switch_happens_exactly_at_the_instant(self):
        old, new = model(-30), model(+1)
        before = new.activation_at - timedelta(seconds=1)
        self.assertEqual(live_model_id([old, new], before), old.id)
        self.assertEqual(live_model_id([old, new], new.activation_at), new.id)

    def test_unapproved_and_cancelled_never_go_live(self):
        base = model(-30)
        draft = model(-2, approved=False)
        cancelled = model(-1, cancelled=True)
        self.assertEqual(live_model_id([base, draft, cancelled], NOW), base.id)

    def test_cancelling_a_scheduled_model_keeps_the_old_one_live(self):
        base, upcoming = model(-30), model(+3)
        cancelled = ScheduledModel(upcoming.id, upcoming.activation_at, approved=True, cancelled=True)
        later = cancelled.activation_at + timedelta(days=1)
        self.assertEqual(live_model_id([base, cancelled], later), base.id)


class StatusTests(unittest.TestCase):
    def test_every_status(self):
        retired, live, scheduled = model(-30), model(-2), model(+5)
        draft, cancelled = model(+9, approved=False), model(+9, cancelled=True)
        live_id = live_model_id([retired, live, scheduled, draft, cancelled], NOW)

        def status(m):
            return status_of(m, live_id=live_id, now=NOW)

        self.assertEqual(status(retired), ModelStatus.RETIRED)
        self.assertEqual(status(live), ModelStatus.LIVE)
        self.assertEqual(status(scheduled), ModelStatus.SCHEDULED)
        self.assertEqual(status(draft), ModelStatus.DRAFT)
        self.assertEqual(status(cancelled), ModelStatus.CANCELLED)

    def test_only_one_live_at_any_instant(self):
        models = [model(-40), model(-20), model(-2), model(+4)]
        for step in range(-45, 10):
            at = NOW + timedelta(days=step, hours=3)
            live_id = live_model_id(models, at)
            live = [m for m in models if status_of(m, live_id=live_id, now=at) is ModelStatus.LIVE]
            self.assertLessEqual(len(live), 1)


class CanStillChangeTests(unittest.TestCase):
    def test_draft_can_change_even_if_its_date_has_passed(self):
        self.assertTrue(can_still_change(model(-1, approved=False), now=NOW))

    def test_scheduled_can_change_until_it_goes_live(self):
        m = model(+1)
        self.assertTrue(can_still_change(m, now=NOW))
        self.assertFalse(can_still_change(m, now=m.activation_at))

    def test_live_retired_and_cancelled_cannot_change(self):
        self.assertFalse(can_still_change(model(-2), now=NOW))
        self.assertFalse(can_still_change(model(+2, cancelled=True), now=NOW))


if __name__ == "__main__":
    unittest.main()
