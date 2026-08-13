from __future__ import annotations

from typing import Any


def _button(text: str, value: str, style: str = "Default") -> dict[str, Any]:
    return {"text": text, "value": value, "style": style}


def _actions(*items: dict[str, Any], limit: int | None = None) -> dict[str, Any]:
    block: dict[str, Any] = {"type": "actions", "items": list(items)}
    if limit is not None:
        block["limit"] = limit
    return block


def _action_rows(*buttons: dict[str, Any]) -> list[dict[str, Any]]:
    return [_actions(*buttons[i : i + 2]) for i in range(0, len(buttons), 2)]


def _section(title: str, *buttons: tuple[str, str]) -> dict[str, Any]:
    return {
        "type": "section",
        "layout": "horizontal",
        "sections": [
            {"type": "message", "text": title},
            _actions(*(_button(label, value) for label, value in buttons)),
        ],
    }


def main_menu() -> dict[str, Any]:
    return {
        "head": {
            "text": "🤖 VietAssist",
            "sub_head": {"text": "Trợ lý AI cho Zoom Team Chat"},
        },
        "body": [
            {"type": "message", "text": "Chọn nhóm chức năng:"},
            *_action_rows(
                _button("📈 Chứng khoán", "menu:stock", "Primary"),
                _button("🧰 Công cụ", "menu:tools"),
                _button("📝 Cá nhân", "menu:personal"),
                _button("⚙️ Hệ thống", "menu:system"),
            ),
            {"type": "message", "text": "Bạn cũng có thể gõ /vietassist <lệnh> để dùng trực tiếp."},
        ],
    }


def stock_menu() -> dict[str, Any]:
    return {
        "head": {"text": "📈 Chứng khoán", "sub_head": {"text": "VietAssist Stock"}},
        "body": [
            {"type": "message", "text": "Chọn chức năng:"},
            *_action_rows(
                _button("📊 Phân tích cổ phiếu", "stock:analyze", "Primary"),
                _button("🔎 Phân tích sâu", "stock:deep"),
                _button("💰 Giá cổ phiếu", "stock:quote"),
                _button("🌐 Vĩ mô", "stock:macro"),
                _button("📋 Danh mục", "portfolio:list"),
                _button("➕ Mua vào", "portfolio:buy"),
                _button("➖ Bán ra", "portfolio:sell"),
                _button("🎯 Stop / Target", "portfolio:target"),
                _button("⬅️ Quay lại", "menu:main"),
            ),
        ],
    }


def tools_menu() -> dict[str, Any]:
    return {
        "head": {"text": "🧰 Công cụ", "sub_head": {"text": "Các tiện ích VietAssist"}},
        "body": [
            *_action_rows(
                _button("🌐 Dịch Việt ↔ Nhật", "tool:translate", "Primary"),
                _button("🛒 Tra giá sản phẩm", "tool:price"),
                _button("🖼️ Prompt tạo ảnh", "tool:prompt"),
                _button("🤖 Hỏi AI", "tool:chat"),
                _button("⬅️ Quay lại", "menu:main"),
            )
        ],
    }


def personal_menu() -> dict[str, Any]:
    return {
        "head": {"text": "📝 Cá nhân", "sub_head": {"text": "Ghi chú, nhắc nhở và danh mục"}},
        "body": [
            *_action_rows(
                _button("📋 Xem danh mục", "portfolio:list", "Primary"),
                _button("🗒️ Ghi chú", "personal:notes"),
                _button("⏰ Nhắc nhở", "personal:reminders"),
                _button("⬅️ Quay lại", "menu:main"),
            )
        ],
    }


def system_menu() -> dict[str, Any]:
    return {
        "head": {"text": "⚙️ Hệ thống", "sub_head": {"text": "Cấu hình VietAssist"}},
        "body": [
            *_action_rows(
                _button("🧠 Bật RAG", "system:rag:on", "Primary"),
                _button("🚫 Tắt RAG", "system:rag:off", "Danger"),
                _button("ℹ️ Trợ giúp", "menu:help"),
                _button("⬅️ Quay lại", "menu:main"),
            )
        ],
    }


