# TMC QR-based Time & Attendance and Access Control System (Milestone 2 – Task 2)

Repository hiện thực hóa **Task 2 (Code Implementation)** của môn **Kiến trúc Phần mềm (HK261 – HCMUT)** cho hệ thống chấm công và kiểm soát ra vào bằng mã QR tại công ty sản xuất **TMC**.

- **Hai module triển khai chính:** **`M1 Gate Core`** và **`M3 Gate Intervention`** (chạy logic thật; việc phê duyệt của HR và Resolution Feed ở trung tâm là giả lập).
- **Thành phần hỗ trợ demo:** Bộ cấp mã QR Ed25519 (một thành phần của `M4`) và bộ mô phỏng cửa xoay `TurnstileAdapter` (`M2`) chỉ bật ở chế độ demo (`demo_mode=True`).

---

## 1. Công nghệ sử dụng

- **Ngôn ngữ:** Python 3.12
- **Web API & Demo UI:** FastAPI + Uvicorn (demo trên Swagger UI tại `http://localhost:8000/docs`)
- **Cơ sở dữ liệu (Demo):** SQLite (`sqlite3` chuẩn của Python, quản lý giao dịch qua `IUnitOfWork`)
- **Chữ ký số QR:** Thư viện `cryptography` (`Ed25519` — Central giữ Private Key ký mã QR sống 60s gắn với từng nhà máy; Factory giữ Public Key trong `EligibilitySnapshot.verificationKeys` để xác thực cục bộ)
- **Kiểm thử tự động:** `pytest` + `httpx`

---

## 2. Cấu trúc thư mục & 6 Gói Công Việc (Work Packages)

| Gói | Nhánh Git | File Code & File Test phụ trách | Nội dung hiện thực hóa |
| :--- | :--- | :--- | :--- |
| **Package 1** | `feat/pkg1-snapshot-rules` | • `src/tmc_gate/domain/snapshot.py`<br>• `tests/test_pkg1_snapshot.py` | `EligibilitySnapshot`: giữ dữ liệu quyền cục bộ & `verificationKeys`; tính thời gian bằng mili-giây nguyên (`<=60_000 ms` `NORMAL`, `<1_800_000 ms` `DEGRADED`, `>=1_800_000 ms` `CANNOT_PROCESS`, sai số đồng hồ `<2_000 ms`). |
| **Package 2** | `feat/pkg2-qr-access-decision` | • `src/tmc_gate/adapters/demo_qr_issuer.py`<br>• `src/tmc_gate/application/access_decision.py`<br>• `tests/test_pkg2_access_decision.py` | `QrCredentialIssuance` (ký Ed25519 QR 60s gắn `factoryId`) và `AccessDecision.evaluate()` (xác thực chữ ký bằng `verificationKeys`, hạn 60s, phạm vi nhà máy và quyền nhân viên theo SRP). |
| **Package 3** | `feat/pkg3-gate-processor` | • `src/tmc_gate/application/processor.py`<br>• `src/tmc_gate/application/facade.py`<br>• `src/tmc_gate/api/gate_controller.py`<br>• `tests/test_pkg3_processor.py`<br>• `tests/test_flow_a_e2e.py` | Khóa Contracts (`domain/`, `ports/`); `GateTransactionProcessor` (Flow A1–A9 dùng `IUnitOfWork` & `IControllerStatusQuery`), `GateCoreFacade` và `GateCoreController`. |
| **Package 4** | `feat/pkg4-sqlite-persistence` | • `src/tmc_gate/adapters/sqlite_repos.py`<br>• `src/tmc_gate/adapters/cross_cutting_stubs.py`<br>• `tests/test_pkg4_sqlite_repos.py` | `SqliteUnitOfWork` (`IUnitOfWork`), `SqliteScanRepository` (claim nguyên tử), `SqliteAttendanceRepository` (`1 GateCycle -> 0..* PassageEvent`, tối đa 1 chấm công gốc/`scanId`), `SqliteOutboxRepository` (chống trùng `(consumer, eventId)`). |
| **Package 5** | `feat/pkg5-turnstile-adapter` | • `src/tmc_gate/adapters/turnstile_adapter.py`<br>• `src/tmc_gate/api/demo_controller.py`<br>• `tests/test_pkg5_turnstile_contract.py` | `ControllerApiClient`, `SimulatedTurnstileAdapter` (chuẩn) & `FaultyTurnstileAdapter` (lỗi) chạy chung bộ contract test minh họa **LSP**, và `demo_controller` (bật theo cờ `demo_mode`). |
| **Package 6** | `feat/pkg6-intervention-tests` | • `src/tmc_gate/application/intervention.py`<br>• `src/tmc_gate/api/intervention_controller.py`<br>• `tests/test_pkg6_intervention.py` | `InterventionService` & `GateInterventionController` (`M3 Gate Intervention` – Flow B: mở hỗ trợ, `EMERGENCY_HOLD`, `requestEmergencyReset`), hoàn thiện `README.md` và cập nhật báo cáo. |

