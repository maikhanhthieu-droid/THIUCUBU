# Thieucutoo Scanner

Bot quet co phieu VN theo huong it nhung chat: chiet khau, di nen, vol kiet, OBV/MFI, sap break, break xit va danh muc dang nam giu.

## Can tao GitHub Secrets

Vao repo -> Settings -> Secrets and variables -> Actions, tao:

- `TELEGRAM_TOKEN`
- `TELEGRAM_CHAT_ID`
- `VNSTOCK_API_KEY`

Khong dua API key vao file code vi repo public.

## Lich chay

- 10:31 VN: quet rong buoi sang, muc tieu tra Telegram truoc 11:15.
- 13:46 VN: quet cac ma chua uu tien truoc; 14:03 quet lai note/co manh/ma phien sang, muc tieu tra truoc 14:15.
- 15:05 VN: tong ket EOD sau ATC, co trang thai VNINDEX va canh bao risk.

Moi lan chay co random start ngan, chia nguon API, dung khoang 75-80% quota moi nguon va nghi ngau nhien de giam rui ro bi limit.

## Co che chong limit API

Scanner chia luong request qua `SCAN_API_SOURCES`, mac dinh `VCI,KBS,DNSE`.
Moi ma co mot nguon uu tien rieng va nguon con lai lam fallback. Truoc moi request co
sleep + jitter ngau nhien, neu mot nguon loi se cooldown ngau nhien truoc khi dung tiep.
Workflow phien chay qua `session_scan.py`, ben trong van dung `scan_safe.py` de boc lop bao ve API quanh scanner goc.
Neu API tra ve dau hieu rate-limit kem `Retry-After`, scanner uu tien dung dung thoi gian do
thay vi chi sleep co dinh. Neu mot nguon chi loi tam thoi lien tiep, nguon do bi park vai phut
roi tu duoc thu lai trong chinh run sau, khong lam chet ca phien quet.

Mac dinh workflow dung `SCAN_SOURCE_LIMITS=VCI=20,KBS=20,DNSE=15` va
`SCAN_SOURCE_USAGE_RATIO=0.78`, tuc chi dung khoang 75-80% quota khai bao moi nguon.
`TCBS` khong duoc dung mac dinh vi source nay co the khong duoc `vnstock` 4.x ho tro on dinh.

Cac bien co the chinh trong workflow:

- `SCAN_API_SOURCES`: danh sach nguon, vi du `VCI,KBS,DNSE`.
- `SCAN_SOURCE_REQUESTS_PER_MINUTE`: tran request/phut cua moi nguon.
- `SCAN_SOURCE_LIMITS`: tran rieng tung nguon, vi du `VCI=20,KBS=20,DNSE=15`.
- `SCAN_SOURCE_USAGE_RATIO`: ty le dung quota, mac dinh workflow `0.78`.
- `SCAN_REQUEST_JITTER_MIN_SEC` / `SCAN_REQUEST_JITTER_MAX_SEC`: jitter truoc moi request.
- `SCAN_SOURCE_ERROR_COOLDOWN_MIN_SEC` / `SCAN_SOURCE_ERROR_COOLDOWN_MAX_SEC`: cooldown khi nguon loi.
- `SCAN_SOURCE_RECOVER_AFTER_SEC`: thoi gian park mot nguon loi tam thoi lien tiep truoc khi thu lai.
- `SCAN_RETRY_AFTER_MAX_SEC`: tran toi da khi doc `Retry-After` tu API.
- `SCAN_MAX_WORKERS`: so luong worker song song, nen <= so nguon API.

## Dieu phoi theo phien

Workflow ngay thuong chay qua `session_scan.py`.

