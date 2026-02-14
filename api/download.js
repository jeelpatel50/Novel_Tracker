export default async function handler(req, res) {
  const azureIp = process.env.AZURE_SERVER_IP; 

  if (!azureIp) {
    return res.status(500).json({ error: "Azure IP not found in Vercel secrets." });
  }

  const targetPath = req.url.replace('/api/download', '');
  const targetUrl = `${azureIp}${targetPath}`;

  try {
    const response = await fetch(targetUrl, {
      method: req.method,
      headers: { 'Content-Type': 'application/json' },
      body: req.method === 'POST' ? JSON.stringify(req.body) : null,
    });

    // 3. Forward the response back to your website
    const data = await response.json();
    res.status(response.status).json(data);
  } catch (error) {
    // This happens if the Azure Python server is turned off
    res.status(502).json({ error: "Azure server is unreachable. Check your Python script." });
  }
}
