// GET /api/reports: the Telegram report archive (newest first).

export async function onRequestGet({ env }) {
  const { results } = await env.DB.prepare("SELECT id, created_at, kind, title, caption FROM telegram_reports ORDER BY created_at DESC LIMIT 100").all();
  return Response.json({ generated_at: new Date().toISOString(), reports: results }, { headers: { "Cache-Control": "no-store" } });
}