---

## 3. Bảng Ánh Xạ Nguyên Lý SOLID → File / Class / Interface

*(Khung bảng để mỗi thành viên điền phần hiện thực hóa của mình)*

| Nguyên lý SOLID | File / Class / Interface trong Repo | Cách áp dụng & Lợi ích đạt được |
| :--- | :--- | :--- |
| **S – Single Responsibility (SRP)** | • `domain/snapshot.py` (`EligibilitySnapshot`)<br>• `application/access_decision.py` (`AccessDecision`)<br>• `application/processor.py` (`GateTransactionProcessor`)<br>• `adapters/sqlite_repos.py` (`SqliteUnitOfWork`, Repositories) | • `EligibilitySnapshot` chỉ giữ dữ liệu & kiểm tra cục bộ (không kiểm chữ ký).<br>• `AccessDecision` chỉ xác thực QR & đánh giá điều kiện (không gọi cửa xoay hay ghi DB).<br>• `GateTransactionProcessor` chỉ điều phối vòng đời giao dịch cổng. |
| **O – Open/Closed (OCP)** | • `ports/turnstile.py` (`ITurnstileControl`)<br>• `adapters/turnstile_adapter.py` | Thêm hoặc thay thế bộ điều khiển cửa xoay mới chỉ cần viết Adapter mới thực thi `ITurnstileControl` mà không sửa `GateTransactionProcessor`. |
| **L – Liskov Substitution (LSP)** | • `ports/turnstile.py` (`IOpenGate`, `ICloseGate`, `IEmergencyGate`)<br>• `tests/test_pkg5_turnstile_contract.py` | Mọi adapter thực thi `ITurnstileControl` phải giữ đúng ngữ nghĩa: tách biệt `RECEIPT_ONLY`, `OPEN_CONFIRMED`, `CONTROLLER_ERROR`, và `UNDETERMINED`. Minh họa qua contract test giữa `SimulatedTurnstileAdapter` và `FaultyTurnstileAdapter`. |
| **I – Interface Segregation (ISP)** | • `ports/inbound.py` (`IScanSubmission`, `IInterventionRequest`, `IFactoryUpdates`)<br>• `ports/turnstile.py` (`IOpenGate`, `ICloseGate`, `IEmergencyGate`, `IPassageEvents`, `IControllerStatusQuery`) | • Kiosk (`StationClient`) chỉ phụ thuộc `IScanSubmission`, không thể gọi hàm khẩn cấp của Bảo vệ.<br>• Tách riêng lệnh đi ra (`IOpenGate`...) và tín hiệu đi vào (`IPassageEvents`: chỉ gồm `onPassage`, `onLaneClear`). |
| **D – Dependency Inversion (DIP)** | • `application/processor.py`<br>• `ports/repositories.py` (`IUnitOfWork`, `IScanRepository`...)<br>• `tests/test_architecture_solid.py` | Tầng `application/` chỉ phụ thuộc vào `domain/` và `ports/`, hoàn toàn không import `sqlite3`, `fastapi` hay `adapters/`. Kiểm tra tự động bằng AST test. |

---

## 4. Hướng dẫn cài đặt & chạy nhanh

```bash
python -m venv .venv
source .venv/Scripts/activate   # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
pytest -v
uvicorn tmc_gate.api.app:app --reload --app-dir src
```
Mở trình duyệt tại: **`http://localhost:8000/docs`**
