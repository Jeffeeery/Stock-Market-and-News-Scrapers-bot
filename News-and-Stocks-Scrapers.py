import os
import json
import hashlib
import requests
import feedparser
import yfinance as yf
from google import genai
from google.genai import types
from datetime import datetime, timezone

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
# 3. 轨道一：常规长篇简报 (双频道 + AI 量化情绪打分)
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

    # --- 抓取双频道新闻并进行 AI 情绪打分 ---
    try:
        # 📡 我们架设两根天线：一根听中东，一根听华尔街
        rss_feeds = {
            "🌍 宏观地缘": "https://news.google.com/rss/search?q=Middle+East+conflict+OR+US+Iran+war&hl=en-US&gl=US",
            "🏦 华尔街/美联储": "https://news.google.com/rss/search?q=Wall+Street+OR+Federal+Reserve+OR+Stock+Market&hl=en-US&gl=US"
        }
        
        new_articles = []
        news_text_for_ai = ""
        news_display_msg = ""

        # 遍历我们的两个频道
        for category, url in rss_feeds.items():
            feed = feedparser.parse(url)
            count = 0
            for article in feed.entries:
                if count >= 3: break # 每个频道最多只抓 3 条最新未读的，防止信息过载
                
                # 查数据库：如果没发过，才记录下来
                if not check_and_mark_news_seen(article.link):
                    new_articles.append(article)
                    news_display_msg += f"<b>{category}</b>: <a href='{article.link}'>{article.title}</a>\n"
                    news_text_for_ai += f"[{category}] {article.title}\n"
                    count += 1
                
        if not new_articles:
            msg += "➖ 过去 4 小时内无未读重大资讯，情绪指标维持现状。\n"
        else:
            msg += "📰 <b>【未读资讯与 AI 情绪研判】</b>\n"
            msg += news_display_msg
            
            # 🚀 召唤大模型进行量化分析！
            if ai_client:
                prompt = f"""
                你是一个顶级的华尔街宏观分析师。请阅读以下最新的【地缘政治】与【华尔街金融】新闻标题。
                请评估它们对全球市场（股市、原油、加密货币等）的综合情绪影响。
                请严格以 JSON 格式输出，不要有任何其他文字：
                {{
                    "score": 填入一个 -100 到 100 的整数（-100代表极其恐慌/崩盘，0代表中性，100代表极其乐观/大牛市）,
                    "trend": "看空" 或 "震荡" 或 "看多",
                    "analysis": "用50个字以内，极其犀利、专业地总结这些资金面和消息面资讯对大盘的利好或利空逻辑"
                }}
                
                新闻标题：
                {news_text_for_ai}
                """
                
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
# ==========================================
# 4. 轨道二：高频紧急预警 (全量数据库防重版)
# ==========================================
def emergency_monitor():
    print("\n🚨 正在执行紧急暴雷扫描...")
    alerts = []
    
    # --- 1. 扫描盘面暴跌 (加装记忆拦截器) ---
    for symbol, name in TICKERS.items():
        try:
            hist = yf.Ticker(symbol).history(period="5d")
            if len(hist) >= 2:
                current_price = hist['Close'].iloc[-1]
                prev_close = hist['Close'].iloc[-2]
                change_pct = ((current_price - prev_close) / prev_close) * 100
                
                # 提取最新交易日的日期，做成独一无二的盘面指纹
                latest_date = hist.index[-1].strftime('%Y-%m-%d')
                price_fingerprint = f"price_alert_{symbol}_{latest_date}"
                
                alert_msg = None
                if symbol == '^VIX' and current_price > 35:
                    alert_msg = f"🚨 <b>【极端恐慌】</b> VIX 突破 {current_price:.2f}！"
                elif symbol == 'CL=F' and change_pct > 8.0:
                    alert_msg = f"🛢️ <b>【原油暴涨】</b> 油价飙升 {change_pct:.2f}%！"
                elif symbol == 'MAGS' and change_pct < -5.0:
                    alert_msg = f"📉 <b>【美股熔断级下跌】</b> 科技巨头暴跌 {change_pct:.2f}%！"
                
                # 如果触发了报警，先查数据库！拦截周六日的重复复读！
                if alert_msg:
                    if not check_and_mark_news_seen(price_fingerprint):
                        alerts.append(alert_msg)
        except:
            pass
            
    if alerts:
        send_telegram("\n".join(alerts))

    # --- 2. 扫描突发核新闻 ---
    try:
        url = "https://news.google.com/rss/search?q=US+Iran+conflict&hl=en-US&gl=US"
        extreme_keywords = ["nuclear", "assassinated", "war", "strike", "missile"]
        
        import feedparser # 确保作用域内有这个库
        for article in feedparser.parse(url).entries[:5]:
            if any(kw in article.title.lower() for kw in extreme_keywords):
                if check_and_mark_news_seen(article.link): continue
                if not ai_client: continue
                
                prompt = f"判断该新闻是否陈述了真实的、已发生的、对全球有毁灭打击的事件。必须输出JSON: {{\"is_critical\": true/false, \"reason\": \"...\"}}。标题：{article.title}"
                try:
                    resp = ai_client.models.generate_content(
                        model='gemini-2.5-flash',
                        contents=prompt,
                        config=types.GenerateContentConfig(response_mime_type="application/json")
                    )
                    import json
                    res = json.loads(resp.text)
                    if res.get("is_critical"):
                        send_telegram(f"☢️ <b>【高信度战争预警】</b>\n{article.title}\n<a href='{article.link}'>阅读原文</a>")
                except:
                    pass
    except:
        pass

