export default async function handler(req, res) {
  const azureIp = process.env.AZURE_SERVER_IP; 
  if (!azureIp) return res.status(500).json({ error: "Azure IP secret missing in Vercel!" });

  const path = req.url.replace('/api/download', '') || '/';
  const targetUrl = `${azureIp}${path}`;

  try {
    const response = await fetch(targetUrl, {
      method: req.method,
      headers: { 'Content-Type': 'application/json' },
      body: req.method === 'POST' ? JSON.stringify(req.body) : null,
    });

    const contentType = response.headers.get('content-type');

    // If the response is a file (EPUB or binary stream)
    if (contentType && (contentType.includes('application/epub+zip') || contentType.includes('application/octet-stream'))) {
        const buffer = await response.arrayBuffer();
        
        // Pass the correct headers so the browser knows a file is arriving
        res.setHeader('Content-Type', contentType);
        res.setHeader('Content-Disposition', response.headers.get('content-disposition') || 'attachment; filename="novel.epub"');
        
        return res.send(Buffer.from(buffer));
    }

    // If the response is a standard JSON message (like a status update)
    const data = await response.json();
    res.status(response.status).json(data);

  } catch (error) {
    // This now only triggers if the connection actually fails
    res.status(502).json({ 
        error: "Connection failed.", 
        details: error.message 
    });
  }
}