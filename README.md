# Thieucutoo Scanner

Bot quet co phieu VN theo huong it nhung chat: chiet khau, di nen, vol kiet, OBV/MFI, sap break, break xit va danh muc dang nam giu.

## Can tao GitHub Secrets

Vao repo -> Settings -> Secrets and variables -> Actions, tao:

- `TELEGRAM_TOKEN`
- `TELEGRAM_CHAT_ID`
- `VNSTOCK_API_KEY`

Khong dua API key vao file code vi repo public.

## Lich chay

- 10:01 VN: quet sang.
- 13:31 VN: quet chieu.
- 15:10 VN: tong ket EOD.

Moi lan chay co random start 0-5 phut, quet 10 ma/phut va nghi ngau nhien de giam rui ro bi limit.

## Co che chong limit API

Scanner chia luong request qua `SCAN_API_SOURCES`, mac dinh `VCI,TCBS`.
Moi ma co mot nguon uu tien rieng va nguon con lai lam fallback. Truoc moi request co
sleep + jitter ngau nhien, neu mot nguon loi se cooldown ngau nhien truoc khi dung tiep.
Workflow chay qua `scan_safe.py` de boc lop bao ve API quanh scanner goc.

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
