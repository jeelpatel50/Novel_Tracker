import firebase_admin
from firebase_admin import credentials, firestore
import cloudscraper
from lxml import html, etree
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

# --- BROWSER SETUP ---
scraper = cloudscraper.create_scraper(
    browser={'browser': 'chrome', 'platform': 'windows', 'mobile': False},
    delay=10
)
# Standard Headers
scraper.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36',
})

def get_scribblehub_chapters(url):
    """
    Special function to handle ScribbleHub's strict blocking.
    Tries 1. RSS Direct -> 2. RSS via Proxy -> 3. Main Page via Proxy
    """
    sid_match = re.search(r'/series/(\d+)/', url)
    if not sid_match: return 0
    sid = sid_match.group(1)
    
    rss_url = f"https://www.scribblehub.com/rssfeed.php?type=series&sid={sid}"
    
    # ATTEMPT 1: Direct Connection (Will likely fail on GitHub)
    try:
        # print("   > Attempting Direct RSS...")
        resp = scraper.get(rss_url, timeout=10)
        if resp.status_code == 200 and "blocked" not in resp.text.lower():
            return parse_rss(resp.content)
    except: pass

    # ATTEMPT 2: Public Proxy (The Bypass)
    # We use corsproxy.io to hide our GitHub IP address
    try:
        # print("   > Attempting Proxy RSS (Bypassing IP Block)...")
        proxy_url = f"https://corsproxy.io/?{rss_url}"
        resp = requests.get(proxy_url, timeout=15)
        if resp.status_code == 200:
            return parse_rss(resp.content)
    except: pass

    # ATTEMPT 3: Alternative Proxy (CodeTabs)
    try:
        proxy_url = f"https://api.codetabs.com/v1/proxy?quest={rss_url}"
        resp = requests.get(proxy_url, timeout=15)
        if resp.status_code == 200:
            return parse_rss(resp.content)
    except: pass
    
    print(f"   !!! All methods failed for ScribbleHub. IP is banned.")
    return 0

def parse_rss(content):
    try:
        root = etree.fromstring(content)
        titles = root.xpath('//item/title/text()')
        highest_num = 0
        for title in titles:
            nums = re.findall(r'\d+', title)
            if nums:
                for num in nums:
                    if int(num) > highest_num and int(num) < 99999:
                        highest_num = int(num)
        return highest_num
    except:
        return 0

def get_chapter_count(url):
    try:
        time.sleep(random.uniform(2, 4))
        
        # --- SCRIBBLEHUB STRATEGY ---
        if "scribblehub.com" in url:
            return get_scribblehub_chapters(url)

        # --- NOVELBIN & READNOVELFULL ---
        elif "novelbin" in url or "readnovelfull" in url:
            response = scraper.get(url, timeout=30)
            tree = html.fromstring(response.content)
            
            novel_id = tree.xpath('//div[@data-novel-id]/@data-novel-id')
            if novel_id:
                domain = url.split('/')[2]
                ajax_url = f"https://{domain}/ajax/chapter-archive?novelId={novel_id[0]}"
                # Proxies usually aren't needed for NovelBin, but if it fails, we could add them here too.
                ajax_response = scraper.get(ajax_url)
                chapters = html.fromstring(ajax_response.content).xpath('//li')
                if chapters: return len(chapters)
            
            # Fallback
            latest_text = tree.xpath('//ul[@class="list-chapter"]//li[1]//a/text()')
            if latest_text:
                nums = re.findall(r'\d+', latest_text[0])
                if nums: return int(nums[-1])
            return len(tree.xpath('//ul[@class="list-chapter"]//li'))

        # --- FREEWEBNOVEL ---
        elif "freewebnovel" in url:
            response = scraper.get(url, timeout=30)
            tree = html.fromstring(response.content)
            
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
        # For title, we don't use proxy (less critical if it fails once)
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
    except: pass

# --- MAIN LOGIC ---
novels = db.collection_group('novels').stream()

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
        
        user_id = novel.reference.parent.parent.id
        is_paused = user_id in paused_users
        
        unread = real_total - data.get('readChapters', 0)
        milestone = data.get('milestone', 5)

        if unread >= milestone and data.get('email') and not is_paused:
            print(f"   -> Milestone Reached! ({unread} new chapters)")
            send_email(data.get('email'), current_title, unread)

if not found_any:
    print("No novels found in database.")
