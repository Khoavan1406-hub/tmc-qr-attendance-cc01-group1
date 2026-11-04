# TMC QR-based Time & Attendance and Access Control System (Milestone 2 – Task 2)

Repository hiện thực hóa **Task 2 (Code Implementation)** của môn **Kiến trúc Phần mềm (HK261 – HCMUT)** cho hệ thống chấm công và kiểm soát ra vào bằng mã QR tại công ty sản xuất **TMC**.

Thiết kế mã nguồn bám sát **Báo cáo Milestone 2 (`main.pdf`)**, tập trung vào cụm cổng nhà máy (**M1 Gate Core**, kết hợp **M2 Turnstile Integration** và **M3 Gate Intervention**) và tuân thủ nghiêm ngặt 5 nguyên lý **SOLID** cùng kiến trúc **Ports & Adapters**.

---

## 1. Công nghệ sử dụng

- **Ngôn ngữ:** Python 3.12
- **Web API & Demo UI:** FastAPI + Uvicorn (trải nghiệm trực tiếp trên Swagger UI tại `http://localhost:8000/docs`)
- **Cơ sở dữ liệu (Demo):** SQLite (`sqlite3` chuẩn của Python, hỗ trợ giao dịch nguyên tử)
- **Chữ ký số QR:** Thư viện `cryptography` (`Ed25519` — Central giữ Private Key ký mã QR sống 60s gắn với từng nhà máy; Factory giữ Public Key xác thực cục bộ)
- **Kiểm thử tự động:** `pytest` + `httpx` (bao gồm cả Architectural Fitness Functions kiểm tra tự động nguyên lý SOLID)

---

## 2. Cấu trúc thư mục & 6 Gói Công Việc (Work Packages)

Bộ **Code Contracts (`domain/enums.py`, `domain/models.py`, `ports/inbound.py`, `ports/turnstile.py`, `ports/repositories.py`)** đã được khóa sẵn trên nhánh `main` và `dev`. Các thành viên triển khai phần logic (`TODO`) theo 6 gói độc lập:

| Gói | Nhánh Git đề xuất | File code & file test phụ trách | Nội dung hiện thực hóa (Code + Unit Test) |
| :--- | :--- | :--- | :--- |
| **Package 1** | `feat/pkg1-snapshot-rules` | • `src/tmc_gate/domain/snapshot.py`<br>• `tests/test_pkg1_snapshot.py` | Hiện thực hóa & viết unit test cho `EligibilitySnapshot.getOperatingMode()` (`NORMAL` <=60s, `DEGRADED` <=30m, `CANNOT_PROCESS` >30m) và `isEmployeeEligible()` (§5.7.2, I-10, ADR-06). |
| **Package 2** | `feat/pkg2-qr-access-decision` | • `src/tmc_gate/adapters/demo_qr_issuer.py`<br>• `src/tmc_gate/application/access_decision.py`<br>• `tests/test_pkg2_access_decision.py` | Hiện thực hóa & viết unit test cho ký số Ed25519 tạo QR 60s gắn `factoryId` (`QrCredentialIssuance`) và xác thực chữ ký, hạn 60s, phạm vi nhà máy trong `AccessDecision.evaluate()` (§7.3.1, ADR-11). |
| **Package 3** | `feat/pkg3-gate-processor` | • `src/tmc_gate/application/processor.py`<br>• `src/tmc_gate/application/facade.py`<br>• `src/tmc_gate/api/gate_controller.py`<br>• `tests/test_pkg3_processor.py` | Hiện thực hóa & viết test cho `GateTransactionProcessor` (Flow A1–A9: chống trùng `scanId`, gọi lưu nguyên tử `ACCEPTED`, gọi `openGate`, xử lý `PASSAGE` tạo chấm công, `LANE_CLEAR` đóng cổng, timeout 15s), `GateCoreFacade` và `GateCoreController` (§4.2.2, §4.3, ADR-02). |
| **Package 4** | `feat/pkg4-sqlite-persistence` | • `src/tmc_gate/adapters/sqlite_repos.py`<br>• `src/tmc_gate/adapters/cross_cutting_stubs.py`<br>• `tests/test_pkg4_sqlite_repos.py` | Hiện thực hóa & viết test cho `SqliteScanRepository` (giao dịch nguyên tử `atomic_reserve_lane_claim_token_and_accept`), `SqliteAttendanceRepository` (tối đa 1 bản ghi gốc/`scanId`), `SqliteOutboxRepository` và các cross-cutting stubs (§5.6.1, ADR-02, ADR-05). |
| **Package 5** | `feat/pkg5-turnstile-adapter` | • `src/tmc_gate/adapters/turnstile_adapter.py`<br>• `src/tmc_gate/api/demo_controller.py`<br>• `tests/test_pkg5_turnstile_adapter.py` | Hiện thực hóa & viết test cho `ControllerApiClient`, `TurnstileAdapter` tuân thủ hợp đồng hành vi **LSP** (§7.1.3: phân biệt `OPEN_CONFIRMED`, `CONTROLLER_ERROR`, `UNDETERMINED`) và các demo endpoints. |
| **Package 6** | `feat/pkg6-intervention-tests` | • `src/tmc_gate/application/intervention.py`<br>• `src/tmc_gate/api/intervention_controller.py`<br>• `tests/test_pkg6_intervention.py` | Hiện thực hóa & viết test cho `InterventionService` (`M3 Gate Intervention` – Flow B: mở cổng hỗ trợ & mở khẩn cấp `EMERGENCY_HOLD`) và `GateInterventionController` (§4.2.3, ADR-07, Table 4.22). |

---

## 3. Hướng dẫn cài đặt & chạy nhanh

### Bước 1: Tạo môi trường ảo và cài đặt thư viện
```bash
python -m venv .venv
# Windows (PowerShell / Git Bash):
source .venv/Scripts/activate   # hoặc .venv\Scripts\activate
pip install -r requirements.txt
```

### Bước 2: Chạy bộ kiểm thử tự động (Architecture & Unit Tests)
```bash
pytest
```

### Bước 3: Khởi động server FastAPI và mở Swagger UI
```bash
uvicorn tmc_gate.api.app:app --reload --app-dir src
```
Sau đó mở trình duyệt tại địa chỉ: **`http://localhost:8000/docs`**

---

## 4. Quy trình làm việc với Git (3 tầng nhánh: `main` ← `dev` ← `feat/*`)

1. Clone repo về máy và chuyển sang nhánh **`dev`**.
2. Tạo nhánh mới từ `dev` theo tên gói công việc của mình (`feat/pkg1-...` đến `feat/pkg6-...`).
3. Đọc kỹ file [`ARCHITECTURE_RULES.md`](ARCHITECTURE_RULES.md) trước khi code, chỉ sửa các file thuộc gói của mình.
4. Chạy `pytest` đảm bảo bài test kiến trúc xanh trước khi tạo Pull Request vào nhánh **`dev`**.
