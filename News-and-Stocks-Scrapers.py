"""
市场雷达 v5.1 — 安全加固版
改进点：Upstash POST Body 防日志泄露、精细化异常捕获
"""

import os
import json
import hashlib
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

import requests
import feedparser
import yfinance as yf
from google import genai
from google.genai import types

# ==========================================
# 0. 日志配置（替代满天飞的 print）
# ==========================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


# ==========================================
# 1. 集中配置管理（所有魔术数字都在这里）
# ==========================================
@dataclass
class Config:
    # Secrets — 从环境变量读取，绝不硬编码
    telegram_token: str = field(default_factory=lambda: os.environ.get("TELEGRAM_TOKEN", ""))
    telegram_chat_id: str = field(default_factory=lambda: os.environ.get("TELEGRAM_CHAT_ID", ""))
    gemini_key: str = field(default_factory=lambda: os.environ.get("GEMINI_API_KEY", ""))
    upstash_url: str = field(default_factory=lambda: os.environ.get("UPSTASH_REDIS_REST_URL", ""))
    upstash_token: str = field(default_factory=lambda: os.environ.get("UPSTASH_REDIS_REST_TOKEN", ""))
    rapidapi_key: str = field(default_factory=lambda: os.environ.get("RAPIDAPI_KEY", ""))
    rapidapi_host: str = field(default_factory=lambda: os.environ.get("RAPIDAPI_HOST", ""))

    # 业务阈值 — 调参只需改这里
    vix_panic_threshold: float = 35.0
    oil_surge_pct: float = 8.0
    tech_crash_pct: float = -5.0

    # 系统参数
    news_ttl_seconds: int = 604800       # 7天去重窗口
    max_articles_per_feed: int = 3        # 每个 RSS 频道最多拉取条数
    max_tweets_per_account: int = 3       # 每个 Twitter 账号最多分析条数
    min_tweet_length: int = 20            # 过滤掉废话短推文
    request_timeout: int = 15            # 所有 HTTP 请求超时秒数
    recursion_depth_limit: int = 10       # JSON 递归提取最大深度
    gemini_model: str = "gemini-2.5-flash"

    # 监控资产
    tickers: dict = field(default_factory=lambda: {
        "CL=F":   "原油(WTI)",
        "GC=F":   "黄金",
        "SI=F":   "白银",
        "MAGS":   "科技七巨头",
        "BTC-USD": "比特币",
        "^VIX":   "恐慌指数",
    })

    # RSS 频道
    rss_feeds: dict = field(default_factory=lambda: {
        "🌍 宏观地缘": (
            "https://news.google.com/rss/search"
            "?q=Middle+East+conflict+OR+US+Iran+war&hl=en-US&gl=US"
        ),
        "🏦 华尔街/美联储": (
            "https://news.google.com/rss/search"
            "?q=Wall+Street+OR+Federal+Reserve+OR+Stock+Market&hl=en-US&gl=US"
        ),
    })

    # 核战/战争关键词
    extreme_keywords: list = field(default_factory=lambda: [
        "nuclear", "assassinated", "war", "strike", "missile"
    ])

    # 各资产的静态熔断门槛（绝对涨跌幅，单位：小数）
    # 设计意图：动态阈值在低波动资产上容易「过敏」（如黄金 0.1% 就触发）。
    # 引入此静态地板：「统计异常」+「绝对幅度够大」同时成立才报警。
    # 调参指南：波动性越高的资产，门槛越宽松。
    static_alert_floors: dict = field(default_factory=lambda: {
        "CL=F":    {"upper":  0.05, "lower": -0.05},  # 原油     ±5%
        "GC=F":    {"upper":  0.03, "lower": -0.03},  # 黄金     ±3%
        "SI=F":    {"upper":  0.04, "lower": -0.04},  # 白银     ±4%
        "MAGS":    {"upper":  0.04, "lower": -0.04},  # 科技     ±4%
        "BTC-USD": {"upper":  0.08, "lower": -0.08},  # 比特币   ±8%
        "^VIX":    {"upper":  0.20, "lower": -0.15},  # 恐慌指数（非对称：暴涨比暴跌更危险）
    })
    # 未在上表配置的资产兜底门槛
    static_alert_floor_default: dict = field(
        default_factory=lambda: {"upper": 0.05, "lower": -0.05}
    )


CFG = Config()


