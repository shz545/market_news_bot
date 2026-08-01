import os
import json
import time
import re
from datetime import datetime, timezone, timedelta
import requests
import feedparser
from bs4 import BeautifulSoup
from deep_translator import GoogleTranslator

import google.generativeai as genai

# ==========================================
# CONFIGURATION & SECRETS
# ==========================================
FINNHUB_KEY = os.environ.get('FINNHUB_KEY', '')
MARKETAUX_KEY = os.environ.get('MARKETAUX_KEY', '')
TG_BOT_TOKEN = os.environ.get('TG_BOT_TOKEN', '')
TG_CHAT_ID = os.environ.get('TG_CHAT_ID', '')
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY', '')
SEEN_FILE = 'seen_news.json'

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

TW_STOCK_MAP = {
    # 半導體
    '2330': '台積電', '2303': '聯電', '2454': '聯發科', '3443': '創意',
    '5347': '世界', '6770': '力積電', '3661': '世芯-KY', '2379': '瑞昱',
    # 電腦/AI伺服器
    '2317': '鴻海', '2382': '廣達', '3231': '緯創', '2356': '英業達',
    '2376': '技嘉', '2357': '華碩', '2353': '宏碁', '3227': '原相',
    # 金融
    '2881': '富邦金', '2882': '國泰金', '2884': '玉山金', '2886': '兆豐金',
    '2891': '中信金', '2880': '華南金', '2892': '第一金',
    # 傳產/鋼鐵/石化
    '1301': '台塑', '1303': '南亞', '1326': '台化', '6505': '台塑化',
    '2002': '中鋼', '1402': '遠東新', '2912': '統一超', '1216': '統一',
    '2207': '和泰車', '9910': '豐泰', '9921': '巨大',
    # 電信/公用
    '2412': '中華電', '3045': '台灣大', '4904': '遠傳',
    # 光電/面板
    '3008': '大立光', '2474': '可成', '6526': '達發',
    # 航運
    '2603': '長榮', '2609': '陽明', '2615': '萬海',
    # 其他
    '2618': '長榮航', '2610': '華航', '2542': '興富發'
}

TW_NAME_TO_CODE = {}
for code, name in TW_STOCK_MAP.items():
    TW_NAME_TO_CODE[name] = code
    if '-' in name:
        TW_NAME_TO_CODE[name.split('-')[0]] = code

TW_NAME_TO_CODE.update({
    '台積': '2330', 'TSMC': '2330', '護國神山': '2330',
    '聯發': '2454', 'MTK': '2454',
    'Foxconn': '2317', '富士康': '2317',
    '國巨': '2327', '台達': '2308',
    '長榮海': '2603', '長榮航空': '2618', '陽明海運': '2609'
})

BULL_KEYWORDS_EN = [
    'surge', 'jump', 'rally', 'soar', 'skyrocket', 'gain', 'climb', 'outperform',
    'bullish', 'upbeat', 'positive', 'strong', 'record', 'high', 'breakout', 'growth',
    'beat', 'beats', 'upgrade', 'raised', 'buy', 'dividend', 'profit', 'revenue',
    'expansion', 'partnership', 'approval', 'merger', 'acquisition', 'optimism', 'boom'
]

BEAR_KEYWORDS_EN = [
    'plunge', 'dive', 'crash', 'tumble', 'drop', 'fall', 'slump', 'underperform',
    'bearish', 'downbeat', 'negative', 'weak', 'low', 'breakdown', 'decline', 'loss',
    'miss', 'misses', 'downgrade', 'cut', 'sell', 'warning', 'deficit', 'debt',
    'recession', 'inflation', 'lawsuit', 'investigation', 'scandal', 'bankruptcy', 'pessimism'
]

BULL_KEYWORDS_ZH = [
    '大漲', '上漲', '狂飆', '飆升', '強勢', '創新高', '利多', '看好', '買進', '加碼',
    '超預期', '爆發', '受惠', '成長', '優於預期', '紅盤', '利潤大增', '訂單滿載', '升級',
    '降息', '樂觀', '大單', '獲利', '營收創高', '翻紅', '反彈', '漲停'
]

