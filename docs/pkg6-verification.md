# Kiểm chứng Package 6 — Bửu

Tài liệu ghi nhận kết quả kiểm chứng cho **Package 6 — Gate Intervention (M3 Flow B)** tại mốc ngày **06/11/2026**.

---

## 1. Hướng Dẫn Tái Lập Kết Quả (Reproducibility)

Chạy từ thư mục gốc của repository (`tmc-qr-attendance-cc01-group1`):

```bash
# Kích hoạt môi trường ảo
source .venv/bin/activate       # Linux/macOS
.venv\Scripts\Activate.ps1      # Windows PowerShell

# Chạy riêng bộ kiểm thử Package 6
pytest -v tests/test_pkg6_intervention.py

# Chạy bài kiểm tra kiến trúc SOLID (AST analysis)
pytest -v tests/test_architecture_solid.py

# Chạy toàn bộ test suite của dự án
pytest -v
```

---

## 2. Kết Quả Kiểm Thử Đã Ghi Nhận

| Phạm vi kiểm thử | Số ca test | Kết quả | Ghi chú |
| :--- | :---: | :---: | :--- |
| **Package 6 (M3 Intervention)** | 4 | **4 passed (100%)** | Bao phủ trọn vẹn Flow B, Scoped RBAC, và `EMERGENCY_HOLD` |
| **Architectural Fitness (SOLID)** | 4 | **4 passed (100%)** | Xác nhận không có vi phạm phụ thuộc kiến trúc AST |
| **Flow A E2E (Kiosk Lifecycle)** | 1 | **1 passed (100%)** | Luồng quét QR thông thường chạy thông suốt |
| **Package 1 (Snapshot Rules)** | 146 | **146 passed (100%)** | Biên thời gian mili-giây và phân loại chế độ hoạt động |
| **Package 3 (Gate Processor)** | 4 | **4 passed (100%)** | Vòng đời giao dịch nguyên tử và điều phối cổng |
| **Toàn bộ Repository** | 165 | **159 passed, 6 skipped** | 6 test skip thuộc TODO của Pkg 2, 4, 5 đang chờ merge |

---

## 3. Các Nhóm Ca Kiểm Thử Cốt Lõi Trong `test_pkg6_intervention.py`

### Ca 1: `test_assisted_access_requires_guard_authorization_and_creates_no_automatic_attendance`
* **Kiểm tra thẩm quyền bảo vệ (Scoped RBAC):**
  * Bảo vệ không có trong hệ thống (`UNKNOWN-GUARD`) bị từ chối với lý do `GUARD_UNAUTHORIZED`.
  * Bảo vệ thuộc nhà máy Long An (`GUARD-LA-01`) thao tác tại nhà máy Bình Dương (`BD`) bị từ chối với lý do `GUARD_UNAUTHORIZED`.
* **Ghi nhận hồ sơ hỗ trợ:**
  * Bảo vệ hợp lệ tại Bình Dương (`GUARD-BD-01`) thực hiện mở cổng hỗ trợ thành công (`state=OPENED`).
  * Hồ sơ `SupportCase` được lưu đầy đủ vào Gate Core với lý do và mã nhân viên.
* **Kiểm chứng nguyên tắc bất di bất dịch ADR-07 / I-04:**
  * Mở cổng hỗ trợ **hoàn toàn không tạo bản ghi chấm công** (`len(attendance_repo.list_transactions()) == 0`).
  * Khi cảm biến gửi tiếp tín hiệu bước qua (`onPassage`), hệ thống **vẫn không tạo bản ghi chấm công** (chờ HR thẩm định ở trung tâm).
* **Kiểm thử trên tầng API Controller:**
  * Endpoint `POST /api/interventions/assisted-open` hoạt động đồng nhất qua HTTP client.

### Ca 2: `test_emergency_release_hold_blocks_scans_and_unconfirmed_reset_stays_in_hold`
* **Kích hoạt khẩn cấp an toàn:**
  * Yêu cầu mở khẩn cấp từ kẻ lạ bị từ chối `GUARD_UNAUTHORIZED`.
  * Bảo vệ hợp lệ kích hoạt khẩn cấp $\rightarrow$ Chu kỳ làn chuyển sang `ClosureStatus.EMERGENCY_HOLD`, hồ sơ `SupportCase(isEmergency=True)` được lưu trữ.
* **Phong tỏa quét mã mới:**
  * Khi làn ở `EMERGENCY_HOLD`, Kiosk gửi yêu cầu quét mã QR bị từ chối ngay với `state=LANE_BUSY` và `reason="LANE_HAS_ACTIVE_OR_UNRESOLVED_CYCLE"`.
  * Tín hiệu `LANE_CLEAR` từ cảm biến đến trong lúc khẩn cấp bị bỏ qua, không giải phóng làn.
* **Cơ chế thoát hold nghiêm ngặt:**
  * Yêu cầu reset từ bảo vệ lạ bị từ chối.
  * Khi controller trả về lỗi (`CONTROLLER_ERROR`) hoặc bất định (`UNDETERMINED`) $\rightarrow$ làn **tiếp tục ở lại `EMERGENCY_HOLD`**, scan mới vẫn bị chặn.
  * Chỉ khi controller trả về xác nhận `RESET_CONFIRMED` $\rightarrow$ làn mới chuyển sang `OPENED`. Sau khi nhận tín hiệu `LANE_CLEAR`, làn được giải phóng hoàn toàn và trở lại trạng thái đón lượt quét mới.

### Ca 3: `test_assisted_access_facade_separate_record_and_authorize`
* Xác minh 2 phương thức phân tách trong `GateInterventionFacade`:
  * `recordAssistedAccess`: Ghi nhận hồ sơ mà không mở cổng (Flow B3–B4).
  * `authorizeAssistedOpen`: Phát lệnh mở cổng cho hồ sơ hỗ trợ đã được lưu trước đó.

### Ca 4: `test_emergency_reset_on_normal_lane_without_hold_is_rejected`
* Gửi yêu cầu reset khẩn cấp trên một làn đang rỗi (không ở trạng thái `EMERGENCY_HOLD`) sẽ bị từ chối với lý do `LANE_NOT_IN_EMERGENCY_HOLD`.
