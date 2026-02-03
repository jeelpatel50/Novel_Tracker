import firebase_admin
from firebase_admin import credentials, firestore
import cloudscraper
from lxml import html
import os
import json
import requests
import re

# --- CONFIGURATION ---
try:
    cred_json = json.loads(os.environ['FIREBASE_CREDENTIALS'])
    cred = credentials.Certificate(cred_json)
    firebase_admin.initialize_app(cred)
    db = firestore.client()
except Exception as e:
    print(f"Auth Error: {e}")
    exit(1)

scraper = cloudscraper.create_scraper(browser='chrome')

def get_chapter_count(url):
    try:
        # print(f"Scraping: {url}")  <-- Removed to keep logs clean
        response = scraper.get(url, timeout=15)
        tree = html.fromstring(response.content)
        
        if "novelbin" in url:
            chapters = tree.xpath('//ul[@class="list-chapter"]//li//a/text()')
            if chapters:
                last_chap = chapters[0]
                nums = re.findall(r'\d+', last_chap)
                if nums: return int(nums[0])
            return len(chapters)

        elif "scribblehub" in url:
            count_text = tree.xpath('//span[@class="cnt_chapter"]/text()')
            if count_text:
                return int(count_text[0].replace('(', '').replace(')', ''))

        elif "freewebnovel" in url:
            chapters = tree.xpath('//div[@class="m-newest2"]//ul//li')
            if not chapters:
                chapters = tree.xpath('//div[@id="chapterlist"]//p')
            return len(chapters)

        elif "readnovelfull" in url:
            chapters = tree.xpath('//ul[@class="list-chapter"]//li')
            return len(chapters)

        return 0
    except Exception as e:
        print(f"Failed to scrape {url}: {e}")
        return 0

def get_title(url):
    try:
        response = scraper.get(url)
        tree = html.fromstring(response.content)
        title = tree.xpath('//title/text()')
        if title:
            return title[0].split('|')[0].split('-')[0].strip()
        return url
    except:
        return url

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
        print(f"   -> Email sent to {to_email}")
    except Exception as e:
        print(f"   -> Email failed: {e}")

# --- MAIN LOGIC ---
novels = db.collection_group('novels').stream()

found_any = False
for novel in novels:
    found_any = True
    data = novel.to_dict()
    url = data.get('url')
    
    # 1. Fetch Real Count
    real_total = get_chapter_count(url)
    current_title = data.get('title', 'Unknown Title')
    
    if real_total > 0:
        print(f"Checked: {current_title[:20]}... | Found: {real_total} Chapters")
        
        updates = {}
        updates['totalChapters'] = real_total
        
        # 2. Fix Title if missing
        if current_title == "Pending Sync..." or current_title == "Unknown Title":
            new_title = get_title(url)
            updates['title'] = new_title
            current_title = new_title # Update local var for email
        
        # 3. Apply Update
        novel.reference.update(updates)
        
        # 4. Check Milestone & Email
        current_read = data.get('readChapters', 0)
        milestone = data.get('milestone', 5)
        unread = real_total - current_read
        
        if unread >= milestone and data.get('email'):
            print(f"   -> Milestone Reached! ({unread} new chapters)")
            send_email(data.get('email'), current_title, unread)

if not found_any:
    print("No novels found in database.")
