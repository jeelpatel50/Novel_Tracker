export default async function handler(req, res) {
  const azureIp = process.env.AZURE_SERVER_IP; 
  if (!azureIp) return res.status(500).json({ error: "Azure IP secret missing in Vercel!" });

  // This cleans up the URL so it matches your Azure server's routes
  const path = req.url.replace('/api/download', '') || '/';
  const targetUrl = `${azureIp}${path}`;

  try {
    const response = await fetch(targetUrl, {
      method: req.method,
      headers: { 'Content-Type': 'application/json' },
      body: req.method === 'POST' ? JSON.stringify(req.body) : null,
    });

    const data = await response.json();
    res.status(response.status).json(data);
  } catch (error) {
    res.status(502).json({ error: "Azure server is offline. Run 'python server.py' on Azure." });
  }
}
