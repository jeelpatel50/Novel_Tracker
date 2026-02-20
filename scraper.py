import firebase_admin
from firebase_admin import credentials, firestore
import cloudscraper
from lxml import html, etree
import os
import json
import requests
import re
import time

# --- 1. AUTHENTICATION (Using your GitHub Secret) ---
print("--- STARTING MULTI-ACCOUNT CLOUD SCRAPER ---")
try:
    # Loads the FIREBASE_CREDENTIALS secret directly
    cred_json = json.loads(os.environ['FIREBASE_CREDENTIALS'])
    cred = credentials.Certificate(cred_json)
    firebase_admin.initialize_app(cred)
    db = firestore.client()
    print("✅ Connected to Firebase!")
except Exception as e:
    print(f"❌ Auth Error: {e}")
    exit(1)

# --- 2. BROWSER SETUP ---
scraper = cloudscraper.create_scraper(
    browser={'browser': 'chrome', 'platform': 'windows', 'mobile': False},
    delay=10
)

# --- 3. HELPER FUNCTIONS ---
def get_clean_image_url(url):
    if not url: return None
    if url.startswith("//"): return "https:" + url
    if "data:image" in url or "base64" in url: return None
    return url

def extract_image_from_tree(tree):
    og_img = tree.xpath('//meta[@property="og:image"]/@content')
    if og_img: return get_clean_image_url(og_img[0])
    lazy_img = tree.xpath('//div[contains(@class, "book")]//img/@data-src')
    if lazy_img: return get_clean_image_url(lazy_img[0])
    img = tree.xpath('//div[contains(@class, "book")]//img/@src')
    if img: return get_clean_image_url(img[0])
    return None

def parse_rss_count(content):
    try:
        root = etree.fromstring(content)
        titles = root.xpath('//item/title/text()')
        highest = 0
        for t in titles:
            nums = re.findall(r'\d+', t)
            if nums:
                for n in nums:
                    if int(n) > highest and int(n) < 10000: highest = int(n)
        return highest
    except: return 0

def send_email(to_email, novel_title, count):
    if not os.environ.get('EMAILJS_PRIVATE_KEY'): return
    data = {
        "service_id": os.environ['EMAILJS_SERVICE_ID'],
        "template_id": os.environ['EMAILJS_TEMPLATE_ID'],
        "user_id": os.environ['EMAILJS_PUBLIC_KEY'],
        "accessToken": os.environ['EMAILJS_PRIVATE_KEY'],
        "template_params": { "to_email": to_email, "novel_name": novel_title, "chapter_count": str(count) }
    }
    try: requests.post("https://api.emailjs.com/api/v1.0/email/send", json=data)
    except: pass

# --- 4. SCRAPER LOGIC ---
def scrape_data(url, needs_image=True):
    data = {'count': 0, 'image': None}
    try:
        time.sleep(1) 
        if "scribblehub.com" in url:
            if needs_image:
                try:
                    resp = scraper.get(url, timeout=8)
                    if resp.status_code == 200:
                        data['image'] = extract_image_from_tree(html.fromstring(resp.content))
                except: pass
            sid_match = re.search(r'/series/(\d+)/', url)
            if sid_match:
                rss_url = f"https://www.scribblehub.com/rssfeed.php?type=series&sid={sid_match.group(1)}"
                for p in [rss_url, f"https://corsproxy.io/?{rss_url}"]:
                    try:
                        r = requests.get(p, timeout=5)
                        if r.status_code == 200:
                            c = parse_rss_count(r.content)
                            if c > 0: data['count'] = c; break
                    except: continue
            
            if data['count'] == 0:
                 slug_match = re.search(r'/series/\d+/([^/]+)/', url)
                 if slug_match:
                     mirror_data = scrape_data(f"https://readnovelfull.com/{slug_match.group(1)}.html", needs_image=needs_image)
                     if mirror_data['count'] > 0:
                         data['count'] = mirror_data['count']
                         if needs_image and not data['image']: data['image'] = mirror_data['image']

        elif "novelbin" in url or "readnovelfull" in url:
            resp = scraper.get(url, timeout=15)
            if resp.status_code == 200:
                tree = html.fromstring(resp.content)
                if needs_image: data['image'] = extract_image_from_tree(tree)
                novel_id = tree.xpath('//div[@data-novel-id]/@data-novel-id')
                if novel_id:
                     ajax_resp = scraper.get(f"https://{url.split('/')[2]}/ajax/chapter-archive?novelId={novel_id[0]}")
                     data['count'] = len(html.fromstring(ajax_resp.content).xpath('//li'))
                else:
                    latest = tree.xpath('//ul[@class="list-chapter"]//li[1]//a/text()')
                    if latest:
                         nums = re.findall(r'\d+', latest[0])
                         if nums: data['count'] = int(nums[-1])

    except Exception as e: print(f"   Error: {e}")
    return data

def get_title(url):
    try:
        title = html.fromstring(scraper.get(url).content).xpath('//title/text()')
        return title[0].split('|')[0].split('-')[0].strip() if title else url
    except: return url

# --- 5. MAIN LOOP (ALL USERS) ---
# This grabs every novel from every Google account you log in with
novels = db.collection_group('novels').stream()

for novel in novels:
    doc_data = novel.to_dict()
    url = doc_data.get('url')
    current_title = doc_data.get('title', 'Unknown')
    should_fetch_image = not doc_data.get('image')

    print(f"\n📚 Checking: {current_title}")
    result = scrape_data(url, needs_image=should_fetch_image)
    
    if result['count'] > 0:
        print(f"   ✅ Found: {result['count']} Chapters")
        updates = {'totalChapters': result['count']}
        if result['image'] and should_fetch_image: updates['image'] = result['image']
        
        if "Pending Sync" in current_title or "Unknown" in current_title:
             updates['title'] = get_title(url)
             
        novel.reference.update(updates)

        # Email Logic
        user_id = novel.reference.parent.parent.id
        unread = result['count'] - doc_data.get('readChapters', 0)
        milestone = doc_data.get('milestone', 5)

        if unread >= milestone and doc_data.get('email'):
            print(f"   📧 Milestone Reached! Sending email...")
            send_email(doc_data.get('email'), updates.get('title', current_title), unread)

print("\n--- DONE ---")