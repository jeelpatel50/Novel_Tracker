from flask import Flask, request, send_file, jsonify
from flask_cors import CORS
import subprocess
import os
import time
import threading

app = Flask(__name__)
CORS(app) # Allow your website to talk to this server

DOWNLOAD_DIR = "/home/azureuser/novels"
CURRENT_JOBS = {}

def run_download(job_id, url, start, end):
    try:
        CURRENT_JOBS[job_id] = {'status': 'initializing', 'progress': 0}
        
        # Build lncrawl command
        # Auto-mirror logic for ScribbleHub -> NovelBin
        if "scribblehub.com" in url:
            # Simple regex to extract slug could go here, or trust lncrawl's search
            # For now, passing URL directly. lncrawl handles many sites.
            pass

        cmd = ["lncrawl", "-s", url, "--output", DOWNLOAD_DIR, "--format", "epub", "--force"]
        
        # Add range if specified
        if start and end:
            cmd.extend(["--range", str(start), str(end)])
        
        # Run lncrawl
        CURRENT_JOBS[job_id]['status'] = 'downloading'
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        
        # Monitor output for progress (Simple version)
        for line in process.stdout:
            # lncrawl outputs things like "Processing chapter 5 of 10"
            # We can try to parse it, or just set status to 'processing'
            if "Processing" in line or "Downloading" in line:
                CURRENT_JOBS[job_id]['status'] = 'processing'
        
        process.wait()
        
        # Find the generated file
        files = os.listdir(DOWNLOAD_DIR)
        # simplistic: get the newest epub
        paths = [os.path.join(DOWNLOAD_DIR, basename) for basename in files if basename.endswith('.epub')]
        if not paths:
             CURRENT_JOBS[job_id]['status'] = 'error'
             return

        newest_file = max(paths, key=os.path.getctime)
        CURRENT_JOBS[job_id]['status'] = 'done'
        CURRENT_JOBS[job_id]['file'] = newest_file
        
    except Exception as e:
        CURRENT_JOBS[job_id]['status'] = 'error'
        print(f"Error: {e}")

@app.route('/start-download', methods=['POST'])
def start_job():
    data = request.json
    url = data.get('url')
    read = data.get('read', 0)
    total = data.get('total', 0)
    
    job_id = str(int(time.time()))
    
    # Calculate range: Start from read+1
    start = int(read) + 1 if read else None
    end = int(total) if total else None
    
    # Start background thread
    thread = threading.Thread(target=run_download, args=(job_id, url, start, end))
    thread.start()
    
    return jsonify({"job_id": job_id})

@app.route('/status/<job_id>', methods=['GET'])
def get_status(job_id):
    job = CURRENT_JOBS.get(job_id)
    if not job:
        return jsonify({"status": "not_found"}), 404
    return jsonify(job)

@app.route('/download/<job_id>', methods=['GET'])
def download_file(job_id):
    job = CURRENT_JOBS.get(job_id)
    if job and job['status'] == 'done':
        return send_file(job['file'], as_attachment=True)
    return "File not ready", 400

if __name__ == '__main__':
    # Run on port 80 so you don't need to type :5000
    app.run(host='0.0.0.0', port=80)