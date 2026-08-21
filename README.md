# THIEUCUBU Stock Intelligence

THIEUCUBU là lớp lọc thô và chuẩn hóa dữ liệu cho cổ phiếu Việt Nam. Hệ thống theo dõi cơ hội mua/bán hằng ngày, săn tối đa 1–2 cơ hội gom có độ thuyết phục cao vào cuối tuần, đồng thời xuất dữ liệu có schema để các dự án khác sử dụng mà không phải lọc lại từ đầu.

> Đây là công cụ hỗ trợ nghiên cứu và quản trị rủi ro, không phải khuyến nghị đầu tư hay cam kết lợi nhuận.

## Hệ thống làm gì?

### Trong tuần

- Quét rộng vào buổi sáng, buổi chiều và sau ATC.
- Giữa các phiên cố định, radar `Pulse 30P` quét bảng giá toàn thị trường mỗi 30 phút để tìm giá/dòng tiền đột biến.
- Luôn ưu tiên danh mục và mã đã ghi chú.
- Tách riêng điểm `Lướt` và điểm `Gom`.
- Vẫn gửi lại báo cáo đầy đủ mỗi phiên để người dùng không bỏ trôi tín hiệu.
- Mỗi mã chỉ xuất hiện một lần trong một báo cáo, ở nhóm ưu tiên cao nhất.
- Nhận diện nền giá, volume co hẹp, OBV/MFI, relative strength, near-break và failed-break.
- Mã gần/vượt đỉnh 6 năm được gắn cảnh báo nhưng không bị loại khỏi lượt quét.
- Điều chỉnh hành động theo trạng thái VNINDEX và R/R.
- Khám phá universe động và quét xoay vòng các mã ngoài danh sách cốt lõi.

### Cuối tuần

- Kết hợp định giá, chất lượng doanh nghiệp, cấu trúc tuần, thời điểm, ngành và rủi ro.
- Dùng cấu trúc từ Pine Weekly Accumulation Sniper v3.1 làm lớp price/volume, không dùng Pine thay cho định giá.
- Chỉ chọn tối đa 2 mã `ƯU TIÊN GOM`; hệ thống được phép chọn 0 mã nếu thị trường không có cơ hội đủ tốt.
- Lưu vùng gom, vùng breakout, mức vô hiệu luận điểm, bull case và rủi ro.
- Ghi nhớ luận điểm qua nhiều tuần để một cổ phiếu tốt không biến mất chỉ vì nhiễu một phiên.

## Báo cáo hằng ngày theo 5 luồng

Mỗi báo cáo Telegram mở đầu bằng `TÓM TẮT 5 LUỒNG`, liệt kê mã trước rồi mới trình bày nội dung. Một mã chỉ có một luồng chính; portfolio luôn được ưu tiên và vẫn được phân tích đầy đủ dù cấu trúc đang xấu.

1. `PORTFOLIO`: mã đang nắm giữ hoặc có note; phân tích bắt buộc và quản trị vị thế.
2. `OPPORTUNITY`: cơ hội/tích lũy đủ điều kiện để phân tích sâu.
3. `EARLY`: gom sớm E1 cạn bán, E2 đang tạo đáy, E3 đã có xác nhận thăm dò.
4. `TECHNICAL`: RSI/MACD/SMI phân kỳ, hội tụ hoặc gần giao cắt ở vùng đáy; chỉ là cảnh báo theo dõi.
5. `STRUCTURE`: break xịt, retest, reclaim hoặc tái tích lũy; hiển thị ngắn để tránh làm loãng báo cáo.

Phân loại được tạo tự động sau mỗi lần quét. `session_alerts_latest.json` và feature feed đều có payload `five_streams`; mỗi fact có `classification.primary_stream`. Khi một mã đổi luồng, `market_state_history.json` ghi sự kiện `PRIMARY_STREAM` để mục chuyển pha có thể báo lại ở phiên sau.

### Radar đột biến trong phiên 30 phút

