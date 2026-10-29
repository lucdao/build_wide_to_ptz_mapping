# Luồng dữ liệu và đầu ra LUT

Repo này là công cụ **hiệu chuẩn giữa hai camera**. Nó tìm pose PTZ để đưa một pixel Wide về gần tâm ảnh PTZ. Repo không chạy detector người, ReID hay vòng điều khiển tracking liên tục; các phần đó thuộc `LUT_camera_release`.

## 1. Các file làm gì?

| File | Vai trò |
|---|---|
| `capture_onvif.py` | Kết nối ONVIF, tạo AbsoluteMove, đọc RTSP nền và chụp ảnh mới; có thêm chế độ thu PTZ độc lập theo định dạng cũ. |
| `collect_ptz_pan_tilt_grid.py` | Thu lưới PTZ độc lập, hỗ trợ resume, đọc giới hạn Absolute*PositionSpace. Metadata riêng là `capture_metadata.json`. |
| `collect_full_dynamic_pipeline.py` | Thu cả ảnh Wide lẫn lưới PTZ vào đúng layout mà bước fit cần. Đây là collector dùng cho pipeline chính. |
| `corner_grid.py` | Kiểm tra bốn pose đã căn theo góc Wide, nội suy song tuyến tính thành 30 pose và kiểm tra trạng thái thật khi ghi góc. |
| `calibration_preview.py` | Cửa sổ chung Wide/PTZ, nút/WASD điều khiển pan/tilt và xác nhận góc. |
| `easy_calibration.py` | Nhớ cấu hình cục bộ, tự tạo run, chạy capture+fit và cập nhật `output/latest_lut.json` sau khi thành công. |
| `start-ubuntu.sh`, `install_ubuntu_shortcut.py` | Tự chuẩn bị môi trường Ubuntu trong repo và thêm shortcut ứng dụng cho người dùng. |
| `prepare_models.py`, `model-weights.json` | Tải model cố định, kiểm tra SHA256 và chạy thử ALIKED/LightGlue trên cặp ảnh giả lập. |
| `BAT_DAU_HIEU_CHUAN.cmd`, `Start-Calibrate.ps1` | Launcher Windows, chuẩn bị môi trường một lần và mở chế độ đơn giản. |
| `build_wide_ptz_mapping.py` | ALIKED + LightGlue, chọn homography cục bộ đáng tin, xác định tâm PTZ trên Wide, tạo homography toàn cục nền và pose grid. |
| `fit_polynomial_wide_to_grid.py` | Thay homography toàn cục bằng polynomial robust, chọn bậc bằng leave-one-out, xuất LUT runtime. |
| `run_mapping_pipeline.py` | Điều phối hai bước build và fit; có thể dùng lại kết quả cục bộ với `--reuse-local-mapping`. |
| `predict_wide_pixel_pose.py` | Đọc LUT, nhận pixel Wide, in tọa độ lưới và pan/tilt/zoom dự đoán. |
| `export_runtime_lut.py` | Chuẩn hóa và kiểm tra `global_mapping`; có CLI chuyển kết quả polynomial cũ. |

Hai collector độc lập không tự tạo đủ layout của một full run. `run_mapping_pipeline.py` nhận run từ `collect_full_dynamic_pipeline.py`, không nhận trực tiếp thư mục chụp của `capture_onvif.py` hoặc collector PTZ độc lập.

## 2. Thu ảnh và pose

Đầu vào gồm RTSP Wide, RTSP PTZ và ONVIF của PTZ. Cấu hình qua các biến `WIDE_RTSP_URL`, `PTZ_RTSP_URL`, `PTZ_HOST`, `PTZ_PORT`, `PTZ_USER`, `PTZ_PASSWORD` hoặc đối số tương ứng.

Collector chính chụp một ảnh Wide tham chiếu và mặc định thêm 8 ảnh Wide. Sau đó người vận hành đưa **tâm ảnh PTZ** tới bốn góc Wide theo thứ tự trên trái, trên phải, dưới phải, dưới trái; nhấn Enter ở mỗi góc để ghi pose ONVIF thật. Giữ zoom cố định. Tọa độ được lưu dần tại `00_corner_anchors/wide_corners.json` và có thể dùng lại bằng `--corners-file` nếu camera và vị trí lắp không đổi.

