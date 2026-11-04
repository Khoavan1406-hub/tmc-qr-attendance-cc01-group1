# ARCHITECTURE RULES & CONTRACTS (TMC Gate Core – Milestone 2)

Tài liệu này khóa cứng các quy tắc kiến trúc từ báo cáo **Assignment M2 (`main.pdf`)** để toàn bộ thành viên trong nhóm (và các công cụ AI hỗ trợ code) tuân thủ tuyệt đối khi hiện thực hóa **Task 2**.

---

## 1. Cấu trúc thư mục chuẩn (Ports & Adapters / Clean Architecture)

```text
src/tmc_gate/
├── domain/          # Lõi thực thể & trạng thái (§4.2.5, §4.3). KHÔNG import bất kỳ tầng nào khác.
│   ├── enums.py     # ScanState, PassageStatus, ClosureStatus, OperatingMode, Direction...
│   ├── models.py    # Lane, ScanAttempt, TokenReservation, GateCycle, GateCommand, PassageEvent, AttendanceTransaction
│   └── snapshot.py  # EligibilitySnapshot (kiểm tra watermark 60s / 30m & quyền truy cập cục bộ)
├── ports/           # Hợp đồng giao tiếp (Interfaces/Ports) chuẩn ISP & DIP (§7.1.4, §7.1.5). Chỉ import domain/.
│   ├── inbound.py   # GateCoreApi tách thành: IScanSubmission, IInterventionRequest, IFactoryUpdates
│   ├── turnstile.py # ITurnstileControl tách thành: IOpenGate, ICloseGate, IEmergencyGate, IPassageEvents
│   └── repositories.py # IScanRepository, IAttendanceRepository, IOutboxRepository, ISnapshotRepository, IAuditApi...
├── application/     # Tầng nghiệp vụ của M1 Gate Core & M3 Gate Intervention (§4.1.6). Chỉ import domain/ và ports/.
│   ├── access_decision.py # AccessDecision (SRP: chỉ đánh giá QR Ed25519 + EligibilitySnapshot, không gọi cửa xoay)
│   ├── processor.py       # GateTransactionProcessor (SRP: điều phối Flow A & Flow B, giữ trạng thái cổng)
│   ├── facade.py          # GateCoreFacade (thực thi GateCoreApi, điểm vào duy nhất của tầng Business M1)
│   └── intervention.py    # InterventionService & GateInterventionFacade (M3 Gate Intervention - không có DB riêng)
├── adapters/        # Tầng hạ tầng & tích hợp ngoài (§4.1.6, §5.6). Thực thi các interfaces trong ports/.
│   ├── sqlite_repos.py       # SQLite repositories (đảm bảo giao dịch nguyên tử cho ACCEPTED + TokenReservation + GateCycle)
│   ├── turnstile_adapter.py  # TurnstileAdapter & ControllerApiClient (M2 Turnstile Integration - tuân thủ LSP)
│   ├── demo_qr_issuer.py     # Bộ ký mã QR Ed25519 (M4 QrCredentialIssuance) phục vụ demo
│   └── cross_cutting_stubs.py# Stub bất đồng bộ cho AuditApi, HealthApi, AuthorizationApi
└── api/             # Tầng Entry (FastAPI Controllers & Swagger UI tại /docs).
    ├── app.py                     # Khởi tạo FastAPI & lắp ghép Dependency Injection
    ├── gate_controller.py         # GateCoreController & ControllerSignalHandler
    ├── intervention_controller.py # GateInterventionController
    └── demo_controller.py         # Các endpoint hỗ trợ tạo QR 60s và giả lập mất mạng WAN trên Swagger
```

---

## 2. Bảy Luật Kiến Trúc Bất Di Bất Dịch (Mandatory Architectural Rules)

1. **Luật Chiều Phụ Thuộc (Dependency Rule & DIP – §7.1.5):**
   - `domain/` không được import từ `ports/`, `application/`, `adapters/`, hay `api/`.
   - `ports/` chỉ được import từ `domain/`.
   - `application/` chỉ được import từ `domain/` và `ports/`. **Cấm tuyệt đối** `application/` import trực tiếp `sqlite3`, `fastapi`, hoặc bất kỳ class nào trong `adapters/` hay `api/`.
   - Bài test tự động `tests/test_architecture_solid.py` sẽ quét AST toàn bộ mã nguồn và đánh trượt CI nếu vi phạm.

