# Denny Signal

以 Yahoo Finance 日收盤資料追蹤 200 個交易日的 MACD 股票線型警示儀表板。

## 訊號規則

- GREEN：MACD 黑線位於零軸以上
- RED：MACD 黑線位於零軸以下
- YELLOW：MACD 黑線位於 SIGNAL 紅線以上
- BLACK：MACD 黑線位於 SIGNAL 紅線以下

每檔股票會同時顯示一個零軸燈號（GREEN／RED）與一個線位燈號（YELLOW／BLACK）。MACD = EMA(12) - EMA(26)，SIGNAL = MACD 的 EMA(9)。日 K 與 MACD 圖之間顯示每日成交量，顏色與同日 K 線一致。

## 開發與驗證

```bash
npm run build
npm run validate
```

正式站台：<https://denny.usdollar.win>

> 僅供資訊參考，不構成投資建議。
