from types import SimpleNamespace

from app.services.history_service import _assemble_message, _split_messages


def test_history_snapshot_keeps_chat_run_id_and_old_messages() -> None:
    split = _split_messages(
        {
            "chatMessages": [
                {"role": "assistant", "content": "第一轮", "chatRunId": "11111111-aaaa-bbbb-cccc-222222222222", "plan": {"status": "drafting"}},
                {"role": "assistant", "content": "第二轮", "chatRunId": "33333333-aaaa-bbbb-cccc-444444444444", "serverRunId": "55555555-aaaa-bbbb-cccc-666666666666"},
                {"role": "assistant", "content": "旧消息"},
                {"role": "assistant", "content": "坏标识", "chatRunId": "short"},
            ]
        }
    )
    restored = []
    for item in split:
        restored.append(
            _assemble_message(
                SimpleNamespace(
                    role=item["role"],
                    content=item["content"],
                    content_text=item["content_text"],
                    reasoning_content=None,
                    thinking_duration_sec=None,
                    thinking_started_at=None,
                    usage=None,
                    generated_images=[],
                    downloads=[],
                    tool_runs=[],
                    search_results=[],
                    search_query=None,
                    search_error=None,
                    trace=[],
                    planning=item["planning"],
                    history_track=None,
                    track_model=None,
                    created_at_ms=None,
                )
            )
        )

    assert restored[0]["chatRunId"] == "11111111-aaaa-bbbb-cccc-222222222222"
    assert restored[0]["plan"]["status"] == "drafting"
    assert restored[1]["chatRunId"] == "33333333-aaaa-bbbb-cccc-444444444444"
    assert restored[1]["serverRunId"] == "55555555-aaaa-bbbb-cccc-666666666666"
    assert "serverRunId" not in restored[0]
    assert "chatRunId" not in restored[2]
    assert "chatRunId" not in restored[3]
