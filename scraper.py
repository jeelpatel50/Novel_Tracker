import firebase_admin
from firebase_admin import credentials, firestore
import cloudscraper
from lxml import html
import os
import json
import requests
import re
import time
import random

# --- CONFIGURATION ---
try:
    cred_json = json.loads(os.environ['FIREBASE_CREDENTIALS'])
    cred = credentials.Certificate(cred_json)
    firebase_admin.initialize_app(cred)
    db = firestore.client()
except Exception as e:
    print(f"Auth Error: {e}")
    exit(1)

# --- BROWSER SETUP (MAXIMUM STEALTH) ---
# We use a specific User-Agent to look exactly like a real Chrome browser
scraper = cloudscraper.create_scraper(
    browser={'browser': 'chrome', 'platform': 'windows', 'mobile': False},
    delay=10
)
scraper.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.5'
})
scraper.cookies.update({
    'content_warning': '1', 
    'cookieconsent_status': 'dismiss',
    'sh_session': 'true'
})

def get_chapter_count(url):
    try:
        time.sleep(random.uniform(2, 4)) # Polite delay
        response = scraper.get(url, timeout=30)
        tree = html.fromstring(response.content)
        
        # --- 1. NOVELBIN & READNOVELFULL (AJAX METHOD) ---
        # Both sites use the same engine. We find the secret ID and ask for the full list.
        if "novelbin" in url or "readnovelfull" in url:
            # Try to find the hidden ID
            novel_id = tree.xpath('//div[@data-novel-id]/@data-novel-id')
            
            if novel_id:
                # Construct the hidden Archive URL
                # Note: novelbin uses .me, readnovelfull uses .com. We parse the domain dynamically.
                domain = url.split('/')[2] # e.g. "readnovelfull.com"
                ajax_url = f"https://{domain}/ajax/chapter-archive?novelId={novel_id[0]}"
                
                # print(f"   > Found Secret ID: {novel_id[0]} on {domain}, checking Archive...")
                ajax_response = scraper.get(ajax_url)
                ajax_tree = html.fromstring(ajax_response.content)
                
                chapters = ajax_tree.xpath('//li')
                if chapters:
                    return len(chapters)
            
            # Fallback 1: Check "Latest Chapter" link text (ReadNovelFull often puts latest at top)
            latest_text = tree.xpath('//ul[@class="list-chapter"]//li[1]//a/text()')
            if latest_text:
                nums = re.findall(r'\d+', latest_text[0])
                if nums: return int(nums[-1])

            # Fallback 2: Count visible items (Will be 50 max, but better than 0)
            chapters = tree.xpath('//ul[@class="list-chapter"]//li')
            return len(chapters)

        # --- 2. SCRIBBLEHUB STRATEGY ---
        elif "scribblehub" in url:
            # Method A: The badge
            count_text = tree.xpath('//span[contains(@class, "cnt_chapter")]/text()')
            if count_text:
                return int(count_text[0].replace('(', '').replace(')', ''))
            
            # Method B: Table count
            toc_items = tree.xpath('//table[contains(@class, "toc_ol")]//tr') 
            if toc_items: return len(toc_items)
            
            # Debugging
            if "Just a moment" in response.text:
                print(f"   !!! Blocked by Cloudflare: {url}")
            return 0

        # --- 3. FREEWEBNOVEL STRATEGY ---
        elif "freewebnovel" in url:
            latest_text = tree.xpath('//span[contains(@class, "s-last")]/a/text()')
            if not latest_text:
                 latest_text = tree.xpath('//div[@class="m-newest2"]//span[@class="tit"]/text()')

            if latest_text:
                nums = re.findall(r'\d+', latest_text[0])
                if nums: return int(nums[-1])
            
            chapters = tree.xpath('//div[@class="m-newest2"]//ul//li')
            if not chapters: chapters = tree.xpath('//div[@id="chapterlist"]//p')
            return len(chapters)

        return 0
    except Exception as e:
        print(f"   !!! Crash scraping {url}: {e}")
        return 0

def get_title(url):
    try:
        response = scraper.get(url)
        tree = html.fromstring(response.content)
        title = tree.xpath('//title/text()')
        if title: return title[0].split('|')[0].split('-')[0].strip()
        return url
    except: return url

def send_email(to_email, novel_title, count):
    if not os.environ.get('EMAILJS_PRIVATE_KEY'):
        print("Skipping email: No API Key found.")
        return

    data = {
        "service_id": os.environ['EMAILJS_SERVICE_ID'],
        "template_id": os.environ['EMAILJS_TEMPLATE_ID'],
        "user_id": os.environ['EMAILJS_PUBLIC_KEY'],
        "accessToken": os.environ['EMAILJS_PRIVATE_KEY'],
        "template_params": {
            "to_email": to_email,
            "novel_name": novel_title,
            "chapter_count": str(count)
        }
    }
    try:
        requests.post("https://api.emailjs.com/api/v1.0/email/send", json=data)
        # print(f"   -> Email sent to {to_email}") 
    except:
        pass

# --- MAIN LOGIC ---
novels = db.collection_group('novels').stream()

# Get Paused Users
paused_users = []
try:
    all_users = db.collection('users').stream()
    for u in all_users:
        if u.to_dict().get('notificationsPaused') == True:
            paused_users.append(u.id)
except: pass

found_any = False
for novel in novels:
    found_any = True
    data = novel.to_dict()
    url = data.get('url')

    # --- AUTO-FIX BAD LINKS ---
    if "scribblehub.com" in url:
        clean_url = url.split("/glossary/")[0].split("/stats/")[0].split("/chapter/")[0]
        if clean_url != url:
            print(f"   * Auto-fixing bad link: {url} -> {clean_url}")
            url = clean_url
            novel.reference.update({'url': url})
    # --------------------------
    
    real_total = get_chapter_count(url)
    current_title = data.get('title', 'Unknown Title')
    
    if real_total > 0:
        print(f"Checked: {current_title[:20]}... | Found: {real_total} Chapters")
        
        updates = {}
        updates['totalChapters'] = real_total
        
        if current_title == "Pending Sync..." or current_title == "Unknown Title":
            new_title = get_title(url)
            updates['title'] = new_title
            current_title = new_title
        
        novel.reference.update(updates)
        
        # Check Pause Logic
        user_id = novel.reference.parent.parent.id
        is_paused = user_id in paused_users
        
        unread = real_total - data.get('readChapters', 0)
        milestone = data.get('milestone', 5)

        if unread >= milestone and data.get('email') and not is_paused:
            print(f"   -> Milestone Reached! ({unread} new chapters)")
            send_email(data.get('email'), current_title, unread)

if not found_any:
    print("No novels found in database.")
