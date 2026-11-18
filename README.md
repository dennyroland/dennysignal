# Denny Signal

以 Yahoo Finance 日收盤資料追蹤 200 個交易日的 MACD 股票線型警示儀表板。

## 訊號規則

- GREEN：MACD 黑線位於零軸以上
- RED：MACD 黑線位於零軸以下
- YELLOW：MACD 黑線位於 SIGNAL 紅線以上
- BLACK：MACD 黑線位於 SIGNAL 紅線以下

每檔股票會同時顯示一個零軸燈號（GREEN／RED）與一個線位燈號（YELLOW／BLACK）。MACD = EMA(12) - EMA(26)，SIGNAL = MACD 的 EMA(9)。日 K 與 MACD 圖之間顯示每日成交量，顏色與同日 K 線一致；三個圖層共用底部日期軸。

## 開發與驗證

```bash
npm run build
npm run validate
```

正式站台：<https://denny.usdollar.win>

> 僅供資訊參考，不構成投資建議。

## Flask 美股 MACD 掃描版

最後本機版本的 Flask/PWA 掃描器位於 [`flask-scanner/`](flask-scanner/)，與既有 Cloudflare Worker 並存。請依照 [Flask 掃描器說明](flask-scanner/README.md) 在本機啟動。Worker 的現有自動部署流程維持原樣。

Flask 版行情使用 yfinance，支援持股與訊號掃描；Google Sheet ID 放在本機 `.env`，持股資料存在本機 SQLite。不要提交 `.env` 或資料庫檔案。
