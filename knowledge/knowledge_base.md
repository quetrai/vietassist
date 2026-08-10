<!--
  Thư mục knowledge/ giờ hỗ trợ NHIỀU file .md (kể cả thư mục con), không giới hạn ~20k ký
  tự như trước — phù hợp kho tài liệu hàng trăm trang. Cơ chế:

  1. App tự chia mỗi file theo heading Markdown (#, ##...), cắt tiếp đoạn nào còn dài
     (core/knowledge.py: CHUNK_MAX_CHARS ~1200 ký tự, có overlap để không mất ngữ cảnh).
  2. Mỗi đoạn được tính embedding qua Google (cần GOOGLE_API_KEY) rồi lưu vào Postgres/
     Supabase bằng pgvector — không còn nhồi nguyên văn cả file vào mỗi lần chat.
  3. Khi người dùng hỏi, app chỉ lấy ra TOP_K (mặc định 5) đoạn liên quan nhất theo cosine
     similarity để đưa vào system prompt — tiết kiệm token, mở rộng được nhiều tài liệu.

  Cách cập nhật tài liệu:
  - Sửa/thêm file .md trong thư mục này (hoặc thư mục con), commit lên GitHub, deploy lại.
  - Sau khi deploy, chạy lệnh Telegram /kbreindex (chỉ owner) để tính embedding cho file mới/
    đổi — app cũng tự chạy 1 lần lúc khởi động (bỏ qua file không đổi nội dung nên rất nhanh
    ở các lần sau, chỉ tốn embedding cho file thật sự mới/sửa).
  - Xoá file khỏi thư mục rồi /kbreindex sẽ tự dọn các đoạn tương ứng khỏi DB.

  Yêu cầu: GOOGLE_API_KEY phải được set (dùng để tính embedding). Thiếu key thì tính năng
  tra cứu tạm không hoạt động, các phần khác của bot không bị ảnh hưởng.
-->

# Cơ sở dữ liệu tham khảo — VietAssist

## Giới thiệu
(Điền: bot này phục vụ ai, mục đích chính là gì.)

## Câu hỏi thường gặp
**Hỏi:** ...
**Đáp:** ...

## Chính sách / quy định
(Điền các quy tắc, giới hạn, chính sách mà bot cần tuân theo khi trả lời.)

## Thông tin liên hệ
(Điền nếu cần bot hướng dẫn người dùng liên hệ ai đó khi vượt quá khả năng trả lời.)
