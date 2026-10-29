# Build Wide-to-PTZ Mapping

Dự án này xây dựng mapping để đưa một điểm ảnh trên camera Wide về pose PTZ sao cho điểm đó nằm gần tâm ảnh PTZ.

## Bắt đầu trên Ubuntu Desktop

Sau khi clone, mở terminal trong thư mục dự án và chạy một lệnh:

```bash
bash ./start-ubuntu.sh
```

Lần đầu, chương trình tự tải Python riêng, thư viện CPU và trọng số ALIKED/LightGlue vào `.runtime/`. Bước này cần Internet và có thể mất vài phút; không cần cài Python toàn hệ thống hay CUDA. Launcher hỗ trợ Ubuntu Desktop x86_64 và aarch64, yêu cầu môi trường đồ họa X11/XWayland và glibc từ 2.28; Ubuntu 20.04 trở lên đáp ứng mức glibc này. Ứng dụng tạo mục **Hieu chuan Wide PTZ** trong menu ứng dụng của người dùng để mở ở những lần sau.

Lần đầu, nhập RTSP Wide và RTSP PTZ trong hộp thoại. Thông tin được lưu trong `.env.calibration.json` của thư mục dự án. Nếu ONVIF dùng cổng hoặc tài khoản khác RTSP PTZ, chọn phần cấu hình ONVIF nâng cao. Cấu hình này chứa thông tin đăng nhập và được Git bỏ qua.

Sau đó một cửa sổ hiển thị **Wide bên trái, PTZ bên phải**. Có thể chỉnh zoom ngay trong cửa sổ trước khi lưu góc đầu; zoom được giữ cố định sau đó. Dùng nút mũi tên hoặc WASD đưa điểm được đánh dấu trên Wide vào **dấu cộng đỏ nhỏ ở tâm PTZ**, rồi bấm **LƯU GÓC**. Lặp lại 4 góc theo thứ tự chỉ dẫn. Sau góc thứ tư, chương trình tự chụp 30 điểm, khớp ảnh và xuất `output/latest_lut.json`.

Để đổi kết nối đã lưu, chạy `bash ./start-ubuntu.sh --configure`. Để dùng lại bốn góc của phiên đã hoàn tất khi camera và vị trí lắp không đổi, chạy `bash ./start-ubuntu.sh --reuse-last-corners`. Trường hợp chỉ muốn chuẩn bị thư viện/model mà chưa mở camera: `bash ./start-ubuntu.sh --prepare-only`.

Ứng dụng giữ các phiên ảnh trong `runs/`; `output/latest_lut.json` chỉ được cập nhật khi phiên mới hoàn tất và định dạng LUT hợp lệ. Xem mục **Điều kiện để dùng trên hệ thống thật** trong [HUONG_DAN_HE_THONG.md](HUONG_DAN_HE_THONG.md) để đánh giá chất lượng hiệu chuẩn.

## Cách dùng trên Windows

1. Nhấp đúp **`BAT_DAU_HIEU_CHUAN.cmd`**. Lần đầu, launcher tự chuẩn bị Python 3.12 và thư viện; cần Internet để cài đặt/tải model.
2. Lần đầu nhập hai URL RTSP Wide/PTZ có tài khoản. Cấu hình được nhớ tại `.env.calibration.json`, được Git bỏ qua. Tài khoản ONVIF mặc định lấy từ RTSP PTZ, cổng 80. Nếu ONVIF dùng tài khoản/cổng riêng, sửa các trường `PTZ_HOST`, `PTZ_PORT`, `PTZ_USER`, `PTZ_PASSWORD` trong file cấu hình này.
3. Một cửa sổ hiển thị **Wide bên trái, PTZ bên phải**. Dùng nút mũi tên hoặc WASD để đưa điểm ở góc đang đánh dấu trên Wide vào tâm ảnh PTZ. Nhấn **Enter** hoặc nút **LƯU GÓC**; làm lần lượt trên trái → trên phải → dưới phải → dưới trái.
4. Sau góc thứ tư, công cụ tự chụp 30 điểm, chạy khớp ảnh, tính LUT và mở thư mục kết quả. File dùng cho runtime luôn là **`output/latest_lut.json`**; mỗi phiên gốc vẫn giữ riêng trong `runs/`.

