from services.web_dashboard.app import _layout
from services.web_dashboard.product import ADVANCED_SURFACES, SURFACES, assert_non_mutating_surfaces


def test_m12_surface_inventory_is_complete_and_non_mutating() -> None:
    assert_non_mutating_surfaces()
    paths = {surface.path for surface in (*SURFACES, *ADVANCED_SURFACES)}
    assert {
        "/markets",
        "/breaking",
        "/forecasting",
        "/learning",
        "/opportunities",
        "/backtests",
        "/portfolio",
        "/system",
    } <= paths
    assert not any(surface.production_write for surface in SURFACES)


def test_layout_has_accessible_landmarks_and_truthful_global_status() -> None:
    page = _layout("Research", "<h1>Research</h1>").decode()
    assert "href=#main-content" in page
    assert 'aria-label="Primary product navigation"' in page
    assert "<main id=main-content tabindex=-1>" in page
    assert "PRODUCTION WRITES: <strong>OFF</strong>" in page
    assert "Simulations are not orders" in page
