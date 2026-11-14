# Source report M2

Đây là nơi chỉnh sửa report của nhóm cùng với code trong repo. Sau khi PR nhập source được merge vào `dev`, cập nhật bản clone và sửa trực tiếp trong thư mục `report/`. Bản trên OneDrive chỉ giữ để đối chiếu; không chỉnh sửa song song hai bản.

- `main.tex`: bìa, Division of Work, mục lục và thứ tự các chương.
- `chapters/`: nội dung LaTeX. `reflection.tex` là Ch.8 Reflection, `task2.tex` là Ch.9 Task 2 Implementation, `chapter8.tex` là Ch.10 Conclusion.
- `figure/`: logo và ảnh chạy thật dùng trong Ch.9.
- `diagram/`: source `.drawio`, PlantUML, HTML và PNG đã xuất, cùng các script hỗ trợ trong `src/`.
- `evidence/`: script demo và log dùng làm minh chứng cho Ch.9.

## Build PDF

Cài TeX Live hoặc MiKTeX có pdfLaTeX, `latexmk` và các package mà `main.tex` sử dụng. Cần font tiếng Việt T5 (`vntex`); bản MiKTeX trên Windows cũng cần Perl để chạy `latexmk`.

Chạy từ thư mục gốc repo:

```sh
cd report
latexmk -pdf -interaction=nonstopmode -halt-on-error main.tex
```

Kết quả nằm ở `report/main.pdf`. PDF và các file build được bỏ qua bởi `.gitignore`; chỉ commit source và các hình cần cho report. Có thể dọn file phụ bằng `latexmk -c main.tex`.

Build PDF dùng PNG đã có nên không cần chạy script sinh hình. Khi sửa hình, mở source `.drawio` tương ứng và xuất lại PNG vào đúng đường dẫn đang được LaTeX dùng.

`diagram/src/render_viewer.js` là công cụ hỗ trợ cũ: nó cần Playwright/Chromium và file `diagram/dio/src/main/webapp/js/viewer-static.min.js` từ một bản source draw.io cài riêng. Các phụ thuộc này không được đưa vào repo và không cần để build PDF.

## Trạng thái

Phase B đã xong: Ch.9 (Task 2), Fig 4.18–4.20 vẽ theo code, Division of Work, Reflection và link GitHub (§9.9) đều đã có trong report. Kết quả test trong Ch.9 lấy từ commit 5bd6721; code trong `src/` và `tests/` không đổi từ đó.

Không đưa thư mục review `tmp/`, backup, report tham khảo, slide bài giảng hay đề bài PDF vào đây. README và tài liệu triển khai trong `docs/` ở gốc repo code vẫn thuộc dự án; thư mục bị loại khi nhập là `Report/docs/` chứa tài liệu môn học trên máy.