`intraday_pulse.py` là radar nhẹ chạy độc lập với các phiên quét cố định. Radar dùng bảng giá bulk của KBS, tự fallback sang VCI cho mã thiếu và đối chiếu VCI cho các sự kiện nổi bật khi nguồn thứ hai sẵn sàng. Vì đây là dữ liệu bảng giá ngắn hạn, FiinQuant/VCI/KBS/DNSE của scanner ngày vẫn được giữ nguyên và không bị Pulse thay thế.

- Chạy mỗi 30 phút trong phiên sáng và chiều; lượt `09:00` và `13:00` gieo baseline mới.
- Tìm biến động giá 30 phút, giá trị giao dịch tăng nhanh, bám đỉnh/đáy phiên và mất cân bằng mua/bán.
- Vẫn gửi heartbeat khi không có mã vượt ngưỡng, để biết radar đang hoạt động.
- Pulse không tạo Luồng 6 và không tự phát lệnh mua. Mã đột biến được lưu vào `top_symbols`, rồi tự được ưu tiên trong lượt quét sâu cố định kế tiếp để xếp vào đúng một trong 5 luồng.
- Nếu KBS lỗi, VCI tiếp quản theo batch; nếu nguồn xác minh lỗi thì sự kiện vẫn được giữ với nhãn `1 nguồn`, không làm hỏng toàn bộ lượt quét.
- Pulse và scanner có workflow, concurrency group và file universe riêng; một bên lỗi hoặc xếp hàng không chặn bên còn lại. Cơ chế safe-commit tự hợp nhất snapshot khi hai workflow cùng đẩy dữ liệu.

Báo cáo EOD ghép VNINDEX với tóm tắt 5 luồng và xuất riêng bốn trạng thái hành động: `LƯỚT`, `CẦM`, `GOM`, `RỦI RO`. Trạng thái thị trường chỉ điều chỉnh mức độ thận trọng; mọi bộ quét vẫn tiếp tục chạy và thông báo.

### Sửa nhanh Luồng 1

Luồng 1 lấy trực tiếp từ `data/portfolio.json`. Hiện `NVL` và `SMC` đã được thêm ở trạng thái `watch`. Muốn thêm mã, chỉ cần sao chép một object, đổi `symbol` và `note`; có thể chỉnh `buy_more_score`, `sell_score`, `position` nhưng không cần tự xếp lại luồng. Hệ thống luôn ưu tiên portfolio/note và tự phân loại các mã còn lại.

## Kiến trúc

```text
FiinQuant / VCI / KBS / DNSE
        │
        ▼
Safe fetch + chuẩn hóa nghìn VND + provenance + cache + source health
        │
        ├── Pulse 30P ─────► radar đột biến, nâng mã vào lượt quét sâu
        │
        ├── Daily scanner ──► Telegram mua/bán, lướt/gom
        │
        ├── Feature feed ───► dự án downstream
        │
        └── Weekend engine
             ├── định giá và chất lượng
             ├── weekly_sniper.py
             ├── sector + risk gate
             └── 0–2 mã ưu tiên gom
```

## Score v2: rõ ràng và không còn tràn điểm 100

Phiên bản cũ cộng nhiều bonus nhị phân rồi cắt tại 100, khiến nhiều setup khác nhau cùng hiển thị 100. Score v2 dùng trung bình cân bằng giữa các nhóm bằng chứng: một nhóm rất mạnh không thể che một nhóm quá yếu.

- Điểm hành động tối đa là `97`, không có mã `100/100`.
- `trade_score`: ưu tiên timing, dòng tiền và điểm phá nền.
- `position_score`: ưu tiên xu hướng, chất lượng nền, dòng tiền và biên chiết khấu.
- `confidence`: độ đầy đủ và đồng thuận của dữ liệu, không phải xác suất chắc chắn tăng giá.
- `grade`: cấp độ đọc nhanh.

| Grade | Điểm | Ý nghĩa |
|---|---:|---|
| S | 94–97 | Rất hiếm; nhiều lớp bằng chứng cùng mạnh |
| A+ | 88–93 | Rất hấp dẫn |
| A | 82–87 | Hấp dẫn |
| B+ | 75–81 | Có thể hành động nếu đúng vùng giá |
| B | 68–74 | Theo dõi |
| C | 55–67 | Chưa đủ điều kiện |
| D | dưới 55 | Yếu hoặc rủi ro cao |