Các mức `1/2/3` điều chỉnh bước di chuyển tinh/vừa/nhanh. `X/Y` đảo hướng pan/tilt nếu chiều điều khiển của camera khác mong muốn. `Q` hoặc đóng cửa sổ để dừng. Zoom được giữ cố định theo pose đã đọc.

Từ lần sau, không cần nhập RTSP, đặt biến môi trường, gõ lệnh hay chọn thư mục kết quả. Dùng `CAU_HINH_CAMERA.cmd` khi cần đổi kết nối. Nếu camera và vị trí lắp vẫn như cũ, `CHAY_LAI_4_GOC_DA_LUU.cmd` sẽ dùng lại bốn góc từ phiên đã hoàn tất và tự thu/tính lại.

Launcher cập nhật `output/latest_lut.json` sau khi phiên chạy hoàn tất và kiểm tra định dạng thành công. Nếu phiên mới lỗi, LUT cũ được giữ. Có thể cấu hình `LUT_MAPPING_HOST_PATH` trong `LUT_camera_release/.env.lut` một lần tới `../build_wide_to_ptz_mapping/output/latest_lut.json`; runtime đọc lại file khi service khởi động lại.

Các lệnh ở phần dưới dành cho chạy từng bước hoặc tùy chỉnh.

## Pipeline đã chọn

```text
Ảnh Wide + ảnh PTZ tại nhiều pose
              │
              ▼
ALIKED + LightGlue khớp đặc trưng từng cặp ảnh
              │
              ▼
Homography cục bộ Hᵢ (PTZ pixel → Wide pixel)
              │
              ▼
Chiếu tâm ảnh PTZ qua Hᵢ → các điểm tâm trên Wide
              │
              ▼
Poly toàn cục bậc 2: Wide pixel (x,y) → tọa độ lưới PTZ (u,v)
              │
              ▼
PCHIP trên pose grid → pan, tilt, zoom
```

Homography được fit riêng cho từng ảnh PTZ/Wide bằng MAGSAC RANSAC. Tầng toàn cục giữ các homography đó, lấy tâm PTZ chiếu lên Wide làm mẫu và fit đa thức từ tọa độ Wide sang cột/hàng của lưới PTZ. Đầu ra cuối dùng nội suy PCHIP trên bảng pose đã thu, đúng như C++ runtime của `LUT_camera_release`. Chương trình dự đoán chỉ in pose; nó không gửi lệnh di chuyển camera.

Mặc định fitter so sánh mô hình tuyến tính và bậc hai bằng leave-one-out rồi chọn mô hình có lỗi dự đoán thấp hơn. `--max-degree 2` giới hạn ứng viên ở bậc 1 và 2. Khi bậc 1 thắng, file xuất vẫn có đủ 6×2 hệ số: các hàng `x², xy, y²` bằng 0, nên kết quả ánh xạ không đổi. Bậc đã fit được ghi trong `fit_quality.selected_degree`.

Xem [HUONG_DAN_HE_THONG.md](HUONG_DAN_HE_THONG.md) để đọc luồng dữ liệu, tham số và các giới hạn của hệ thống.

## Cài đặt

Khuyến nghị Python 3.10–3.12. Từ PowerShell tại thư mục repo:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Lần chạy ALIKED đầu tiên cần Internet để tải trọng số pretrained của Kornia. Với GPU NVIDIA đã cài PyTorch CUDA phù hợp, có thể dùng `--device cuda`; nếu không, dùng CPU.

## 1. Thu dữ liệu hiệu chuẩn

Mặc định collector dùng **4 góc Wide do người vận hành căn PTZ** để tạo vùng quét. Bạn đưa **tâm ảnh PTZ** tới lần lượt góc **trên trái → trên phải → dưới phải → dưới trái** của Wide, rồi nhấn Enter ở terminal sau mỗi lần căn. Chương trình đọc pose ONVIF ổn định và lưu ngay từng góc. Giữ nguyên zoom trong cả bốn lần.