def help_menu() -> dict[str, Any]:
    return {
        "head": {"text": "❓ VietAssist Help"},
        "body": [
            {
                "type": "message",
                "text": (
                    "Các lệnh chính:\n"
                    "/vietassist stock FPT\n"
                    "/vietassist quote FPT\n"
                    "/vietassist vimo <câu hỏi>\n"
                    "/vietassist dich <nội dung>\n"
                    "/vietassist gia <sản phẩm>\n"
                    "/vietassist prompt <mô tả>\n"
                    "/vietassist danhmuc\n"
                    "/vietassist ghichu <nội dung>\n"
                    "/vietassist nhac <thời gian> <nội dung>\n"
                    "/vietassist rag on|off"
                ),
            },
            _actions(_button("⬅️ Quay lại", "menu:main", "Primary")),
        ],
    }


def prompt_card(title: str, instruction: str, action: str = "menu:main") -> dict[str, Any]:
    return {
        "head": {"text": title},
        "body": [
            {"type": "message", "text": instruction},
            _actions(_button("⬅️ Quay lại", action)),
        ],
    }


def card_for_action(action: str) -> dict[str, Any] | None:
    cards = {
        "menu:main": main_menu,
        "menu:stock": stock_menu,
        "menu:tools": tools_menu,
        "menu:personal": personal_menu,
        "menu:system": system_menu,
        "menu:help": help_menu,
        "stock:analyze": lambda: prompt_card("📊 Phân tích cổ phiếu", "Gõ /vietassist stock <MÃ>. Ví dụ: /vietassist stock FPT", "menu:stock"),
        "stock:deep": lambda: prompt_card("🔎 Phân tích sâu", "Gõ /vietassist stock <MÃ> sâu. Ví dụ: /vietassist stock FPT sâu", "menu:stock"),
        "stock:quote": lambda: prompt_card("💰 Giá cổ phiếu", "Gõ /vietassist quote <MÃ>. Ví dụ: /vietassist quote FPT", "menu:stock"),
        "stock:macro": lambda: prompt_card("🌐 Vĩ mô", "Gõ /vietassist vimo <câu hỏi> để tra cứu vĩ mô/tin tức.", "menu:stock"),
        "portfolio:list": lambda: prompt_card("📋 Danh mục", "Đang mở danh mục của bạn...", "menu:stock"),
        "portfolio:buy": lambda: prompt_card("➕ Mua vào", "Gõ /vietassist muavao <MÃ> <KL> <giá>", "menu:stock"),
        "portfolio:sell": lambda: prompt_card("➖ Bán ra", "Gõ /vietassist banra <MÃ> <KL>", "menu:stock"),
        "portfolio:target": lambda: prompt_card("🎯 Stop / Target", "Gõ /vietassist muctieu <MÃ> <stop> <target>", "menu:stock"),
        "tool:translate": lambda: prompt_card("🌐 Dịch Việt ↔ Nhật", "Gõ /vietassist dich <nội dung>. Có thể dùng ja>vi hoặc vi>ja.", "menu:tools"),
        "tool:price": lambda: prompt_card("🛒 Tra giá sản phẩm", "Gõ /vietassist gia <sản phẩm>", "menu:tools"),
        "tool:prompt": lambda: prompt_card("🖼️ Prompt tạo ảnh", "Gõ /vietassist prompt <mô tả ảnh>", "menu:tools"),
        "tool:chat": lambda: prompt_card("🤖 Hỏi AI", "Gõ /vietassist <câu hỏi bất kỳ> hoặc gửi câu hỏi trực tiếp.", "menu:tools"),
        "personal:notes": lambda: prompt_card("🗒️ Ghi chú", "Gõ /vietassist ghichu <nội dung>. Gõ /vietassist ghichu để xem danh sách.", "menu:personal"),
        "personal:reminders": lambda: prompt_card("⏰ Nhắc nhở", "Gõ /vietassist nhac <30p|2h|1ngay|HH:MM> <nội dung>.", "menu:personal"),
        "system:rag:on": lambda: prompt_card("🧠 RAG", "Đã chọn bật RAG. Hệ thống sẽ áp dụng thay đổi ngay sau khi xử lý.", "menu:system"),
        "system:rag:off": lambda: prompt_card("🚫 RAG", "Đã chọn tắt RAG. Hệ thống sẽ áp dụng thay đổi ngay sau khi xử lý.", "menu:system"),
    }
    factory = cards.get(action)
    return factory() if factory else None
