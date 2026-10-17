import pytest


@pytest.fixture(autouse=True)
def no_real_grids(settings, tmp_path):
    """Tests never read forecast grids from the real cache; grid tests write their own."""
    settings.USE_NOAA_GRIDS = False
    settings.CACHE_DIR = str(tmp_path)
