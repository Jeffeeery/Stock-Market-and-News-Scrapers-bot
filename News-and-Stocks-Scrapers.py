import os
import json
import requests
import feedparser
import yfinance as yf
import google.generativeai as genai

# ==========================================
# 1. 安全读取环境变量 (绝不能在这里写死密钥！)
# ==========================================
TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")

# 初始化 AI
if GEMINI_KEY:
    genai.configure(api_key=GEMINI_KEY)

# ==========================================
# 2. 基础通信组件
# ==========================================
def send_telegram(message):
    """负责将消息推送到你的手机"""
    if not TOKEN or not CHAT_ID:
        print("未检测到 Telegram 密钥，跳过发送。")
        return
        
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        response = requests.post(url, json=payload)
        if response.status_code == 200:
            print("✅ 警报已成功推送到 Telegram！")
        else:
            print(f"❌ Telegram 发送失败: {response.text}")
    except Exception as e:
        print(f"❌ 网络请求错误: {e}")

# ==========================================
# 3. 市场异动监控器 (大盘与资产)
# ==========================================
def check_market_emergency():
    """扫描金融市场，只有发生重大异动时才报警"""
    print("\n📊 开始扫描市场数据...")
    
    tickers = {
        '^VIX': '恐慌指数', 
        'MAGS': '科技七巨头 ETF',
        'CL=F': '原油(WTI)',
        'BTC-USD': '比特币'
    }
    
    alerts = []
    
    for symbol, name in tickers.items():
        try:
            # 获取最近 5 天的数据（防止周末休市导致取不到“昨天”）
            hist = yf.Ticker(symbol).history(period="5d")
            if len(hist) >= 2:
                prev_close = hist['Close'].iloc[-2] # 昨天收盘
                current_price = hist['Close'].iloc[-1] # 当前最新
                change_pct = ((current_price - prev_close) / prev_close) * 100
                
                print(f"{name} ({symbol}): 当前 {current_price:.2f}, 涨跌 {change_pct:+.2f}%")
                
                # 【极其严苛的报警阈值设定】
                if symbol == '^VIX' and current_price > 35:
                    alerts.append(f"🚨 <b>【极端恐慌】</b> VIX 突破 {current_price:.2f}！")
                elif symbol == 'MAGS' and change_pct < -5.0:
                    alerts.append(f"📉 <b>【美股熔断级下跌】</b> 科技巨头暴跌 {change_pct:.2f}%！")
                elif symbol == 'CL=F' and change_pct > 8.0:
                    alerts.append(f"🛢️ <b>【原油暴涨】</b> 疑有断供风险，油价飙升 {change_pct:.2f}%！")
        except Exception as e:
            print(f"获取 {symbol} 失败: {e}")

    # 如果有警报，合并发送
    if alerts:
        send_telegram("\n".join(alerts))
    else:
        print("✅ 盘面无极端异常。")

# ==========================================
# 4. 新闻 AI 双层漏斗核查器
# ==========================================
def verify_with_ai(title):
    """第二层：调用 Gemini 进行语义测谎"""
    if not GEMINI_KEY:
        return {"is_critical": False, "reason": "未配置 API Key"}
        
    print(f"🤖 触发第一层敏感词，呼叫 AI 深度核查: {title}")
    model = genai.GenerativeModel('gemini-1.5-flash-latest') # 使用兼容性强的名字
    
    prompt = f"""
    你是一个极其冷静且专业的国际地缘政治评估系统。
    判断以下新闻标题是否陈述了【真实的、已经发生的、对全球大局有毁灭性打击】的事件。
    （如：核武攻击、国家首脑遇刺、全面战争爆发）。
    排除：未来预测、演习、谈判、制裁、或标题党。
    
    必须输出合法 JSON，格式：
    {{"is_critical": true或false, "confidence": 0.0-1.0, "reason": "20字内解释"}}
    
    标题：{title}
    """
    try:
        response = model.generate_content(prompt)
        clean_text = response.text.strip().strip('`').replace('json\n', '')
        return json.loads(clean_text)
    except Exception as e:
        print(f"⚠️ AI 核查出错: {e}")
        return {"is_critical": False, "confidence": 0, "reason": "API出错"}

def check_news_emergency():
    """第一层：抓取最新 RSS 并用关键词过滤"""
    print("\n📰 开始抓取并分析最新资讯...")
    url = "https://news.google.com/rss/search?q=US+Iran+conflict&hl=en-US&gl=US"
    feed = feedparser.parse(url)
    
    # 我们只看最近更新的前 5 条新闻，避免查旧账
    recent_entries = feed.entries[:5]
    extreme_keywords = ["nuclear", "assassinated", "war", "strike", "missile"]
    
    for article in recent_entries:
        title_lower = article.title.lower()
        
        # 第一层：本地词库粗筛
        if any(keyword in title_lower for keyword in extreme_keywords):
            # 第二层：AI 精准判定
            ai_judgment = verify_with_ai(article.title)
            
            if ai_judgment.get("is_critical"):
                alert_msg = (
                    f"☢️ <b>【高信度战争预警】</b>\n\n"
                    f"<b>事件:</b> {article.title}\n"
                    f"<b>AI确信度:</b> {ai_judgment.get('confidence')}\n"
                    f"<b>判断理由:</b> {ai_judgment.get('reason')}\n\n"
                    f"<a href='{article.link}'>阅读原文</a>"
                )
                send_telegram(alert_msg)
                print("🚨 已发送高级别新闻警报！")
            else:
                print(f"🛡️ 假阳性拦截 (理由: {ai_judgment.get('reason')})")
        else:
             print(f"➖ 忽略常规新闻: {article.title}")

# ==========================================
# 5. 云端执行入口 (只运行一次)
# ==========================================
if __name__ == "__main__":
    print("🚀 系统启动：开始执行雷达扫描...")
    check_market_emergency()
    check_news_emergency()
    print("\n✅ 本次巡逻任务结束，进程退出。")