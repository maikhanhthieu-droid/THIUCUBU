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
