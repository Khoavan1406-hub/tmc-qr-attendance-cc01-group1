# TMC QR-based Time & Attendance and Access Control System (Milestone 2 – Task 2)

Repository hiện thực hóa **Task 2 (Code Implementation)** của môn **Kiến trúc Phần mềm (HK261 – HCMUT)** cho hệ thống chấm công và kiểm soát ra vào bằng mã QR tại công ty sản xuất **TMC** (hai nhà máy Bình Dương và Long An).

- **Report kiến trúc:** [Source LaTeX và hướng dẫn build](report/README.md) nằm trong `report/`, cùng repo với `src/` và `tests/` để đối chiếu thiết kế với code.
- **Hai module triển khai chính:** **`M1 Gate Core`** (Xử lý quét QR – Flow A) và **`M3 Gate Intervention`** (Bảo vệ can thiệp hỗ trợ & khẩn cấp – Flow B) chạy logic nghiệp vụ thật trên SQLite (`SqliteUnitOfWork` + repositories). Test đơn vị dùng fake repository; test tích hợp `tests/test_e2e_sqlite_flows.py` và `tests/test_submission_audit_regressions.py` chạy qua `create_app()` với file SQLite thật.
- **Trạng thái hiện tại:** Package 1–6 đã merge vào `dev`. Snapshot quyền truy cập được lưu trong SQLite cùng dấu chống trùng update, nên khởi động lại vẫn giữ quyền đã áp dụng. Một worker nền tự chuyển cycle quá 15 giây không có PASSAGE sang `NO_PASSAGE` / `PENDING_RECONCILIATION` (không gửi `closeGate`).
- **Giới hạn của prototype:** controller, WAN/Site Sync, HR/Payroll và identity/authorization là giả lập hoặc stub; test chức năng không thay cho đo hiệu năng (2 giây, 800 lượt/30 phút) hay đánh giá bảo mật.
- **Thành phần hỗ trợ demo:** Bộ cấp mã QR Ed25519 (một thành phần của `M4 QrCredentialIssuance`) và bộ mô phỏng cửa xoay `TurnstileAdapter` (`M2 Turnstile Integration`) chỉ kích hoạt ở chế độ demo (`demo_mode=True`).

---

## 1. Công nghệ Sử Dụng

- **Ngôn ngữ:** Python 3.12+ (hỗ trợ đầy đủ type annotations và pattern matching)
- **Web Framework & Demo UI:** FastAPI + Uvicorn (tương tác và demo trực tiếp trên Swagger UI tại `http://localhost:8000/docs`)
- **Cơ sở dữ liệu (Edge Persistence):** SQLite (`sqlite3` chuẩn Python), quản lý giao dịch nguyên tử theo mẫu **Unit of Work** (`IUnitOfWork`)
- **Chữ ký số & An toàn:** Thư viện `cryptography` (`Ed25519` — Central ký mã QR động sống 60 giây gắn `factoryId`; Factory giữ Public Key trong `EligibilitySnapshot.verificationKeys` để xác thực cục bộ không phụ thuộc WAN)
- **Kiểm thử tự động:** `pytest` + `httpx` (bao gồm bài test kiến trúc kiểm tra vi phạm AST tự động `test_architecture_solid.py`)

---

## 2. Phân Định Phạm Vi Triển Khai (Scope of Implementation)

Nhằm bám sát yêu cầu đề bài **Assignment M2** và các quyết định kiến trúc trong Báo cáo Milestone 2, phạm vi thực thi được phân định minh bạch như sau:

### A. Trong phạm vi triển khai thật (In-Scope)
1. **Module M1 Gate Core (Flow A – §4.2.2):**
   * Đánh giá điều kiện truy cập cục bộ (`AccessDecision`) dựa trên QR Ed25519 và `EligibilitySnapshot`.
   * Giao dịch nguyên tử 3 thành phần trước khi mở cổng (`ScanAttempt` ACCEPTED, `TokenReservation`, `GateCycle`).
   * Điều phối vòng đời cửa xoay qua cổng hẹp ISP (`IOpenGate`, `ICloseGate`, `IEmergencyGate`, `IPassageEvents`).
   * Ghi nhận giao dịch chấm công gốc (`AttendanceTransaction`) khi và chỉ khi nhận tín hiệu bước qua vật lý (`PassageEvent`).
   * Chế độ suy giảm giới hạn (Degraded mode) khi mất WAN: `NORMAL` ($\le 60s$), `DEGRADED` ($> 60s$ và $< 30m$), `CANNOT_PROCESS` ($\ge 30m$ hoặc sai số đồng hồ $\ge 2s$).
