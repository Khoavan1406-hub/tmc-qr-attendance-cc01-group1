# Kiểm chứng Package 1

Kết quả ghi nhận ngày **05/11/2026** cho code `7b55b5d`, trên Python **3.12.12**. Đây là kết quả tại thời điểm bàn giao; khi repo thay đổi, chạy lại thay vì coi số lượng test là cố định.

## Chạy lại

Chạy ở thư mục gốc repo. Nếu chưa có virtualenv, tạo bằng Python 3.12:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Chạy Package 1 hoặc toàn repo:

```bash
.venv/bin/python -m pytest tests/test_pkg1_snapshot.py
.venv/bin/python -m pytest
```

Coverage và Ruff là công cụ dev bổ sung, chưa nằm trong `requirements.txt`; cài riêng nếu cần:

```bash
.venv/bin/python -m pip install pytest-cov ruff
.venv/bin/python -m pytest tests/test_pkg1_snapshot.py -q -o addopts= --cov=tmc_gate.domain.snapshot --cov-branch --cov-report=term-missing --cov-fail-under=100
.venv/bin/ruff check src/tmc_gate/domain/snapshot.py tests/test_pkg1_snapshot.py
.venv/bin/ruff format --check src/tmc_gate/domain/snapshot.py tests/test_pkg1_snapshot.py
git diff --check
```

## Kết quả unit test đã ghi nhận

| Phạm vi | Kết quả |
| --- | --- |
| Package 1 | 146 pass, 0 skip |
| Coverage `snapshot.py` | 100% statement và branch: 84 statements, 36 branches |
| Toàn repo trên nhánh Brian | 154 pass, 8 skip |
| Architecture và processor tests | Pass |
| Ruff check / format / diff whitespace | Pass |
| GitHub Actions cho commit triển khai | Pass |
| Khởi động FastAPI và truy cập `/docs`, `/openapi.json` | HTTP 200 |

8 test skip thuộc TODO của Package 2/4/5/6, mỗi package 2 test. Không diễn giải chúng là tính năng đã chạy hoặc đã pass. Có một deprecation warning FastAPI/Starlette về `httpx` trong test client, đã xuất hiện trước khi triển khai Package 1.

## Các nhóm edge case đã có trong test được commit

- Tuổi watermark `0`, `59_999`, `60_000`, `60_001`, `1_799_999`, `1_800_000`, `1_800_001 ms` và dữ liệu cũ nhiều ngày.
- Lặp các mốc trên với clock bound hợp lệ `0`, `0.5`, `1`, `1.999 s` để tránh vô tình cộng sai số vào tuổi dữ liệu.
- Bound `2`, `2.001 s`, `None`, âm, NaN, vô cực, boolean và chuỗi; bound không hợp lệ phải chặn cả dữ liệu còn mới lẫn degraded.
- Làm tròn bound lên và hồi quy lỗi khi Decimal context bên ngoài có precision thấp.
- Snapshot không đầy đủ/liên tục, cờ không phải boolean `True`, phiên bản 0/âm/sai kiểu; phục hồi sau khi cả clock bound và continuity đều hợp lệ.
- Phần dư dưới mili giây, watermark tương lai dù chỉ `1 µs`, thiếu timezone hoặc timestamp, timezone khác nhau, giờ lặp DST và tràn phạm vi đổi UTC.
- Thiếu nhân viên/làn, inactive, sai nhà máy/làn, quyền rỗng, phân biệt hoa/thường, thứ tự lý do từ chối, dictionary gắn quyền dưới ID khác.
- ENTRY/EXIT cùng dùng quy tắc quyền; kiểm quyền độc lập với freshness.
- Tương thích một hoặc nhiều public key, key xung đột, không có key, collection mặc định độc lập và bộ key không alias list đầu vào.
- Gọi kiểm tra nhiều lần không mutate snapshot; thay snapshot bằng `replace` giữ phiên bản cũ không đổi.

## Kiểm chứng bổ sung đã thực hiện ngoài pytest

Các lượt dưới chạy bằng script tạm để audit, **không được commit và không tự chạy trong CI**. Unit test ở trên là bộ kiểm chứng tái lập được từ repo. Không mô tả các lượt tạm như test suite có sẵn.

| Kiểm chứng | Kết quả ghi nhận |
| --- | --- |
| Duyệt từng tuổi nguyên từ `0` đến `1_800_001 ms` | 1.800.002 trường hợp đúng quy tắc |
| Bound `0..2001 ms` kết hợp 5 mốc tuổi quan trọng | 10.010 trường hợp pass |
| Tổ hợp làn/nhân viên tồn tại, active, nhà máy, quyền làn | 48 tổ hợp pass |
| Chèn 13 lỗi logic trên bản sao thư mục tạm | Test phát hiện đủ 13 lỗi |

13 biến thể lỗi gồm đổi sai dấu so sánh ở ba ngưỡng; bỏ kiểm continuity/version; cộng clock bound vào tuổi; cho watermark tương lai qua; bỏ kiểm active/factory/lane; đổi nghĩa quyền làn rỗng; dùng quyền dưới ID khác; làm tròn clock bound xuống. Mỗi biến thể được chạy độc lập với pytest; source trong working tree không bị sửa bởi lượt kiểm này.

Các kết quả này bổ sung bằng chứng cho logic hiện tại; coverage 100% và phát hiện đủ các biến thể đã chọn không chứng minh mọi lỗi có thể xảy ra đều đã được loại trừ.
