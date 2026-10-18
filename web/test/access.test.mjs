// Run: npm test (in web/)
import { test } from "node:test";
import assert from "node:assert/strict";
import { verifyAccessJwt, readToken } from "../lib/access.js";

const TEAM = "example.cloudflareaccess.com";
const AUD = "aud-123";
const b64url = (bytes) => Buffer.from(bytes).toString("base64url");

async function setup() {
  const { privateKey, publicKey } = await crypto.subtle.generateKey(
    { name: "RSASSA-PKCS1-v1_5", modulusLength: 2048, publicExponent: new Uint8Array([1, 0, 1]), hash: "SHA-256" },
    true, ["sign", "verify"],
  );
  const kid = crypto.randomUUID(); // new key ID per test, like a key rotation
  const jwk = { ...(await crypto.subtle.exportKey("jwk", publicKey)), kid };
  const fetchImpl = async () => new Response(JSON.stringify({ keys: [jwk] }));
  const sign = async (payload, header = { alg: "RS256", kid }) => {
    const h = b64url(JSON.stringify(header));
    const p = b64url(JSON.stringify(payload));
    const sig = await crypto.subtle.sign("RSASSA-PKCS1-v1_5", privateKey, new TextEncoder().encode(`${h}.${p}`));
    return `${h}.${p}.${b64url(new Uint8Array(sig))}`;
  };
  return { sign, fetchImpl };
}

const good = () => ({ aud: [AUD], iss: `https://${TEAM}`, exp: Math.floor(Date.now() / 1000) + 600, email: "me@example.com" });

test("accepts a valid Access token", async () => {
  const { sign, fetchImpl } = await setup();
  const payload = await verifyAccessJwt(await sign(good()), TEAM, AUD, { fetchImpl });
  assert.equal(payload.email, "me@example.com");
});

test("rejects wrong audience, issuer, expired, tampered and missing tokens", async () => {
  const { sign, fetchImpl } = await setup();
  const opts = { fetchImpl };
  await assert.rejects(verifyAccessJwt(await sign({ ...good(), aud: ["other"] }), TEAM, AUD, opts), /audience/);
  await assert.rejects(verifyAccessJwt(await sign({ ...good(), iss: "https://evil" }), TEAM, AUD, opts), /issuer/);
  await assert.rejects(verifyAccessJwt(await sign({ ...good(), exp: 1 }), TEAM, AUD, opts), /expired/);
  const token = await sign(good());
  const [h, , s] = token.split(".");
  const forged = `${h}.${b64url(JSON.stringify({ ...good(), email: "attacker@x" }))}.${s}`;
  await assert.rejects(verifyAccessJwt(forged, TEAM, AUD, opts), /signature/);
  await assert.rejects(verifyAccessJwt(null, TEAM, AUD, opts), /malformed/);
  await assert.rejects(verifyAccessJwt(await sign(good(), { alg: "none" }), TEAM, AUD, opts), /algorithm/);
});

test("reads the token from header or cookie", () => {
  assert.equal(readToken(new Request("https://x", { headers: { "Cf-Access-Jwt-Assertion": "abc" } })), "abc");
  assert.equal(readToken(new Request("https://x", { headers: { Cookie: "a=1; CF_Authorization=xyz" } })), "xyz");
  assert.equal(readToken(new Request("https://x")), null);
});
