# EligibilitySnapshot: yêu cầu và Contracts

## Yêu cầu được giao

`EligibilitySnapshot` là bản sao dữ liệu quyền truy cập tại nhà máy. Nó giữ phiên bản, watermark, danh sách quyền, cấu hình làn và public key; chỉ thực hiện kiểm tra cục bộ. Không xác thực chữ ký QR, gọi controller, ghi DB hay tự đọc đồng hồ hệ thống.

Tracker yêu cầu tính thời gian bằng số nguyên mili giây và cung cấp:

```python
def getOperatingMode(self, now: datetime) -> OperatingMode: ...
def isEmployeeEligible(self, employeeId: str, laneId: str) -> tuple[bool, str]: ...
```

### Operating mode

Các điều kiện chặn được xét trước tuổi watermark:

- Sai số đồng hồ không xác định hoặc ≥ `2_000 ms` → `CANNOT_PROCESS`.
- Snapshot không liên tục → `CANNOT_PROCESS`.
- Chỉ tiếp tục khi biết sai số và sai số < `2_000 ms`.

Khi các điều kiện tin cậy đạt:

| Tuổi watermark | Kết quả |
| --- | --- |
| ≤ `60_000 ms` | `NORMAL` |
| > `60_000 ms` và < `1_800_000 ms` | `DEGRADED` |
| ≥ `1_800_000 ms` | `CANNOT_PROCESS` |

Đặc biệt, **đúng 30 phút phải chặn** và **đúng 2 giây sai số phải chặn**. `DEGRADED` vẫn cho phép tiếp tục đánh giá QR và quyền; nó không tự động có nghĩa là cho phép mở cổng.

Watermark là mốc đã xác nhận dữ liệu đồng bộ đầy đủ tới đó, khác thời hạn sử dụng token QR.

### Quyền truy cập

Kiểm tra nhân viên đang hoạt động, được phép tại nhà máy của snapshot và được phép dùng làn. Giữ kiểu trả về và thứ tự lý do từ chối của scaffold:

| Thứ tự | Điều kiện | Kết quả |
| --- | --- | --- |
| 1 | Làn chưa cấu hình | `(False, "UNKNOWN_LANE")` |
| 2 | Không tìm thấy nhân viên | `(False, "EMPLOYEE_NOT_FOUND")` |
| 3 | Nhân viên không hoạt động | `(False, "EMPLOYEE_SUSPENDED_OR_INACTIVE")` |
| 4 | Nhà máy không được cấp quyền | `(False, "FACTORY_NOT_PERMITTED")` |
| 5 | Làn không được cấp quyền | `(False, "LANE_NOT_PERMITTED")` |
| 6 | Đạt các điều kiện | `(True, "OK")` |

## Dữ liệu hiện tại

| Trường | Kiểu | Ý nghĩa/quy ước |
| --- | --- | --- |
| `factoryId` | `str` | Nhà máy mà snapshot phục vụ |
| `snapshotVersion` | `int` | Phiên bản nguyên dương; loại cả `bool` |
| `confirmedWatermark` | `datetime` | Timestamp có timezone |
| `clockErrorBoundSec` | `float \| None` | Đầu vào giây tương thích app/demo; mặc định `None` |
| `clockErrorBoundMs` | property `int \| None` | Sai số đổi sang mili giây nguyên, làm tròn lên; chỉ đọc |
| `isCompleteAndContinuous` | `bool` | Phải là `True` để được xử lý |
| `employees` | `dict[str, EmployeeRight]` | Tra cứu theo ID nhân viên |
| `lanes` | `dict[str, Direction]` | Làn đã cấu hình và hướng ENTRY/EXIT |
| `verificationKeys` | `tuple[bytes, ...]` | Các public key dạng PEM; tham số constructor keyword-only |
| `publicKeyPem` | `bytes` | Trường tương thích với consumer cũ dùng một public key |

`EmployeeRight` gồm `employeeId`, `active`, `allowedFactories: set[str]` và `allowedLanes: set[str]`.

## Quyết định triển khai bổ sung

Những điểm dưới đây đã được code và test, nhưng không phải tất cả đều được tracker quy định chi tiết. Đổi chúng cần xem ảnh hưởng consumer trước.

### Thời gian và sai số

- Tuổi watermark và clock bound được xét riêng; **không cộng clock bound vào tuổi watermark**. Điều này giữ đúng các test biên tracker với clock bound hợp lệ khác 0.
- Trừ hai timestamp sau khi chuẩn hóa UTC để không tính sai khi đổi timezone hoặc gặp giờ lặp DST (`fold`).
- Tuổi được tính từ các thành phần nguyên của `timedelta`; không dùng `total_seconds()` hay float để so mốc. Bỏ phần dư dưới 1 ms: tuổi `60_000 ms + 999 µs` vẫn có tuổi nguyên `60_000 ms`.
- Watermark tương lai, dù chỉ `1 µs`, bị chặn trước khi bỏ phần dư. Timestamp thiếu timezone, không phải `datetime` hoặc tràn phạm vi khi đổi UTC cũng trả `CANNOT_PROCESS`.
- Giữ đầu vào `clockErrorBoundSec` để `app.py` và demo hiện tại không phải đổi giao diện. `None` mặc định là chưa biết; không giả định sai số 0,5 giây khi caller không cung cấp.
- Sai số âm, NaN, vô cực, chuỗi và boolean được xem là không xác định.
- Sai số đổi sang integer ms bằng biểu diễn thập phân và phép chia số nguyên, làm tròn lên để không đánh giá thấp giới hạn sai số. Ví dụ `1.999 s → 1999 ms`, `1.9991 s → 2000 ms` nên bị chặn. Phép tính không phụ thuộc precision của Decimal context bên ngoài.
- `clockErrorBoundMs` là property chuyển đổi, không phải tham số constructor hoặc field có thể gán. Hiện vẫn truyền `clockErrorBoundSec=1.999` khi cần bound `1999 ms`.
- Snapshot chỉ dùng cờ `isCompleteAndContinuous`, không tự xây lịch sử đồng bộ hoặc suy ra tính liên tục từ số phiên bản. Phiên bản 42 vẫn hợp lệ nếu cờ là `True`.

