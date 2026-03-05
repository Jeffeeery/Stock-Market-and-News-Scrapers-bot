import os
import json
import hashlib
import requests
import feedparser
import yfinance as yf
import google.generativeai as genai
from datetime import datetime

# ==========================================
# 1. 密钥加载 (新增了 Upstash 数据库密钥)
# ==========================================
TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")
UPSTASH_URL = os.environ.get("UPSTASH_REDIS_REST_URL")
UPSTASH_TOKEN = os.environ.get("UPSTASH_REDIS_REST_TOKEN")

if GEMINI_KEY:
    genai.configure(api_key=GEMINI_KEY)

TICKERS = {'CL=F': '原油(WTI)', 'GC=F': '黄金', 'SI=F': '白银', 'MAGS': '科技七巨头', 'BTC-USD': '比特币', '^VIX': '恐慌指数'}

# ==========================================
# 2. 基础通信与【数据库防重组件】
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
    """
    核心记忆引擎：检查一条新闻是否处理过。
    如果没处理过，返回 False，并在数据库里登记（保存7天）。
    """
    if not UPSTASH_URL or not UPSTASH_TOKEN:
        print("⚠️ 未配置 Upstash 数据库，防重机制未启用，默认全部放行。")
        return False # 如果没配数据库，就按老规矩办，不拦截

    # 将长长的网址压缩成 32 位的 MD5 短指纹
    url_fingerprint = hashlib.md5(news_url.encode('utf-8')).hexdigest()
    headers = {"Authorization": f"Bearer {UPSTASH_TOKEN}"}
    
    try:
        # 1. 查询数据库：你有这个指纹吗？
        check_url = f"{UPSTASH_URL}/get/{url_fingerprint}"
        resp = requests.get(check_url, headers=headers).json()
        
        if resp.get('result') is not None:
            return True # 数据库里有，说明发过了
            
        # 2. 没发过：登记指纹，设定过期时间 EX 604800 (7天)
        save_url = f"{UPSTASH_URL}/set/{url_fingerprint}/1/EX/604800"
        requests.get(save_url, headers=headers)
        return False # 告诉主程序：这是一条新新闻！
        
    except Exception as e:
        print(f"❌ 数据库请求异常: {e}")
        return False # 数据库出错时，宁可错发也不漏发

# ==========================================
# 3. 轨道一：常规长篇简报 (带防重机制)
# ==========================================
def routine_report():
    print("\n📝 正在生成常规市场简报...")
    msg = "📊 <b>【市场常规巡逻报告】</b>\n\n"

    for symbol, name in TICKERS.items():
        try:
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

    msg += "📰 <b>【最新重大未读资讯】</b>\n"
    try:
        url = "https://news.google.com/rss/search?q=US+Iran+conflict&hl=en-US&gl=US"
        feed = feedparser.parse(url)
        
        new_news_count = 0
        for article in feed.entries[:10]: # 往前多看几条，防止前面都是发过的
            if new_news_count >= 5: break # 每次最多只推送5条新的
            
            # 【防重拦截】如果发过了，直接跳过
            if check_and_mark_news_seen(article.link):
                continue
                
            new_news_count += 1
            msg += f"{new_news_count}. <a href='{article.link}'>{article.title}</a>\n"
            
        if new_news_count == 0:
            msg += "➖ 过去 4 小时内无未读新资讯。\n"
            
    except Exception:
        msg += "获取新闻失败。\n"

    send_telegram(msg)

# ==========================================
# 4. 轨道二：高频紧急预警 (AI 核查前置阻拦)
# ==========================================
def emergency_monitor():
    print("\n🚨 正在执行紧急暴雷扫描...")
    alerts = []
    
    # --- 1. 扫描盘面暴跌 ---
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

    # --- 2. 扫描突发核新闻 ---
    url = "https://news.google.com/rss/search?q=US+Iran+conflict&hl=en-US&gl=US"
    feed = feedparser.parse(url)
    extreme_keywords = ["nuclear", "assassinated", "war", "strike", "missile"]
    
    for article in feed.entries[:5]:
        title_lower = article.title.lower()
        if any(keyword in title_lower for keyword in extreme_keywords):
            
            # 【省钱大法】先查数据库！如果以前发过了，就不要去浪费 Gemini 的 API 额度了！
            if check_and_mark_news_seen(article.link):
                print(f"🛡️ 发现极端新闻，但数据库显示已处理过，拦截: {article.title}")
                continue
                
            if not GEMINI_KEY: continue
            
            # 只有全新的爆炸性新闻，才会送到 AI 这里做最终测谎
            model = genai.GenerativeModel('gemini-1.5-flash-latest')
            prompt = f"判断该新闻是否陈述了真实的、已发生的、对全球有毁灭打击的事件。必须输出JSON: {{\"is_critical\": true/false, \"reason\": \"...\"}}。标题：{article.title}"
            try:
                resp = model.generate_content(prompt)
                res = json.loads(resp.text.strip().strip('`').replace('json\n', ''))
                if res.get("is_critical"):
                    send_telegram(f"☢️ <b>【高信度战争预警】</b>\n{article.title}\n<a href='{article.link}'>阅读原文</a>")
            except Exception as e:
                print(f"⚠️ AI 核查出错: {e}")

# ==========================================
# 5. 云端智能调度中心
# ==========================================
if __name__ == "__main__":
    print("🚀 云端智能雷达 (v2.0 数据库版) 启动...")
    
    emergency_monitor()

    is_manual_trigger = os.environ.get('GITHUB_EVENT_NAME') == 'workflow_dispatch'
    utc_now = datetime.utcnow()
    kl_hour = (utc_now.hour + 8) % 24
    is_report_time = (kl_hour % 4 == 0) and (utc_now.minute < 30)

    if is_manual_trigger:
        print("👆 检测到手动触发，立即发送完整报告。")
        routine_report()
    elif is_report_time:
        print(f"⏰ 当前马来西亚时间 {kl_hour} 点，符合发送周期！")
        routine_report()
    else:
        print(f"➖ 当前马来西亚时间 {kl_hour} 点 {utc_now.minute} 分，任务静默结束。")
