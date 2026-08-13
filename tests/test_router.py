import pytest

from ai.contracts import AIResponse, ProviderError, ProviderUnavailable
from ai.router import AIRouter


class _FakeProvider:
    def __init__(self, name: str, *, fail: bool = False) -> None:
        self.name = name
        self.fail = fail
        self.calls = 0

    async def generate(self, messages, *, system="", temperature=0.5, max_tokens=2048):
        self.calls += 1
        if self.fail:
            raise ProviderUnavailable(f"{self.name} chưa cấu hình")
        return AIResponse(text=f"trả lời từ {self.name}", provider=self.name, model="m")


@pytest.fixture
def router() -> AIRouter:
    instance = AIRouter.__new__(AIRouter)
    instance.groq = _FakeProvider("groq")
    instance.openrouter = _FakeProvider("openrouter")
    instance.google = None
    return instance


async def test_text_prefers_groq(router):
    from ai.contracts import TaskType

    response = await router.text(TaskType.CHAT, [{"role": "user", "content": "hi"}], system="s")
    assert response.provider == "groq"
    assert router.openrouter.calls == 0


async def test_text_ignores_router9_by_default(router):
    """Mặc định (chưa /ai on, prefer_router9=False) hành vi phải y hệt bản gốc — không
    được đụng tới router9 dù attribute có tồn tại hay không."""
    from ai.contracts import TaskType

    router.router9 = _FakeProvider("router9")
    response = await router.text(TaskType.CHAT, [{"role": "user", "content": "hi"}], system="s")
    assert response.provider == "groq"
    assert router.router9.calls == 0


async def test_text_prefers_router9_when_requested(router):
    """/ai on -> prefer_router9=True -> router9 phải được thử TRƯỚC groq."""
    from ai.contracts import TaskType

    router.router9 = _FakeProvider("router9")
    response = await router.text(
        TaskType.CHAT, [{"role": "user", "content": "hi"}], system="s", prefer_router9=True
    )
    assert response.provider == "router9"
    assert router.groq.calls == 0


async def test_text_falls_back_past_router9_when_it_fails(router):
    """router9 lỗi (vd chưa cấu hình ROUTER9_API_KEY, hoặc timeout) -> vẫn phải fallback
    tiếp qua groq/openrouter/google như bình thường, không được raise ngay."""
    from ai.contracts import TaskType

    router.router9 = _FakeProvider("router9", fail=True)
    response = await router.text(
        TaskType.CHAT, [{"role": "user", "content": "hi"}], system="s", prefer_router9=True
    )
    assert response.provider == "groq"


async def test_text_falls_back_to_openrouter_when_groq_fails():
    from ai.contracts import TaskType

    instance = AIRouter.__new__(AIRouter)
    instance.groq = _FakeProvider("groq", fail=True)
    instance.openrouter = _FakeProvider("openrouter")
    response = await instance.text(TaskType.CHAT, [{"role": "user", "content": "hi"}], system="s")
    assert response.provider == "openrouter"


async def test_deep_report_prefers_openrouter(router):
    response = await router.deep_report([{"role": "user", "content": "hi"}], system="s")
    assert response.provider == "openrouter"
    assert router.groq.calls == 0


async def test_deep_report_falls_back_to_groq_when_openrouter_fails():
    instance = AIRouter.__new__(AIRouter)
    instance.groq = _FakeProvider("groq")
    instance.openrouter = _FakeProvider("openrouter", fail=True)
    response = await instance.deep_report([{"role": "user", "content": "hi"}], system="s")
    assert response.provider == "groq"


async def test_deep_report_raises_when_both_fail():
    instance = AIRouter.__new__(AIRouter)
    instance.groq = _FakeProvider("groq", fail=True)
    instance.openrouter = _FakeProvider("openrouter", fail=True)
    with pytest.raises(ProviderError):
        await instance.deep_report([{"role": "user", "content": "hi"}], system="s")


async def test_text_raises_with_task_label_when_all_providers_fail():
    """Regression test: `task` không còn là tham số chết — lỗi cuối cùng phải nêu rõ task nào
    đã thất bại, để dễ debug/log khi cả hai provider đều lỗi."""
    from ai.contracts import TaskType

    instance = AIRouter.__new__(AIRouter)
    instance.groq = _FakeProvider("groq", fail=True)
    instance.openrouter = _FakeProvider("openrouter", fail=True)
    with pytest.raises(ProviderError, match=r"\[stock_narrative\]"):
        await instance.text(
            TaskType.STOCK_NARRATIVE, [{"role": "user", "content": "hi"}], system="s"
        )


async def test_text_falls_back_to_google_when_groq_and_openrouter_fail():
    from ai.contracts import TaskType

    instance = AIRouter.__new__(AIRouter)
    instance.groq = _FakeProvider("groq", fail=True)
    instance.openrouter = _FakeProvider("openrouter", fail=True)
    instance.google = _FakeProvider("google")
    response = await instance.text(TaskType.CHAT, [{"role": "user", "content": "hi"}], system="s")
    assert response.provider == "google"


async def test_deep_report_falls_back_to_google_when_others_fail():
    instance = AIRouter.__new__(AIRouter)
    instance.groq = _FakeProvider("groq", fail=True)
    instance.openrouter = _FakeProvider("openrouter", fail=True)
    instance.google = _FakeProvider("google")
    response = await instance.deep_report([{"role": "user", "content": "hi"}], system="s")
    assert response.provider == "google"


async def test_text_raises_when_all_three_providers_fail():
    from ai.contracts import TaskType

    instance = AIRouter.__new__(AIRouter)
    instance.groq = _FakeProvider("groq", fail=True)
    instance.openrouter = _FakeProvider("openrouter", fail=True)
    instance.google = _FakeProvider("google", fail=True)
    with pytest.raises(ProviderError):
        await instance.text(TaskType.CHAT, [{"role": "user", "content": "hi"}], system="s")


async def test_translate_prefers_openrouter(router):
    response = await router.translate([{"role": "user", "content": "hi"}], system="s")
    assert response.provider == "openrouter"
    assert router.groq.calls == 0


async def test_translate_falls_back_to_groq_when_openrouter_fails():
    instance = AIRouter.__new__(AIRouter)
    instance.groq = _FakeProvider("groq")
    instance.openrouter = _FakeProvider("openrouter", fail=True)
    response = await instance.translate([{"role": "user", "content": "hi"}], system="s")
    assert response.provider == "groq"


async def test_translate_falls_back_to_google_when_others_fail():
    instance = AIRouter.__new__(AIRouter)
    instance.groq = _FakeProvider("groq", fail=True)
    instance.openrouter = _FakeProvider("openrouter", fail=True)
    instance.google = _FakeProvider("google")
    response = await instance.translate([{"role": "user", "content": "hi"}], system="s")
    assert response.provider == "google"


async def test_translate_raises_with_task_label_when_all_providers_fail():
    instance = AIRouter.__new__(AIRouter)
    instance.groq = _FakeProvider("groq", fail=True)
    instance.openrouter = _FakeProvider("openrouter", fail=True)
    instance.google = _FakeProvider("google", fail=True)
    with pytest.raises(ProviderError, match=r"\[translation\]"):
        await instance.translate([{"role": "user", "content": "hi"}], system="s")


async def test_macro_news_rejects_empty_query(router):
    with pytest.raises(ValueError, match="Thiếu câu hỏi"):
        await router.macro_news("   ")


async def test_product_search_rejects_empty_query(router):
    with pytest.raises(ValueError, match="Thiếu sản phẩm"):
        await router.product_search("")
