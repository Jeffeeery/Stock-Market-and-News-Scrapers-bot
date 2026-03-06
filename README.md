# Stock-Market-and-News-Scrapers-bot
# AI-Powered Market & Sentiment Radar (Serverless)
**智能量化情绪与市场监控雷达 (无服务器架构)**

An automated, serverless financial intelligence bot that tracks global geopolitical news and Wall Street dynamics, leverages Large Language Models (LLMs) for quantitative sentiment scoring, and delivers real-time alerts via Telegram. 

本项目是一个基于 Serverless (无服务器) 架构的自动化金融情报机器人。它能全天候追踪全球地缘政治与华尔街动态，利用大语言模型 (LLM) 进行量化情绪打分，并通过 Telegram 进行实时推送。

---

## 🌟 Key Features | 核心亮点

* **AI Quantitative Sentiment (AI 量化情绪大脑):** Uses `google-genai` (Gemini 2.5) to parse unstructured news and output strict JSON with sentiment scores (-100 to +100) and concise market analysis.
* **Idempotency & Cost Optimization (幂等性与成本优化):** Integrates **Upstash Redis** to generate MD5 fingerprints for news URLs, ensuring zero duplicate alerts and saving up赌 90% of redundant AI API calls.
* **Serverless Precision (无服务器精准调度):** Bypasses GitHub Actions' native cron delays by exposing a `repository_dispatch` Webhook, allowing external precise triggers (e.g., cron-job.org) for zero-latency execution.
* **Dual-Channel Tracking (双核信息天线):** Simultaneously tracks macro-geopolitics (wars/conflicts) and Wall Street financials (Federal Reserve/Tech Stocks).



## Tech Stack | 技术栈

* **Language:** Python 3.12
* **Infrastructure:** GitHub Actions (CI/CD & Compute)
* **Database:** Upstash Serverless Redis (State Management & TTL Caching)
* **AI Engine:** Google Gemini 2.5 Flash (`google-genai` SDK)
* **Data Sources:** `yfinance` (Market Data), `feedparser` (Google News RSS)
* **Notification:** Telegram Bot API

---

## Quick Start | 快速部署

### 1. Prerequisites (准备工作)
* A Telegram Bot Token and Chat ID.
* A Google Gemini API Key.
* A free Upstash Redis database (URL & Token).
* A GitHub Personal Access Token (PAT) with `repo` scope for Webhook triggering.

### 2. Environment Variables (配置环境变量)
Add the following to your GitHub repository's **Secrets**:
`TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID`, `GEMINI_API_KEY`, `UPSTASH_REDIS_REST_URL`, `UPSTASH_REDIS_REST_TOKEN`.

### 3. Execution (运行)
The script is designed to run completely hands-off in the cloud. It can be triggered via:
* **Automated:** External Webhook via POST request with `{"event_type": "precision-strike"}` payload.
* **Manual:** GitHub Actions `workflow_dispatch`.
  
---