2. **Module M3 Gate Intervention (Flow B – §4.2.3):**
   * Xác định bảo vệ từ phiên đăng nhập (header `X-Guard-Session`, qua `IGuardIdentityApi`), không lấy `guardId` từ body; thiếu hoặc sai phiên trả HTTP 401.
   * Xác thực thẩm quyền bảo vệ phía server (`IAuthorizationApi` - Scoped RBAC theo nhà máy).
   * Mỗi `supportCaseId` chỉ ghi một lần: gửi lại cùng nội dung được xử lý như lần gửi lại, khác nội dung thì bị từ chối (`SUPPORT_CASE_NOT_RECORDED`) và giữ hồ sơ gốc.
   * Mỗi `supportCaseId` chỉ phát một lệnh mở hỗ trợ hoặc một lệnh mở khẩn cấp; gửi lại trả về lệnh cũ, kết quả chưa rõ thì hỏi trạng thái controller chứ không gửi lại lệnh.
   * Mở cổng hỗ trợ (Assisted Open) khi công nhân gặp sự cố (quên/hỏng điện thoại): Ghi nhận `SupportCase`, **tuyệt đối không tạo bản ghi chấm công tự động** (tuân thủ nguyên tắc I-04, ADR-07).
   * Mở khẩn cấp (Emergency Release): Đưa làn vào trạng thái `EMERGENCY_HOLD`, khóa mọi lượt quét QR mới tại Kiosk.
   * Reset khẩn cấp: Chỉ cho phép thoát `EMERGENCY_HOLD` khi controller phản hồi xác nhận `RESET_CONFIRMED`.

### B. Ngoài phạm vi triển khai / Giả lập phục vụ Demo (Out-of-Scope / Simulated)
* **Xác thực chấm công của HR:** Việc phê duyệt của HR đối với các trường hợp mở hỗ trợ và luồng giải quyết sai lệch (Resolution Feed) tại trung tâm là giả lập (**Flow B không gọi là E2E**).
* **Đăng nhập của bảo vệ:** Phiên đăng nhập được mô phỏng bằng `DemoGuardSessionStub` với hai token cố định (`demo-session-guard-bd-01`, `demo-session-guard-la-01`), chỉ bật khi `demo_mode=True`. Khi tắt demo, cần truyền identity provider qua `create_app(guard_identity=...)`; nếu không, các endpoint can thiệp trả HTTP 503. Xác thực thật thuộc hệ thống định danh trung tâm, nằm ngoài phạm vi M2.
* **Đồng bộ qua mạng WAN:** Module `M9 Site Sync` chỉ tiếp nhận thay đổi snapshot và đọc outbox cục bộ; không triển khai đường truyền mạng vật lý WAN.
* **Tính lương & Quản lý khách:** Quản lý khách vãng lai (Visitor Management) và tính toán bảng lương (Payroll Calculation) nằm ngoài phạm vi đồ án theo quy định của đề bài M2.

---

## 3. Cấu trúc Thư mục & 6 Gói Công Việc (Work Packages)

Mã nguồn được tổ chức theo kiến trúc **Ports & Adapters (Hexagonal / Clean Architecture)**:

