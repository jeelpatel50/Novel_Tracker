import firebase_admin
from firebase_admin import credentials, firestore
import cloudscraper
from lxml import html
import os
import json
import requests

# 1. Setup Firebase
cred_json = json.loads(os.environ['FIREBASE_CREDENTIALS'])
cred = credentials.Certificate(cred_json)
firebase_admin.initialize_app(cred)
db = firestore.client()

# 2. Setup Scraper (Bypasses Cloudflare)
scraper = cloudscraper.create_scraper()

def get_chapter_count(url):
    try:
        response = scraper.get(url)
        tree = html.fromstring(response.content)
        
        # LOGIC FOR DIFFERENT SITES
        if "novelbin" in url:
            # Novelbin usually lists chapters in a list or has a 'Chapter X' text
            # We look for the latest chapter number in the list
            chapters = tree.xpath('//ul[@class="list-chapter"]//li//a/text()')
            # Extract numbers from strings usually like "Chapter 100: The end"
            return len(chapters) # Or parse the highest number
            
        elif "scribblehub" in url:
            # Scribblehub logic
            count_text = tree.xpath('//span[@class="cnt_chapter"]/text()')
            if count_text:
                return int(count_text[0].replace('(', '').replace(')', ''))
                
        elif "freewebnovel" in url:
            chapters = tree.xpath('//div[@class="m-newest2"]//ul//li')
            return len(chapters) # This might need adjustment based on specific page structure
            
        elif "readnovelfull" in url:
            # Often loaded via AJAX, might need specific endpoint parsing
            # Simple fallback for static lists:
            chapters = tree.xpath('//ul[@class="list-chapter"]//li')
            return len(chapters)

        return 0
    except Exception as e:
        print(f"Error scraping {url}: {e}")
        return 0

def send_email(to_email, novel_title, new_chapters):
    # Using EmailJS REST API
    service_id = os.environ['EMAILJS_SERVICE_ID']
    template_id = os.environ['EMAILJS_TEMPLATE_ID']
    user_id = os.environ['EMAILJS_PUBLIC_KEY']
    private_key = os.environ['EMAILJS_PRIVATE_KEY'] # Enable in EmailJS security settings

    data = {
        "service_id": service_id,
        "template_id": template_id,
        "user_id": user_id,
        "accessToken": private_key,
        "template_params": {
            "to_email": to_email,
            "novel_name": novel_title,
            "chapter_count": new_chapters
        }
    }
    requests.post("https://api.emailjs.com/api/v1.0/email/send", json=data)

# 3. Main Loop
users = db.collection('users').stream()

for user in users:
    novels = db.collection('users').document(user.id).collection('novels').stream()
    
    for novel in novels:
        data = novel.to_dict()
        url = data.get('url')
        current_read = data.get('readChapters', 0)
        milestone = data.get('milestone', 10)
        
        # Scrape real total
        real_total = get_chapter_count(url)
        
        if real_total > 0:
            # Update Title if missing (First run)
            if data.get('title') == "Pending Sync...":
                # Add title scraping logic here if needed, or just use URL
                db.collection('users').document(user.id).collection('novels').document(novel.id).update({
                    'totalChapters': real_total,
                    'title': url.split('/')[-1].replace('-', ' ').title()
                })
            else:
                db.collection('users').document(user.id).collection('novels').document(novel.id).update({
                    'totalChapters': real_total
                })

            # Check Milestone
            unread_count = real_total - current_read
            if unread_count >= milestone:
                # OPTIONAL: Check if we already emailed about this to avoid spam
                # For now, we just send
                print(f"Milestone reached for {data.get('title')}. Sending email...")
                if data.get('email'):
                    send_email(data.get('email'), data.get('title'), unread_count)