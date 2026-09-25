"""Role rules for PLATFORM_ADMIN, OWNER, ACCOUNTANT and BUYER — pure logic."""

import unittest

from core.permissions import (
    CONSOLE_ROLES,
    MFA_ROLES,
    can_assign_roles,
    can_manage_account,
    can_set_temporary_password,
    role_satisfies,
)
from models.enums import Role

OWNER, ACCOUNTANT, BUYER, ADMIN = Role.OWNER, Role.ACCOUNTANT, Role.BUYER, Role.PLATFORM_ADMIN


class RoleSatisfiesTests(unittest.TestCase):
    def test_platform_admin_passes_wherever_owner_passes(self):
        self.assertTrue(role_satisfies(ADMIN, (OWNER,)))
        self.assertTrue(role_satisfies(ADMIN, (OWNER, ACCOUNTANT)))
        self.assertTrue(role_satisfies(ADMIN, (OWNER, ACCOUNTANT, BUYER)))

    def test_platform_admin_never_passes_routes_that_exclude_owner(self):
        self.assertFalse(role_satisfies(ADMIN, (BUYER,)))
        self.assertFalse(role_satisfies(ADMIN, (ACCOUNTANT,)))

    def test_existing_roles_are_unchanged(self):
        self.assertTrue(role_satisfies(OWNER, (OWNER,)))
        self.assertFalse(role_satisfies(ACCOUNTANT, (OWNER,)))
        self.assertFalse(role_satisfies(BUYER, (OWNER, ACCOUNTANT)))
        self.assertTrue(role_satisfies(BUYER, (BUYER,)))

    def test_a_platform_admin_is_only_ever_admitted_via_owner_or_explicitly(self):
        self.assertTrue(role_satisfies(ADMIN, (ADMIN,)))
        self.assertFalse(role_satisfies(ADMIN, ()))


class PlatformAdminIsolationTests(unittest.TestCase):
    def test_only_a_platform_admin_can_assign_or_change_platform_admin(self):
        self.assertTrue(can_assign_roles(ADMIN, ADMIN))
        self.assertFalse(can_assign_roles(OWNER, ADMIN))
        self.assertFalse(can_assign_roles(OWNER, OWNER, ADMIN))  # promoting or demoting one
        self.assertTrue(can_assign_roles(OWNER, ACCOUNTANT, BUYER))

    def test_owner_cannot_manage_a_platform_admin_account(self):
        self.assertFalse(can_manage_account(OWNER, ADMIN))
        self.assertTrue(can_manage_account(ADMIN, ADMIN))
        for target in (OWNER, ACCOUNTANT, BUYER):
            self.assertTrue(can_manage_account(OWNER, target))
            self.assertTrue(can_manage_account(ADMIN, target))


class TemporaryPasswordTests(unittest.TestCase):
    def test_owner_may_reset_accountant_and_buyer_only(self):
        self.assertTrue(can_set_temporary_password(OWNER, ACCOUNTANT))
        self.assertTrue(can_set_temporary_password(OWNER, BUYER))
        self.assertFalse(can_set_temporary_password(OWNER, OWNER))
        self.assertFalse(can_set_temporary_password(OWNER, ADMIN))

    def test_platform_admin_may_reset_anyone(self):
        for target in (OWNER, ACCOUNTANT, BUYER, ADMIN):
            self.assertTrue(can_set_temporary_password(ADMIN, target))

    def test_accountant_and_buyer_may_reset_nobody(self):
        for actor in (ACCOUNTANT, BUYER):
            for target in (OWNER, ACCOUNTANT, BUYER, ADMIN):
                self.assertFalse(can_set_temporary_password(actor, target))


class RoleGroupTests(unittest.TestCase):
    def test_platform_admin_is_a_console_and_mfa_role_but_buyer_is_neither(self):
        self.assertIn(ADMIN, CONSOLE_ROLES)
        self.assertIn(ADMIN, MFA_ROLES)
        self.assertNotIn(BUYER, CONSOLE_ROLES)
        self.assertNotIn(BUYER, MFA_ROLES)


if __name__ == "__main__":
    unittest.main()