Trường `win_score` vẫn được giữ để tương thích với dự án cũ. Dự án mới nên đọc `scores.trade`, `scores.position`, `scores.advanced`, `grade`, `confidence` và `score_version` trong feed v2.

## Trạng thái đa khung và bộ lọc break xịt

`market_phase.py` đọc cấu trúc độc lập trên `1D`, `1W` và `1M`, sau đó tạo một trạng thái chung. Scanner lấy mặc định 520 phiên để khung tháng có đủ lịch sử thay vì suy luận từ vài tháng gần nhất.

| Trạng thái chung | Ý nghĩa hành động |
|---|---|
| `CƠ HỘI` | Khung tuần/tháng đồng thuận; vẫn phải chờ đúng trigger và vùng rủi ro |
| `TÍCH LŨY` | Nền đang hình thành hoặc nghi tái tích lũy; theo dõi, chưa xem là breakout hoàn tất |
| `CẨN THẬN` | Đa khung xung đột, break chưa giữ nền hoặc đang chờ reclaim |
| `PHÂN PHỐI` | Có áp lực cung trên nhiều khung; chặn mua mới và ưu tiên quản trị rủi ro |

Breakout không còn được kết luận chỉ từ một cây nến. Bộ chẩn đoán theo dõi tối đa 25 phiên sau sự kiện và phân loại:

- `BREAKOUT_CONFIRMED`: có ít nhất hai lần đóng cửa giữ trên nền.
- `HEALTHY_RETEST`: retest gần mốc breakout với volume co lại.
- `RECLAIMED_BREAK`: từng mất nền nhưng đã lấy lại mốc breakout.
- `REACCUMULATION`: nằm sát dưới mốc, biên độ và volume cùng co; chỉ theo dõi tới khi reclaim.
- `FAILED_BREAK_WATCH`: có dấu hiệu break xịt nhưng chưa đủ xác nhận; chặn mua đuổi.
- `FAILED_BREAK_CONFIRMED`: mất nền đủ sâu hoặc nhiều phiên kèm cung lớn; chặn tín hiệu mua.

Mỗi stock card hiển thị dòng `TT ... | D ... | W ... | M ...`. Báo cáo phiên có thêm `BẢN ĐỒ TRẠNG THÁI 1D / 1W / 1M` và nhóm riêng `BREAK XỊT / RETEST / TÁI TÍCH LŨY` để không đánh đồng retest lành mạnh với phân phối.

`data/market_state_history.json` giữ snapshot trước đó. Mục `CHUYỂN PHA / ĐIỂM MỚI ĐÁNG CHÚ Ý` chỉ xuất hiện khi cấu trúc đổi trạng thái hoặc điểm thay đổi ít nhất 8 điểm và vượt một mốc quan trọng; lần chạy đầu chỉ gieo trạng thái, không phát cảnh báo hàng loạt.

## Năm cửa của bộ lọc cuối tuần

Một mã chỉ có thể vào nhóm `ƯU TIÊN GOM` khi đồng thời vượt qua:

1. `Valuation`: PE/PB so với ngành, ngưỡng tuyệt đối và lịch sử snapshot của chính doanh nghiệp.
2. `Business quality`: ROE, ROA, EPS, biên lợi nhuận, nợ và thanh khoản tài chính phù hợp ngành.
3. `Weekly structure`: nền co hẹp, higher-low, volume cạn, CMF/OBV, EMA và RS so với VNINDEX.
4. `Timing`: spring, reclaim, pocket pivot hoặc early break kèm momentum xác nhận.
5. `Risk`: thanh khoản, failed-break, value trap, R/R và mức vô hiệu cấu trúc.

Chiết khấu sâu so với đỉnh 104 tuần chỉ là một dữ kiện giá, không đồng nghĩa với định giá rẻ. EPS âm, cấu trúc gãy, dữ liệu stale hoặc R/R yếu sẽ chặn mã khỏi nhóm ưu tiên.

## Pine đi kèm

