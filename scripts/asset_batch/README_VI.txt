TELLA ASSET BATCH — HƯỚNG DẪN NHANH
=======================================

Bộ này gồm:
- batch_slice_assets.py
- asset_batch_config.json

Script đã cấu hình 12 sheet:
1. female_01_expressions_r3_c3 (sheet biểu cảm nữ đã có)
2. female_01_views_r2_c2
3. male_01_views_r2_c2
4. male_01_expressions_r3_c3
5. female_01_stand_walk_r2_c2
6. female_01_mixed_poses_r3_c3
7. male_01_mixed_poses_r3_c3
8. female_01_phone_poses_r2_c3
9. female_01_sitting_poses_r3_c2
10. female_01_emotional_standing_r3_c2
11. male_01_master_poses_r3_c5
12. crowd_group_cheering_01

QUY ƯỚC GRID
-------------
r2_c3 = 2 hàng, 3 cột.
Script đọc trái sang phải, sau đó từ trên xuống dưới.

THƯ MỤC SOURCE MONG ĐỢI
-----------------------
D:\tella-assets-staging\mvp_v1\
  characters\
    female_01\
      source_sheets\
        female_01_expressions_3x3.png
        female_01_views_r2_c2.png
        female_01_stand_walk_r2_c2.png
        female_01_mixed_poses_r3_c3.png
        female_01_phone_poses_r2_c3.png
        female_01_sitting_poses_r3_c2.png
        female_01_emotional_standing_r3_c2.png
    male_01\
      source_sheets\
        male_01_views_r2_c2.png
        male_01_expressions_r3_c3.png
        male_01_mixed_poses_r3_c3.png
        male_01_master_poses_r3_c5.png
  backgrounds\
    source_sheets\
      crowd_group_cheering_01.png

Bạn có thể:
- đổi tên ảnh theo danh sách trên; hoặc
- sửa trường "source" tương ứng trong asset_batch_config.json.

CÁCH CHẠY
---------
Từ PowerShell:

cd D:\tella

uv run --with pillow python D:\DUONG_DAN\batch_slice_assets.py `
  --config D:\DUONG_DAN\asset_batch_config.json `
  --root D:\tella-assets-staging\mvp_v1 `
  --overwrite

Chạy và xóa output cũ của từng sheet trước khi cắt lại:

uv run --with pillow python D:\DUONG_DAN\batch_slice_assets.py `
  --config D:\DUONG_DAN\asset_batch_config.json `
  --root D:\tella-assets-staging\mvp_v1 `
  --clean `
  --overwrite

Chỉ xử lý một vài sheet:

uv run --with pillow python D:\DUONG_DAN\batch_slice_assets.py `
  --config D:\DUONG_DAN\asset_batch_config.json `
  --root D:\tella-assets-staging\mvp_v1 `
  --only female_01_phone_poses_r2_c3 male_01_master_poses_r3_c5 `
  --overwrite

Liệt kê sheet_id:

uv run --with pillow python D:\DUONG_DAN\batch_slice_assets.py `
  --config D:\DUONG_DAN\asset_batch_config.json `
  --list

KIỂU HOẠT ĐỘNG
--------------
- Mặc định: source nào chưa có thì SKIP, không làm hỏng toàn batch.
- Thêm --strict: thiếu một source là dừng ngay.
- Thêm --overwrite: ghi đè PNG đã sinh.
- Thêm --clean: xóa output folder của các sheet được chọn trước khi cắt.
- Thêm --no-preview: không tạo preview contact sheet.

OUTPUT CHÍNH
------------
D:\tella-assets-staging\mvp_v1\
  asset_manifest.json
  active_asset_index.json
  manifests\
  previews\
  characters\...\poses\...
  backgrounds\crowd\...

active_asset_index.json chỉ chứa các asset đã đánh dấu enabled_by_default=true.
Các pose trùng được giữ trong manifest nhưng đánh dấu tier=backup và mặc định không active.
