# Context bàn giao Package 1 — Brian

Tài liệu giúp người hoặc AI tiếp quản hiểu phần Eligibility Snapshot mà không cần đọc lại hội thoại. Mốc ghi nhận: **05/11/2026**, code triển khai tại commit `7b55b5d`. Đây là context của Package 1, không phải xác nhận toàn hệ thống đã hoàn thiện.

## Thứ tự đọc

1. [Yêu cầu, Contracts và quyết định triển khai](pkg1-snapshot-context.md).
2. [Cách kiểm chứng và kết quả đã ghi nhận](pkg1-verification.md).
3. [Code snapshot](../src/tmc_gate/domain/snapshot.py) và [unit test Package 1](../tests/test_pkg1_snapshot.py).
4. [Quy tắc kiến trúc](../ARCHITECTURE_RULES.md) và các file của package cần tích hợp.

## Phạm vi bàn giao

- Chủ nhiệm: Brian, **Package 1 — Eligibility Snapshot Rules**.
- Nhánh: `feat/pkg1-snapshot-rules`, đích tích hợp: `dev`.
- File triển khai: `src/tmc_gate/domain/snapshot.py`.
- File test: `tests/test_pkg1_snapshot.py`.
- Package 2 dùng snapshot trong `AccessDecision`; Package 3 điều phối luồng cổng và chốt Contracts dùng chung.
- Không nhận thêm TODO của Package 2–6 chỉ vì chúng chưa hoàn thành.

Nguồn yêu cầu là dòng Package 1 trong `M2 TASK TRACKER - BTL 2.csv` do người dùng cung cấp. Tại thời điểm bàn giao, CSV là file local chưa được Git track và không nằm trong commit tài liệu. Nội dung cần thiết đã được chép lại trong context để tài liệu vẫn đọc độc lập được.

Scaffold, README gốc và `ARCHITECTURE_RULES.md` còn mô tả ngưỡng cũ ở một số chỗ. Với Package 1, dùng các ngưỡng theo tracker được ghi rõ trong [context](pkg1-snapshot-context.md); không khôi phục logic cũ chỉ để khớp comment cũ.

## Khi tiếp quản

Đọc code và chạy test trước khi sửa. Phân biệt yêu cầu tracker với các quyết định bổ sung đã được ghi riêng; nếu đổi Contracts, phối hợp với các package sử dụng chúng.

Theo yêu cầu bàn giao hiện tại, chỉ tạo commit local; người dùng tự quản lý push và PR. Không thêm trailer `Co-authored-by` vào commit. Không suy diễn việc hoàn thành task thành quyền merge `dev` hoặc `main`.
