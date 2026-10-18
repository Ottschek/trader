// GET /api/health: everything the data health page shows, in one request.

const DAYS = 7;

export async function onRequestGet({ env }) {
  const since = new Date(Date.now() - DAYS * 24 * 3600 * 1000).toISOString();
  const [runs, latest, datasets] = await env.DB.batch([
    env.DB.prepare(
      "SELECT run_at, status, n_ok, n_warn, n_fail, rows_added, duration_s, trigger " +
      "FROM collector_runs WHERE run_at >= ? ORDER BY run_at",
    ).bind(since),
    env.DB.prepare("SELECT run_at FROM collector_runs ORDER BY run_at DESC LIMIT 1"),
    env.DB.prepare(
      "SELECT dataset, kind, source, symbol, rows, first_ts, last_ts, updated_at " +
      "FROM dataset_status ORDER BY kind, source, symbol",
    ),
  ]);

  const latestRunAt = latest.results[0]?.run_at ?? null;
  const checks = latestRunAt
    ? (await env.DB.prepare(
        "SELECT check_name, dataset, status, detail FROM check_results WHERE run_at = ?",
      ).bind(latestRunAt).all()).results
    : [];

  return Response.json(
    {
      generated_at: new Date().toISOString(),
      runs: runs.results,
      latest_run_at: latestRunAt,
      checks,
      datasets: datasets.results,
    },
    { headers: { "Cache-Control": "no-store" } },
  );
}