```text
src/tmc_gate/
├── domain/          # Lõi thực thể & trạng thái (§4.2.5, §4.3). Độc lập 100%, không import bất kỳ tầng nào.
│   ├── enums.py     # ScanState, PassageStatus, ClosureStatus, OperatingMode, Direction...
│   ├── models.py    # Lane, ScanAttempt, TokenReservation, GateCycle, GateCommand, PassageEvent, AttendanceTransaction
│   └── snapshot.py  # EligibilitySnapshot (kiểm tra watermark 60s / 30m & quyền truy cập cục bộ)
├── ports/           # Hợp đồng giao tiếp (Interfaces) chuẩn ISP & DIP (§7.1.4, §7.1.5). Chỉ import domain/.
│   ├── inbound.py   # IScanSubmission, IInterventionRequest, IFactoryUpdates, GateCoreApi, GateInterventionApi
│   ├── turnstile.py # IOpenGate, ICloseGate, IEmergencyGate, IPassageEvents, IControllerStatusQuery, ITurnstileControl
│   └── repositories.py # IUnitOfWork, IScanRepository, IPassageWindowQuery, IAttendanceRepository, IOutboxRepository, ISnapshotRepository, IAuditApi, IAuthorizationApi...
├── application/     # Nghiệp vụ điều phối M1 Gate Core & M3 Gate Intervention. Chỉ import domain/ và ports/.
│   ├── access_decision.py # AccessDecision (SRP: kiểm tra QR Ed25519 + EligibilitySnapshot)
│   ├── processor.py       # GateTransactionProcessor (SRP: điều phối Flow A & Flow B, giữ trạng thái cổng)
│   ├── facade.py          # GateCoreFacade (điểm vào duy nhất của M1)
│   └── intervention.py    # InterventionService & GateInterventionFacade (M3 - điều phối can thiệp bảo vệ)
├── adapters/        # Hạ tầng & tích hợp ngoài. Thực thi các ports/.
│   ├── sqlite_repos.py       # SQLite repositories & SqliteUnitOfWork
│   ├── turnstile_adapter.py  # SimulatedTurnstileAdapter & FaultyTurnstileAdapter (minh họa LSP)
│   ├── demo_qr_issuer.py     # Bộ ký mã QR Ed25519 phục vụ demo
│   └── cross_cutting_stubs.py# Stubs bất đồng bộ cho AuditApi, HealthApi, AuthorizationApi
└── api/             # Tầng Entry (FastAPI Controllers & Swagger UI).
    ├── app.py                     # Khởi tạo FastAPI & Composition Root (DIP)
    ├── gate_controller.py         # GateCoreController & ControllerSignalHandler
    ├── intervention_controller.py # GateInterventionController
    └── demo_controller.py         # Endpoints hỗ trợ tạo QR 60s và giả lập môi trường mạng
```

### Phân công 6 Gói Công Việc (Work Packages)

| Gói | Nhánh Git | File Code & Test chính | Nội dung hiện thực hóa |
| :--- | :--- | :--- | :--- |
| **Package 1** | `feat/pkg1-snapshot-rules` | • `src/tmc_gate/domain/snapshot.py`<br>• `tests/test_pkg1_snapshot.py` | `EligibilitySnapshot`: lưu dữ liệu quyền cục bộ & `verificationKeys`; tính thời gian bằng mili-giây nguyên (`<=60_000 ms` `NORMAL`, `<1_800_000 ms` `DEGRADED`, `>=1_800_000 ms` `CANNOT_PROCESS`, sai số đồng hồ `<2_000 ms`). |
| **Package 2** | `feat/pkg2-qr-access-decision` | • `src/tmc_gate/adapters/demo_qr_issuer.py`<br>• `src/tmc_gate/application/access_decision.py`<br>• `tests/test_pkg2_access_decision.py` | `QrCredentialIssuance` (ký Ed25519 QR 60s gắn `factoryId`) và `AccessDecision.evaluate()` (xác thực chữ ký bằng `verificationKeys`, hạn 60s, phạm vi nhà máy và quyền nhân viên theo SRP). |
| **Package 3** | `feat/pkg3-gate-processor` | • `src/tmc_gate/application/processor.py`<br>• `src/tmc_gate/application/facade.py`<br>• `src/tmc_gate/api/gate_controller.py`<br>• `tests/test_pkg3_processor.py`<br>• `tests/test_flow_a_e2e.py` | Khóa Contracts (`domain/`, `ports/`); `GateTransactionProcessor` (Flow A1–A9 dùng `IUnitOfWork` & `IControllerStatusQuery`), `GateCoreFacade` và `GateCoreController`. |
| **Package 4** | `feat/pkg4-sqlite-persistence` | • `src/tmc_gate/adapters/sqlite_repos.py`<br>• `src/tmc_gate/adapters/cross_cutting_stubs.py`<br>• `tests/test_pkg4_sqlite_repos.py` | `SqliteUnitOfWork` (`IUnitOfWork`), `SqliteScanRepository` (claim nguyên tử), `SqliteAttendanceRepository` (`1 GateCycle -> 0..* PassageEvent`, tối đa 1 chấm công gốc/`scanId`), `SqliteOutboxRepository` (chống trùng `(consumer, eventId)`). |
| **Package 5** | `feat/pkg5-turnstile-adapter` | • `src/tmc_gate/adapters/turnstile_adapter.py`<br>• `src/tmc_gate/api/demo_controller.py`<br>• `tests/test_pkg5_turnstile_contract.py`<br>• `tests/test_pkg5_adapter_with_processor.py`<br>• `tests/test_pkg5_demo_controller.py` | `ControllerApiClient`, `SimulatedTurnstileAdapter` (chuẩn) & `FaultyTurnstileAdapter` (lỗi) chạy chung bộ contract test minh họa **LSP**, và `demo_controller` (bật theo cờ `demo_mode`). |
| **Package 6** | `feat/pkg6-intervention-tests` | • `src/tmc_gate/application/intervention.py`<br>• `src/tmc_gate/api/intervention_controller.py`<br>• `tests/test_pkg6_intervention.py` | `InterventionService` & `GateInterventionController` (`M3 Gate Intervention` – Flow B: mở hỗ trợ, `EMERGENCY_HOLD`, `requestEmergencyReset`), hoàn thiện `README.md` và cập nhật báo cáo. |

