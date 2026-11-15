import { betterAuth, type BetterAuthOptions } from "better-auth";

export function createAuth(database: BetterAuthOptions["database"], baseURL: string, secret: string) {
  const origin = new URL(baseURL).origin;
  if (baseURL.replace(/\/$/, "") !== origin || secret.length < 32) {
    throw new Error("BETTER_AUTH_URL must be an origin and BETTER_AUTH_SECRET must have at least 32 characters");
  }
  return betterAuth({
    database,
    baseURL: origin,
    secret,
    trustedOrigins: [origin],
    emailAndPassword: { enabled: true },
    user: { modelName: "auth_user" },
    session: {
      modelName: "auth_session",
      expiresIn: 12 * 60 * 60,
      disableSessionRefresh: true,
      cookieCache: { enabled: false },
    },
    account: { modelName: "auth_account", accountLinking: { enabled: false } },
    verification: { modelName: "auth_verification" },
    rateLimit: { enabled: true, storage: "database" },
    advanced: {
      useSecureCookies: origin.startsWith("https://"),
      ipAddress: { ipAddressHeaders: ["x-real-ip"] },
    },
  });
}