BEAR_KEYWORDS_ZH = [
    '大跌', '下跌', '崩盤', '暴跌', '跳水', '重挫', '創新低', '利空', '看壞', '賣出',
    '減碼', '不如預期', '衰退', '虧損', '受創', '綠盤', '獲利衰退', '砍單', '降級',
    '升息', '悲觀', '違約', '裁員', '破產', '爆雷', '警訊', '跌停', '通膨'
]

# ==========================================
# UTILS
# ==========================================
def load_seen_news():
    if os.path.exists(SEEN_FILE):
        try:
            with open(SEEN_FILE, 'r', encoding='utf-8') as f:
                return set(json.load(f))
        except:
            pass
    return set()

def save_seen_news(seen_set):
    # Keep only the last 1000 items to avoid infinite growth
    seen_list = list(seen_set)[-1000:]
    with open(SEEN_FILE, 'w', encoding='utf-8') as f:
        json.dump(seen_list, f)

def is_english(text):
    if not text: return False
    letters = sum(1 for c in text if c.isalpha() and ord(c) < 128)
    return (letters / max(len(text), 1)) > 0.5

def translate_to_zh(text):
    if not text or not is_english(text):
        return text
    try:
        translator = GoogleTranslator(source='en', target='zh-TW')
        return translator.translate(text)
    except Exception as e:
        print(f"Translation error: {e}")
        return text

def evaluate_and_summarize_news(headline, summary):
    if not GEMINI_API_KEY:
        return "NO"
    try:
        model = genai.GenerativeModel("gemini-2.5-flash")
        prompt = f"""
        你是一位嚴格的對沖基金經理人。請判定以下這則財經新聞是否符合以下任一條件：
        1. 核彈級市場黑天鵝事件（如：聯準會意外升降息、戰爭爆發、重量級企業財報大爆雷）。
        2. 大盤指數漲跌的關鍵原因分析（例如說明今日美股、台股為何大漲或大跌的核心理由）。

        標題：{headline}
        摘要：{summary}
        
        要求：
        - 如果是普通新聞、日常財報、個股小幅波動、無關緊要的分析師評論，請一律判定為「NO」。
        - 如果符合上述重大條件，請判定為「YES」，並提供 3 個條列式的重點總結。
        
        輸出格式嚴格要求：
        第一行只能是「YES」或「NO」。
        如果第一行是 YES，則從第二行開始輸出 3 點繁體中文總結，格式為：
        - 重點一
        - 重點二
        - 重點三
        """
        response = model.generate_content(prompt)
        if response.text:
            return response.text.strip()
    except Exception as e:
        print(f"Gemini evaluate error: {e}")
    return "NO"

def extract_tickers(text):
    tickers = set()
    # US Tickers
    noise = {'THE','AND','FOR','WITH','FROM','THIS','THAT','HAS','HAVE','HAD','WAS','ARE',
             'NOT','BUT','ITS','ALL','NEW','HIS','HER','OUT','MAY','CAN','NOW','SAY','SAYS','CEO','CFO','IPO',
             'GDP','FED','SEC','FDA','WHO','ETF','USD','EUR','JPY','API','AI','US','UK','EU','UN','IMF','ECB',
             'BOJ','PMI','CPI','PPI','DOJ','EPS','PE','PEG','ROE','ROA','BPS','GOP','DNC','BY','IS','IN','ON',
             'AT','TO','OF','OR','AN','AS','IF','DO','UP','SO','NO','BE','WE','IT','AM','HE','MY','BIG','TOP',
             'VS','A','I','BEEN','ALSO','OVER','INTO','THAN','WILL','JUST','MORE','SOME','VERY','WHEN','WHAT',
             'MOST','HIGH','LOW','SET','RSI','ATH','ATL','CPU','GPU','TPU','NPU','LLM','GPT','AWS','GCP',
             'NYSE','NASDAQ','EBITDA','YOY','QOQ'}
    us_matches = re.findall(r'\b[A-Z]{2,5}\b', text)
    for t in us_matches:
        if t not in noise:
            tickers.add(t)
    
    # TW Tickers
    tw_matches = re.findall(r'[\(（](\d{4})[\)）]', text)
    for m in tw_matches:
        tickers.add(m)
            
    for name, code in TW_NAME_TO_CODE.items():
        if len(name) >= 2 and name in text:
            tickers.add(code)
            
    num_matches = re.findall(r'\b(\d{4})\b', text)
    for m in num_matches:
        if m in TW_STOCK_MAP:
            tickers.add(m)
            
    return list(tickers)[:8]