File [`pine/THIEUCUBU_WEEKLY_ACCUMULATION_SNIPER_v3.pine`](pine/THIEUCUBU_WEEKLY_ACCUMULATION_SNIPER_v3.pine) hiện là Pine v3.1, dùng để kiểm tra trực quan trên TradingView khung `1W`.

Pine hiển thị:

- `EARLY MARKUP`: dấu hiệu đầu pha tăng đã có trigger và momentum.
- `PREP BASE`: cấu trúc gom đang chuẩn bị nhưng chưa đủ xác nhận.
- Vùng gom, breakout, mức vô hiệu, R/R, RS 13 tuần và thanh khoản.
- Tự nhận chart đang dùng VND hay nghìn VND để không làm sai thanh khoản 1.000 lần; tín hiệu alert chỉ xác nhận khi nến tuần đóng.

Pine không có PE/PB và chất lượng doanh nghiệp. Kết quả Pine không tự động trở thành mã ưu tiên; `weekly_sniper.py` tái hiện lớp cấu trúc trong Python rồi weekend engine mới ghép các lớp còn lại.

## Dữ liệu dùng chung cho dự án sau

| File | Vai trò |
|---|---|
| `data/stock_features_latest.json` | Feature feed đầy đủ của lần quét gần nhất |
| `data/filter_feed_latest.json` | Alias tương thích của raw filter feed v2 |
| `data/candidate_book_latest.json` | 0–2 conviction và watchlist cuối tuần |
| `data/investment_theses.json` | Sổ luận điểm bền vững qua nhiều tuần |
| `data/fundamental_history.json` | Snapshot PE/PB/chất lượng để xây lịch sử riêng |
| `data/session_alerts_latest.json` | Snapshot báo cáo phiên gần nhất |
| `data/intraday_pulse_latest.json` | Mã đột biến và snapshot Pulse 30 phút gần nhất |
| `data/intraday_pulse_state.json` | Baseline bảng giá để tính thay đổi giữa hai lần Pulse |
| `data/intraday_universe_state.json` | Universe riêng của Pulse, tách khỏi cursor của scanner cố định |
| `data/market_state_history.json` | Bộ nhớ chuyển pha và thay đổi điểm đáng kể |
| `data/signal_tracker.json` | Episode tín hiệu v2 đo đúng T+5/T+10/T+20 phiên, MFE/MAE và excess so với VNINDEX |
| `data/source_health.json` | Sức khỏe từng nguồn dữ liệu |
| `data/source_routing.json` | Bản đồ phân luồng FiinQuant và VCI/KBS/DNSE, tự cập nhật theo trạng thái |
| `data/universe_state.json` | Universe động, cursor và lát quét gần nhất |

`filter_feed_latest.json` sử dụng schema `thieucubu.raw_filter.v2`. Các trường v1 quan trọng vẫn được giữ tại root để tránh làm hỏng consumer cũ.

Trong mỗi phần tử `facts`, trường `market_structure` chứa `overall_state`, `timeframes.1D/1W/1M`, chẩn đoán `breakout`, tuổi sự kiện, khoảng cách tới nền và mức vô hiệu. Đây là lớp trạng thái dùng chung để dự án sau không phải tự phân loại lại.

## Universe động

Danh sách cốt lõi được quét thường xuyên. Ngoài ra hệ thống tải danh sách mã niêm yết, lọc mã cổ phiếu ba ký tự và lấy một lát xoay vòng mỗi phiên. Cursor được lưu trong `data/universe_state.json`, nhờ đó scanner phủ dần thị trường thay vì luôn bắt đầu lại từ chữ A.

- Portfolio/note luôn đứng đầu hàng đợi.
- Lát discovery mặc định: 36 mã mỗi broad scan.
- Các mã discovery đạt điểm đáng chú ý được đưa vào memory và sang vòng cuối tuần.
- Cấu hình bằng `SCAN_ROTATING_UNIVERSE_SIZE`.
- Số phiên dùng cho đa khung mặc định là `SCAN_HISTORY_BARS=520`.

## Lịch chạy mặc định

