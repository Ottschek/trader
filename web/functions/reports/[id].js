// GET /reports/<id>: a report card (HTML written by scripts/validate.py, stored in R2).

export async function onRequestGet({ params, env }) {
  const id = String(params.id || "");
  if (!/^[0-9a-f]{12}$/.test(id)) return new Response("Not found", { status: 404 });
  const object = await env.REPORTS.get(`reports/${id}.html`);
  if (!object) return new Response("Report card not found", { status: 404 });
  return new Response(object.body, {
    headers: {
      "Content-Type": "text/html; charset=utf-8",
      "Cache-Control": "public, max-age=3600",   // report cards never change once written
      "X-Robots-Tag": "noindex",
    },
  });
}
