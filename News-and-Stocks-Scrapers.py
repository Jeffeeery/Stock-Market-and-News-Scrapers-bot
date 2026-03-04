import os
import json
import requests
import feedparser
import yfinance as yf
import google.generativeai as genai
from datetime import datetime

# ==========================================
# 1. 密钥加载
# ==========================================
TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")

if GEMINI_KEY:
    genai.configure(api_key=GEMINI_KEY)

TICKERS = {'CL=F': '原油(WTI)', 'GC=F': '黄金', 'SI=F': '白银', 'MAGS': '科技七巨头', 'BTC-USD': '比特币', '^VIX': '恐慌指数'}

# ==========================================
# 2. 基础通信组件
# ==========================================
def send_telegram(message):
    if not TOKEN or not CHAT_ID:
        print("未检测到 Telegram 密钥。")
        return
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML", "disable_web_page_preview": True}
    requests.post(url, json=payload)

# ==========================================
# 3. 轨道一：常规长篇简报 (价格对比昨日收盘)
# ==========================================
def routine_report():
    print("\n📝 正在生成常规市场简报...")
    msg = "📊 <b>【市场常规巡逻报告】</b>\n\n"

    for symbol, name in TICKERS.items():
        try:
            # Serverless 没有本地内存，所以我们直接拉取 5 天历史，对比倒数第一天(今天)和倒数第二天(昨天)
            hist = yf.Ticker(symbol).history(period="5d")
            if len(hist) >= 2:
                prev_close = hist['Close'].iloc[-2]
                current_price = hist['Close'].iloc[-1]
                diff_val = current_price - prev_close
                diff_pct = (diff_val / prev_close) * 100
                trend = "🟢" if diff_val > 0 else "🔴"
                msg += f"<b>{name}</b>:\n当前: {current_price:.2f} | 相比昨日: {trend}{diff_val:+.2f} ({diff_pct:+.2f}%)\n\n"
        except Exception:
            msg += f"<b>{name}</b>: 获取数据失败\n\n"

    msg += "📰 <b>【最新 5 条相关资讯】</b>\n"
    try:
        url = "https://news.google.com/rss/search?q=US+Iran+conflict&hl=en-US&gl=US"
        feed = feedparser.parse(url)
        for i, article in enumerate(feed.entries[:5]):
            msg += f"{i+1}. <a href='{article.link}'>{article.title}</a>\n"
    except Exception:
        msg += "获取新闻失败。\n"

    send_telegram(msg)
    print("✅ 常规报告已发送！")

# ==========================================
# 4. 轨道二：高频紧急预警 (AI 核查)
# ==========================================
def emergency_monitor():
    print("\n🚨 正在执行紧急暴雷扫描...")
    alerts = []
    for symbol, name in TICKERS.items():
        try:
            hist = yf.Ticker(symbol).history(period="5d")
            if len(hist) >= 2:
                prev_close = hist['Close'].iloc[-2]
                current_price = hist['Close'].iloc[-1]
                change_pct = ((current_price - prev_close) / prev_close) * 100
                
                if symbol == '^VIX' and current_price > 35:
                    alerts.append(f"🚨 <b>【极端恐慌】</b> VIX 突破 {current_price:.2f}！")
                elif symbol == 'MAGS' and change_pct < -5.0:
                    alerts.append(f"📉 <b>【美股熔断级下跌】</b> 科技巨头暴跌 {change_pct:.2f}%！")
                elif symbol == 'CL=F' and change_pct > 8.0:
                    alerts.append(f"🛢️ <b>【原油暴涨】</b> 油价飙升 {change_pct:.2f}%！")
        except Exception:
            pass
    
    if alerts:
        send_telegram("\n".join(alerts))

    # AI 新闻核查
    url = "https://news.google.com/rss/search?q=US+Iran+conflict&hl=en-US&gl=US"
    feed = feedparser.parse(url)
    extreme_keywords = ["nuclear", "assassinated", "war", "strike", "missile"]
    
    for article in feed.entries[:3]:
        title_lower = article.title.lower()
        if any(keyword in title_lower for keyword in extreme_keywords):
            if not GEMINI_KEY: continue
            model = genai.GenerativeModel('gemini-1.5-flash-latest')
            prompt = f"判断该新闻是否陈述了真实的、已发生的、对全球有毁灭打击的事件。必须输出JSON: {{\"is_critical\": true/false, \"reason\": \"...\"}}。标题：{article.title}"
            try:
                resp = model.generate_content(prompt)
                res = json.loads(resp.text.strip().strip('`').replace('json\n', ''))
                if res.get("is_critical"):
                    send_telegram(f"☢️ <b>【高信度战争预警】</b>\n{article.title}\n<a href='{article.link}'>阅读原文</a>")
            except:
                pass

# ==========================================
# 5. 云端智能调度中心
# ==========================================
if __name__ == "__main__":
    print("🚀 云端雷达启动...")
    
    # 第一步：无论如何，先查有没有天塌下来的大事
    emergency_monitor()

    # 第二步：情境感知，判断是否该发送长篇常规报告
    
    # 检查是否是你手动点击了 "Run workflow" 按钮
    is_manual_trigger = os.environ.get('GITHUB_EVENT_NAME') == 'workflow_dispatch'
    
    # 获取马来西亚当前时间 (UTC+8)
    utc_now = datetime.utcnow()
    kl_hour = (utc_now.hour + 8) % 24
    
    # 如果时间刚好是 0点, 4点, 8点, 12点, 16点, 20点，且是该小时的前 15 分钟内
    is_report_time = (kl_hour % 4 == 0) and (utc_now.minute < 15)

    if is_manual_trigger:
        print("👆 检测到手动触发，立即发送完整报告。")
        routine_report()
    elif is_report_time:
        print(f"⏰ 当前时间符合 4 小时巡逻周期 (马来西亚时间 {kl_hour} 点)，发送完整报告。")
        routine_report()
    else:
        print("➖ 当前不是发送长报告的时间，且盘面无暴雷，任务静默结束。")
