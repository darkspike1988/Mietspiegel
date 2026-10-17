"""Tests für den Telegram-Bot (Command-Handling, offline)."""
from __future__ import annotations

import pytest

from app.services import telegram_bot as tb


pytestmark = pytest.mark.asyncio


class TestHelpCommands:
    async def test_start(self) -> None:
        r = await tb.process_message("/start")
        assert r is not None and "Befehle" in r

    async def test_hilfe(self) -> None:
        r = await tb.process_message("/hilfe")
        assert "check" in r

    async def test_no_command(self) -> None:
        assert await tb.process_message("hallo welt") is None
        assert await tb.process_message("") is None


class TestCheckCommand:
    async def test_berlin_fair(self) -> None:
        # 400 € für 60 m² in Berlin 10117 → fair
        r = await tb.process_message("/check 400 60 10117")
        assert r is not None
        assert "✅" in r
        assert "ortsübliche" in r.lower() or "Ortsübliche" in r

    async def test_berlin_wucher(self) -> None:
        # 1100 € für 60 m² → wucher (+73%)
        r = await tb.process_message("/check 1100 60 10117")
        assert "🔴" in r
        assert "WiStrG" in r
        assert "Überzahlung" in r

    async def test_bad_format(self) -> None:
        r = await tb.process_message("/check 850")
        assert "Format" in r

    async def test_invalid_numbers(self) -> None:
        r = await tb.process_message("/check abc def 10117")
        assert "Ungültige" in r


class TestOtherCommands:
    async def test_kaution_ok(self) -> None:
        r = await tb.process_message("/kaution 800 2400")
        assert "2400" in r and "im Rahmen" in r

    async def test_kaution_over(self) -> None:
        r = await tb.process_message("/kaution 850 3000")
        assert "450" in r and "überhöht" in r

    async def test_mieterhoehung_violation(self) -> None:
        r = await tb.process_message("/mieterhoehung 1000 1200")
        assert "VERLETZT" in r

    async def test_mieterhoehung_ok(self) -> None:
        r = await tb.process_message("/mieterhoehung 1000 1050")
        assert "eingehalten" in r

    async def test_wohnprofil(self) -> None:
        r = await tb.process_message("/wohnprofil 850 2500")
        assert "/100" in r

    async def test_vermieter_vonovia(self) -> None:
        r = await tb.process_message("/vermieter Vonovia")
        assert "Vonovia SE" in r
        assert "550" in r  # Bestand

    async def test_nk_frist_missed(self) -> None:
        r = await tb.process_message("/nk 2024-01-01 2024-12-31 1500 1500 0")
        assert "Frist" in r

    async def test_nk_bad_format(self) -> None:
        r = await tb.process_message("/nk 2024-01-01")
        assert "Format" in r