- `09:00–11:30` và `13:00–14:30`: Pulse toàn thị trường mỗi 30 phút; không thay đổi các phiên chính.
- `10:31` giờ Việt Nam: quét rộng buổi sáng.
- `13:31`: quét phần rộng chưa ưu tiên; sau `14:00` quét lại focus.
- `15:05`: tổng kết EOD sau ATC.
- `08:30` và `14:30` thứ Bảy: quét cơ hội cuối tuần.

GitHub schedule có các mốc dự phòng và watchdog. Duplicate guard, hard timeout và run journal ngăn các phiên cố định trùng hoặc treo; Pulse chạy độc lập để lỗi radar không làm mất báo cáo chính.

## Nguồn dữ liệu và khả năng tự phục hồi

- Mã cần chú ý trong `data/source_routing.json` ưu tiên `FIINQUANT`, sau đó tự fallback `VCI,KBS,DNSE`.
- Mã bình thường được cân tải giữa `VCI/KBS/DNSE`; FiinQuant chỉ là cứu hộ cuối cùng khi cả ba nguồn thường cùng không lấy được dữ liệu.
- Khi chưa có đủ hai FiinQuant Secret, nguồn này tự bị loại khỏi lượt chạy; các luồng cũ vẫn hoạt động bình thường.
- FiinQuantX chỉ chạy historical request, dùng chung một phiên đăng nhập trong mỗi process và không mở WebSocket realtime.
- Mỗi mã có nguồn ưu tiên và fallback riêng.
- Có jitter, rate limiter, `Retry-After`, cooldown và phục hồi nguồn trong cùng run.
- Cache parquet được dùng khi phù hợp; stale cache luôn được gắn provenance.
- Giá cổ phiếu từ mọi nguồn được chuẩn hóa về `thousand_vnd`; cache và nguồn mới còn được đối chiếu ngày trùng nhau để tự sửa sai lệch 1.000 lần.
- Dữ liệu stale có thể xuất hiện trong báo cáo tham khảo nhưng không được chọn làm conviction cuối tuần.
- Source health được lưu để lần chạy sau ưu tiên nguồn khỏe hơn.

### Phân luồng tự động

`data/source_routing.json` là file duy nhất cần xem khi muốn biết mã nào đang đi qua nguồn nào:

- `fiinquant_priority`: mã cơ hội, tích lũy, gần break, retest/tái tích lũy, break xịt cần theo dõi, memory strong/watchlist và mã trong portfolio/note.
- `standard_routes.VCI/KBS/DNSE`: các mã chưa cần chú ý nhiều, được chia tải ổn định theo mã.
- Mã mất điều kiện phải qua 3 lần quét liên tiếp mới bị hạ từ FiinQuant xuống luồng thường, tránh nhảy nhóm do nhiễu một phiên.
- Kết quả cuối tuần `ƯU TIÊN GOM/CHỜ ĐIỂM GOM` tự được nâng vào luồng FiinQuant cho các phiên sau.

Chỉnh tay chỉ tại hai danh sách trong `manual`: `force_fiinquant` để ghim mã cần theo dõi sát, và `force_standard` để buộc mã dùng luồng thường. Các phần sinh tự động không nên sửa tay.

## Thiết lập

Tạo GitHub Actions Secrets:

- `TELEGRAM_TOKEN`
- `TELEGRAM_CHAT_ID`
- `VNSTOCK_API_KEY`
- `FIINQUANT_USERNAME`
- `FIINQUANT_PASSWORD`

`FIINQUANT_USERNAME` là email/tên đăng nhập FiinQuant đã đăng ký; `FIINQUANT_PASSWORD` là mật khẩu tương ứng. Không đưa token, API key hoặc mật khẩu vào code vì repository là public.

Sau khi tạo hai FiinQuant Secret, vào **Actions → THIEUCUBU FiinQuant Check → Run workflow**. Workflow này chỉ đăng nhập và lấy mẫu lịch sử VCB, không gửi Telegram và không ghi dữ liệu vào repository. Khi job xanh, scanner hằng ngày và cuối tuần sẽ tự ưu tiên FiinQuantX.

Chạy local:

```bash
python -m pip install -r requirements.txt -r requirements-dev.txt
python -m pytest -q
```

Nếu muốn dùng FiinQuantX khi chạy local:

