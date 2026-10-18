import { readToken, verifyAccessJwt } from "../../lib/access.js";

const json = (body, status) =>
  Response.json(body, { status, headers: { "Cache-Control": "no-store" } });

// Every /api route goes through here first.
export async function onRequest(ctx) {
  const { request, env } = ctx;
  if (!env.ACCESS_TEAM_DOMAIN || !env.ACCESS_AUD) {
    // Secure by default: no data until the Access login is in front of the site.
    return json({ error: "locked", reason: "Cloudflare Access is not set up yet." }, 403);
  }
  try {
    ctx.data.user = await verifyAccessJwt(readToken(request), env.ACCESS_TEAM_DOMAIN, env.ACCESS_AUD);
  } catch (err) {
    return json({ error: "unauthorized", reason: err.message }, 401);
  }
  return ctx.next();
}