# ==========================================
# 2. AI 客户端（懒加载，避免密钥缺失时崩溃）
# ==========================================
def get_ai_client() -> Optional[genai.Client]:
    if not CFG.gemini_key:
        log.warning("未配置 GEMINI_API_KEY，AI 功能将跳过。")
        return None
    return genai.Client(api_key=CFG.gemini_key)


def ai_json(client: genai.Client, prompt: str) -> dict:
    """
    调用 Gemini 并强制返回 JSON dict。
    遇到任何解析/网络错误均向上抛出，由调用方决定如何处理。
    """
    resp = client.models.generate_content(
        model=CFG.gemini_model,
        contents=prompt,
        config=types.GenerateContentConfig(response_mime_type="application/json"),
    )
    return json.loads(resp.text)


# ==========================================
# 3. 基础通信组件
# ==========================================
def send_telegram(message: str) -> bool:
    """推送消息到 Telegram。返回是否成功。"""
    if not CFG.telegram_token or not CFG.telegram_chat_id:
        log.warning("未配置 Telegram 密钥，跳过发送。")
        return False

    url = f"https://api.telegram.org/bot{CFG.telegram_token}/sendMessage"
    payload = {
        "chat_id": CFG.telegram_chat_id,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        resp = requests.post(url, json=payload, timeout=CFG.request_timeout)
        resp.raise_for_status()
        log.info("✅ 消息成功推送到 Telegram。")
        return True
    except requests.HTTPError as e:
        log.error(f"Telegram API 返回错误: {e.response.text}")
    except requests.RequestException as e:
        log.error(f"Telegram 网络异常: {e}")
    return False


# ==========================================
# 4. Upstash 去重组件（职责单一：查 + 写分离）
# ==========================================
# 安全说明：使用 POST + JSON Body 而非 GET + URL 路径。
# 原因：GET 请求的 URL（含 key）会出现在：
#   ① Upstash 服务端访问日志  ② 中间代理/CDN 的请求日志
#   ③ 本地系统的 shell history（如果通过 curl 调试）
# POST Body 走 HTTPS 加密传输，不会被上述日志记录。
def _upstash_exec(command: list) -> Optional[str]:
    """
    通过 POST Body 向 Upstash 执行一条 Redis 命令。
    command 示例: ["GET", "mykey"]  /  ["SET", "mykey", "1", "EX", "604800"]
    成功返回 result 字段（字符串或 None），失败返回 None。
    """
    if not CFG.upstash_url or not CFG.upstash_token:
        return None
    try:
        r = requests.post(
            CFG.upstash_url,                                  # POST 到根路径
            headers={
                "Authorization": f"Bearer {CFG.upstash_token}",
                "Content-Type": "application/json",
            },
            json=command,                                      # key 在 Body 里，不在 URL 里
            timeout=CFG.request_timeout,
        )
        r.raise_for_status()
        return r.json().get("result")
    except requests.exceptions.Timeout:
        log.debug(f"Upstash 请求超时: {command[0]} {command[1]}")
    except requests.exceptions.ConnectionError as e:
        log.debug(f"Upstash 连接失败: {e}")
    except requests.exceptions.HTTPError as e:
        log.debug(f"Upstash HTTP 错误 {e.response.status_code}: {e.response.text[:200]}")
    except (ValueError, KeyError) as e:
        # ValueError: r.json() 解析失败；KeyError: 响应结构不符合预期
        log.debug(f"Upstash 响应解析失败: {e}")
    return None


def _upstash_get(key: str) -> Optional[str]:
    """从 Upstash 读取一个 key，失败返回 None。"""
    return _upstash_exec(["GET", key])


def _upstash_set(key: str, ttl: int = CFG.news_ttl_seconds) -> None:
    """向 Upstash 写入一个 key（带 TTL），失败静默降级。"""
    _upstash_exec(["SET", key, "1", "EX", str(ttl)])


def is_seen(raw_id: str) -> bool:
    """
    判断某条资讯是否已推送过。
    若 Upstash 不可用，默认视为「未见过」（降级策略：宁可重发也不漏发）。
    """
    fingerprint = hashlib.md5(raw_id.encode()).hexdigest()
    return _upstash_get(fingerprint) is not None


def mark_seen(raw_id: str) -> None:
    """将某条资讯标记为已推送。"""
    fingerprint = hashlib.md5(raw_id.encode()).hexdigest()
    _upstash_set(fingerprint)


# ==========================================
# 5. 行情模块（批量拉取，避免逐个 HTTP）
# ==========================================
def fetch_market_snapshot() -> dict[str, dict]:
    """
    批量下载所有 ticker 的近 5 日行情。
    返回: {symbol: {"name": str, "price": float, "change_pct": float}}
    """
    symbols = list(CFG.tickers.keys())
    snapshot: dict[str, dict] = {}

    try:
        # 一次 HTTP 请求拉取所有 ticker，而非 N 次循环
        raw = yf.download(symbols, period="5d", auto_adjust=True, progress=False)
        close = raw["Close"]

        for symbol in symbols:
            try:
                series = close[symbol].dropna()
                if len(series) < 2:
                    continue
                prev, curr = float(series.iloc[-2]), float(series.iloc[-1])
                snapshot[symbol] = {
                    "name": CFG.tickers[symbol],
                    "price": curr,
                    "change_pct": (curr - prev) / prev * 100,
                    "latest_date": series.index[-1].strftime("%Y-%m-%d"),
                }
            except KeyError:
                # yfinance 对某个 symbol 没有返回数据列
                log.warning(f"行情数据中找不到 {symbol}，可能是非交易日或 symbol 已下架。")
            except (IndexError, ZeroDivisionError) as e:
                # iloc 越界 或 prev_close 为零（极罕见的数据质量问题）
                log.warning(f"解析 {symbol} 行情计算异常: {e}")
            except ValueError as e:
                # float() 转换失败，说明数据不是数值型
                log.warning(f"{symbol} 行情值无法转为浮点数: {e}")

    except requests.exceptions.RequestException as e:
        # yfinance 底层使用 requests，网络层错误从这里冒出
        log.error(f"行情网络请求失败: {e}")
    except KeyError:
        # raw["Close"] 不存在，说明 yfinance 返回了空 DataFrame
        log.error("yfinance 返回数据中不含 'Close' 列，可能所有 symbol 均无效。")

    return snapshot


def format_market_snapshot(snapshot: dict) -> str:
    lines = ["📈 <b>【全球核心资产跟踪】</b>"]
    for data in snapshot.values():
        trend = "🟢" if data["change_pct"] > 0 else "🔴"
        lines.append(
            f"• <b>{data['name']}</b>: {data['price']:.2f} | "
            f"{trend}{data['change_pct']:+.2f}%"
        )
    return "\n".join(lines)


# ==========================================
# 6. 新闻模块
# ==========================================
def fetch_new_articles(feeds: dict, max_per_feed: int) -> list[dict]:
    """
    从多个 RSS 频道拉取未读文章。
    返回: [{"category": str, "title": str, "link": str}]
    """
    results = []
    for category, url in feeds.items():
        try:
            feed = feedparser.parse(url)
            count = 0
            for article in feed.entries:
                if count >= max_per_feed:
                    break
                if not is_seen(article.link):
                    mark_seen(article.link)
                    results.append({
                        "category": category,
                        "title": article.title,
                        "link": article.link,
                    })
                    count += 1
        except AttributeError as e:
            # article 对象缺少 .link 或 .title 属性（feed 结构异常）
            log.warning(f"RSS 文章字段缺失 [{category}]: {e}")
        except requests.exceptions.RequestException as e:
            # feedparser 内部也用 urllib，但部分异常会以 requests 形式冒出
            log.warning(f"RSS 网络请求失败 [{category}]: {e}")
    return results


# ==========================================
# 7. JSON 安全递归提取（带深度限制，防爆栈）
# ==========================================
def extract_text_fields(data, key: str = "full_text", _depth: int = 0) -> list[str]:
    """
    递归提取 JSON 中所有指定 key 的值。
    _depth 限制递归深度，防止超深结构导致栈溢出。
    """
    if _depth > CFG.recursion_depth_limit:
        return []
    results = []
    if isinstance(data, dict):
        if key in data and isinstance(data[key], str):
            results.append(data[key])
        for v in data.values():
            results.extend(extract_text_fields(v, key, _depth + 1))
    elif isinstance(data, list):
        for item in data:
            results.extend(extract_text_fields(item, key, _depth + 1))
    return results


# ==========================================
# 8. AI 情绪分析提示词（集中管理，方便 A/B 测试）
# ==========================================
PROMPT_NEWS_SENTIMENT = """
你是一个顶级的华尔街宏观分析师。请阅读以下最新的【地缘政治】与【华尔街金融】新闻标题。
请评估它们对全球市场（股市、原油、加密货币等）的综合情绪影响。
请严格以 JSON 格式输出，不要有任何其他文字：
{{
    "score": 填入一个 -100 到 100 的整数（-100代表极其恐慌，0代表中性，100代表极其乐观）,
    "trend": "看空" 或 "震荡" 或 "看多",
    "analysis": "用50个字以内，极其犀利、专业地总结利好或利空逻辑"
}}

新闻标题：
{headlines}
"""

PROMPT_IS_CRITICAL = """
判断该新闻是否陈述了真实的、已发生的、对全球有毁灭打击的事件。
必须输出JSON: {{"is_critical": true/false, "reason": "..."}}}。
标题：{title}
"""

PROMPT_TWEET_SENTIMENT = """
你是一位顶级的华尔街宏观交易员。以下是重要人物在 X 上的最新发言。
请评估这些发言对全球金融市场（特别是美股、加密货币、原油）的潜在影响。
必须严格输出 JSON 格式：
{{
    "score": 填入 -100 到 100 的整数 (负代表利空，正代表利好),
    "target_asset": "最可能受此推文波动的资产(如：BTC, 原油, 美元, 无)",
    "analysis": "用50字犀利解读这条推文的言外之意和潜在交易信号"
}}
推文内容：
{tweets}
"""


def sentiment_emoji(score: int) -> str:
    if score < -30:
        return "🥶"
    if score > 30:
        return "🔥"
    return "🤔"


# ==========================================
# 9. 轨道一：常规长篇简报
# ==========================================
def routine_report() -> None:
    log.info("📝 生成常规市场简报...")
    ai = get_ai_client()

    snapshot = fetch_market_snapshot()
    msg = "📊 <b>【市场情绪与盘面巡逻报告】</b>\n\n"
    msg += format_market_snapshot(snapshot) + "\n\n"

    articles = fetch_new_articles(CFG.rss_feeds, CFG.max_articles_per_feed)

    if not articles:
        msg += "➖ 过去 4 小时内无未读重大资讯，情绪指标维持现状。\n"
    else:
        news_display = "\n".join(
            f"<b>{a['category']}</b>: <a href='{a['link']}'>{a['title']}</a>"
            for a in articles
        )
        headlines = "\n".join(f"[{a['category']}] {a['title']}" for a in articles)

        msg += f"📰 <b>【未读资讯与 AI 情绪研判】</b>\n{news_display}\n"

        if ai:
            try:
                result = ai_json(ai, PROMPT_NEWS_SENTIMENT.format(headlines=headlines))
                score = result.get("score", 0)
                msg += (
                    f"\n🧠 <b>AI 量化情绪得分</b>: {score} {sentiment_emoji(score)}"
                    f" (趋势: {result.get('trend')})\n"
                    f"💡 <b>核心研判</b>: {result.get('analysis')}\n"
                )
            except json.JSONDecodeError as e:
                # Gemini 没有严格返回 JSON（极少见，但 prompt 失控时会发生）
                log.error(f"AI 返回内容无法解析为 JSON: {e}")
                msg += "⚠️ AI 返回格式异常，分析跳过。\n"
            except requests.exceptions.RequestException as e:
                # Gemini SDK 底层网络失败
                log.error(f"AI 网络请求失败: {e}")
                msg += "⚠️ AI 服务暂时不可用。\n"

    send_telegram(msg)


# ==========================================
# 10. 轨道二：高频紧急预警
# ==========================================
def emergency_monitor_v2() -> None:
    """
    动态统计级暴雷扫描（替代基于固定阈值的 emergency_monitor）。

    核心升级：
      - 每个资产使用自身 30 天历史数据自动校准「正常波动区间」
      - 双重门槛过滤：统计异常 + 绝对幅度，缺一不可
      - Bug 修复：涨跌幅显示从小数正确格式化为百分比
    """
    log.info("🚨 执行【动态统计级双重校验】暴雷扫描...")
    ai = get_ai_client()
    symbols = list(CFG.tickers.keys())

    try:
        hist = yf.download(
            symbols, period="30d", interval="1d",
            auto_adjust=True, progress=False
        )["Close"]
    except requests.exceptions.RequestException as e:
        log.error(f"行情基准拉取网络失败: {e}")
        return
    except KeyError:
        log.error("yfinance 返回数据不含 'Close' 列。")
        return

    alerts: list[str] = []

    for symbol in symbols:
        try:
            data = hist[symbol].dropna()
        except KeyError:
            log.warning(f"行情数据中找不到 {symbol}，跳过。")
            continue

        if len(data) < 2:
            continue

        current_price = float(data.iloc[-1])
        last_price    = float(data.iloc[-2])

        if last_price == 0:
            continue

        current_change = (current_price - last_price) / last_price  # 小数，如 0.05 = 5%

        # --- 第一步：计算动态阈值 ---
        dynamic_upper, dynamic_lower = calculate_dynamic_threshold(data, multiplier=2.5)
        if dynamic_upper is None:
            continue

        # --- 第二步：双重校验 ---
        should_alert, direction = _dual_validate(
            symbol, current_change, dynamic_upper, dynamic_lower
        )
        if not should_alert:
            continue

        # --- 第三步：构造报警消息（修复 Bug：乘以 100 转为百分比显示）---
        name = CFG.tickers[symbol]
        change_display = current_change * 100   # 0.05 → 5.0
        if direction == "up":
            alert_msg = (
                f"🚀 <b>【{name}】异常超涨</b>: {change_display:+.2f}%"
                f"（动态高位 {dynamic_upper * 100:+.2f}% | "
                f"静态门槛 {CFG.static_alert_floors.get(symbol, CFG.static_alert_floor_default)['upper'] * 100:.1f}%）"
            )
        else:
            alert_msg = (
                f"📉 <b>【{name}】异常暴跌</b>: {change_display:+.2f}%"
                f"（动态低位 {dynamic_lower * 100:+.2f}% | "
                f"静态门槛 {CFG.static_alert_floors.get(symbol, CFG.static_alert_floor_default)['lower'] * 100:.1f}%）"
            )

        # --- 第四步：去重 ---
        fingerprint = f"dynamic_alert_{symbol}_{time.strftime('%Y%m%d')}"
        if not is_seen(fingerprint):
            mark_seen(fingerprint)
            alerts.append(alert_msg)

    if alerts:
        send_telegram("\n".join(alerts))
    else:
        log.info("➖ 所有资产均在双重门槛内，无需报警。")

# ==========================================
# 11. 轨道三：VIP 领袖 Twitter 监控
# ==========================================
def twitter_vip_monitor(target_id: str = "25073877", target_name: str = "realDonaldTrump") -> None:
    log.info(f"🦅 扫描 Twitter 领袖账号: @{target_name}")
    ai = get_ai_client()

    if not CFG.rapidapi_key or not CFG.rapidapi_host:
        log.warning("未配置 RapidAPI 密钥，跳过 Twitter 扫描。")
        return

    try:
        resp = requests.get(
            "https://twitter241.p.rapidapi.com/user-tweets",
            headers={
                "x-rapidapi-key": CFG.rapidapi_key,
                "x-rapidapi-host": CFG.rapidapi_host,
            },
            params={"user": target_id, "count": "20"},
            timeout=CFG.request_timeout,
        )
        resp.raise_for_status()
        raw_json = resp.json()
    except requests.RequestException as e:
        log.error(f"Twitter API 请求失败: {e}")
        return

    all_texts = extract_text_fields(raw_json, key="full_text")
    valid_texts = [t for t in all_texts if len(t) >= CFG.min_tweet_length]

    new_tweets: list[str] = []
    for text in valid_texts[: CFG.max_tweets_per_account * 3]:  # 多看几条以凑满配额
        if len(new_tweets) >= CFG.max_tweets_per_account:
            break
        if not is_seen(text):
            mark_seen(text)
            new_tweets.append(f"• {text}")

    if not new_tweets:
        log.info(f"@{target_name} 过去几小时内无未读新发言。")
        return

    tweets_str = "\n\n".join(new_tweets)
    msg = f"🦅 <b>【领袖情报拦截: @{target_name}】</b>\n\n{tweets_str}\n"

    if ai:
        try:
            result = ai_json(ai, PROMPT_TWEET_SENTIMENT.format(tweets=tweets_str))
            score = result.get("score", 0)
            msg += (
                f"\n🧠 <b>AI 推文情绪解析</b>: {score} {sentiment_emoji(score)}\n"
                f"🎯 <b>异动雷达</b>: {result.get('target_asset')}\n"
                f"💡 <b>深度解读</b>: {result.get('analysis')}\n"
            )
        except json.JSONDecodeError as e:
            log.error(f"AI 推文分析返回非法 JSON: {e}")
        except requests.exceptions.RequestException as e:
            log.error(f"AI 推文分析网络失败: {e}")

    send_telegram(msg)


# ==========================================
# 12. 动态阈值引擎（双重校验版）
# ==========================================

def calculate_dynamic_threshold(
    series: "pd.Series", multiplier: float = 3.0
) -> tuple[float, float] | tuple[None, None]:
    """
    计算动态统计阈值：mean ± k * std（基于历史日收益率分布）。

    返回 (upper, lower) 均为小数形式的收益率（与 pct_change() 单位一致）。
    样本不足 10 时返回 (None, None)，由调用方降级处理。

    multiplier 选择建议：
      2.0 → 覆盖正态分布 95.4% 的波动，灵敏度高，适合高频监控
      2.5 → 覆盖 98.8%，均衡选择
      3.0 → 覆盖 99.7%，只响应极端尾部事件
    """
    import pandas as pd

    if len(series) < 10:
        return None, None

    returns: pd.Series = series.pct_change().dropna()
    mean = returns.mean()
    std = returns.std()

    if std == 0:          # 极罕见：资产价格完全没动（停牌等）
        return None, None

    return mean + (multiplier * std), mean - (multiplier * std)


def _passes_static_floor(symbol: str, change: float) -> tuple[bool, str]:
    """
    静态熔断门槛校验。

    返回 (passed, direction)，其中 direction 是 "up" / "down" / ""。
    只有绝对涨跌幅超过该资产的静态最低门槛，才算通过。

    设计意图：防止动态阈值在极低波动资产（如黄金连续平静期）上
    把 0.1% 的微小波动误判为「统计异常」。
    """
    floors = CFG.static_alert_floors.get(symbol, CFG.static_alert_floor_default)
    if change > floors["upper"]:
        return True, "up"
    if change < floors["lower"]:
        return True, "down"
    return False, ""


def _dual_validate(
    symbol: str,
    current_change: float,
    dynamic_upper: float,
    dynamic_lower: float,
) -> tuple[bool, str]:
    """
    双重校验：动态统计异常 AND 静态熔断门槛，两者必须同时成立。

    逻辑矩阵：
    ┌──────────────────┬────────────────────┬────────┐
    │  动态阈值被突破？ │  静态门槛被突破？  │  报警？│
    ├──────────────────┼────────────────────┼────────┤
    │       ✅         │        ✅          │   ✅   │  ← 真正的异常
    │       ✅         │        ❌          │   ❌   │  ← 统计异常但幅度太小（过敏）
    │       ❌         │        ✅          │   ❌   │  ← 幅度大但属于该资产正常波动
    │       ❌         │        ❌          │   ❌   │  ← 正常
    └──────────────────┴────────────────────┴────────┘

    返回 (should_alert, direction)。
    """
    # 第一关：动态统计异常
    if current_change > dynamic_upper:
        dynamic_direction = "up"
    elif current_change < dynamic_lower:
        dynamic_direction = "down"
    else:
        return False, ""          # 未突破动态阈值，直接放行

    # 第二关：静态绝对门槛
    static_passed, static_direction = _passes_static_floor(symbol, current_change)
    if not static_passed:
        log.debug(
            f"{symbol} 触发动态阈值({dynamic_direction})但未达静态门槛，抑制报警。"
            f" change={current_change:+.2%}"
        )
        return False, ""

    # 方向一致性校验（极端情况下两者方向不同，说明配置有问题）
    if dynamic_direction != static_direction:
        log.warning(f"{symbol} 动态与静态方向不一致，跳过。")
        return False, ""

    return True, dynamic_direction

# ==========================================
# 13. 云端调度中心
# ==========================================
DISPATCH_MAP = {
    "twitter-scan":    lambda: twitter_vip_monitor(),
    "precision-strike": lambda: routine_report(),
}

if __name__ == "__main__":
    log.info("🚀 云端智能雷达 v5.1 启动")

    event_type = os.environ.get("WEBHOOK_EVENT", "")
    is_manual = os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"

    if event_type in DISPATCH_MAP:
        log.info(f"📡 接收到事件: {event_type}")
        DISPATCH_MAP[event_type]()
    elif is_manual:
        log.info("👆 手动触发：生成长篇市场简报")
        routine_report()
    else:
        log.info("➖ 定时静默扫描（暴雷预警 v2）")
        emergency_monitor_v2()
