"""Singapore-date activation: 00:00 SGT on the chosen date, as a UTC instant."""

import unittest
from datetime import UTC, date, datetime

from core.activation_time import activation_date_of, activation_instant_for, describe_activation, singapore_today


class ActivationTimeTests(unittest.TestCase):
    def test_midnight_singapore_is_16_00_utc_the_day_before(self):
        self.assertEqual(activation_instant_for(date(2026, 10, 1)), datetime(2026, 9, 30, 16, 0, tzinfo=UTC))

    def test_round_trips_to_the_same_singapore_date(self):
        for d in (date(2026, 10, 1), date(2026, 12, 31), date(2027, 3, 1)):
            self.assertEqual(activation_date_of(activation_instant_for(d)), d)

    def test_melbourne_lags_by_two_hours_before_dst_and_three_after(self):
        # Victoria's daylight saving starts Sunday 4 Oct 2026.
        self.assertEqual(describe_activation(activation_instant_for(date(2026, 10, 1))),
                         {"singapore": "2026-10-01 00:00", "melbourne": "2026-10-01 02:00"})
        self.assertEqual(describe_activation(activation_instant_for(date(2026, 10, 5))),
                         {"singapore": "2026-10-05 00:00", "melbourne": "2026-10-05 03:00"})

    def test_singapore_today_flips_at_16_00_utc(self):
        self.assertEqual(singapore_today(datetime(2026, 9, 30, 15, 59, 59, tzinfo=UTC)), date(2026, 9, 30))
        self.assertEqual(singapore_today(datetime(2026, 9, 30, 16, 0, 0, tzinfo=UTC)), date(2026, 10, 1))


if __name__ == "__main__":
    unittest.main()
