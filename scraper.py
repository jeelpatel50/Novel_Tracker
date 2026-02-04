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
# We use a specific User-Agent to look exactly like a real Chrome browser
scraper = cloudscraper.create_scraper(
    browser={'browser': 'chrome', 'platform': 'windows', 'mobile': False},
    delay=10
)
scraper.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36',
})
scraper.cookies.update({
    'content_warning': '1', 
    'cookieconsent_status': 'dismiss',
    'sh_session': 'true'
})

def get_chapter_count(url):
    try:
        time.sleep(random.uniform(2, 4)) # Polite delay
        
        # --- 1. SCRIBBLEHUB (RSS STRATEGY - THE FIX) ---
        if "scribblehub.com" in url:
            # Step 1: Extract the Series ID from the URL
            # URL format: https://www.scribblehub.com/series/12345/novel-name/
            # We need "12345"
            try:
                sid_match = re.search(r'/series/(\d+)/', url)
                if sid_match:
                    sid = sid_match.group(1)
                    # Step 2: Hit the Secret RSS Feed
                    rss_url = f"https://www.scribblehub.com/rssfeed.php?type=series&sid={sid}"
                    # print(f"   > Checking ScribbleHub RSS: {rss_url}")
                    
                    rss_response = scraper.get(rss_url, timeout=30)
                    
                    # Step 3: Parse the XML to find the latest chapter title
                    # The feed has items like <title>Chapter 145: The End</title>
                    # We just need the biggest number from the first few items.
                    if rss_response.status_code == 200:
                        root = etree.fromstring(rss_response.content)
                        # Get all titles from the feed
                        titles = root.xpath('//item/title/text()')
                        
                        highest_num = 0
                        for title in titles:
                            # Look for numbers in the title
                            nums = re.findall(r'\d+', title)
                            if nums:
                                # Start from the right (usually the chapter number is at the start, but sometimes "Chapter 5 part 2")
                                # We assume the first distinct number is the chapter, but let's be safe and check all.
                                for num in nums:
                                    if int(num) > highest_num and int(num) < 99999: # Sanity check
                                        highest_num = int(num)
                        
                        if highest_num > 0:
                            return highest_num
            except Exception as e:
                print(f"   ! RSS Method failed for ScribbleHub: {e}")

            # Fallback to standard scraping if RSS fails
            response = scraper.get(url, timeout=30)
            tree = html.fromstring(response.content)
            count_text = tree.xpath('//span[contains(@class, "cnt_chapter")]/text()')
            if count_text: return int(count_text[0].replace('(', '').replace(')', ''))
            return 0

        # --- 2. NOVELBIN & READNOVELFULL (AJAX STRATEGY) ---
        elif "novelbin" in url or "readnovelfull" in url:
            response = scraper.get(url, timeout=30)
            tree = html.fromstring(response.content)
            
            # Find the secret ID
            novel_id = tree.xpath('//div[@data-novel-id]/@data-novel-id')
            if novel_id:
                domain = url.split('/')[2] 
                ajax_url = f"https://{domain}/ajax/chapter-archive?novelId={novel_id[0]}"
                ajax_response = scraper.get(ajax_url)
                ajax_tree = html.fromstring(ajax_response.content)
                chapters = ajax_tree.xpath('//li')
                if chapters: return len(chapters)
            
            # Fallbacks
            latest_text = tree.xpath('//ul[@class="list-chapter"]//li[1]//a/text()')
            if latest_text:
                nums = re.findall(r'\d+', latest_text[0])
                if nums: return int(nums[-1])
            chapters = tree.xpath('//ul[@class="list-chapter"]//li')
            return len(chapters)

        # --- 3. FREEWEBNOVEL STRATEGY ---
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
        response = scraper.get(url)
        tree = html.fromstring(response.content)
        title = tree.xpath('//title/text()')
        if title: return title[0].split('|')[0].split('-')[0].strip()
        return url
    except: return url

def send_email(to_email, novel_title, count):
    if
