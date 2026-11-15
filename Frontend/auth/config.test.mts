import assert from "node:assert/strict";
import test from "node:test";
import { memoryAdapter } from "better-auth/adapters/memory";
import { createAuth } from "./config.mts";

const origin = "http://localhost:5000";
const secret = "test-secret-for-authentication-only-32-characters";

test("password accounts authenticate, reject bad credentials and cross-site requests, and revoke sessions", async () => {
  const database: Record<string, Record<string, unknown>[]> = {
    auth_user: [],
    auth_session: [],
    auth_account: [],
    auth_verification: [],
    rateLimit: [],
  };
  const auth = createAuth(memoryAdapter(database), origin, secret);
  const post = (path: string, body: object, cookie = "", requestOrigin = origin) =>
    auth.handler(
      new Request(`${origin}/api/auth/${path}`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Origin: requestOrigin,
          Cookie: cookie,
          "X-Real-IP": "127.0.0.1",
        },
        body: JSON.stringify(body),
      }),
    );
  const credentials = { email: "user@example.test", password: "a-test-password-123" };
  const signup = await post("sign-up/email", { ...credentials, name: "Test User" });
  assert.equal(signup.status, 200);
  assert.equal(database.auth_account.length, 1);
  assert.notEqual(database.auth_account[0].password, credentials.password);
  assert.equal(typeof database.auth_account[0].password, "string");
  const cookies = (response: Response) =>
    response.headers
      .getSetCookie()
      .map((value) => value.split(";")[0])
      .join("; ");
  const signupCookie = cookies(signup);
  assert.match(signupCookie, /better-auth.session_token=/);
  assert.match(signup.headers.get("set-cookie")!, /HttpOnly/i);
  const session = (cookie: string) =>
    auth.handler(
      new Request(`${origin}/api/auth/get-session`, {
        headers: { Cookie: cookie, "X-Real-IP": "127.0.0.1" },
      }),
    );
  assert.equal((await (await session(signupCookie)).json()).user.email, credentials.email);
  assert.equal((await post("sign-in/email", { ...credentials, password: "wrong-password" })).status, 401);
  assert.equal((await post("sign-in/email", credentials, "", "https://attacker.example")).status, 403);
  const signin = await post("sign-in/email", credentials);
  assert.equal(signin.status, 200);
  const cookie = cookies(signin);
  assert.equal((await post("sign-out", {}, cookie)).status, 200);
  assert.equal(await (await session(cookie)).json(), null);
  assert.equal(await (await session("better-auth.session_token=forged")).json(), null);
});

test("invalid public URLs and short secrets fail at startup", () => {
  assert.throws(() => createAuth(memoryAdapter({}), `${origin}/untrusted-path`, secret));
  assert.throws(() => createAuth(memoryAdapter({}), origin, "short"));
});
