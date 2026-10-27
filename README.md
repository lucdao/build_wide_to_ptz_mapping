# Build Wide-to-PTZ Mapping

Dự án này xây dựng mapping để đưa một điểm ảnh trên camera Wide về pose PTZ sao cho điểm đó nằm gần tâm ảnh PTZ.

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

Homography được fit riêng cho từng ảnh PTZ/Wide bằng MAGSAC RANSAC. Tầng toàn cục giữ các homography đó, lấy tâm PTZ chiếu lên Wide làm mẫu và fit đa thức từ tọa độ Wide sang cột/hàng của lưới PTZ. Pose surface cuối dùng bảng trạng thái camera đã chụp và nội suy PCHIP. Chương trình dự đoán chỉ in pose; nó không gửi lệnh di chuyển camera.

Mặc định fitter so sánh mô hình tuyến tính và bậc hai bằng leave-one-out rồi chọn mô hình có lỗi dự đoán thấp hơn. Bộ dữ liệu 5×6 lưu trong workspace chọn bậc hai. `--max-degree 2` (mặc định) giới hạn ứng viên ở bậc 1 và 2; bậc 2 được chọn khi lỗi leave-one-out tốt hơn.

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

Collector chụp nhiều ảnh Wide, sau đó đưa camera PTZ qua lưới pan/tilt và lưu ảnh cùng trạng thái ONVIF thực tế. Ví dụ dùng lưới 5 cột × 6 hàng, tương ứng 30 pose:

```powershell
$env:PTZ_HOST = "192.0.2.10"       # thay bằng địa chỉ camera của bạn
$env:PTZ_PORT = "80"
$env:PTZ_USER = "admin"
$env:PTZ_PASSWORD = "mat-khau-camera"
$env:WIDE_RTSP_URL = "rtsp://user:password@wide-camera:554/stream"

python .\collect_full_dynamic_pipeline.py `
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

## 2. Fit homography cục bộ + poly toàn cục

```powershell
python .\run_mapping_pipeline.py `
  --run-root .\runs\calibration_001 `
  --device cpu
```

Runner thực hiện lần lượt:

1. Đọc Wide/PTZ captures, trích đặc trưng ALIKED và ghép bằng LightGlue.
2. Fit/chọn homography cục bộ PTZ→Wide cho từng pose bằng MAGSAC RANSAC; lưu tâm PTZ đã chiếu lên Wide.
3. Fit mô hình toàn cục Wide→tọa độ lưới. Mặc định dùng RANSAC ngưỡng `0.35` grid unit, 3000 vòng; chọn giữa bậc 1 và 2 bằng leave-one-out.
4. Lưu pose grid thực tế và PCHIP surface để đổi `(u,v)` thành `(pan,tilt,zoom)`.

Đầu ra chính:

```text
runs/calibration_001/
├── 02_aliked_lightglue_wide_ptz_mapping/
│   ├── selected_homographies.json
│   ├── ptz_center_to_wide_pairs.csv
│   └── wide_pixel_to_ptz_mapping.json   # homography toàn cục nền để đối chiếu
└── 03_polynomial_wide_to_grid/
    ├── polynomial_wide_pixel_to_ptz_mapping.json  # mapping được chọn
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

Có thể chạy từng bước riêng bằng `build_wide_ptz_mapping.py`, rồi `fit_polynomial_wide_to_grid.py`. Fit toàn cục nhận lưới có ít nhất sáu tâm không suy biến; với lưới chữ nhật, nên có ít nhất ba cột và ba hàng để xác định đầy đủ các hạng tử bậc hai.

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
- `build_wide_ptz_mapping.py`: ALIKED/LightGlue, homography cục bộ, tâm PTZ trên Wide và pose surface.
- `fit_polynomial_wide_to_grid.py`: poly robust toàn cục, lựa chọn theo leave-one-out, chẩn đoán sai số.
- `run_mapping_pipeline.py`: chạy hai bước fit theo đúng cấu hình đã chốt.
- `predict_wide_pixel_pose.py`: ánh xạ điểm Wide sang pose PTZ, không điều khiển camera.

Ảnh hiệu chuẩn, cache đặc trưng, model weights và thông tin camera không được lưu trong repo này; chúng nằm trong các thư mục `runs/` bị Git ignore.