# ==========================================
# 6. 新增轨道：社交媒体 VIP 领袖情绪监控
# ==========================================
def extract_tweets_from_json(data):
    """
    智能递归提取器：无视复杂的 JSON 结构，强行抓取所有 'full_text'
    """
    texts = []
    if isinstance(data, dict):
        if 'full_text' in data:
            texts.append(data['full_text'])
        for key, value in data.items():
            texts.extend(extract_tweets_from_json(value))
    elif isinstance(data, list):
        for item in data:
            texts.extend(extract_tweets_from_json(item))
    return texts

def twitter_vip_monitor(target_id="25073877", target_name="realDonaldTrump"):
    print(f"\n🦅 正在暗中扫描 X (Twitter) 领袖账号: @{target_name}")
    
    rapidapi_key = os.environ.get("RAPIDAPI_KEY") 
    rapidapi_host = os.environ.get("RAPIDAPI_HOST")
    
    if not rapidapi_key or not rapidapi_host:
        print("⚠️ 未配置 RapidAPI 密钥，跳过 Twitter 扫描。")
        return

    # 这里填入你在 RapidAPI Code Snippet 里看到的真实的 url 和参数
    # 以下为示范，请对照你的 Snippet 确认参数名叫什么（比如是 user 还是 id）
    url = f"https://{rapidapi_host}/user/tweets" # <--- 注意替换成你真实的 URL
    querystring = {"user": target_id, "count": "20"} # 用我们刚才查到的纯数字 ID
    
    headers = {
        "x-rapidapi-key": rapidapi_key,
        "x-rapidapi-host": rapidapi_host
    }

    try:
        response = requests.get(url, headers=headers, params=querystring)
        raw_json = response.json()
        
        # 🚀 使用我们的智能提取器，把几万行的 JSON 浓缩成几句话！
        all_tweets = extract_tweets_from_json(raw_json)
        
        # 过滤掉太短的废话，只保留前 3 条有价值的推文
        valid_tweets = [t for t in all_tweets if len(t) > 20][:3]
        
        new_tweets = []
        for tweet_text in valid_tweets:
            # 【防重拦截】：把推文内容做成指纹存进 Upstash
            if not check_and_mark_news_seen(tweet_text):
                new_tweets.append(f"• {tweet_text}")

        if not new_tweets:
            print(f"➖ @{target_name} 过去几小时内无未读新发言。")
            return

        # 召唤 Gemini 首席宏观交易员进行情绪研判！
        tweets_str = "\n\n".join(new_tweets)
        msg = f"🦅 <b>【领袖情报拦截: @{target_name}】</b>\n\n{tweets_str}\n"
        
        if ai_client:
            prompt = f"""
            你是一位顶级的华尔街宏观交易员。以下是美国领袖或重要人物在 X 上的最新发言。
            请评估这些发言对全球金融市场（特别是美股、加密货币、原油）的潜在影响。
            必须严格输出 JSON 格式：
            {{
                "score": 填入 -100 到 100 的整数 (负代表恐慌/制裁/战争利空，正代表狂热/减税利好),
                "target_asset": "最可能受此推文波动的资产(如：BTC, 原油, 美元, 无)",
                "analysis": "用50字犀利解读这条推文的言外之意和潜在交易信号"
            }}
            推文内容：
            {tweets_str}
            """
            
            from google.genai import types
            import json
            resp = ai_client.models.generate_content(
                model='gemini-2.5-flash',
                contents=prompt,
                config=types.GenerateContentConfig(response_mime_type="application/json")
            )
            ai_result = json.loads(resp.text)
            
            score = ai_result.get('score', 0)
            emoji = "🥶" if score < -30 else ("🔥" if score > 30 else "🤔")
            
            msg += f"\n🧠 <b>AI 推文情绪解析</b>: {score} {emoji}\n"
            msg += f"🎯 <b>异动雷达</b>: {ai_result.get('target_asset')}\n"
            msg += f"💡 <b>深度解读</b>: {ai_result.get('analysis')}\n"
            
        send_telegram(msg)

    except Exception as e:
        print(f"❌ Twitter 抓取或分析失败: {e}")
# ==========================================
# 5. 云端智能调度中心
# ==========================================
if __name__ == "__main__":
    print("🚀 云端智能雷达 (v3.1 架构分离版) 启动...")
    
    # 无论谁唤醒，先静默扫描一遍有没有世界末日或熔断暴跌
    emergency_monitor()

    # 判断唤醒来源：是不是手动点击的，或者是 cron-job.org 打来的专线电话？
    event_name = os.environ.get('GITHUB_EVENT_NAME')
    is_vip_trigger = event_name in ['workflow_dispatch', 'repository_dispatch']

    if is_vip_trigger:
        print("👆 检测到 VIP 专线或手动触发，立即生成长篇情绪简报！")
        routine_report()
    else:
        print("➖ 15分钟常规巡逻结束。未触发报警，系统静默待命。")






