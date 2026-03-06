import os
import json
import hashlib
import requests
import feedparser
import yfinance as yf
from google import genai
from google.genai import types
from datetime import datetime

# ==========================================
# 1. 密钥加载与全新 AI 客户端初始化
# ==========================================
TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")
UPSTASH_URL = os.environ.get("UPSTASH_REDIS_REST_URL")
UPSTASH_TOKEN = os.environ.get("UPSTASH_REDIS_REST_TOKEN")

# 【新版 SDK 核心改动】：使用 Client 架构
ai_client = genai.Client(api_key=GEMINI_KEY) if GEMINI_KEY else None

TICKERS = {'CL=F': '原油(WTI)', 'GC=F': '黄金', 'SI=F': '白银', 'MAGS': '科技七巨头', 'BTC-USD': '比特币', '^VIX': '恐慌指数'}

# ==========================================
# 2. 基础通信与数据库防重组件
# ==========================================
def send_telegram(message):
    if not TOKEN or not CHAT_ID:
        print("❌ 未检测到 Telegram 密钥，跳过发送。")
        return
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML", "disable_web_page_preview": True}
    try:
        resp = requests.post(url, json=payload)
        if resp.status_code != 200:
            print(f"❌ Telegram 发送失败: {resp.text}")
        else:
            print("✅ 消息成功推送到 Telegram！")
    except Exception as e:
        print(f"❌ 网络异常: {e}")

def check_and_mark_news_seen(news_url):
    if not UPSTASH_URL or not UPSTASH_TOKEN:
        return False 
    url_fingerprint = hashlib.md5(news_url.encode('utf-8')).hexdigest()
    headers = {"Authorization": f"Bearer {UPSTASH_TOKEN}"}
    try:
        check_url = f"{UPSTASH_URL}/get/{url_fingerprint}"
        if requests.get(check_url, headers=headers).json().get('result') is not None:
            return True 
        save_url = f"{UPSTASH_URL}/set/{url_fingerprint}/1/EX/604800"
        requests.get(save_url, headers=headers)
        return False 
    except:
        return False

# ==========================================
# 3. 轨道一：常规长篇简报 (带 AI 量化情绪打分)
# ==========================================
def routine_report():
    print("\n📝 正在生成常规市场简报与情绪量化分析...")
    msg = "📊 <b>【市场情绪与盘面巡逻报告】</b>\n\n"

    # --- 获取盘面数据 ---
    msg += "📈 <b>【全球核心资产跟踪】</b>\n"
    for symbol, name in TICKERS.items():
        try:
            hist = yf.Ticker(symbol).history(period="5d")
            if len(hist) >= 2:
                prev_close = hist['Close'].iloc[-2]
                current_price = hist['Close'].iloc[-1]
                diff_val = current_price - prev_close
                diff_pct = (diff_val / prev_close) * 100
                trend = "🟢" if diff_val > 0 else "🔴"
                msg += f"• <b>{name}</b>: {current_price:.2f} | {trend}{diff_pct:+.2f}%\n"
        except:
            pass
    msg += "\n"

    # --- 抓取新闻并进行 AI 情绪打分 ---
    try:
        url = "https://news.google.com/rss/search?q=US+Iran+conflict&hl=en-US&gl=US"
        feed = feedparser.parse(url)
        
        new_articles = []
        for article in feed.entries[:15]: 
            if len(new_articles) >= 5: break 
            if not check_and_mark_news_seen(article.link):
                new_articles.append(article)
                
        if not new_articles:
            msg += "➖ 过去 4 小时内无未读重大资讯，情绪指标维持现状。\n"
        else:
            msg += "📰 <b>【未读资讯与 AI 情绪研判】</b>\n"
            news_text_for_ai = ""
            for i, article in enumerate(new_articles):
                msg += f"{i+1}. <a href='{article.link}'>{article.title}</a>\n"
                news_text_for_ai += f"- {article.title}\n"
            
            # 🚀 召唤大模型进行量化分析！
            if ai_client:
                prompt = f"""
                你是一个顶级的华尔街宏观分析师。请阅读以下关于中东/全球局势的最新新闻标题，并评估其对全球市场（原油、科技股、避险资产）的潜在情绪影响。
                请严格以 JSON 格式输出，不要有任何其他文字：
                {{
                    "score": 填入一个 -100 到 100 的整数（-100代表极度恐慌/爆发战争，0代表中性，100代表极度和平乐观）,
                    "trend": "看空" 或 "震荡" 或 "看多",
                    "analysis": "用50个字以内，极其犀利、专业地总结这几条新闻对大盘潜藏的利好或利空逻辑"
                }}
                
                新闻标题：
                {news_text_for_ai}
                """
                
                # 【新版 SDK 调用方式】：强制要求返回干净的 JSON
                resp = ai_client.models.generate_content(
                    model='gemini-2.5-flash',
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                    ),
                )
                
                ai_result = json.loads(resp.text)
                score = ai_result.get('score', 0)
                emoji = "🥶" if score < -30 else ("🔥" if score > 30 else "🤔")
                
                msg += f"\n🧠 <b>AI 量化情绪得分</b>: {score} {emoji} (趋势: {ai_result.get('trend')})\n"
                msg += f"💡 <b>核心研判</b>: {ai_result.get('analysis')}\n"

    except Exception as e:
        print(f"AI 或新闻获取异常: {e}")
        msg += "获取新闻或分析失败。\n"

    send_telegram(msg)