---

## 4. Bảng Ánh Xạ Nguyên Lý SOLID → File / Class / Interface

| Nguyên lý SOLID | File / Class / Interface trong Mã Nguồn | Cách áp dụng & Lợi ích kiến trúc đạt được |
| :--- | :--- | :--- |
| **S – Single Responsibility Principle (SRP)** | • `domain/snapshot.py` (`EligibilitySnapshot`)<br>• `application/access_decision.py` (`AccessDecision`)<br>• `application/processor.py` (`GateTransactionProcessor`)<br>• `application/intervention.py` (`InterventionService`)<br>• `adapters/sqlite_repos.py` (`SqliteUnitOfWork`, Repositories) | • Mỗi class chỉ có duy nhất một lý do để thay đổi:<br>  - `EligibilitySnapshot` chỉ giữ dữ liệu & phân loại operating mode.<br>  - `AccessDecision` chỉ đánh giá chữ ký số & quyền (không ghi đĩa hay mở cổng).<br>  - `GateTransactionProcessor` chỉ điều phối luồng cổng và trạng thái làn.<br>  - `InterventionService` chỉ điều phối can thiệp bảo vệ & ủy quyền server-side.<br>  - `SqliteUnitOfWork` chỉ quản lý ranh giới commit/rollback giao dịch. |
| **O – Open/Closed Principle (OCP)** | • `ports/turnstile.py` (`ITurnstileControl`, `IOpenGate`, `ICloseGate`, `IEmergencyGate`)<br>• `adapters/turnstile_adapter.py` (`SimulatedTurnstileAdapter`) | • Hệ thống mở rộng đón nhận các dòng phần cứng cửa xoay mới (như Flap Barrier, Swing Barrier, Tripod từ các hãng khác) chỉ bằng cách viết thêm Adapter mới thực thi `ITurnstileControl` mà không cần sửa đổi bất kỳ dòng code nghiệp vụ nào trong `GateTransactionProcessor`. |
| **L – Liskov Substitution Principle (LSP)** | • `ports/turnstile.py` (`ITurnstileControl`)<br>• `adapters/turnstile_adapter.py` (`SimulatedTurnstileAdapter`, `FaultyTurnstileAdapter`)<br>• `tests/test_pkg5_turnstile_contract.py` | • Mọi adapter thay thế cho `ITurnstileControl` phải tuân thủ đúng hợp đồng hành vi (§7.1.3):<br>  - Phân biệt rõ giữa đã nhận lệnh (`RECEIPT_ONLY`), xác nhận mở (`OPEN_CONFIRMED`), lỗi từ chối (`CONTROLLER_ERROR`) và bất định/timeout (`UNDETERMINED`).<br>  - Minh họa trực quan qua bộ test hợp đồng chạy song song trên adapter chuẩn và adapter lỗi mà không gây crash hệ thống. |
| **I – Interface Segregation Principle (ISP)** | • `ports/inbound.py` (`IScanSubmission`, `IInterventionRequest`, `IFactoryUpdates`)<br>• `ports/turnstile.py` (`IOpenGate`, `ICloseGate`, `IEmergencyGate`, `IPassageEvents`, `IControllerStatusQuery`)<br>• `ports/repositories.py` (`IPassageWindowQuery` tách khỏi `IScanRepository`) | • Phân tách giao diện theo đúng vai trò tác nhân (§7.1.4):<br>  - Kiosk Kiosk (`StationClient`) chỉ thấy `IScanSubmission`, không thể kích hoạt lệnh khẩn cấp của bảo vệ.<br>  - Module `M3 Gate Intervention` chỉ dùng `IInterventionRequest`.<br>  - Tách bạch lệnh gửi đi (`IOpenGate`...) và tín hiệu cảm biến nhận về (`IPassageEvents`: chỉ có `onPassage` và `onLaneClear`).<br>  - Worker timeout 15 giây chỉ dùng port đọc `IPassageWindowQuery` (`list_cycles_awaiting_passage`, `get_cycle_by_scan`), không phụ thuộc toàn bộ `IScanRepository`. |
| **D – Dependency Inversion Principle (DIP)** | • `src/tmc_gate/application/*`<br>• `src/tmc_gate/ports/*`<br>• `src/tmc_gate/api/app.py`<br>• `tests/test_architecture_solid.py` | • Tầng nghiệp vụ (`application/`) chỉ phụ thuộc vào trừu tượng (`domain/` và `ports/`), hoàn toàn không import `sqlite3`, `fastapi`, hay các class trong `adapters/`.<br>• Toàn bộ phụ thuộc được lắp ghép tại điểm khởi tạo duy nhất (`app.py` - Composition Root). Kiểm tra tự động bằng bài test AST (`test_architecture_solid.py`). |

