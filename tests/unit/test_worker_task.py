from smm_gpt.workers.tasks import system_ping


def test_system_ping_is_safe_and_serializable() -> None:
    result = system_ping.run()

    assert result["status"] == "ok"
    assert result["checked_at"].endswith("+00:00")