### Quyền

- Theo TODO Contracts hiện có, `allowedLanes` rỗng nghĩa là mọi làn đã cấu hình tại nhà máy được cấp quyền. `allowedFactories` rỗng không cấp quyền nhà máy nào.
- Nhân viên phải có `active is True`; dữ liệu truthy như `1` hoặc `"true"` không thay thế boolean hợp lệ.
- Nếu khóa dictionary khác `EmployeeRight.employeeId`, trả `EMPLOYEE_NOT_FOUND`, tránh dùng quyền của người khác.
- ID được so chính xác, có phân biệt hoa/thường; không trim hoặc chuẩn hóa ngầm.
- `isEmployeeEligible()` chỉ kiểm quyền, không gọi `getOperatingMode()`. Snapshot cũ có thể vẫn chứa quyền cho kết quả `(True, "OK")`; consumer phải xét cả hai bước.

### Public key và tương thích

- `verificationKeys` giữ public PEM bytes, không parse key, không kiểm chữ ký, không chứa private key. Bộ key rỗng không tự đổi operating mode; Package 2 phải xử lý trường hợp không có key xác minh phù hợp.
- Constructor chỉ có `publicKeyPem` sẽ tạo tuple một phần tử trong `verificationKeys`.
- Constructor chỉ có `verificationKeys` sẽ lấy key đầu tiên làm `publicKeyPem` cho consumer cũ.
- Nếu truyền cả hai, key cũ phải có trong tuple; không khớp sẽ ném `ValueError`.
- Tuple được chụp từ collection lúc khởi tạo để việc sửa list đầu vào sau đó không đổi bộ key của snapshot.
- Đây vẫn là dataclass mutable, không phải immutable snapshot hoàn chỉnh. Hai field public key chỉ được đồng bộ trong `__post_init__`, không tự đồng bộ khi gán sau đó.
- Khi thay bộ key, tạo snapshot thay thế. Với `dataclasses.replace`, cập nhật cả hai field hoặc đặt `publicKeyPem=b""` để chọn key đầu tiên trong tuple mới. `replace` là shallow copy; không được suy diễn các dictionary/set quyền cũng đã được copy sâu.

Ví dụ thay bộ public key, với `new_public_key_pem` đã được cung cấp từ nguồn hợp lệ:

```python
from dataclasses import replace

updated = replace(
    snapshot,
    snapshotVersion=snapshot.snapshotVersion + 1,
    verificationKeys=(new_public_key_pem,),
    publicKeyPem=b"",
)
```

## Tích hợp với package khác

| Package | Trách nhiệm liên quan |
| --- | --- |
| 1 — Brian | Giữ snapshot, phân loại mode, kiểm quyền, cung cấp public key |
| 2 — A Tào | Xác thực chữ ký/hạn QR/phạm vi nhà máy, dùng mode và quyền trong `AccessDecision` |
| 3 — Khoa Phạm | Chốt Contracts, gọi `AccessDecision`, điều phối transaction và controller; facade tiếp nhận snapshot cập nhật |
| 4 — Khang | Lưu trữ và giao dịch; không thuộc logic domain của snapshot |
| 5 — Khoa Văn | API demo thay tuổi watermark/sai số để minh họa mất WAN |

Ở Package 2, luồng dự kiến là tải snapshot → kiểm mode → xác thực QR bằng public key → kiểm quyền → trả `DecisionResult`. Package 1 không claim token, ghi chấm công, ghi degraded flag hay gửi lệnh mở cổng.

Tên các hàm và tuple trả về vẫn giữ Contracts hiện có. `verificationKeys` dạng tuple là quyết định triển khai để đáp ứng tracker, không phải giao diện key-ID map được tracker mô tả. Nếu Package 2 cần key ID hoặc định dạng khác, thống nhất thay đổi rõ ràng thay vì tự giả định tuple hỗ trợ lookup theo key ID.

## Chưa thuộc phạm vi này

Không triển khai cơ chế đo sai số đồng hồ, tải/sync snapshot qua WAN, xác định khoảng trống từ stream cập nhật, giải mã QR, key rotation service, DB hoặc controller. Các quyết định về timestamp tương lai và phần lẻ mili giây được ghi để reviewer có thể đánh giá, không xem chúng là yêu cầu đã được report/tracker xác nhận.
