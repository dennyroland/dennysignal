# Flask 美股 MACD 掃描版

此目錄包含最後本機版本的 Flask/PWA 美股訊號掃描器，與 repo 根目錄既有的 Cloudflare Worker 並存。

## Windows 啟動

需要 Python 3.10 或更新版本。於此資料夾開啟 PowerShell：

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env  # 填入自己的 Google Sheet ID
python app.py
```

在本機開啟 <http://localhost:5001>。Google Sheet 設定使用 `SHEET_ID` 和 `SHEET_GID`；請勿提交 `.env`。持股 SQLite 資料庫會建立於此資料夾的 `portfolio.db`。

## 功能

包含美股持股表、MACD(12,26,9) 個股圖表與訊號、S&P 500／醫療／金融／電信營運商／消費品／能源掃描、爆大量與 MACD 訊號組合篩選及手機 PWA 介面。詳細規則與使用說明請見本目錄的程式及 repo 根目錄 README。

本版行情資料使用 yfinance/Yahoo Finance；瀏覽器通知需使用 HTTPS，且網頁保持開啟時才會定時掃描。僅供資訊參考，不構成投資建議。
