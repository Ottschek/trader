// GET /api/experiments: the experiment ledger for the Strategy lab page.

const JSON_FIELDS = ["symbols", "gates", "grid", "folds", "holdout"];

export async function onRequestGet({ env }) {
  const { results } = await env.DB.prepare(
    "SELECT * FROM experiments ORDER BY created_at DESC LIMIT 500",
  ).all();
  const experiments = results.map((row) => {
    const out = { ...row, dirty: Boolean(row.dirty), has_report: Boolean(row.has_report) };
    for (const f of JSON_FIELDS) out[f] = row[f] == null ? null : JSON.parse(row[f]);
    return out;
  });
  return Response.json(
    { generated_at: new Date().toISOString(), experiments },
    { headers: { "Cache-Control": "no-store" } },
  );
}