def analyze_sentiment(text):
    text_lower = text.lower()
    bull_score = 0
    bear_score = 0
    
    for word in BULL_KEYWORDS_EN:
        if re.search(r'\b' + word + r'\b', text_lower): bull_score += 1
    for word in BEAR_KEYWORDS_EN:
        if re.search(r'\b' + word + r'\b', text_lower): bear_score += 1
    
    for word in BULL_KEYWORDS_ZH:
        if word in text: bull_score += 1.5
    for word in BEAR_KEYWORDS_ZH:
        if word in text: bear_score += 1.5
        
    total = bull_score + bear_score
    if total == 0: return 'neutral'
    
    ratio = (bull_score - bear_score) / total
    if ratio > 0.2: return 'bullish'
    if ratio < -0.2: return 'bearish'
    return 'neutral'

# ==========================================
# FETCHERS
# ==========================================
def fetch_finnhub():
    if not FINNHUB_KEY: return []
    news = []
    cats = ['general', 'forex', 'merger']
    for cat in cats:
        try:
            r = requests.get(f'https://finnhub.io/api/v1/news?category={cat}&token={FINNHUB_KEY}', timeout=10)
            if r.status_code == 200:
                data = r.json()
                for item in data[:15]:
                    full_text = item.get('headline', '') + " " + item.get('summary', '')
                    related = item.get('related', '')
                    tickers = [t.strip() for t in related.split(',') if t.strip()] if related else extract_tickers(full_text)
                    
                    news.append({
                        'id': str(item.get('id')),
                        'headline': item.get('headline', ''),
                        'summary': item.get('summary', ''),
                        'url': item.get('url', ''),
                        'datetime': item.get('datetime', int(time.time())),
                        'tickers': tickers,
                        'sentiment': analyze_sentiment(full_text),
                        'source': 'Finnhub',
                        'important': False
                    })
        except Exception as e:
            print(f"Finnhub fetch error: {e}")
    return news

