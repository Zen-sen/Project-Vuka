from datetime import datetime
from unittest.mock import patch

import pytest

from vuka.core.state import s


@pytest.fixture(autouse=True)
def _filters_state():
    s.STRATEGY = "INGWE"
    s.SA_OFFSET = 2
    s.INGWE_BLACKOUTS_WINTER = [(8, 30, 9, 45)]
    s.INGWE_BLACKOUTS_SUMMER = [(14, 15, 14, 45)]
    yield


class TestNewsBlackout:
    def test_same_hour_window_active(self):
        from vuka.risk import filters
        with patch.object(filters, "is_eu_summer", return_value=True), \
             patch.object(filters, "now_sast", return_value=datetime(2026, 8, 2, 14, 20)):
            assert filters.is_in_news_blackout() is True

    def test_same_hour_window_after_end_clear(self):
        from vuka.risk import filters
        with patch.object(filters, "is_eu_summer", return_value=True), \
             patch.object(filters, "now_sast", return_value=datetime(2026, 8, 2, 14, 50)):
            assert filters.is_in_news_blackout() is False

    def test_before_window_clear(self):
        from vuka.risk import filters
        with patch.object(filters, "is_eu_summer", return_value=True), \
             patch.object(filters, "now_sast", return_value=datetime(2026, 8, 2, 14, 10)):
            assert filters.is_in_news_blackout() is False

    def test_cross_midnight_window(self):
        from vuka.risk import filters
        s.INGWE_BLACKOUTS_SUMMER = [(23, 30, 0, 30)]
        with patch.object(filters, "is_eu_summer", return_value=True), \
             patch.object(filters, "now_sast", return_value=datetime(2026, 8, 2, 23, 45)):
            assert filters.is_in_news_blackout() is True
        with patch.object(filters, "is_eu_summer", return_value=True), \
             patch.object(filters, "now_sast", return_value=datetime(2026, 8, 2, 0, 15)):
            assert filters.is_in_news_blackout() is True
        with patch.object(filters, "is_eu_summer", return_value=True), \
             patch.object(filters, "now_sast", return_value=datetime(2026, 8, 2, 0, 45)):
            assert filters.is_in_news_blackout() is False

    def test_multi_hour_window(self):
        from vuka.risk import filters
        s.INGWE_BLACKOUTS_SUMMER = [(8, 30, 9, 45)]
        with patch.object(filters, "is_eu_summer", return_value=True), \
             patch.object(filters, "now_sast", return_value=datetime(2026, 8, 2, 8, 40)):
            assert filters.is_in_news_blackout() is True
        with patch.object(filters, "is_eu_summer", return_value=True), \
             patch.object(filters, "now_sast", return_value=datetime(2026, 8, 2, 9, 30)):
            assert filters.is_in_news_blackout() is True
        with patch.object(filters, "is_eu_summer", return_value=True), \
             patch.object(filters, "now_sast", return_value=datetime(2026, 8, 2, 9, 50)):
            assert filters.is_in_news_blackout() is False
