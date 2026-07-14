"""速率限制固定窗口测试。"""

from app.storage import redis_client as redis_client_module


def test_memory_rate_limit_window_does_not_slide(monkeypatch):
    """持续请求不应刷新窗口过期时间，窗口结束后计数应归一。"""
    client = redis_client_module.redis_client
    monkeypatch.setattr(client, "_use_redis", False)
    monkeypatch.setattr(client, "_client", None)
    monkeypatch.setattr(client, "_memory_rate_limits", {})

    current_time = [100.0]
    monkeypatch.setattr(
        redis_client_module.time,
        "monotonic",
        lambda: current_time[0],
    )

    assert client.increment_with_window("rate_limit:test", 60) == 1

    current_time[0] = 159.0
    assert client.increment_with_window("rate_limit:test", 60) == 2

    current_time[0] = 160.0
    assert client.increment_with_window("rate_limit:test", 60) == 1


def test_redis_rate_limit_uses_atomic_fixed_window_script(monkeypatch):
    """Redis 模式应通过单个 Lua 脚本完成递增和首次过期设置。"""

    class FakeRedis:
        def __init__(self):
            self.calls = []

        def eval(self, script, key_count, key, window):
            self.calls.append((script, key_count, key, window))
            return 3

    client = redis_client_module.redis_client
    fake_redis = FakeRedis()
    monkeypatch.setattr(client, "_use_redis", True)
    monkeypatch.setattr(client, "_client", fake_redis)
    monkeypatch.setattr(client, "_retry_on_fail", lambda operation: operation())

    assert client.increment_with_window("rate_limit:test", 60) == 3
    assert fake_redis.calls == [
        (client._FIXED_WINDOW_INCREMENT_SCRIPT, 1, "rate_limit:test", 60)
    ]
