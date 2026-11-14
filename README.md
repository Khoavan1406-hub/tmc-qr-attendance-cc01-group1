# TMC QR-based Attendance and Access Control — M2 Task 2

Prototype Python của **M1 Gate Core** (Flow A) và **M3 Gate Intervention** (Flow B), dùng FastAPI và SQLite. Repo này chứa mã nguồn, test và cấu hình chạy. Report, source LaTeX, hình và minh chứng được nộp riêng.

## Cấu trúc

- `src/tmc_gate/`: `domain`, `ports`, `application`, `adapters` và `api`.
- `tests/`: test nghiệp vụ, hợp đồng adapter, kiến trúc và tích hợp SQLite.
- `.github/workflows/ci.yml`: chạy test trên Python 3.12 khi push hoặc mở PR vào `dev`/`main`.
- `pyproject.toml`, `requirements.txt`, `Dockerfile`: đóng gói, thư viện và cấu hình container.

## Cài đặt và kiểm thử

Cần Python 3.12 trở lên. Chạy từ thư mục gốc repo:

```sh
python -m venv .venv
```

Kích hoạt bằng `.venv\Scripts\Activate.ps1` trên PowerShell hoặc `source .venv/bin/activate` trên Linux/macOS, rồi chạy:

```sh
python -m pip install -r requirements.txt
python -m pytest -q
```

Mốc kiểm chứng code `5bd6721`: **473 passed, 0 skipped, 1 xfailed**. Xfail là phản ví dụ LSP có chủ đích trong bộ contract test; các test kiến trúc chỉ kiểm một số ràng buộc import và interface.

## Chạy ứng dụng

Dữ liệu trong bộ nhớ, mất khi dừng server:

```sh
python -m uvicorn tmc_gate.api.app:app --app-dir src --port 8000
```

Lưu dữ liệu vào file SQLite trên PowerShell:

```powershell
$env:TMC_GATE_DB = "factory.sqlite3"
python -m uvicorn tmc_gate.api.app:app --app-dir src --port 8000
```

Trên Linux/macOS:

```sh
TMC_GATE_DB=factory.sqlite3 python -m uvicorn tmc_gate.api.app:app --app-dir src --port 8000
```

Mở [Swagger UI](http://localhost:8000/docs). Không dùng `--reload` khi demo khả năng giữ trạng thái qua restart. Khi dùng lại DB cũ, có thể cần gọi `POST /api/demo/watermark-age` với `watermarkAgeSeconds=10` để làm mới dữ liệu quyền demo.

Chạy bằng Docker (mặc định dùng SQLite trong bộ nhớ):

```sh
docker build -t tmc-gate .
docker run --rm -p 8000:8000 tmc-gate
```

Controller, guard sessions, audit/health và hệ thống ngoài là simulator hoặc stub. Prototype chưa triển khai WAN/Site Sync, quy trình duyệt HR hay tích hợp phần cứng thật. Ngân sách 2.000 ms bắt đầu ở `processScan`, chưa dành thời gian cho kiosk, mạng và hiển thị. Kết quả test chức năng không thay cho đo hiệu năng hoặc đánh giá bảo mật.

## Kịch Bản Demo Trực Tiếp Trên Swagger UI (`/docs`)

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
