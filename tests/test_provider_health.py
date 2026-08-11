from ai.router import AIRouter


def test_health_snapshot_is_copy():
    router = object.__new__(AIRouter)
    router._health = {"groq": {"ok": 2, "errors": 1}}
    snapshot = router.health_snapshot()
    snapshot["groq"]["ok"] = 99
    assert router._health["groq"]["ok"] == 2
