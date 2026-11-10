"""
Tests unitaires pour src/scraper.py
"""

import pytest
from src.scraper import PoliteScraper, ScrapeResult


@pytest.mark.asyncio
async def test_robots_allowed():
    scraper = PoliteScraper(delay_seconds=0.1)
    allowed = scraper.is_allowed_by_robots("https://example.com")
    assert allowed is True


@pytest.mark.asyncio
async def test_scraper_real_page():
    # Test avec 0.5 seconde de délai pour tester le scraper en conditions réelles
    async with PoliteScraper(delay_seconds=0.5) as scraper:
        res = await scraper.scrape("https://example.com")
        assert isinstance(res, ScrapeResult)
        assert res.success is True
        assert len(res.html) > 0
        assert "example" in res.html.lower() or "exemple" in res.html.lower()


@pytest.mark.asyncio
async def test_scraper_invalid_url():
    async with PoliteScraper(delay_seconds=0.1) as scraper:
        res = await scraper.scrape("https://cettedressenedoitpastoutafaitexister123456.com")
        assert res.success is False
        assert res.error_message is not None