---

## 5. Hướng Dẫn Cài Đặt & Chạy Ứng Dụng

### Bước 1: Khởi tạo môi trường ảo và cài đặt thư viện
```bash
python -m venv .venv
# Trên Linux/macOS:
source .venv/bin/activate
# Trên Windows PowerShell:
.venv\Scripts\Activate.ps1
# Trên Windows Git Bash:
source .venv/Scripts/activate

pip install -r requirements.txt
```

### Bước 2: Chạy bộ kiểm thử tự động
```bash
# Chạy toàn bộ test suite (bao gồm bài test kiến trúc SOLID và các test packages)
pytest -v

# Chạy riêng bài kiểm tra kiến trúc SOLID (AST analysis)
pytest -v tests/test_architecture_solid.py

# Chạy riêng kiểm thử Flow B Gate Intervention (Package 6)
pytest -v tests/test_pkg6_intervention.py tests/test_pkg6_pr8_regressions.py tests/test_pkg6_pr8_followup.py
```

### Bước 3: Khởi động server FastAPI & Mở Swagger UI
```bash
# Dữ liệu trong bộ nhớ (mất khi tắt server)
uvicorn tmc_gate.api.app:app --app-dir src

# Lưu vào file SQLite để demo khởi động lại vẫn giữ dữ liệu
# Linux/macOS/Git Bash:
TMC_GATE_DB=factory.sqlite3 uvicorn tmc_gate.api.app:app --app-dir src
# Windows PowerShell:
$env:TMC_GATE_DB="factory.sqlite3"; uvicorn tmc_gate.api.app:app --app-dir src
```
Không dùng `--reload` khi demo với file: chế độ reload khởi động lại tiến trình mỗi khi sửa code. Khi mở lại một file cũ, snapshot đã lưu có watermark cũ nên có thể rơi vào `CANNOT_PROCESS`; gọi `POST /api/demo/watermark-age` với `watermarkAgeSeconds=10` để làm mới.
Truy cập giao diện tương tác Swagger UI tại: **`http://localhost:8000/docs`**

---

## 6. Kịch Bản Demo Trực Tiếp Trên Swagger UI (`/docs`)

### Kịch bản 1: Luồng Quét QR Giờ Cao Điểm Thông Thường (Flow A)
1. **Tạo mã QR hợp lệ:** Gọi `POST /api/demo/qr-tokens` với `employeeId="EMP-001"`, `factoryId="BD"`. Sao chép chuỗi `qrToken` nhận được.
2. **Quét tại Kiosk:** Gọi `POST /api/gate/scans` với `scanId="scan-001"`, `qrToken` vừa tạo, `laneId="LANE-BD-01"`.
   * *Kết quả:* Nhận `state: "OPENED"` và chuỗi `commandId` (ví dụ `cmd-open-...`).
3. **Mô phỏng bước qua:** Gọi `POST /api/controller/signals/passage` với `commandId` trên, `laneId="LANE-BD-01"`, `passageEventId="pas-001"`.
   * *Kết quả:* Hệ thống ghi nhận 1 bản ghi `AttendanceTransaction` cho `EMP-001`.