- Buoi sang: lay data sau 10:31, quet rong va gui report som de kip soi trong phien.
- Buoi chieu: lay data sau 13:46, quet cac ma khong uu tien truoc; sau 14:03 quet lai note/co manh/ma phien sang, gui report truoc 14:15 neu API khong ngheo mang bat thuong.
- EOD: chay sau 15:05, khong ep nhanh, uu tien ket qua muot va co trang thai VNINDEX.
- Cac ma trong `data/portfolio.json` va `data/notes.json` luon duoc dua vao focus scan vi day la nhom chiem ty trong lon trong danh muc.

Ket qua focus gan nhat duoc luu vao `data/session_alerts_latest.json`.

Neu GitHub Actions bi tre/hut cron trong cac moc quan trong, workflow `Thieucutoo Scanner Watchdog`
se kiem tra sau do va tu dispatch lai scanner neu chua co report cung ngay. Day la lop fallback,
khong thay the scanner chinh.

## Ngay nghi / data khong doi

Bot doc lich nghi trong `data/market_holidays.json`. Neu hom nay la thu 7, chu nhat hoac ngay nghi
co trong file nay, mac dinh `MARKET_CLOSED_POLICY=skip`: bot gui mot notice ngan, ghi marker vao
`data/session_alerts_latest.json`, roi dung quet de watchdog khong dispatch lap lai.

Neu van muon quet data cu trong ngay nghi, doi bien workflow thanh:

```yaml
MARKET_CLOSED_POLICY: "scan_old"
```

Khi do scanner van chay va dung du lieu moi nhat ma API tra ve, chap nhan tin hieu co the la stale.

## Tri nho cua bot

Bot co file `data/memory_state.json` va lop `StateManager` trong `state_manager.py` de giu tri nho
giua cac lan chay GitHub Actions:

- `strong_stocks`: toi da 7 ma dang rat manh/dong tien tot.
- `watchlist`: toi da 15 ma dang co form nen/VCP/VSA can theo doi 1-3 tuan.
- `session_focus`: toi da 40 ma uu tien cho lan quet nhanh tiep theo.
- `retired`: cac ma bi loai do failed-break hoac diem yeu.

File nay chi luu trang thai gon nhe, khong luu OHLCV day du. State duoc cap version/timestamp,
gioi han toi da 7 ma manh, 15 ma watchlist, 40 ma focus, va tu prune entry cu qua han de file
khong phinh vo han. Sau moi phien quet that, workflow se commit lai file nay voi `[skip ci]`
de lan chay sau bot van nho nhom co can uu tien.

## Quet co hoi cuoi tuan

Workflow `Thieucutoo Weekend Opportunities` chay luc 08:30 va 14:30 Thu bay gio Viet Nam
de co them mot vong loc sau, va co the chay tay voi mode `test` hoac `full`.

Workflow goi `weekend_plus_safe.py`, file nay boc `weekend_plus.py` va `weekend_opportunities.py`
de chong quota kill, them format Telegram dep hon va chay weekend scan song song co gioi han.
Script `weekend_opportunities.py` la loi TradingAgents-lite cho CK VN:

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

Sua `data/portfolio.json` de khai bao cac ma dang nam giu:

Luu y: file nay bat buoc la JSON array/list `[...]`, khong phai single object `{...}`;
neu khai bao sai dang thi danh muc se khong duoc dua vao focus scan.

```json
[
  {
    "symbol": "VNM",
    "note": "ghi chu rieng",
    "buy_more_score": 78,
    "sell_score": 45,
    "position": "holding"
  }
]
```

Sua `data/notes.json` de them ma can theo doi sat ngoai danh muc:

```json
{
  "VCB": "ghi chu rieng",
  "FPT": {
    "note": "uu tien bao khi tin hieu xau/tot"
  }
}
```

He thong se bao ve Telegram:

- Diem <= `sell_score`: canh ban/giam ty trong.
- Diem >= `buy_more_score`: canh mua them.
- O giua: giu/theo doi.

## Chay thu

Vao tab Actions -> Thieucutoo Scanner -> Run workflow -> mode `test`.

Sau khi test gui duoc Telegram, chay `morning`, `afternoon`, `eod` hoac de lich tu dong.
