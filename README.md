# Thieucutoo Scanner

Bot quet co phieu VN theo huong it nhung chat: chiet khau, di nen, vol kiet, OBV/MFI, sap break, break xit va danh muc dang nam giu.

## Can tao GitHub Secrets

Vao repo -> Settings -> Secrets and variables -> Actions, tao:

- `TELEGRAM_TOKEN`
- `TELEGRAM_CHAT_ID`
- `VNSTOCK_API_KEY`

Khong dua API key vao file code vi repo public.

## Lich chay

- 10:35 VN: bat dau quet rong buoi sang, chi tra report sau 12:30.
- 13:45 VN: quet rong phien chieu, quet lai note/co manh sau 14:00 va tra report sau 14:15.
- 15:05 VN: tong ket EOD sau ATC, co trang thai VNINDEX va canh bao risk.

Moi lan chay co random start ngan, chia nguon API, dung khoang 70% quota moi nguon va nghi ngau nhien de giam rui ro bi limit.

## Co che chong limit API

Scanner chia luong request qua `SCAN_API_SOURCES`, mac dinh `VCI,TCBS`.
Moi ma co mot nguon uu tien rieng va nguon con lai lam fallback. Truoc moi request co
sleep + jitter ngau nhien, neu mot nguon loi se cooldown ngau nhien truoc khi dung tiep.
Workflow phien chay qua `session_scan.py`, ben trong van dung `scan_safe.py` de boc lop bao ve API quanh scanner goc.

Mac dinh moi nguon duoc khai bao `SCAN_SOURCE_REQUESTS_PER_MINUTE=10` va chi dung
`SCAN_SOURCE_USAGE_RATIO=0.70`, tuc khoang 7 request/phut/nguon.

Cac bien co the chinh trong workflow:

- `SCAN_API_SOURCES`: danh sach nguon, vi du `VCI,TCBS`.
- `SCAN_SOURCE_REQUESTS_PER_MINUTE`: tran request/phut cua moi nguon.
- `SCAN_SOURCE_LIMITS`: tran rieng tung nguon, vi du `VCI=10,TCBS=8`.
- `SCAN_SOURCE_USAGE_RATIO`: ty le dung quota, mac dinh `0.70`.
- `SCAN_REQUEST_JITTER_MIN_SEC` / `SCAN_REQUEST_JITTER_MAX_SEC`: jitter truoc moi request.
- `SCAN_SOURCE_ERROR_COOLDOWN_MIN_SEC` / `SCAN_SOURCE_ERROR_COOLDOWN_MAX_SEC`: cooldown khi nguon loi.
- `SCAN_MAX_WORKERS`: so luong worker song song, nen <= so nguon API.

## Dieu phoi theo phien

Workflow ngay thuong chay qua `session_scan.py`.

- Buoi sang: lay data sau 10:30, quet rong truoc, den sau 12:30 quet lai ma trong note/danh muc va cac co manh roi moi gui Telegram.
- Buoi chieu: lay data sau 13:45, quet cac ma khong uu tien truoc; sau 14:00 quet lai note/co manh, gui report sau 14:15.
- EOD: chay sau 15:05, khong ep nhanh, uu tien ket qua muot va co trang thai VNINDEX.
- Cac ma trong `data/portfolio.json` va `data/notes.json` luon duoc dua vao focus scan vi day la nhom chiem ty trong lon trong danh muc.

Ket qua focus gan nhat duoc luu vao `data/session_alerts_latest.json`.

## Quet co hoi cuoi tuan

Workflow `Thieucutoo Weekend Opportunities` chay luc 08:30 va 14:30 Thu bay gio Viet Nam
de co them mot vong loc sau, va co the chay tay voi mode `test` hoac `full`.

Script `weekend_opportunities.py` la ban TradingAgents-lite cho CK VN:

- Valuation analyst: so PE/PB cua tung ma voi median nganh.
- Fundamental analyst: check ROE/ROA, EPS, bien loi nhuan, no vay.
- Technical analyst: dung lai diem scan, chiet khau gia, nen gia, near-break, failed-break.
- Sector analyst: xep hang nganh dang co dong tien va nen gia tot.
- Risk manager: tru diem khi PE/EPS am, failed-break, nganh yeu, chat luong thap.
- High-confidence: tach rieng nhom diem cao, risk thap, dinh gia tot va nganh ung ho.

Ket qua duoc gui Telegram va luu vao:

- `data/weekend_opportunities_latest.json`
- `data/weekend_opportunities_history.json`

## File danh muc

Sua `data/portfolio.json` de khai bao 4 ma dang nam giu:

```json
{
  "symbol": "VNM",
  "note": "ghi chu rieng",
  "buy_more_score": 78,
  "sell_score": 45,
  "position": "holding"
}
```

He thong se bao ve Telegram:

- Diem <= `sell_score`: canh ban/giam ty trong.
- Diem >= `buy_more_score`: canh mua them.
- O giua: giu/theo doi.

## Chay thu

Vao tab Actions -> Thieucutoo Scanner -> Run workflow -> mode `test`.

Sau khi test gui duoc Telegram, chay `morning`, `afternoon`, `eod` hoac de lich tu dong.
