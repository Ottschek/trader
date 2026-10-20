// GET /telegram/<id>: a Telegram report picture (PNG in R2 under telegram/<id>.png).

export async function onRequestGet({ params, env }) {
  const id = String(params.id || "").replace(/\.png$/, "");
  if (!/^[0-9a-f]{12}$/.test(id)) return new Response("Not found", { status: 404 });
  const object = await env.REPORTS.get(`telegram/${id}.png`);
  if (!object) return new Response("Report not found", { status: 404 });
  return new Response(object.body, {
    headers: { "Content-Type": "image/png", "Cache-Control": "public, max-age=86400", "X-Robots-Tag": "noindex" },
  });
}