```bash
python -m pip install -r requirements-fiinquant.txt
python -m pip install --no-deps --extra-index-url https://fiinquant.github.io/fiinquantx/simple fiinquantx==0.1.67
```

Test scanner không gửi Telegram:

```bash
DRY_RUN=1 python session_plus.py --mode test
DRY_RUN=1 python weekend_plus_safe.py --mode test
```

### VIMO support flow (optional)

VIMO is an independent confirmation layer, not an OHLCV dependency. Daily
scans use `get_ta_signals` for a small top-candidate set; weekend scans use
`get_bctc_profile`. A VIMO timeout, quota error, missing key, or malformed
response is logged and skipped without changing the THIUCUBU score or failing
the core scanner.

Configure `VIMO_API_KEY` only in `.env` locally or GitHub Actions Secrets. Raw
VIMO responses and narratives are never committed; compact support state lives
under the ignored `data/cache/vimo/` directory. The support message is sent
only for a new confirmation/conflict, a changed VIMO signal, or a THIUCUBU
score move of at least 7 points.

```bash
python scripts/check_vimo.py --symbol FPT
python vimo_support.py --mode daily
python vimo_support.py --mode weekend
```

Trên Windows PowerShell:

```powershell
$env:DRY_RUN = "1"
python session_plus.py --mode test
python weekend_plus_safe.py --mode test
```

## Portfolio và ghi chú cá nhân

Hai file chạy thật được để trống mặc định để scanner không nhầm dữ liệu mẫu là danh mục của bạn. Sao chép cấu trúc từ `examples/portfolio.example.json` và `examples/notes.example.json` khi cần cấu hình.

`data/portfolio.json` phải là một JSON array:

```json
[
  {
    "symbol": "VNM",
    "note": "Luận điểm riêng",
    "buy_more_score": 78,
    "sell_score": 45,
    "position": "holding"
  }
]
```

`data/notes.json` nhận chuỗi hoặc object:

```json
{
  "VCB": "Theo dõi vùng hỗ trợ",
  "FPT": {
    "note": "Ưu tiên báo khi cấu trúc thay đổi"
  }
}
```

Các mã này luôn được báo lại đầy đủ trong mỗi phiên, kể cả khi điểm không đổi.

## Cấu hình quan trọng

- `SCAN_API_SOURCES=FIINQUANT,VCI,KBS,DNSE`
- `SCAN_SOURCE_LIMITS=FIINQUANT=80,VCI=20,KBS=20,DNSE=15`
- `FIINQUANT_REQUESTS_PER_MINUTE=80`
- `FIINQUANT_USAGE_RATIO=0.75`
- `FIINQUANT_MAX_CONCURRENCY=1`
- `SCAN_SOURCE_USAGE_RATIO=0.78`
- `SCAN_ROTATING_UNIVERSE_SIZE=36`
- `WEEKEND_HISTORY_BARS=780`
- `WEEKEND_CONVICTION_LIMIT=2`
- `WEEKEND_MIN_SCORE=60`
- `WEEKEND_MIN_SECTOR_SCORE=52`
- `WEEKEND_MIN_OWN_HISTORY_OBSERVATIONS=4`

`WEEKEND_CONVICTION_LIMIT` được chặn cứng tối đa 2 trong code. Hạ threshold có thể làm watchlist nhạy hơn nhưng không làm tăng số conviction.

Tracker v2 chỉ mở một episode cho mỗi mã cho tới khi đủ T+20 phiên. Dữ liệu tracker v1 bị loại khỏi thống kê vì dùng ngày lịch, lặp cùng mã trong ngày và từng chứa giá từ nhiều đơn vị không đồng nhất.

## Kiểm thử và an toàn vận hành

CI chạy compile check và pytest trên mọi thay đổi Python/workflow. Dữ liệu generated được commit bằng `scripts/safe_commit_data.py`: snapshot đầu ra, cập nhật remote, áp lại snapshot và thử push tối đa ba lần. Báo cáo Telegram và artifact vẫn được giữ nếu data commit gặp conflict.

Khi thay đổi Pine đang dùng trong TradingView, cần xóa và tạo lại alert vì TradingView lưu một bản snapshot của script và input tại thời điểm tạo alert.
