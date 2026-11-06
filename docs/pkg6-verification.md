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
pytest -v tests/test_pkg6_intervention.py tests/test_pkg6_pr8_regressions.py tests/test_pkg6_pr8_followup.py

# Chạy bài kiểm tra kiến trúc SOLID (AST analysis)
pytest -v tests/test_architecture_solid.py

# Chạy toàn bộ test suite của dự án
pytest -v
```

---

## 2. Kết Quả Kiểm Thử Đã Ghi Nhận

Chạy trên nhánh `feat/pkg6-intervention-tests` sau khi gộp `dev` (PR1 + PR2), Python 3.13.5. Tất cả test dưới đây dùng fake repository và fake controller; hành vi với SQLite thật chưa được kiểm chứng (chờ Package 4).

| Phạm vi kiểm thử | Số ca test | Kết quả | Ghi chú |
| :--- | :---: | :---: | :--- |
| **Package 6 (M3 Intervention)** | 4 | 4 passed | Các nhánh chính của Flow B: phân quyền theo nhà máy, mở hỗ trợ không tạo chấm công, `EMERGENCY_HOLD` và reset. HR duyệt và Resolution Feed là giả lập |
| **Package 6 – hồi quy review PR #8** | 15 | 15 passed | Phiên đăng nhập bảo vệ qua HTTP, hồ sơ không lưu được, `supportCaseId` dùng lại |
| **Package 6 – review PR #8 vòng 2** | 8 | 8 passed | Transaction được đóng ở nhánh chỉ đọc, phiên demo chỉ nhận khi `demo_mode`, mỗi `supportCaseId` chỉ một lệnh `emergencyRelease` |
| **Architectural Fitness (SOLID)** | 4 | 4 passed | Kiểm tra phụ thuộc giữa các tầng bằng AST |
| **Flow A qua HTTP (fake ports)** | 1 | 1 passed | Chưa phải E2E với SQLite và adapter mô phỏng |
| **Package 1 (Snapshot Rules)** | 146 | 146 passed | Biên thời gian mili-giây và phân loại chế độ hoạt động |
| **Package 3 (Gate Processor)** | 37 | 37 passed | `test_pkg3_processor`, `test_pkg3_rules`, hai file hồi quy PR2 |
| **Toàn bộ Repository** | 221 | **215 passed, 6 skipped** | 6 test skip thuộc TODO của Package 2, 4, 5 |

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
  * Không có header `X-Guard-Session` thì trả HTTP 401, kể cả khi body ghi `guardId` hợp lệ.
  * Phiên của bảo vệ Long An gọi tại Bình Dương bị từ chối `GUARD_UNAUTHORIZED`; phiên bảo vệ Bình Dương mở được cổng.

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

### Ca bổ sung: `test_pkg6_pr8_regressions.py` (review PR #8)
* Ba endpoint `/api/interventions/*` từ chối request không có phiên đăng nhập; `guardId` trong body bị bỏ qua.
* Khi Gate Core không lưu được `SupportCase` (`recordIntervention` trả `False`), M3 trả `SUPPORT_CASE_NOT_RECORDED` và không gửi lệnh mở hay mở khẩn cấp.
* Dùng lại `supportCaseId` với nội dung khác (đổi nhân viên, hoặc đổi từ mở hỗ trợ sang khẩn cấp) bị từ chối và hồ sơ gốc giữ nguyên; gửi lại đúng nội dung thì trả về lệnh cũ, không mở cổng lần hai.
* M3 dùng timeout mở hỗ trợ 3.000 ms và gọi `requestEmergencyReset`; reset chưa xác nhận vẫn giữ `EMERGENCY_HOLD`.

### Ca bổ sung: `test_pkg6_pr8_followup.py` (review PR #8 vòng 2)
* Gửi lại hồ sơ (cùng hoặc khác nội dung) đóng transaction trước khi trả kết quả; kiểm tra bằng `BEGIN IMMEDIATE` của SQLite. Fake UoW trong `test_pkg3_processor.py` cũng báo lỗi nếu một transaction còn mở khi bắt đầu transaction mới.
* Token demo cố định chỉ được nhận khi `demo_mode=True`; ngoài demo, app dùng identity provider được truyền vào, nếu không có thì trả HTTP 503.
* Gửi lại cùng hồ sơ khẩn cấp trả về `commandId` cũ và không gọi controller lần hai; nếu lần đầu `UNDETERMINED` thì hỏi trạng thái một lần thay vì gửi lại lệnh.