4. **Mô phỏng công nhân rời làn:** Gọi `POST /api/controller/signals/lane-clear` với `commandId` trên.
   * *Kết quả:* Cổng gửi lệnh `closeGate` và giải phóng làn trở về trạng thái sẵn sàng.

### Kịch bản 2: Mô Phỏng Mất Mạng WAN & Chế Độ Suy Giảm (Degraded Mode)
1. **Thiết lập chế độ Degraded (Watermark cũ 5 phút):** Gọi `POST /api/demo/watermark-age` với `watermarkAgeSeconds=300`.
2. **Quét QR:** Gọi `POST /api/gate/scans`.
   * *Kết quả:* Vẫn mở cổng `state: "OPENED"`, nhưng cờ `degradedFlag: true` được gắn vào giao dịch chấm công để đối soát sau.
3. **Thiết lập chế độ Quá hạn (Watermark cũ 45 phút > 30 phút):** Gọi `POST /api/demo/watermark-age` với `watermarkAgeSeconds=2700`.
4. **Quét QR:** Gọi `POST /api/gate/scans`.
   * *Kết quả:* Bị từ chối với `state: "CANNOT_PROCESS"`, `reason: "ELIGIBILITY_DATA_UNUSABLE_OR_EXPIRED"` theo đúng luật an toàn.

### Kịch bản 3: Can Thiệp Hỗ Trợ Của Bảo Vệ (Flow B – Assisted Open)
Mọi endpoint `/api/interventions/*` cần header `X-Guard-Session` (phiên đăng nhập mô phỏng). Dùng `demo-session-guard-bd-01` cho bảo vệ nhà máy BD.

1. **Bảo vệ mở hỗ trợ:** Gọi `POST /api/interventions/assisted-open` với header `X-Guard-Session: demo-session-guard-bd-01` và body:
   ```json
   {
     "supportCaseId": "case-demo-01",
     "employeeId": "EMP-001",
     "laneId": "LANE-BD-01",
     "reason": "Employee smartphone battery depleted"
   }
   ```
   * *Kết quả:* Trả về `state: "OPENED"`, lưu `SupportCase`.
   * *Điểm cốt lõi:* Kiểm tra danh sách chấm công thấy **hoàn toàn không phát sinh bản ghi chấm công tự động** (tuân thủ ADR-07 & I-04; chờ HR duyệt).
2. **Chưa đăng nhập:** Gửi lại không có header `X-Guard-Session` (hoặc token lạ).
   * *Kết quả:* HTTP 401 `GUARD_NOT_AUTHENTICATED`; không ghi hồ sơ, không mở cổng. `guardId` trong body bị bỏ qua.
3. **Bảo vệ nhà máy khác:** Dùng `X-Guard-Session: demo-session-guard-la-01` tại nhà máy BD.
   * *Kết quả:* Bị từ chối với `state: "REJECTED"`, `reason: "GUARD_UNAUTHORIZED"`.

### Kịch bản 4: Mở Khẩn Cấp & Thoát Khóa An Toàn (Flow B – Emergency Hold & Reset)
1. **Kích hoạt khẩn cấp:** Gọi `POST /api/interventions/emergency-release` (header `X-Guard-Session: demo-session-guard-bd-01`) với `supportCaseId="emg-01"`, `laneId="LANE-BD-01"`, `reason="Fire alarm evacuation"`.
   * *Kết quả:* Cổng mở tự do, làn được đưa vào `EMERGENCY_HOLD`.
2. **Kiểm tra chặn quét mới:** Thử gọi `POST /api/gate/scans` tại `LANE-BD-01`.
   * *Kết quả:* Bị từ chối với `state: "LANE_BUSY"`, `reason: "LANE_HAS_ACTIVE_OR_UNRESOLVED_CYCLE"`. Mọi lượt quét mới bị phong tỏa.
3. **Reset khẩn cấp:** Gọi `POST /api/interventions/emergency-reset` (cùng header) với `supportCaseId="emg-01"`, `laneId="LANE-BD-01"`.
   * *Kết quả:* Khi controller xác nhận `RESET_CONFIRMED`, làn chuyển về `OPENED` và khi có tín hiệu `lane-clear` sẽ giải phóng làn về trạng thái hoạt động bình thường.
