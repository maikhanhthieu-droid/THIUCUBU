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
- 13:31 VN: quet cac ma chua uu tien truoc; 14:00 quet lai note/co manh/ma phien sang, muc tieu tra truoc 14:15.
- 15:05 VN: tong ket EOD sau ATC, co trang thai VNINDEX va canh bao risk.

Moi lan chay co random start ngan, chia nguon API, dung khoang 75-80% quota moi nguon va nghi ngau nhien de giam rui ro bi limit.

## Co che chong limit API

Scanner chia luong request qua `SCAN_API_SOURCES`, mac dinh `VCI,KBS,DNSE`.
Moi ma co mot nguon uu tien rieng va nguon con lai lam fallback. Truoc moi request co
sleep + jitter ngau nhien, neu mot nguon loi se cooldown ngau nhien truoc khi dung tiep.
Workflow phien chay qua `session_scan.py`, ben trong van dung `scan_safe.py` de boc lop bao ve API quanh scanner goc.
Lop `fetcher.py` cach ly scanner khoi thay doi import/API cua `vnstock`: `VCI/KBS` di qua
`vnstock` va co fallback HTTP truc tiep toi endpoint cua VCI/KBS neu `vnstock` loi. `DNSE` di qua
`vietfin` va co fallback HTTP truc tiep toi EnTrade neu adapter loi.
`VIETFIN` trong `SCAN_API_SOURCES` duoc hieu nhu alias cua `DNSE`, khong phai mot lane API rieng.
Neu API tra ve dau hieu rate-limit kem `Retry-After`, scanner uu tien dung dung thoi gian do
thay vi chi sleep co dinh. Neu mot nguon chi loi tam thoi lien tiep, nguon do bi park vai phut
roi tu duoc thu lai trong chinh run sau, khong lam chet ca phien quet.
Suc khoe tung nguon duoc ghi vao `data/source_health.json`; nguon diem qua yeu se bi day xuong
cuoi thu tu uu tien o run sau, nhung van co co hoi tu hoi phuc.
Neu tat ca nguon live deu fail, scanner duoc phep dung lai parquet cache cu toi da
`SCAN_STALE_CACHE_MAX_DAYS` ngay (mac dinh workflow ngay thuong 3 ngay, weekend 7 ngay) de van
co report tham khao thay vi im lang.

Mac dinh workflow dung `SCAN_SOURCE_LIMITS=VCI=20,KBS=20,DNSE=15` va
`SCAN_SOURCE_USAGE_RATIO=0.78`, tuc chi dung khoang 75-80% quota khai bao moi nguon.
`TCBS` khong duoc dung mac dinh vi source nay co the khong duoc `vnstock` 4.x ho tro on dinh.

Cac bien co the chinh trong workflow:

- `SCAN_API_SOURCES`: danh sach nguon, vi du `VCI,KBS,DNSE`.
- `VIETFIN`: alias cua `DNSE`; dung de tranh cau hinh sai, nhung khong tang them quota.
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
- Buoi chieu: lay data sau 13:31, quet cac ma khong uu tien truoc; sau 14:00 quet lai toi da 50 ma note/co manh/ma phien sang, gui report truoc 14:15 neu API khong ngheo mang bat thuong.
- EOD: chay sau 15:05, khong ep nhanh, uu tien ket qua muot va co trang thai VNINDEX.
- Cac ma trong `data/portfolio.json` va `data/notes.json` luon duoc dua vao focus scan vi day la nhom chiem ty trong lon trong danh muc.

Ket qua focus gan nhat duoc luu vao `data/session_alerts_latest.json`.
Neu den deadline (`morning_broad` mac dinh 11:13, `afternoon_split` mac dinh 14:13) ma chua quet
xong, scanner cat phan con lai va gui report voi du lieu da co. Muc tieu la dung gio hon la co
quet bang moi ma nhung tre co hoi mua ban.

Neu GitHub Actions bi tre/hut cron trong cac moc quan trong, workflow `Thieucutoo Scanner Watchdog`
se kiem tra sau do va tu dispatch lai scanner neu chua co report cung ngay. Day la lop fallback,
khong thay the scanner chinh.
Watchdog phien chieu chay sau deadline, luc 14:18 VN, va chi dispatch mode `afternoon_focus`
de quet nhanh nhom note/co manh thay vi quet rong lai tu dau.
Ngoai ra watchdog co cac moc quick fallback som: 10:39/11:12 VN cho buoi sang va 13:39/14:12 VN
cho buoi chieu. Cac moc nay chi dispatch neu chua co report cung phien va khong thay run scanner
dang khoe, giup he thong tu hoi phuc khi cron bi hut hoac run loi som.
Moi scanner run co hard timeout theo mode (`focus` 25 phut, morning broad 55 phut, afternoon 50 phut,
EOD 160 phut) de mot run treo khong nam do ca ngay.

## Ngay nghi / data khong doi

Bot doc lich nghi trong `data/market_holidays.json`. Workflow production mac dinh
`MARKET_CLOSED_POLICY=scan_old`: neu hom nay la ngay nghi/le, bot van quet bang du lieu moi nhat
API tra ve va gan canh bao DATA CU trong report. Muc tieu la he thong ben, khong chet chi vi
lich nghi chua khai bao hoac API tra du lieu phien cu.

Ngoai lich nghi khai bao, scanner con co activity probe tu dong. Truoc khi quet nang, bot lay mau
khoang 38 ma lon/bluechip/nganh dan dat va luu moc vao `data/market_probe_state.json`. Neu mau nay
cho thay hang loat ma khong doi so voi lan truoc, ngay candle cu, hoac volume bang 0, bot se ket luan
thi truong nghi / API chua cap nhat / data dang dung. Mac dinh `MARKET_ACTIVITY_PROBE_ACTION=warn`:
bot van quet tiep va chi chen canh bao vao report, de stale data khong lam sap workflow.

Neu muon doi sang kieu nghi han vao ngay nghi, doi bien workflow thanh:

```yaml
MARKET_CLOSED_POLICY: "skip"
```

Neu muon activity probe dung som khi data dung, doi `MARKET_ACTIVITY_PROBE_ACTION=skip`.
Bien `MARKET_ACTIVITY_PROBE_ENABLED=0` co the tat lop probe nay neu can test tay.

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

## Run journal va fallback

Moi lan scanner/weekend chay se ghi `data/run_journal.json` voi trang thai `started`, `success`
hoac `failed`, so ma OK/fail, elapsed time va viec Telegram da gui hay chua. Neu scanner gap loi
giua phien, `session_plus.py` se co gang gui mot fallback report ngan gom loi, memory gan nhat va
suc khoe API. Workflow van de fail de watchdog co the dispatch lai neu chua co report moi dung phien.

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