Từ bốn pose, chương trình tạo lưới 5 cột × 6 hàng bằng nội suy song tuyến tính. Với `s=column/4`, `t=row/5`:

```text
P(s,t) = (1-s)(1-t) P_tren_trai + s(1-t) P_tren_phai
       + st P_duoi_phai + (1-s)t P_duoi_trai
```

Công thức áp dụng cho pan và tilt; zoom lấy cố định từ góc trên trái. Vì vậy lưới có thể nghiêng, mỗi hàng/cột thay đổi cả hai trục. 30 điểm bao gồm bốn góc và được quét zíc zắc; vị trí pixel bên trong là dự kiến, bước ALIKED/LightGlue sẽ đo lại tâm thực. Tứ giác trùng/chéo, zoom khác nhau, file sai camera hoặc độ phân giải và khoảng pan quá lớn/qua nhánh cuộn sẽ bị từ chối trước khi quét.

`--record-corners` là mặc định. `--auto-ranges` chọn cách cũ: dùng toàn bộ khoảng camera công bố hoặc giới hạn pan/tilt nhập riêng. Thêm `--fit` để sau khi chụp đủ 30 ảnh, tự chạy khớp ảnh và xuất LUT; lỗi fit được ghi vào manifest, ảnh thu được vẫn còn.

Chế độ đơn giản mở bằng `BAT_DAU_HIEU_CHUAN.cmd` tự chọn `--record-corners --preview --fit`. Người dùng xem và điều khiển ngay trong một cửa sổ, không cần chuyển sang web camera/terminal để lưu góc. Nó nhớ kết nối trong `.env.calibration.json`, tự tạo tên run và đưa kết quả đã kiểm tra tới `output/latest_lut.json`. `output/latest_corners.json` dùng cho launcher thu lại bằng bốn góc cũ khi vị trí lắp không thay đổi.

Mặc định: chờ ổn định 2 giây, timeout di chuyển 45 giây, sai lệch pose cho phép 0.04 và 3 mẫu ổn định. Một vị trí lỗi được thử lại tối đa 3 lần. Reader RTSP chạy nền để lấy frame mới sau thời điểm yêu cầu; ảnh raw không vẽ dấu, preview đánh dấu tâm được ghi riêng.

Chế độ bốn góc yêu cầu pose ONVIF thật, hợp lệ, ổn định khi ghi góc, và xác nhận pose trước/sau từng ảnh. Nó từ chối việc dùng lệnh yêu cầu để thay cho trạng thái lỗi. Trong chế độ cũ `--auto-ranges`, metadata vẫn có thể dùng giá trị lệnh thay thế khi ONVIF lỗi/cũ; cần xem `actual_after_capture_normalization`, `*_raw` và ảnh khi đánh giá dữ liệu chế độ này.

## 3. Khớp từng ảnh PTZ với Wide

ALIKED pretrained `aliked-n16` lấy mặc định tối đa 2048 keypoint trên ảnh làm việc có cạnh dài tối đa 960 pixel. Keypoint được quy đổi về pixel ảnh gốc. LightGlue ghép mỗi ảnh PTZ với từng ảnh Wide; với 30 PTZ và 9 Wide là 270 cặp ứng viên.

MAGSAC/RANSAC ước lượng homography `H_ptz_to_wide`. Bộ lọc mặc định yêu cầu:

- Tối thiểu 12 inlier và tỷ lệ inlier 0.18.
- Median reprojection error không quá 7 pixel ảnh gốc.
- Diện tích vùng PTZ chiếu lên Wide nằm trong tỷ lệ 0.001–1.2.
- Tâm PTZ chiếu lên Wide nằm trong biên cho phép, margin 200 pixel.

Nếu capture có anchor Wide đo trước thì anchor được dùng để kiểm tra. Collector chính chỉ ghi `wide_pixel_nominal` cho lưới nội suy, không dùng các pixel dự kiến này làm đo đạc thật; tâm Wide được tìm từ feature matching. Trong các ứng viên hợp lệ, chương trình chọn một cặp tốt nhất cho mỗi pose PTZ.

