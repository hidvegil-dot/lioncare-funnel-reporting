const ENDPOINT = 'https://api.fireflies.ai/graphql';

async function ff(query, variables = {}) {
  const key = process.env.FIREFLIES_API_KEY;
  if (!key) throw new Error('FIREFLIES_API_KEY nincs beállítva');
  const r = await fetch(ENDPOINT, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${key}`
    },
    body: JSON.stringify({ query, variables })
  });
  const data = await r.json();
  if (!r.ok || data.errors) {
    throw new Error(data?.errors?.map(e => e.message).join('; ') || `Fireflies HTTP ${r.status}`);
  }
  return data.data;
}

module.exports = { ff };
