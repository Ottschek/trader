// Verifies the Cloudflare Access JWT, so the API only answers requests that came
// through the Access login (GitHub, Ottschek only), even if someone finds the raw URL.

const certCache = new Map(); // team domain -> { keys, expires }

function b64urlToBytes(s) {
  s = s.replace(/-/g, "+").replace(/_/g, "/");
  while (s.length % 4) s += "=";
  return Uint8Array.from(atob(s), (c) => c.charCodeAt(0));
}

const decodeJson = (part) => JSON.parse(new TextDecoder().decode(b64urlToBytes(part)));

async function signingKeys(team, fetchImpl = fetch, { refresh = false } = {}) {
  const cached = certCache.get(team);
  if (!refresh && cached && cached.expires > Date.now()) return cached.keys;
  const res = await fetchImpl(`https://${team}/cdn-cgi/access/certs`);
  if (!res.ok) throw new Error(`could not load Access certs (${res.status})`);
  const { keys } = await res.json();
  certCache.set(team, { keys, expires: Date.now() + 60 * 60 * 1000 });
  return keys;
}

export async function verifyAccessJwt(token, team, aud, { now = Date.now(), fetchImpl } = {}) {
  const [h, p, sig] = (token || "").split(".");
  if (!h || !p || !sig) throw new Error("malformed token");
  const header = decodeJson(h);
  const payload = decodeJson(p);
  if (header.alg !== "RS256") throw new Error("unexpected algorithm");

  let jwk = (await signingKeys(team, fetchImpl)).find((k) => k.kid === header.kid);
  if (!jwk) {
    // Cloudflare rotates its signing keys; an unknown key ID means our cached list is old.
    jwk = (await signingKeys(team, fetchImpl, { refresh: true })).find((k) => k.kid === header.kid);
  }
  if (!jwk) throw new Error("unknown signing key");
  const key = await crypto.subtle.importKey(
    "jwk", { kty: jwk.kty, n: jwk.n, e: jwk.e, alg: "RS256", ext: true },
    { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" }, false, ["verify"],
  );
  const valid = await crypto.subtle.verify(
    "RSASSA-PKCS1-v1_5", key, b64urlToBytes(sig), new TextEncoder().encode(`${h}.${p}`),
  );
  if (!valid) throw new Error("bad signature");

  const auds = Array.isArray(payload.aud) ? payload.aud : [payload.aud];
  if (!auds.includes(aud)) throw new Error("wrong audience");
  if (payload.iss !== `https://${team}`) throw new Error("wrong issuer");
  if (!payload.exp || payload.exp * 1000 < now) throw new Error("expired");
  return payload;
}

export function readToken(request) {
  const header = request.headers.get("Cf-Access-Jwt-Assertion");
  if (header) return header;
  const cookie = request.headers.get("Cookie") || "";
  const match = cookie.match(/(?:^|;\s*)CF_Authorization=([^;]+)/);
  return match ? match[1] : null;
}