Sau góc cuối, chương trình nội suy song tuyến tính từ bốn pose để tạo **5 cột × 6 hàng = 30 điểm**, quét zíc zắc, chờ xác nhận pose rồi chụp. 30 điểm bao gồm bốn góc; mỗi cột/hàng có thể thay đổi đồng thời cả pan và tilt để theo vùng đã căn.

```powershell
$env:PTZ_HOST = "192.0.2.10"       # thay bằng địa chỉ camera của bạn
$env:PTZ_PORT = "80"
$env:PTZ_USER = "admin"
$env:PTZ_PASSWORD = "mat-khau-camera"
$env:WIDE_RTSP_URL = "rtsp://user:password@wide-camera:554/stream"
$env:PTZ_RTSP_URL = "rtsp://user:password@ptz-camera:554/stream"

python .\collect_full_dynamic_pipeline.py `
  --record-corners `
  --run-root .\runs\calibration_001 `
  --wide-url $env:WIDE_RTSP_URL `
  --wide-count 8 `
  --pan-count 5 `
  --tilt-count 6 `
  --ptz-host $env:PTZ_HOST `
  --ptz-port ([int]$env:PTZ_PORT) `
  --ptz-user $env:PTZ_USER `
  --ptz-password $env:PTZ_PASSWORD
```

Collector có điều khiển PTZ thật và chụp 30 pose trong ví dụ trên. Chỉ chạy khi camera được phép di chuyển và vùng pan/tilt an toàn. Mật khẩu lấy từ biến môi trường; không ghi thông tin đăng nhập vào mã nguồn hoặc commit dữ liệu capture.

Có thể đặt `PTZ_RTSP_URL` nếu đường dẫn RTSP không theo mặc định mà collector dựng. Đường dẫn này cũng có thể chứa thông tin đăng nhập, nên chỉ để trong biến môi trường cục bộ.

Tọa độ bốn góc nằm tại `runs/calibration_001/00_corner_anchors/wide_corners.json`. Mỗi góc có pixel Wide, pose `(pan,tilt,zoom)` thật và thời điểm ghi. File được lưu dần; file chưa đủ bốn góc hoặc hình tứ giác bị chéo/trùng sẽ không được dùng để tự di chuyển. Chế độ này yêu cầu pose ONVIF đo được trước và sau mỗi ảnh; không dùng pose lệnh thay cho trạng thái lỗi.

Để tự chạy tiếp ALIKED/LightGlue, fit và xuất LUT ngay sau khi chụp, thêm `--fit`:

```powershell
python .\collect_full_dynamic_pipeline.py `
  --record-corners --fit --device cpu `
  --run-root .\runs\calibration_001
```

Lần sau có thể dùng lại tọa độ đã lưu nếu camera, vị trí lắp, profile, luồng Wide và zoom vẫn phù hợp:

```powershell
python .\collect_full_dynamic_pipeline.py `
  --corners-file .\runs\calibration_001\00_corner_anchors\wide_corners.json `
  --run-root .\runs\calibration_002 --fit
```

`--record-corners` là chế độ mặc định. `--auto-ranges` chọn cách cũ, tự tạo pan/tilt theo toàn bộ khoảng ONVIF hoặc các giới hạn bạn truyền. Các đối số `--pan-min/--pan-max/--tilt-min/--tilt-max/--pans/--tilts/--zoom` chỉ dùng với `--auto-ranges`.

Lưới pose nội suy từ bốn góc là kế hoạch thu ảnh, không bảo đảm các tâm thật nằm cách đều trên pixel Wide. Bước khớp ảnh vẫn đo lại tâm từng ảnh PTZ trên Wide. Bốn góc phải thuộc cùng một nhánh tọa độ pan liên tục; chế độ hiện tại từ chối khoảng pan lớn hơn nửa range camera để tránh nội suy qua điểm cuộn pan.

## 2. Fit homography cục bộ + poly toàn cục

Bỏ qua lệnh này nếu collector đã chạy thành công với `--fit`.

```powershell
python .\run_mapping_pipeline.py `
  --run-root .\runs\calibration_001 `
  --device cpu
```

Runner thực hiện lần lượt:

