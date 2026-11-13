"""方舟文件只属于上传者。"""

from __future__ import annotations

import uuid

from app.api.v1.endpoints.ark import (
    IMAGE_DEFAULT_TOKENS,
    IMAGE_TOOL_NAME,
    _public_image_usage,
    _usage_amount,
    owns_ark_file,
)


def test_ark_file_is_visible_only_to_its_owner() -> None:
    owner = uuid.uuid4()
    other = uuid.uuid4()
    assert owns_ark_file(owner, owner) is True
    assert owns_ark_file(owner, other) is False


def test_image_usage_falls_back_to_output_tokens() -> None:
    total, prompt, completion = _usage_amount(
        {"usage": {"generated_images": 1, "output_tokens": 9408}}
    )
    assert (total, prompt, completion) == (9408, 0, 9408)


def test_image_usage_does_not_add_output_tokens_twice() -> None:
    total, prompt, completion = _usage_amount(
        {"usage": {"generated_images": 8, "output_tokens": 23107, "total_tokens": 23107}}
    )
    assert (total, prompt, completion) == (23107, 0, 23107)


def test_image_count_is_not_billed_as_tokens() -> None:
    assert _usage_amount({"usage": {"generated_images": 2}}) == (0, 0, 0)


def test_public_image_usage_bills_fixed_tokens_per_image(monkeypatch) -> None:
    monkeypatch.delenv("TOOL_PRICE_IMAGE_GENERATION_TOKENS", raising=False)
    public, meter = _public_image_usage(
        {"model": "doubao-seedream-5-0-flash-260915", "data": [{"url": "https://x/1.png"}], "usage": {"generated_images": 1}},
        prompt="画一只猫",
        images=["https://example.com/ref.png"],
    )
    # 图片是工具固定计费，不是模型 token：输入/输出/合计都为 0。
    assert public["prompt_tokens"] == 0
    assert public["completion_tokens"] == 0
    assert public["total_tokens"] == 0
    assert public["generated_images"] == 1
    tools = public["tool_usage"]
    assert len(tools) == 1
    tool = tools[0]
    assert tool["tool_name"] == IMAGE_TOOL_NAME
    assert tool["calls"] == 1
    assert tool["priced_tokens"] == IMAGE_DEFAULT_TOKENS
    assert tool["unit"] == "image"
    assert meter is None


def test_public_image_usage_bills_per_image(monkeypatch) -> None:
    monkeypatch.delenv("TOOL_PRICE_IMAGE_GENERATION_TOKENS", raising=False)
    public, _meter = _public_image_usage(
        {"model": "doubao-seedream-5-0-flash-260915", "data": [{"url": "1"}, {"url": "2"}, {"url": "3"}], "usage": {"generated_images": 3}},
        prompt="猫",
    )
    tools = public["tool_usage"]
    assert len(tools) == 1
    assert tools[0]["calls"] == 3
    assert tools[0]["priced_tokens"] == IMAGE_DEFAULT_TOKENS * 3


def test_public_image_usage_unpriced_model_charges_zero(monkeypatch) -> None:
    monkeypatch.delenv("TOOL_PRICE_IMAGE_GENERATION_TOKENS", raising=False)
    public, _meter = _public_image_usage(
        {"model": "doubao-seedream-5-0-pro-260628", "data": [{"url": "1"}, {"url": "2"}], "usage": {"generated_images": 2}},
        prompt="猫",
    )
    assert public["tool_usage"][0]["calls"] == 2
    assert public["tool_usage"][0]["priced_tokens"] == 0


def test_public_image_usage_no_images_charges_zero(monkeypatch) -> None:
    monkeypatch.delenv("TOOL_PRICE_IMAGE_GENERATION_TOKENS", raising=False)
    public, _meter = _public_image_usage(
        {"model": "doubao-seedream-5-0-flash-260915", "data": [], "usage": {"generated_images": 0}},
        prompt="猫",
    )
    assert public["tool_usage"] == []
    assert public["total_tokens"] == 0
