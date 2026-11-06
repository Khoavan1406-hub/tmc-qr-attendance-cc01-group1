# M3 Gate Intervention: Yêu cầu, Contracts và Quyết định Thiết kế (Package 6)

## 1. Yêu cầu Được Giao (Requirements & Scope)

Module **`M3 Gate Intervention`** chịu trách nhiệm điều phối các hành động can thiệp của nhân viên bảo vệ (Security Guard) tại cổng nhà máy theo **Flow B (§4.2.3)**, bao gồm hai nghiệp vụ chính:
1. **Mở cổng hỗ trợ (Assisted Access - Flow B3–B4):** Hỗ trợ công nhân không thể tự quét mã QR hợp lệ do điện thoại hết pin, vỡ màn hình, lỗi camera hoặc chưa kịp nhận mã.
2. **Xử lý sự cố khẩn cấp (Emergency Release & Reset - Table 4.22):** Mở tự do toàn bộ làn khi có báo cháy, sự cố an toàn hoặc sơ tán khẩn cấp, sau đó thiết lập lại trạng thái vận hành bình thường khi sự cố kết thúc.

### Đặc trưng Kiến trúc Cốt lõi
* **Không sở hữu cơ sở dữ liệu riêng:** Module M3 không lưu trữ dữ liệu độc lập mà ghi nhận các trường hợp can thiệp (`SupportCase`) vào tầng lưu trữ của Gate Core thông qua hợp đồng giao diện hẹp **`IInterventionRequest`** (**ISP – §7.1.4**).
* **Xác thực quyền hạn phía server (Server-side Scoped RBAC):** Thẩm quyền của bảo vệ được xác thực qua **`IAuthorizationApi`** (thuộc module M10 Identity & RBAC). Hệ thống từ chối mọi yêu cầu nếu bảo vệ không mang vai trò `SECURITY_GUARD` hoặc cố tình can thiệp tại nhà máy ngoài phạm vi được phân công.
* **Nguyên tắc No-loss & Tính toàn vẹn dữ liệu chấm công (ADR-07, I-04):** Mở cổng hỗ trợ hoặc mở khẩn cấp cơ học **tuyệt đối không tạo bản ghi chấm công tự động (`AttendanceTransaction`)**. Dữ liệu chấm công chỉ được ghi nhận sau khi phòng Nhân sự (HR) thẩm định hồ sơ hỗ trợ và đồng bộ qua Resolution Feed ở trung tâm.
* **Khóa làn khẩn cấp nghiêm ngặt (`EMERGENCY_HOLD`):** Làn khi mở khẩn cấp sẽ bị giữ ở trạng thái `EMERGENCY_HOLD`, phong tỏa mọi lượt quét QR mới tại Kiosk. Làn **chỉ được phép thoát khỏi chế độ này khi controller phần cứng gửi tín hiệu xác nhận `RESET_CONFIRMED`**.

---

## 2. Thiết kế Thành phần & Contracts

### A. Tầng Nghiệp vụ (`src/tmc_gate/application/intervention.py`)