2. **Luật Phân Tách Giao Diện (ISP – §7.1.4):**
   - `GateCoreApi` được tách thành 3 interface hẹp:
     - `IScanSubmission` (`submit_scan`, `get_scan_status`) → dành cho `StationClient` (Kiosk).
     - `IInterventionRequest` (`record_intervention`, `request_assisted_open`, `request_emergency_release`, `reset_emergency`) → dành cho `M3 Gate Intervention`.
     - `IFactoryUpdates` (`apply_eligibility_changes`, `apply_scan_resolution`, `get_committed_events`) → dành cho `M9 Site Sync`.
   - `ITurnstileControl` được tách thành 4 port hẹp: `IOpenGate`, `ICloseGate`, `IEmergencyGate`, `IPassageEvents`.

3. **Luật Ghi Bền Vững Trước Khi Mở Cổng (`ADR-02` – §6.2.2):**
   - Trước khi gọi `IOpenGate.open_gate()`, `GateTransactionProcessor` bắt buộc phải thực hiện **1 giao dịch nguyên tử (atomic write)** lưu đồng thời:
     1. `ScanAttempt` ở trạng thái `ACCEPTED`
     2. `TokenReservation` (claim `tokenId` duy nhất tại nhà máy)
     3. `GateCycle` mới cho làn (`lane_id`)
   - Chỉ khi nhận sự kiện `PASSAGE` (`PassageEvent`) mới được tạo `AttendanceTransaction` (tối đa 1 bản ghi gốc cho mỗi `scan_id` và mỗi `passage_event_id` chỉ nhận 1 lần).

4. **Luật Làn Bận Không Làm Mất Mã QR (Flow A4 – §4.2.2):**
   - Một `Lane` tại một thời điểm chỉ có **tối đa 1 `GateCycle` đang hoạt động hoặc chưa giải quyết xong**.
   - Nếu làn đang bận, trả về `LANE_BUSY` ngay lập tức, **không** claim `tokenId` và **không** gửi lệnh `open_gate()`.

5. **Luật Cấm Gửi Lại Lệnh Mở Cổng Mù Quáng (`I-03` & LSP – §7.1.3):**
   - `TurnstileAdapter` phải phân biệt rõ 3 kết quả: đã nhận lệnh (`RECEIPT_ONLY`), xác nhận đã mở (`OPEN_CONFIRMED`), lỗi từ chối thực thi (`CONTROLLER_ERROR`), và không xác định do timeout/mất kết nối (`UNDETERMINED`).
   - Khi gặp `UNDETERMINED`, giữ nguyên khóa làn để kiểm tra/đối soát, **tuyệt đối không tự động gọi lại `open_gate()`**.

6. **Luật Chế Độ Suy Giảm Có Giới Hạn (`I-10` & `ADR-06` – §5.7.2):**
   - Dựa trên tuổi của `confirmed_watermark` trong `EligibilitySnapshot` (và sai số đồng hồ `<= 2 giây`):
     - `<= 60 giây`: Chế độ `NORMAL`.
     - `> 60 giây` và `<= 30 phút (1800 giây)`: Chế độ `DEGRADED` (vẫn cho phép quét QR hợp lệ nhưng gắn `degraded_flag = True` vào `ScanAttempt` và `AttendanceTransaction`).
     - `> 30 phút` hoặc sai số đồng hồ `> 2 giây` hoặc dữ liệu thiếu tính liên tục: Trả về `CANNOT_PROCESS`.

7. **Luật Tách Biệt Trạng Thái Bước Qua và Đóng Cổng (§4.2.5 – Table 4.23b):**
   - Trong `GateCycle`, `passage_status` (`WAITING`, `PASSED`, `NO_PASSAGE`) và `closure_status` (`OPENED`, `CLEAR_TO_CLOSE`, `CLOSE_SENT`, `CLOSED`, `CLOSE_FAILED`, `CLOSE_UNCERTAIN`, `EMERGENCY_HOLD`) chạy độc lập.
   - Bộ đếm 15 giây chỉ dùng để đánh dấu thiếu sự kiện bước qua (`NO_PASSAGE` → `PENDING_RECONCILIATION`), **tuyệt đối không dùng timer 15 giây để tự động đóng cổng**. Cổng chỉ gửi `close_gate()` sau khi nhận tín hiệu `LANE_CLEAR`.
