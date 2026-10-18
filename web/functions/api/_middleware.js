import { readToken, verifyAccessJwt } from "../../lib/access.js";

const json = (body, status) =>
  Response.json(body, { status, headers: { "Cache-Control": "no-store" } });

const READ_ONLY = new Set(["GET", "HEAD", "OPTIONS"]);

// Every /api route goes through here first.
//
// The site has no login: anyone may read (data health shows statistics, never raw prices
// or keys). Anything that would change something (the M5 control panel, kill switch) is
// refused unless Cloudflare Access is configured, in which case its JWT is verified.
export async function onRequest(ctx) {
  const { request, env } = ctx;
  const accessConfigured = Boolean(env.ACCESS_TEAM_DOMAIN && env.ACCESS_AUD);

  if (!accessConfigured) {
    if (READ_ONLY.has(request.method)) return ctx.next();
    return json({ error: "read_only", reason: "This site is read-only: changes need a login, which is not set up." }, 403);
  }
  try {
    ctx.data.user = await verifyAccessJwt(readToken(request), env.ACCESS_TEAM_DOMAIN, env.ACCESS_AUD);
  } catch (err) {
    return json({ error: "unauthorized", reason: err.message }, 401);
  }
  return ctx.next();
}
