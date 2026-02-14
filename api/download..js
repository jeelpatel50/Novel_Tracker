export default async function handler(req, res) {
  const azureIp = process.env.AZURE_SERVER_IP;
  if (!azureIp) {
    return res.status(500).json({ error: "Azure IP not configured in Vercel" });
  }

  const targetUrl = `${azureIp}${req.url.replace('/api/download', '')}`;

  try {
    const response = await fetch(targetUrl, {
      method: req.method,
      headers: { 'Content-Type': 'application/json' },
      body: req.method === 'POST' ? JSON.stringify(req.body) : null,
    });

    const data = await response.json();
    res.status(response.status).json(data);
  } catch (error) {
    res.status(500).json({ error: "Could not reach Azure server" });
  }
}