# ==========================================
# 4. 轨道二：高频紧急预警 (新版 AI 调用)
# ==========================================
def emergency_monitor():
    print("\n🚨 正在执行紧急暴雷扫描...")
    alerts = []
    
    for symbol, name in TICKERS.items():
        try:
            hist = yf.Ticker(symbol).history(period="5d")
            if len(hist) >= 2:
                current_price = hist['Close'].iloc[-1]
                change_pct = ((current_price - hist['Close'].iloc[-2]) / hist['Close'].iloc[-2]) * 100
                if symbol == '^VIX' and current_price > 35:
                    alerts.append(f"🚨 <b>【极端恐慌】</b> VIX 突破 {current_price:.2f}！")
                elif symbol == 'CL=F' and change_pct > 8.0:
                    alerts.append(f"🛢️ <b>【原油暴涨】</b> 油价飙升 {change_pct:.2f}%！")
        except:
            pass
    if alerts:
        send_telegram("\n".join(alerts))

    url = "https://news.google.com/rss/search?q=US+Iran+conflict&hl=en-US&gl=US"
    extreme_keywords = ["nuclear", "assassinated", "war", "strike", "missile"]
    
    for article in feedparser.parse(url).entries[:5]:
        if any(kw in article.title.lower() for kw in extreme_keywords):
            if check_and_mark_news_seen(article.link): continue
            if not ai_client: continue
            
            prompt = f"判断该新闻是否陈述了真实的、已发生的、对全球有毁灭打击的事件。必须输出JSON: {{\"is_critical\": true/false, \"reason\": \"...\"}}。标题：{article.title}"
            try:
                # 【新版 SDK 调用方式】
                resp = ai_client.models.generate_content(
                    model='gemini-2.5-flash',
                    contents=prompt,
                    config=types.GenerateContentConfig(response_mime_type="application/json")
                )
                res = json.loads(resp.text)
                if res.get("is_critical"):
                    send_telegram(f"☢️ <b>【高信度战争预警】</b>\n{article.title}\n<a href='{article.link}'>阅读原文</a>")
            except:
                pass

# ==========================================
# 5. 云端智能调度中心
# ==========================================
if __name__ == "__main__":
    print("🚀 云端智能雷达 (v3.0 量化情绪版) 启动...")
    
    emergency_monitor()

    is_manual_trigger = os.environ.get('GITHUB_EVENT_NAME') in ['workflow_dispatch', 'repository_dispatch']
    utc_now = datetime.utcnow()
    kl_hour = (utc_now.hour + 8) % 24
    is_report_time = (kl_hour % 4 == 0) and (utc_now.minute < 30)

    if is_manual_trigger or is_report_time:
        routine_report()
    else:
        print(f"➖ 当前马来西亚时间 {kl_hour} 点 {utc_now.minute} 分，任务静默结束。")
