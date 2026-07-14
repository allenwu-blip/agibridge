"""E2E test package.

These tests exercise the full agibridge user journey across Clerk auth,
R2 storage, Stripe billing, and the `embodied-data` subprocess conversion.

Distinct from unit tests (87 tests in `tests/test_*.py`): unit tests pin
single-module behavior with mocks at module boundaries; E2E tests pin
cross-module user-visible flows with mocks at vendor boundaries only.

See `/Users/allenwu/embodied-data-hosted/agibridge-saas-bootstrap/tests/integration_e2e_plan.md`
for the harness design, mocking strategy, and CI integration.

All bodies in this package are marked `# IMPLEMENT IN D5` until D4 ships
the underlying surfaces (Clerk JWT middleware, billing routes, R2 wire-up).
"""
