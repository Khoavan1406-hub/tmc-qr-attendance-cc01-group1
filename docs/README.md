# Tài Liệu Bàn Giao Kỹ Thuật (Work Packages Context & Verification)

Thư mục này lưu trữ tài liệu bàn giao, bối cảnh quyết định kiến trúc và kết quả kiểm chứng chi tiết của từng Gói công việc (Work Package) trong dự án **TMC Gate Core (Milestone 2 – Task 2)**.

---

## 1. Package 6 — Gate Intervention (M3 Flow B) & Report Finalization

* **Phụ trách:** Bửu
* **Nhánh Git:** `feat/pkg6-intervention-tests`
* **Tài liệu chi tiết:**
  1. [M3 Gate Intervention: Yêu cầu, Contracts và Quyết định Thiết kế](pkg6-intervention-context.md).
  2. [Kiểm chứng Package 6 và Kết quả Test Tự Động](pkg6-verification.md).
* **Mã nguồn chính:**
  * Application: [`src/tmc_gate/application/intervention.py`](../src/tmc_gate/application/intervention.py) (`InterventionService`, `GateInterventionFacade`).
  * API Controller: [`src/tmc_gate/api/intervention_controller.py`](../src/tmc_gate/api/intervention_controller.py) (`build_intervention_router`).
  * Tests: [`tests/test_pkg6_intervention.py`](../tests/test_pkg6_intervention.py) (4/4 passed).

---

## 2. Package 1 — Eligibility Snapshot Rules

* **Phụ trách:** Brian
* **Nhánh Git:** `feat/pkg1-snapshot-rules` (đã merge vào `dev`)
* **Tài liệu chi tiết:**
  1. [Yêu cầu, Contracts và quyết định triển khai](pkg1-snapshot-context.md).
  2. [Cách kiểm chứng và kết quả đã ghi nhận](pkg1-verification.md).
* **Mã nguồn chính:**
  * Domain: [`src/tmc_gate/domain/snapshot.py`](../src/tmc_gate/domain/snapshot.py) (`EligibilitySnapshot`).
  * Tests: [`tests/test_pkg1_snapshot.py`](../tests/test_pkg1_snapshot.py) (146/146 passed).
