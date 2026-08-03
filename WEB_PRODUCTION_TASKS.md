Tella Web Production Plan

Tài liệu này là prompt và task breakdown để Codex hoàn thiện website Tella. Mục tiêu là website có thể render video thật từ topic/script, dùng pipeline hiện tại, không thay bằng mock, không chạy dry-run và không thay đổi luồng render.

Master prompt gửi cho Codex

Bạn đang làm việc trong repository Tella hiện tại.

Mục tiêu:
Hoàn thiện website local-first để người dùng:
1. Nhập topic hoặc exact narration script.
2. Chọn language, aspect ratio, visual source, theme và duration; narrator
   profile do backend quản lý.
3. Bấm Create video.
4. Website thực sự chạy tella.cli.run_pipeline(...) với credentials thật
   trong environment và tạo MP4 trong thư mục output.
5. Theo dõi tiến độ, phase, log và lỗi.
6. Preview hoặc tải video MP4 sau khi render thành công.

Ràng buộc bắt buộc:
- Giữ nguyên pipeline render hiện tại và contract run_pipeline.
- Không thay video flow, narration timing, composition, subtitle hoặc artifact
  authority hiện có.
- Không dùng mock, fake render, dry-run, preview-only hoặc placeholder MP4 trong
  production path.
- TTS của website phải dùng Gemini với profile do backend quản lý; không tự động fallback sang Edge.
- API key credentials chỉ đọc từ server environment, tuyệt đối
  không gửi xuống frontend.
- Chỉ nhận các enum/config hợp lệ giống CLI hiện tại.
- Mọi job phải có job_id, trạng thái, phase, progress, timestamps, logs, output
  path và error rõ ràng.
- Không chạy hai pipeline đồng thời nếu pipeline hiện tại dùng process-wide
  environment state; dùng worker queue tuần tự hoặc cơ chế isolation an toàn.
- Không phá vỡ CLI hoặc các test hiện có.

Việc phải làm:
- Audit code hiện tại trước khi sửa.
- Xây backend web production-ready cho create/status/log/download/preview.
- Kết nối trực tiếp backend vào run_pipeline thật.
- Thêm persistence cho job metadata để restart server không làm mất trạng thái
  đã hoàn tất.
- Thêm frontend responsive, accessible, có loading state, validation, empty
  state, error state, success state và download state.
- Thêm progress event/polling ổn định; không báo 100% trước khi MP4 tồn tại và
  ffprobe xác nhận file hợp lệ.
- Thêm security cho file serving: chỉ được tải artifact thuộc job hợp lệ,
  không path traversal, không expose .env hoặc credentials.
- Thêm config/health check để báo Gemini planning/narration, ffmpeg và output directory
  đã sẵn sàng hay chưa.
- Thêm test unit/API và một E2E smoke test chạy render thật khi credentials
  được bật qua environment. E2E phải skip rõ ràng nếu thiếu credentials, nhưng
  không được biến production path thành mock.
- Cập nhật README với setup, chạy web, env, Gemini narration, troubleshooting và
  cách xác nhận MP4 thật.

Trước khi kết thúc:
- Chạy ruff và toàn bộ pytest.
- Chạy API smoke test.
- Chạy một render thật bằng topic ngắn, media source rẻ/an toàn và
  Gemini narration profile do backend quản lý.
- Xác minh output/video.mp4 tồn tại, kích thước > 0, ffprobe đọc được duration,
  audio stream và video stream.
- Báo cáo chính xác file đã sửa, command đã chạy, test result và mọi giới hạn
  còn lại.

Task breakdown

Task 1 — Production audit và contract

Mục tiêu: Chốt điểm vào duy nhất từ web tới pipeline thật.

Đọc tella/cli.py, tella/tts/synth_all.py, renderer và output metadata.

Xác định input schema dùng chung với CLI.

Xác định các phase có thể báo tiến độ.

Viết contract cho POST /api/jobs, GET /api/jobs/{id},GET /api/jobs/{id}/download, GET /api/jobs/{id}/preview.

Không sửa pipeline ở task này.

Nghiệm thu:

Có schema rõ ràng.

Có sơ đồ data-flow browser → web worker → run_pipeline → MP4.

Có danh sách env bắt buộc và lỗi startup tương ứng.

Task 2 — Job manager production

Mục tiêu: Job thật chạy nền và không mất metadata.

Dùng queue tuần tự hoặc worker process riêng.

Lưu job.json trong out/web_jobs/{job_id}/.

Trạng thái: queued, running, succeeded, failed, cancelled.

Lưu phase, progress, logs, error, output path, created/started/finished time.

