from app.services.history_service import annotate_history_tracks


def test_mixed_chat_keeps_text_and_image_tracks() -> None:
    messages = [
        {"role": "user", "content": "画一张"},
        {
            "role": "assistant",
            "content": "",
            "generated_images": ["https://example.test/a.png"],
            "gen_image_meta": {"modelId": "doubao-seedream-5-0-flash", "modelName": "Doubao Seedream 5.0 Flash"},
            "usage": {"total_tokens": 4056},
        },
        {"role": "user", "content": "再聊聊"},
        {
            "role": "assistant",
            "content": "好",
            "usage": {"total_tokens": 9131},
        },
    ]
    tracks = annotate_history_tracks(
        messages,
        {
            "modelId": "deepseek-flash",
            "modelName": "DeepSeek-V4.1-Flash",
            "providerId": "deepseek",
            "modelCategory": "chat",
            "modelApi": "chat",
        },
    )

    assert [item["historyTrack"] for item in messages] == ["image", "image", "text", "text"]
    assert tracks[0]["track"] == "text"
    assert tracks[0]["modelName"] == "DeepSeek-V4.1-Flash"
    assert tracks[0]["providerId"] == "deepseek"
    assert tracks[0]["turns"] == 1
    assert tracks[0]["tokens"] == 9131
    assert tracks[1]["track"] == "image"
    assert tracks[1]["modelName"] == "Doubao Seedream 5.0 Flash"
    assert tracks[1]["providerId"] == "ark"
    assert tracks[1]["turns"] == 1
    assert tracks[1]["tokens"] == 4056
