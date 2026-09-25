"""Settings normalisation needed for Railway's Postgres URLs and cookie config."""

import unittest

from core.config import Settings


class DatabaseUrlTests(unittest.TestCase):
    def test_plain_postgresql_url_gets_the_asyncpg_driver(self):
        self.assertEqual(
            Settings(database_url="postgresql://u:p@h.proxy.rlwy.net:1234/railway").database_url,
            "postgresql+asyncpg://u:p@h.proxy.rlwy.net:1234/railway",
        )

    def test_postgres_scheme_alias_is_also_converted(self):
        self.assertEqual(Settings(database_url="postgres://u:p@h/db").database_url, "postgresql+asyncpg://u:p@h/db")

    def test_already_asyncpg_url_is_untouched(self):
        url = "postgresql+asyncpg://u:p@h/db"
        self.assertEqual(Settings(database_url=url).database_url, url)


class CookieDefaultsTests(unittest.TestCase):
    def test_samesite_defaults_to_strict(self):
        self.assertEqual(Settings.model_fields["cookie_samesite"].default, "strict")


if __name__ == "__main__":
    unittest.main()