def fetch_marketaux():
    if not MARKETAUX_KEY: return []
    try:
        url = f"https://api.marketaux.com/v1/news/all?language=en&limit=15&api_token={MARKETAUX_KEY}"
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            data = r.json()
            news = []
            for item in data.get('data', []):
                full_text = item.get('title', '') + " " + item.get('description', '')
                entities = item.get('entities', [])
                tickers = [e['symbol'] for e in entities if e.get('symbol')]
                if not tickers: tickers = extract_tickers(full_text)
                
                # Marketaux AI Sentiment
                api_sent = 0
                for e in entities:
                    s = e.get('sentiment_score')
                    if s is not None: api_sent += s
                if entities: api_sent /= len(entities)
                
                sentiment = 'neutral'
                if api_sent > 0.15: sentiment = 'bullish'
                elif api_sent < -0.15: sentiment = 'bearish'
                else: sentiment = analyze_sentiment(full_text)
                
                dt = datetime.strptime(item.get('published_at'), "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc).timestamp()
                
                news.append({
                    'id': str(item.get('uuid')),
                    'headline': item.get('title', ''),
                    'summary': item.get('description', ''),
                    'url': item.get('url', ''),
                    'datetime': int(dt),
                    'tickers': tickers[:6],
                    'sentiment': sentiment,
                    'source': 'Marketaux',
                    'important': False
                })
            return news
    except Exception as e:
        print(f"Marketaux fetch error: {e}")
    return []

def fetch_rss_feeds():
    feeds = [
        {'url': 'https://news.google.com/rss/search?q=台股+OR+台灣股市+OR+財報+OR+台積電+OR+半導體+when:1d&hl=zh-TW&gl=TW&ceid=TW:zh-Hant', 'source': 'Google財經(台股)'},
        {'url': 'https://news.google.com/rss/search?q=股市+OR+升息+OR+降息+OR+央行+OR+地緣政治+OR+關稅+when:1d&hl=zh-TW&gl=TW&ceid=TW:zh-Hant', 'source': 'Google總經'},
        {'url': 'https://money.udn.com/rssfeed/news/10511/10538?ch=money', 'source': '經濟日報'}
    ]
    news = []
    for f in feeds:
        try:
            d = feedparser.parse(f['url'])
            for entry in d.entries[:10]:
                desc_html = getattr(entry, 'description', '')
                desc_text = BeautifulSoup(desc_html, "html.parser").get_text(separator=' ') if desc_html else ""
                
                full_text = entry.title + " " + desc_text
                import hashlib
                item_id = hashlib.md5(entry.title.encode()).hexdigest()
                
                sentiment = analyze_sentiment(full_text)
                tickers = extract_tickers(full_text)
                important = any(kw in full_text for kw in ['崩盤', '暴跌', '創新高', '降息', '升息'])
                
                news.append({
                    'id': item_id,
                    'headline': entry.title,
                    'summary': desc_text,
                    'url': entry.link,
                    'datetime': int(time.time()),
                    'tickers': tickers,
                    'sentiment': sentiment,
                    'source': f['source'],
                    'important': important
                })
        except Exception as e:
            print(f"RSS fetch error: {e}")
    return news

# ==========================================
# TELEGRAM SENDER
# ==========================================
def send_telegram(item):
    if not TG_BOT_TOKEN or not TG_CHAT_ID: return
    
    # 交給 AI 守門員判定
    gemini_result = evaluate_and_summarize_news(item['headline'], item['summary'])
    
    if not gemini_result.startswith("YES"):
        print(f"[{item['source']}] AI rejected: {item['headline']}")
        return # 放棄發送這則新聞
        
    # 擷取 YES 之後的總結內容
    gemini_summary = gemini_result[3:].strip()
    summary_str = f"🚨 <b>重大警報 (AI 判定)</b>\n{gemini_summary}\n"
    
    sent_emoji = '🟢 利多' if item['sentiment'] == 'bullish' else '🔴 利空' if item['sentiment'] == 'bearish' else '⚪ 中性'
    
    zh_title = f"<b>{item['headline']}</b>\n\n"
    en_title = ""
    if is_english(item['headline']):
        zh = translate_to_zh(item['headline'])
        if zh: 
            zh_title = f"<b>{zh}</b>\n\n"
            en_title = f"📝 <i>{item['headline']}</i>\n\n"
        
    stock_links = []
    for t in item['tickers'][:4]:
        is_tw = re.match(r'^\d{4}$', t) or t.endswith('.TW') or t.endswith('.TWO')
        code = re.sub(r'\.(TW|TWO)$', '', t) if is_tw else t
        label = TW_STOCK_MAP.get(code, code) if is_tw else t
        url = f"https://tw.stock.yahoo.com/quote/{code}.TW" if is_tw else f"https://tw.stock.yahoo.com/quote/{t}"
        stock_links.append(f"  • <a href='{url}'>{label} ({t})</a>")
        
    stock_str = "\n📊 相關股票：\n" + "\n".join(stock_links) + "\n" if stock_links else ""
    url_str = f"\n📰 新聞連結：{item['url']}" if item['url'] else ""
    
    message = (
        f"{sent_emoji} {zh_title}"
        f"{en_title}"
        f"{summary_str}"
        f"{stock_str}"
        f"{url_str}\n\n"
        f"📡 來源: {item['source']}"
    )
    
    try:
        url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
        payload = {
            'chat_id': TG_CHAT_ID,
            'text': message,
            'parse_mode': 'HTML',
            'disable_web_page_preview': False
        }
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Telegram error: {e}")

# ==========================================
# MAIN LOOP
# ==========================================
def main():
    print("Starting News Fetcher...")
    seen_ids = load_seen_news()
    
    all_news = []
    all_news.extend(fetch_finnhub())
    all_news.extend(fetch_marketaux())
    all_news.extend(fetch_rss_feeds())
    
    # Sort by datetime
    all_news.sort(key=lambda x: x['datetime'])
    
    new_items_sent = 0
    for item in all_news:
        if item['id'] not in seen_ids:
            # We don't send anything on the very first run (when seen_ids is empty)
            # just to avoid spamming 100 old news.
            if len(seen_ids) > 0:
                print(f"Sending: {item['headline']}")
                send_telegram(item)
                new_items_sent += 1
                time.sleep(1) # Rate limit telegram
            seen_ids.add(item['id'])
            
    save_seen_news(seen_ids)
    print(f"Finished. Sent {new_items_sent} new items.")

if __name__ == '__main__':
    main()
