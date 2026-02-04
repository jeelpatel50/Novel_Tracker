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

# --- BROWSER SETUP ---
scraper = cloudscraper.create_scraper(
    browser={'browser': 'chrome', 'platform': 'windows', 'mobile': False},
    delay=10
)
scraper.cookies.update({
    'content_warning': '1', 
    'cookieconsent_status': 'dismiss',
    'sh_session': 'true'
})

def get_chapter_count(url):
    try:
        time.sleep(random.uniform(2, 4)) # Be polite
        response = scraper.get(url, timeout=30)
        tree = html.fromstring(response.content)
        
        # --- STRATEGY 1: NOVELBIN AJAX (THE FIX) ---
        if "novelbin" in url:
            # 1. We need the internal "novel-id" hidden in the HTML
            # It's usually in a div like <div id="rating" data-novel-id="12345">
            novel_id = tree.xpath('//div[@data-novel-id]/@data-novel-id')
            
            if novel_id:
                # 2. Ask the server for the FULL chapter list
                ajax_url = f"https://novelbin.me/ajax/chapter-archive?novelId={novel_id[0]}"
                # print(f"   > Found Secret ID: {novel_id[0]}, checking Archive...")
                
                ajax_response = scraper.get(ajax_url)
                ajax_tree = html.fromstring(ajax_response.content)
                
                # 3. Count the chapters in this full list
                chapters = ajax_tree.xpath('//li')
                if chapters:
                    return len(chapters)
            
            # Fallback (Old way)
            chapters = tree.xpath('//ul[@class="list-chapter"]//li//a/text()')
            if chapters:
                last_chap = chapters[0]
                nums = re.findall(r'\d+', last_chap)
                if nums: return int(nums[0])
            return len(chapters)

        # --- SCRIBBLEHUB STRATEGY ---
        elif "scribblehub" in url:
            count_text = tree.xpath('//span[contains(@class, "cnt_chapter")]/text()')
            if count_text:
                return int(count_text[0].replace('(', '').replace(')', ''))
            
            toc_items = tree.xpath('//table[contains(@class, "toc_ol")]//tr') 
            if toc_items: return len(toc_items)
            return 0

        # --- FREEWEBNOVEL STRATEGY ---
        elif "freewebnovel" in url:
            # Read header text "Latest Chapter: 1234"
            latest_text = tree.xpath('//span[contains(@class, "s-last")]/a/text()')
            if not latest_text:
                 latest_text = tree.xpath('//div[@class="m-newest2"]//span[@class="tit"]/text()')

            if latest_text:
                nums = re.findall(r'\d+', latest_text[0])
                if nums: return int(nums[-1])
            
            # Fallback
            chapters = tree.xpath('//div[@class="m-newest2"]//ul//li')
            if not chapters: chapters = tree.xpath('//div[@id="chapterlist"]//p')
            return len(chapters)

        elif "readnovelfull" in url:
            chapters = tree.xpath('//ul[@class="list-chapter"]//li')
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
        response = requests.post("https://api.emailjs.com/api/v1.0/email/send", json=data)
        if response.status_code == 200:
            print(f"   -> Email sent successfully to {to_email}")
        else:
            print(f"   -> EMAIL FAILED! Status: {response.status_code}")
            print(f"   -> Server Message: {response.text}")
    except Exception as e:
        print(f"   -> Connection failed: {e}")

# --- MAIN LOGIC ---
novels = db.collection_group('novels').stream()

# 1. Fetch ALL users to check their pause settings
# (We need to know who paused emails)
paused_users = []
try:
    all_users = db.collection('users').stream()
    for u in all_users:
        if u.to_dict().get('notificationsPaused') == True:
            paused_users.append(u.id)
except:
    pass # If user collection read fails, default to sending emails

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
        
        # --- NOTIFICATION LOGIC ---
        current_read = data.get('readChapters', 0)
        milestone = data.get('milestone', 5)
        unread = real_total - current_read
        
        # Check if this user paused emails
        user_id = novel.reference.parent.parent.id
        is_paused = user_id in paused_users

        if unread >= milestone and data.get('email'):
            if is_paused:
                print(f"   -> Milestone Reached ({unread}), but emails are PAUSED for this user.")
            else:
                print(f"   -> Milestone Reached! ({unread} new chapters)")
                send_email(data.get('email'), current_title, unread)

if not found_any:
    print("No novels found in database.")