* **`InterventionService`:**
  * `__init__(factory_id, intervention_port, auth_api, audit_api)`
  * `record_and_open_assisted(supportCaseId, guardId, employeeId, laneId, reason, linkedScanId=None, now=None) -> ScanResult`:
    1. Gọi `self._auth_api.is_authorized(guardId, "SECURITY_GUARD", "ASSISTED_OPEN", self._factory_id)`. Nếu không hợp lệ $\rightarrow$ trả về `ScanState.REJECTED` với lý do `GUARD_UNAUTHORIZED`.
    2. Tạo `SupportCase(isEmergency=False, occurredAt=...)`.
    3. Ghi nhận hồ sơ qua `self._intervention_port.recordIntervention(case)`.
    4. Gọi `self._intervention_port.requestAssistedOpen(...)` để mở cổng cho làn rỗi.
    5. Ghi nhật ký kiểm toán bất đồng bộ qua `self._audit_api.append_async(...)`.
  * `record_assisted_case(...) -> bool`: Tách riêng bước ghi nhận hồ sơ hỗ trợ (Flow B3–B4) mà chưa mở cổng.
  * `authorize_assisted_open(...) -> ScanResult`: Tách riêng bước phát lệnh mở cổng cho hồ sơ đã có.
  * `trigger_emergency_release(supportCaseId, guardId, laneId, reason, now=None) -> ScanResult`:
    1. Kiểm tra thẩm quyền bảo vệ với action `"EMERGENCY_RELEASE"`.
    2. Tạo `SupportCase(isEmergency=True)`.
    3. Ghi nhận case qua `recordIntervention`.
    4. Gọi `self._intervention_port.requestEmergencyRelease(...)` $\rightarrow$ Gate Core đưa chu kỳ vào `EMERGENCY_HOLD`.
  * `reset_emergency(supportCaseId, guardId, laneId, now=None) -> ScanResult`:
    1. Kiểm tra thẩm quyền bảo vệ với action `"EMERGENCY_RESET"`.
    2. Gọi `self._intervention_port.requestEmergencyReset(...)` $\rightarrow$ chỉ trả về `OPENED` khi controller xác nhận `RESET_CONFIRMED`.

* **`GateInterventionFacade` (Thực thi `GateInterventionApi` - Table 4.20):**
  * Đóng vai trò là Façade công khai duy nhất của M3.
  * Thực thi: `recordAssistedAccess`, `authorizeAssistedOpen`, `recordAssistedAccessAndOpen`, `emergencyRelease`, `resetEmergency`, `requestEmergencyReset`.

### B. Tầng Giao diện API (`src/tmc_gate/api/intervention_controller.py`)

* `POST /api/interventions/assisted-open`: Nhận `AssistedAccessRequest` $\rightarrow$ Trả về kết quả mở hỗ trợ.
* `POST /api/interventions/emergency-release`: Nhận `EmergencyActionRequest` $\rightarrow$ Kích hoạt `EMERGENCY_HOLD`.
* `POST /api/interventions/emergency-reset`: Nhận `EmergencyActionRequest` $\rightarrow$ Thực hiện reset giải tỏa.

---

## 3. Phối Hợp Liên Package (Cross-Package Interaction)

| Package | Thành phần phụ thuộc | Phương thức phối hợp |
| :--- | :--- | :--- |
| **Package 3 (Khoa Phạm)** | `IInterventionRequest` (`ports/inbound.py`) | Gọi `recordIntervention`, `requestAssistedOpen`, `requestEmergencyRelease`, `requestEmergencyReset`. Tái sử dụng `_build_fixture()` để test tích hợp. |
| **Package 4 (Khang)** | `IAuthorizationApi`, `IAuditApi` (`ports/repositories.py`) | Xác thực vai trò `SECURITY_GUARD` theo phạm vi nhà máy và ghi log kiểm toán bất biến. |
| **Package 5 (Khoa Văn)** | `IEmergencyGate` (`ports/turnstile.py`) | Đối chiếu kết quả phản hồi của controller: `RESET_CONFIRMED`, `CONTROLLER_ERROR`, `UNDETERMINED`. |

---

## 4. Giới Hạn Phạm Vi & Thuật Ngữ Chuẩn

* **Không gọi là E2E Flow B:** Trong module M3, việc xác thực quyền của nhân viên và mở cổng chạy logic thật, nhưng quy trình phê duyệt sau đó của phòng HR và feed đồng bộ giải quyết sai lệch ở trung tâm là giả lập. Do đó, các bài test của Package 6 được gọi chính xác là **Intervention Unit & Integration Tests**, không gọi là E2E.
* **Flow A E2E:** Chỉ luồng Flow A (quét QR Kiosk) mới được gọi là E2E khi chạy tích hợp với SQLite và `SimulatedTurnstileAdapter`.
