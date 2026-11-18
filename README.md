# Denny Signal

以 Yahoo Finance 日收盤資料追蹤 200 個交易日的 MACD 股票線型警示儀表板。

## 訊號規則

- GREEN：MACD 線由零軸下方穿越到零軸上方
- RED：MACD 線由零軸上方跌破零軸
- YELLOW：MACD 線由下往上穿越訊號線（黃金交叉）
- BLACK：MACD 線由上往下跌破訊號線（死亡交叉）
- BLUE：最新交易日沒有新交叉，持續監測

MACD = EMA(12) - EMA(26)，訊號線 = MACD 的 EMA(9)。同日同時觸發時，零軸事件優先於訊號線交叉事件。

## 開發與驗證

```bash
npm run build
npm run validate
```

正式站台：<https://denny.usdollar.win>

> 僅供資訊參考，不構成投資建議。
