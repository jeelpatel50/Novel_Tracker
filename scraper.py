import firebase_admin
from firebase_admin import credentials, firestore
import cloudscraper
from lxml import html, etree
import os
import requests
import re
import time
from dotenv import load_dotenv

# --- CONFIGURATION (GITHUB SAFE) ---
# Load secrets from the hidden .env file
load_dotenv()
TARGET_USER_ID = os.getenv("TARGET_USER_ID")

if not TARGET_USER_ID:
    print("❌ ERROR: TARGET_USER_ID is missing! Please create a .env file.")
    exit(1)

# --- AUTH ---
print("--- STARTING LOCAL SCRAPER (GITHUB SAFE EDITION) ---")
try:
    # Ensure serviceAccountKey.json is in your .gitignore!
    if os.path.exists('serviceAccountKey.json'):
        cred = credentials.Certificate('serviceAccountKey.json')
        firebase_admin.initialize_app(cred)
        db = firestore.client()
        print("✅ Connected to Firebase!")
    else:
        print("❌ ERROR: 'serviceAccountKey.json' not found.")
        exit(1)
except Exception as e:
    print(f"❌ Auth Crash: {e}")
    exit(1)

# --- BROWSER ---
scraper = cloudscraper.create_scraper(
    browser={'browser': 'chrome', 'platform': 'windows', 'mobile': False},
    delay=10
)

# --- HELPER FUNCTIONS ---
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
    
    fwn_img = tree.xpath('//div[@class="m-img"]//img/@src')
    if fwn_img: return get_clean_image_url(fwn_img[0])
    
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

# --- SCRAPER LOGIC ---
def scrape_data(url, needs_image=True):
    data = {'count': 0, 'image': None}
    
    try:
        time.sleep(1) # Be polite
        
        # --- SCRIBBLEHUB ---
        if "scribblehub.com" in url:
            if needs_image:
                try:
                    resp = scraper.get(url, timeout=8)
                    if resp.status_code == 200:
                        tree = html.fromstring(resp.content)
                        data['image'] = extract_image_from_tree(tree)
                        if not data['image']:
                            img = tree.xpath('//div[@class="fic_image"]//img/@src')
                            if img: data['image'] = get_clean_image_url(img[0])
                except: pass

            sid_match = re.search(r'/series/(\d+)/', url)
            if sid_match:
                sid = sid_match.group(1)
                rss_url = f"https://www.scribblehub.com/rssfeed.php?type=series&sid={sid}"
                proxies = [rss_url, f"https://corsproxy.io/?{rss_url}"]
                for p in proxies:
                    try:
                        r = requests.get(p, timeout=5)
                        if r.status_code == 200:
                            c = parse_rss_count(r.content)
                            if c > 0: 
                                data['count'] = c
                                break
                    except: continue
            
            if data['count'] == 0:
                 slug_match = re.search(r'/series/\d+/([^/]+)/', url)
                 if slug_match:
                     slug = slug_match.group(1)
                     print(f"     ⚠️ SH Blocked. Checking Mirror: {slug}...")
                     mirror_data = scrape_data(f"https://readnovelfull.com/{slug}.html", needs_image=needs_image)
                     if mirror_data['count'] > 0:
                         print(f"     ✅ Found on Mirror!")
                         data['count'] = mirror_data['count']
                         if needs_image and not data['image']: 
                             data['image'] = mirror_data['image']

        # --- FREEWEBNOVEL ---
        elif "freewebnovel" in url:
            if "/novel/" in url: url = url.replace("/novel/", "/").rstrip("/") + ".html"
            resp = scraper.get(url, timeout=15)
            if resp.status_code == 200:
                tree = html.fromstring(resp.content)
                if needs_image: data['image'] = extract_image_from_tree(tree)

                latest = tree.xpath('//span[contains(@class, "s-last")]/a/text()')
                if latest: 
                    nums = re.findall(r'\d+', latest[0])
                    if nums: data['count'] = int(nums[-1])
                else:
                    data['count'] = len(tree.xpath('//div[@class="m-newest2"]//ul//li'))

        # --- NOVELBIN / READNOVELFULL ---
        elif "novelbin" in url or "readnovelfull" in url:
            resp = scraper.get(url, timeout=15)
            if resp.status_code == 200:
                tree = html.fromstring(resp.content)
                if needs_image: data['image'] = extract_image_from_tree(tree)
                
                novel_id = tree.xpath('//div[@data-novel-id]/@data-novel-id')
                if novel_id:
                     domain = url.split('/')[2]
                     ajax_url = f"https://{domain}/ajax/chapter-archive?novelId={novel_id[0]}"
                     ajax_resp = scraper.get(ajax_url)
                     data['count'] = len(html.fromstring(ajax_resp.content).xpath('//li'))
                else:
                    latest = tree.xpath('//ul[@class="list-chapter"]//li[1]//a/text()')
                    if latest:
                         nums = re.findall(r'\d+', latest[0])
                         if nums: data['count'] = int(nums[-1])

    except Exception as e:
        print(f"   Error: {e}")
    
    return data

def get_title(url):
    try:
        resp = scraper.get(url)
        title = html.fromstring(resp.content).xpath('//title/text()')
        return title[0].split('|')[0].split('-')[0].strip() if title else url
    except: return url

# --- MAIN LOOP ---
print(f"\n🔍 Scanning Database...")
novels = db.collection('users').document(TARGET_USER_ID).collection('novels').stream()

for novel in novels:
    doc_data = novel.to_dict()
    url = doc_data.get('url')
    current_title = doc_data.get('title', 'Unknown')
    
    existing_image = doc_data.get('image')
    should_fetch_image = not existing_image or existing_image.strip() == ""

    print(f"\n📚 Checking: {current_title}")
    
    result = scrape_data(url, needs_image=should_fetch_image)
    
    if result['count'] > 0:
        print(f"   ✅ Chapters: {result['count']}")
        updates = {'totalChapters': result['count']}
        
        if result['image'] and should_fetch_image:
            print(f"   🖼️  New Cover Image Found!")
            updates['image'] = result['image']
        
        if "Pending Sync" in current_title or "Unknown" in current_title or "New Novel" in current_title:
             new_title = get_title(url)
             updates['title'] = new_title
             print(f"   ✏️  Title Updated: {new_title}")
             
        novel.reference.update(updates)
    else:
        print("   ⚠️  Failed to get data.")

print("\n--- DONE ---")