Chiếu tâm ảnh PTZ qua homography được chọn tạo một mẫu `(Wide X, Wide Y) → (column, row)`. Bước này cần ít nhất 8 cặp đáng tin và các inlier của homography toàn cục nền phải phủ ít nhất 3 hàng, 3 cột. Không bắt buộc mọi ảnh PTZ đều khớp thành công, nhưng pose grid vẫn phải đủ 30 ô.

## 4. Fit mô hình toàn cục

Ảnh Wide được chuẩn hóa bằng kích thước gốc `W,H`:

```text
x = (X - W/2) / (W/2)
y = (Y - H/2) / (H/2)
[u,v] = [1, x, y, x², xy, y²] × C
```

`u` là cột PTZ, `v` là hàng PTZ. Fitter so sánh bậc 1 và 2. Mỗi mô hình dùng RANSAC, mặc định ngưỡng 0.35 grid unit, 3000 vòng; sau đó refit least squares và đánh giá leave-one-out. Mô hình có leave-one-out RMSE trên inlier thấp hơn được chọn. RMSE này có đơn vị lưới, không phải pixel hay độ quay.

Nếu bậc 1 thắng, ba hàng hệ số bậc hai được điền 0 khi xuất. Phép tính Wide→grid được giữ nguyên; JSON vẫn có 6×2 hệ số như runtime yêu cầu. Bậc fit thật được lưu trong `fit_quality.selected_degree`.

## 5. Nội suy pose và định dạng cuối

Từ `(u,v)`, runtime kẹp `u` vào `[0,4]`, `v` vào `[0,5]`, rồi nội suy PCHIP theo cột trước, theo hàng sau. Nó áp dụng riêng cho pan, tilt, zoom. File cuối và công cụ dự đoán Python đều dùng PCHIP để khớp cách tính của C++.

```text
global_mapping.coefficients_uv                  6×2 = 12 số
global_mapping.state_grid_actual_after_capture  6×5×3 = 90 số
global_mapping.input_normalization.width/height       2 số
```

Lưới có thứ tự `[v][u][pan,tilt,zoom]`. Hệ số theo thứ tự `[1,x,y,x²,xy,y²]`; mỗi hàng là `[hệ số u, hệ số v]`. Pose là tọa độ vị trí ONVIF của camera, không phải góc tính bằng độ. Đây là LUT tham số hóa, không phải bảng lưu một pose cho từng pixel.

Kết quả cuối ở:

```text
runs/<run>/03_polynomial_wide_to_grid/polynomial_wide_pixel_to_ptz_mapping.json
```

Khối `global_mapping` khớp schema runtime của `LUT_camera_release/mapping/wide_1.3_ptz_1.8.json`. Các khối báo cáo phụ có thể khác vì file mẫu được tạo từ một thí nghiệm khác. File homography `02.../wide_pixel_to_ptz_mapping.json` là dữ liệu trung gian cho fitter và đối chiếu, không phải LUT runtime.

## 6. Điều kiện để dùng trên hệ thống thật

- Đúng 5 cột × 6 hàng và một mức zoom cho run.
- Ảnh Wide dùng khi chạy phải cùng hệ tọa độ, độ phân giải, crop và hướng ảnh với lúc hiệu chuẩn.
- Các camera, vị trí lắp và thiết lập quang học phải phù hợp với lần hiệu chuẩn đó.
- Schema hợp lệ chỉ chứng minh có thể đọc dữ liệu. Cần kiểm tra ảnh đối sánh, outlier và sai số đưa điểm Wide vào tâm PTZ trên camera thật.

Homography là xấp xỉ hình học phẳng; cảnh nhiều độ sâu, vật chuyển động, ít chi tiết hoặc quét ngoài vùng chung có thể tạo cặp khớp sai hay thiếu cặp. Với dữ liệu cũ/`--auto-ranges`, việc thay pose đọc lỗi bằng pose lệnh cũng có thể làm LUT sai dù JSON vẫn đúng schema. Nội suy từ bốn góc giúp xác định vùng thu; độ chính xác pixel-to-pose cuối cùng vẫn phải được đo bằng bộ ảnh và kiểm tra thực tế.

Lệnh thu dữ liệu, fit, chuyển file cũ và kiểm tra tương thích nằm trong `README.md`.
