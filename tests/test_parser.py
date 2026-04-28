from unittest.mock import AsyncMock, patch

import pytest

from core.schemas import ParsedRequirement
from services import parser


@pytest.mark.asyncio
async def test_parse_valid_input():
    mock_llm = AsyncMock(
        return_value={
            "item": "steel rods",
            "quantity": 25,
            "unit": "kg",
            "location": "delhi",
            "current_price": 80,
            "category": "metals",
        }
    )

    with patch("services.parser.call_llm", mock_llm):
        result = await parser._parse_requirement(
            "Need 25 kg steel rods in Delhi at Rs 80 per kg"
        )

    assert isinstance(result, ParsedRequirement)
    assert result.item == "steel rods"
    assert result.quantity == 25
    assert result.unit == "kg"
    assert result.location == "delhi"
    assert result.current_price == 80
    assert result.category == "metals"


@pytest.mark.asyncio
async def test_parse_llm_down():
    observed = {}

    async def fake_call_llm(prompt: str, fallback_fn=None):
        try:
            raise ConnectionRefusedError("LLM is down")
        except ConnectionRefusedError:
            result = fallback_fn(prompt)
            observed.update(result)
            return result

    with patch("services.parser.call_llm", AsyncMock(side_effect=fake_call_llm)):
        result = await parser._parse_requirement(
            "Need 100 kg cement in Mumbai for 350 rs per kg"
        )

    assert isinstance(result, ParsedRequirement)
    assert observed["fallback_used"] is True
    assert result.item == "need cement in"
    assert result.quantity == 100
    assert result.unit == "kg"
    assert result.location == "mumbai"
    assert result.current_price == 350


@pytest.mark.asyncio
async def test_parse_too_short():
    with pytest.raises(ValueError, match="at least 10 characters"):
        await parser._parse_requirement("too short")


@pytest.mark.asyncio
async def test_parse_no_price_found():
    async def fake_call_llm(prompt: str, fallback_fn=None):
        try:
            raise ConnectionRefusedError("LLM is down")
        except ConnectionRefusedError:
            return fallback_fn(prompt)

    with patch("services.parser.call_llm", AsyncMock(side_effect=fake_call_llm)):
        with pytest.raises(ValueError, match="Could not extract price"):
            await parser._parse_requirement(
                "Need cement bags urgently in Mumbai for regular supply"
            )
