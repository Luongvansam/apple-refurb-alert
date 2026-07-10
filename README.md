# Apple Refurbished iPhone Alert

Bot theo dõi trang **Apple Nhật – iPhone整備済製品** và gửi Telegram khi xuất hiện sản phẩm mới.

## Biến cần thêm trên Railway

| Tên biến | Giá trị |
|---|---|
| `BOT_TOKEN` | Token lấy từ BotFather |
| `CHAT_ID` | Chat ID Telegram của bạn |
| `CHECK_INTERVAL` | `60` |
| `KEYWORDS` | Để trống để theo dõi tất cả iPhone |

Ví dụ chỉ theo dõi Pro Max:

```text
KEYWORDS=Pro Max
```

Ví dụ theo dõi nhiều loại:

```text
KEYWORDS=iPhone 15 Pro Max,iPhone 16 Pro Max
```

## Cách hoạt động

- Lúc bot vừa chạy, bot ghi nhận danh sách hàng đang có và **không gửi hàng loạt**.
- Khi Apple thêm một đường dẫn sản phẩm mới, bot gửi tên, giá và link mua.
- Bot kiểm tra mặc định mỗi 60 giây.
- Không đưa `BOT_TOKEN` vào GitHub. Chỉ nhập nó trong phần Variables của Railway.