1. Đọc Wide/PTZ captures, trích đặc trưng ALIKED và ghép bằng LightGlue.
2. Fit/chọn homography cục bộ PTZ→Wide cho từng pose bằng MAGSAC RANSAC; lưu tâm PTZ đã chiếu lên Wide.
3. Fit mô hình toàn cục Wide→tọa độ lưới. Mặc định dùng RANSAC ngưỡng `0.35` grid unit, 3000 vòng; chọn giữa bậc 1 và 2 bằng leave-one-out.
4. Kiểm tra lưới 6 hàng × 5 cột × 3 trục hữu hạn, chuẩn hóa hệ số thành 6×2 và xuất `global_mapping` để nạp trực tiếp vào `LUT_camera_release`.

Đầu ra chính:

```text
runs/calibration_001/
├── 02_aliked_lightglue_wide_ptz_mapping/
│   ├── selected_homographies.json
│   ├── ptz_center_to_wide_pairs.csv
│   └── wide_pixel_to_ptz_mapping.json   # homography toàn cục nền để đối chiếu
└── 03_polynomial_wide_to_grid/
    ├── polynomial_wide_pixel_to_ptz_mapping.json  # JSON runtime: global_mapping
    ├── polynomial_grid_fit.csv
    ├── model_comparison.json
    └── diagnostics/
```

Nếu homography cục bộ đã fit và muốn chỉ fit lại mô hình toàn cục:

```powershell
python .\run_mapping_pipeline.py `
  --run-root .\runs\calibration_001 `
  --reuse-local-mapping
```

Có thể chạy từng bước riêng bằng `build_wide_ptz_mapping.py`, rồi `fit_polynomial_wide_to_grid.py`. Để nạp vào runtime hiện tại, run cần đúng **5 pan × 6 tilt**. Không đổi cỡ lưới hoặc cắt bớt dữ liệu để ép đủ 30 pose. Các tâm được chọn để fit vẫn cần đủ điểm không suy biến và phân bố theo cả hàng/cột. Mọi ảnh Wide phải cùng độ phân giải và cùng vùng nhìn.

## Định dạng đầu ra cho LUT_camera_release

File cuối xuất trực tiếp cùng cấu trúc đầu vào với `LUT_camera_release/mapping/wide_1.3_ptz_1.8.json`:

```text
global_mapping
├── mapping_type: polynomial_wide_pixel_to_ptz_lattice
├── degree: 2
├── terms_powers_xy: [[0,0], [1,0], [0,1], [2,0], [1,1], [0,2]]
├── coefficients_uv: 6×2 số hữu hạn
├── input_normalization: {width: W, height: H}
├── state_surface: pchip_2d_separable
└── state_grid_actual_after_capture: 6×5×3 số hữu hạn
```

Lưới được xếp theo `[row=v][column=u][pan,tilt,zoom]`, với `u=0..4`, `v=0..5`. Pose là tọa độ vị trí ONVIF, không phải góc độ. Khối `fit_quality` và `centre_predictions` bổ sung báo cáo hiệu chuẩn; không cần giả lập các báo cáo `local_stage`/`comparison` của file mẫu.

Kiểm tra bằng validator của repo bên cạnh:

```powershell
python ..\LUT_camera_release\calibration\validate_lut_mapping.py `
  .\runs\calibration_001\03_polynomial_wide_to_grid\polynomial_wide_pixel_to_ptz_mapping.json
```

Sau khi xác minh chất lượng trên camera, chép file vào `LUT_camera_release/mapping/` với tên riêng cho cặp camera và đặt `LUT_MAPPING_HOST_PATH` trong `.env.lut` tới file đó. Không dùng file homography trung gian của bước `02` làm đầu vào runtime.

Nếu đã có file polynomial cũ bọc trong `model`, chuyển sang một file mới bằng công cụ chỉ dùng thư viện chuẩn Python:

```powershell
python .\export_runtime_lut.py .\old_polynomial_mapping.json --output .\runtime_lut.json
```

Exporter kiểm tra định dạng, sắp đúng thứ tự hệ số, thêm hệ số 0 cho bậc 1 và dùng PCHIP như runtime. Nó từ chối homography, bậc 3, lưới sai cỡ, số NaN/Infinity và ghi đè file đích. Với file cũ dùng affine, phép nội suy pose sẽ chuyển sang PCHIP; lựa chọn nguồn được ghi trong `export_metadata`.

## 3. Dự đoán pose cho điểm Wide

```powershell
python .\predict_wide_pixel_pose.py `
  --mapping .\runs\calibration_001\03_polynomial_wide_to_grid\polynomial_wide_pixel_to_ptz_mapping.json `
  --x 1280 `
  --y 720
```

