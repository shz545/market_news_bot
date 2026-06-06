import os
import time
from datetime import datetime
import requests
import google.generativeai as genai
import bot

GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY', '')
TG_BOT_TOKEN = os.environ.get('TG_BOT_TOKEN', '')
TG_CHAT_ID = os.environ.get('TG_CHAT_ID', '')

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

def generate_briefing():
    if not GEMINI_API_KEY or not TG_BOT_TOKEN or not TG_CHAT_ID:
        print("Missing API Keys for briefing.")
        return

    print("Fetching news for briefing...")
    news = []
    news.extend(bot.fetch_finnhub())
    news.extend(bot.fetch_marketaux())
    news.extend(bot.fetch_rss_feeds())
    
    # Filter for last 12 hours
    now = time.time()
    recent_news = [n for n in news if (now - n['datetime']) < 12 * 3600]
    
    # Sort by sentiment importance (put bullish/bearish first)
    recent_news.sort(key=lambda x: 0 if x['sentiment'] != 'neutral' else 1)
    
    if not recent_news:
        print("No recent news found for briefing.")
        return
        
    text_corpus = ""
    # Take top 30 most relevant news from the last 12 hours
    for idx, n in enumerate(recent_news[:30]):
        text_corpus += f"[{idx+1}] 標題: {n['headline']}\n摘要: {n['summary'][:150]}\n情感: {n['sentiment']}\n\n"
        
    date_str = datetime.now().strftime("%Y-%m-%d")
    hour = datetime.now().hour
    period = "晨間" if hour < 12 else "晚間"
        
    prompt = f"""
    你現在是一位華爾街與台股的資深證券分析師。
    請根據以下過去 12 小時內的全球市場重點新聞，寫出一份精簡、專業的「市場盤前/盤後總整理」。
    請著重在對股市有重大影響的總體經濟事件、美國科技巨頭動態、以及台灣半導體與電子股的影響。

    新聞清單：
    {text_corpus}

    要求：
    1. 輸出格式必須嚴格依照以下 Markdown 格式：
    
    🌅 **{date_str} 股市{period}總整理**
    
    📈 **國際宏觀與美股重點**
    • (重點一，請附上相關公司代碼)
    • (重點二)
    
    🇹🇼 **台股與供應鏈動態**
    • (重點一，請盡量帶出相關台股的影響)
    • (重點二)

    💡 **分析師短評**
    (一句話總結目前的市場情緒，偏向樂觀、悲觀或觀望)

    2. 全文請使用流暢的繁體中文，語氣客觀專業。
    3. 如果新聞清單中沒有提到太多台股，請自行根據美股科技股的狀況，推測對台股供應鏈（如台積電、鴻海等）可能的影響。
    4. 不要有任何多餘的開場白與問候語。
    """
    
    try:
        print("Generating summary with Gemini...")
        model = genai.GenerativeModel("gemini-2.5-flash")
        response = model.generate_content(prompt)
        briefing_text = response.text.strip()
        
        print("Sending to Telegram...")
        url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
        payload = {
            'chat_id': TG_CHAT_ID,
            'text': briefing_text,
            'parse_mode': 'Markdown',
            'disable_web_page_preview': True
        }
        r = requests.post(url, json=payload, timeout=10)
        if r.status_code == 200:
            print("Briefing sent successfully.")
        else:
            print(f"Telegram error: {r.text}")
    except Exception as e:
        print(f"Failed to generate/send briefing: {e}")

if __name__ == "__main__":
    generate_briefing()
