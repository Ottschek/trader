// Starts the GitHub Actions workflows on time (see wrangler.toml for the schedule).

const COLLECTOR_CRON = "5 * * * *";

export function localTime(date, timeZone) {
  return new Intl.DateTimeFormat("en-GB", {
    timeZone, hour: "2-digit", minute: "2-digit", hourCycle: "h23",
  }).format(date);
}

/** Which workflow (if any) a cron firing should start. */
export function workflowFor(cron, date, env) {
  if (cron === COLLECTOR_CRON) return "collect.yml";
  const now = localTime(date, env.TIMEZONE);
  return env.SUMMARY_TIMES.split(",").includes(now) ? "summary.yml" : null;
}

async function dispatch(env, workflow) {
  const res = await fetch(
    `https://api.github.com/repos/${env.GITHUB_REPO}/actions/workflows/${workflow}/dispatches`,
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${env.GITHUB_DISPATCH_TOKEN}`,
        Accept: "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "trader-scheduler",
      },
      body: JSON.stringify({ ref: "main" }),
    },
  );
  if (res.status !== 204) {
    throw new Error(`GitHub answered ${res.status}: ${(await res.text()).slice(0, 200)}`);
  }
}

async function telegram(env, text) {
  await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ chat_id: env.TELEGRAM_CHAT_ID, text }),
  });
}

async function run(cron, date, env) {
  const workflow = workflowFor(cron, date, env);
  if (!workflow) return;
  try {
    await dispatch(env, workflow);
  } catch (err) {
    // If this fails the collector doesn't run, so nothing else would notice: say it loudly.
    await telegram(env, `🚨 Scheduler could not start ${workflow}: ${err.message}`);
    throw err;
  }
}

export default {
  async scheduled(event, env, ctx) {
    ctx.waitUntil(run(event.cron, new Date(event.scheduledTime), env));
  },
};