JSON đầu ra có `lattice_uv_raw`, tọa độ lưới đã kẹp vào vùng hiệu chuẩn, và pose `pan`, `tilt`, `zoom`. Tọa độ pixel phải thuộc ảnh Wide dùng trong run. Dùng `--no-clamp` chỉ khi chủ động muốn ngoại suy ngoài lưới đã đo.

## Mô hình toàn cục

Với Wide ảnh `W×H`, chuẩn hóa điểm `(X,Y)`:

```text
x = (X - W/2) / (W/2)
y = (Y - H/2) / (H/2)
φ = [1, x, y, x², xy, y²]
[u, v] = φ C
```

`C` được ước lượng từ các cặp `(tâm PTZ chiếu lên Wide, cột/hàng PTZ)` bằng least squares trên tập inlier. Đa thức trả tọa độ lưới liên tục; PCHIP dùng pose grid để suy ra pan/tilt/zoom. Đây là LUT tham số hóa bằng hệ số poly cộng với pose grid, không phải bảng pixel-to-pose được sinh cho từng pixel ảnh.

## Kết quả tham khảo

Trong run 5×6 có trong workspace nguồn, poly toàn cục bậc 2 đạt RMSE `0.0832` grid unit, leave-one-out RMSE `0.1069`, 30/30 inlier. Homography toàn cục nền trên cùng tập có RMSE `0.1187`. Đây là kết quả trên bộ hiệu chuẩn đó, không phải đảm bảo cho camera hoặc cảnh khác.

## File chính

- `collect_full_dynamic_pipeline.py`: thu Wide/PTZ run và trạng thái camera.
- `corner_grid.py`: kiểm tra bốn góc đã lưu và tạo lưới pose 5×6 bằng nội suy song tuyến tính.
- `calibration_preview.py`: xem hai camera và điều khiển PTZ/lưu góc trong một cửa sổ.
- `easy_calibration.py`, `Start-Calibrate.ps1`: nhớ cấu hình, tạo phiên mới và tự chạy toàn bộ pipeline từ launcher Windows.
- `build_wide_ptz_mapping.py`: ALIKED/LightGlue, homography cục bộ, tâm PTZ trên Wide và pose surface.
- `fit_polynomial_wide_to_grid.py`: poly robust toàn cục, lựa chọn theo leave-one-out, chẩn đoán sai số.
- `run_mapping_pipeline.py`: chạy hai bước fit theo đúng cấu hình đã chốt.
- `predict_wide_pixel_pose.py`: ánh xạ điểm Wide sang pose PTZ, không điều khiển camera.
- `export_runtime_lut.py`: định nghĩa và kiểm tra đầu ra runtime; chuyển file polynomial cũ sang `global_mapping`.

## Kiểm tra tương thích

```powershell
python -m unittest discover -s tests -v
```

Bộ kiểm tra giả lập bốn lần lưu góc và 30 vòng điều khiển/chụp, kiểm tra lưới nghiêng, pose lỗi và dùng lại file sai camera. Phần kiểm tra LUT tạo dữ liệu tuyến tính và bậc hai, chạy bước fit với `--reuse-local-mapping`, rồi nạp JSON bằng validator của `LUT_camera_release` nếu repo đó có ở thư mục bên cạnh. Đặt `LUT_CPP_CHECK` trỏ tới binary được biên dịch từ `LUT_camera_release/tests/lut_mapping_test.cpp` để so sánh thêm kết quả Python với C++ trên 15 điểm mỗi LUT. Các kiểm tra không kết nối camera. Chúng chứng minh tương thích dữ liệu/phép tính; độ chính xác ngoài thực địa phải được đo riêng.

Ảnh hiệu chuẩn, cache đặc trưng, model weights và thông tin camera không được lưu trong repo này; chúng nằm trong các thư mục `runs/` bị Git ignore.