Không chạy đồng thời nếu process-wide env của TTS/media gây race.

Khi server khởi động lại, job cũ được khôi phục thành succeeded hoặc failed/unknown, không tự nhận là đang chạy.

Nghiệm thu:

Gửi hai job liên tiếp: job thứ hai chờ job thứ nhất.

Không có job “succeeded” nếu MP4 không tồn tại.

Task 3 — Real pipeline adapter

Mục tiêu: Website render video thật.

Gọi trực tiếp run_pipeline, không gọi --dry-run-plan.

Truyền topic/script và toàn bộ option hợp lệ.

TTS Gemini-only; đặt strict mode để lỗi Gemini không fallback Edge.

Giữ narration continuous và timing hiện tại.

Sau render dùng ffprobe kiểm tra MP4, duration, video stream, audio stream.

Đọc plan.json và tts_metadata.json để hiển thị metadata.

Nghiệm thu bắt buộc:

POST /api/jobs
→ pipeline chạy thật
→ Gemini TTS request thật
→ ffmpeg render thật
→ output/video.mp4 tồn tại
→ ffprobe xác nhận video + audio
→ download trả về file MP4 thật

Task 4 — Progress và logs

Mục tiêu: UI phản ánh trạng thái thật, không giả lập.

Chuyển log step 1/6…step 6/6 thành phase/progress.

Không tăng progress giả khi pipeline chưa phát log tương ứng.

Polling có backoff hoặc SSE/WebSocket nếu cần.

Hiển thị lỗi cuối cùng và log gần nhất.

100% chỉ sau khi artifact validation thành công.

Task 5 — Frontend UI/UX hoàn chỉnh

Mục tiêu: Người dùng phổ thông có thể tự tạo video.

Form có labels, help text, validation và preview cấu hình.

Có empty/loading/running/success/error states.

Disable submit khi job đang tạo.

Hiển thị estimated steps, không hứa thời gian chính xác.

Có nút retry bằng cấu hình cũ và nút download.

Có video player preview nếu browser hỗ trợ.

Responsive desktop/mobile, keyboard accessible, contrast đủ tốt.

Không hiển thị API key, billing data hoặc stack trace nhạy cảm.

Task 6 — Health check và cấu hình

Mục tiêu: Biết ngay máy đã sẵn sàng render hay chưa.

GET /api/health kiểm tra:

Gemini key

Gemini planning/narration credential

ffmpeg/ffprobe

output directory writable

UI hiển thị checklist trước khi Create video.

Nếu thiếu credential, chặn job với hướng dẫn cụ thể.

README phải ghi rõ Gemini narration profile do backend quản lý.

Task 7 — Artifact serving và security

Mục tiêu: Tải file an toàn.

Chỉ serve file nằm trong job directory đã đăng ký.

Resolve path và chống path traversal.

Không serve .env, plan chứa secret, raw credentials hoặc toàn bộ output root.

Dùng Content-Disposition cho MP4.

Giới hạn kích thước input topic/script.

Nếu mở LAN, bind mặc định vẫn là 127.0.0.1; chỉ bind 0.0.0.0 khi người dùng chủ động cấu hình.

Task 8 — Test và E2E render thật

Mục tiêu: Chứng minh website không còn là pipeline test-only.

Unit test schema/validation/job transitions.

API test create/status/download với pipeline test seam.

Test path traversal và file không thuộc job.

E2E real-render test:

dùng credentials từ environment;

topic ngắn;

Gemini narrator profile do backend quản lý;

xác minh MP4 bằng ffprobe.

Nếu thiếu credentials: skip với lý do rõ ràng, không fake thành pass.

Task 9 — Chạy thử production local

cp .env.example .env
# điền GEMINI_API_KEY, GOOGLE_TTS_API_KEY, GOOGLE_TTS_VOICE
.venv/bin/python -m tella.web

Kiểm tra thủ công:

Mở http://127.0.0.1:8787.

Chọn AI image, Short, 9:16.

Nhập topic ngắn.

Bấm Create video.

Chờ đến Complete.

Mở preview và tải MP4.

Dùng ffprobe xác nhận có audio/video stream.

Definition of Done

Người dùng không cần terminal để tạo video.

Website tạo MP4 thật bằng pipeline hiện tại.

Gemini TTS chạy thật và không fallback âm thầm.

Progress và lỗi phản ánh trạng thái thật.

MP4 tải được và phát được.

Credentials không lộ trong browser hoặc artifact.

CLI cũ vẫn chạy.

Toàn bộ test pass; real-render E2E có bằng chứng hoặc skip có lý do.
