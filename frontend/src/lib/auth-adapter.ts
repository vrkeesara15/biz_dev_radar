import type {
  Adapter,
  AdapterAccount,
  AdapterUser,
  VerificationToken,
} from "@auth/core/adapters";

/**
 * DEV-ONLY in-memory Auth.js adapter.
 *
 * Auth.js requires an adapter for the email (magic-link) provider because the
 * one-time verification token must be stored between "send" and "click", even
 * with JWT sessions. Users live in the backend's regional Postgres (SPEC §3,
 * §10.1), which does not expose them yet, so this process-local store keeps
 * magic links working against Mailpit in `pnpm dev`.
 *
 * TODO(M1): replace with an adapter that calls the backend (users, accounts,
 * verification_tokens) so identities survive restarts and multiple instances.
 * Only the methods used by the email and OAuth flows under
 * `session.strategy = "jwt"` are implemented; session methods are not needed.
 */
export function createMemoryAdapter(): Adapter {
  const users = new Map<string, AdapterUser>();
  const accounts = new Map<string, AdapterAccount>(); // key: provider:providerAccountId
  const tokens = new Map<string, VerificationToken>(); // key: identifier:token

  const accountKey = (a: Pick<AdapterAccount, "provider" | "providerAccountId">) =>
    `${a.provider}:${a.providerAccountId}`;
  const tokenKey = (t: Pick<VerificationToken, "identifier" | "token">) =>
    `${t.identifier}:${t.token}`;
  const findByEmail = (email: string) =>
    [...users.values()].find(
      (u) => u.email.toLowerCase() === email.toLowerCase(),
    ) ?? null;

  return {
    createUser(user) {
      const id = user.id || crypto.randomUUID();
      const record: AdapterUser = { ...user, id };
      users.set(id, record);
      return record;
    },
    getUser(id) {
      return users.get(id) ?? null;
    },
    getUserByEmail(email) {
      return findByEmail(email);
    },
    getUserByAccount(providerAccountId) {
      const account = accounts.get(accountKey(providerAccountId));
      return account ? (users.get(account.userId) ?? null) : null;
    },
    updateUser(user) {
      const existing = users.get(user.id);
      if (!existing) throw new Error(`User ${user.id} not found`);
      const record = { ...existing, ...user };
      users.set(user.id, record);
      return record;
    },
    deleteUser(userId) {
      const user = users.get(userId) ?? null;
      users.delete(userId);
      for (const [key, account] of accounts) {
        if (account.userId === userId) accounts.delete(key);
      }
      return user;
    },
    linkAccount(account) {
      accounts.set(accountKey(account), account);
      return account;
    },
    unlinkAccount(providerAccountId) {
      const key = accountKey(providerAccountId);
      const account = accounts.get(key);
      accounts.delete(key);
      return account;
    },
    createVerificationToken(verificationToken) {
      tokens.set(tokenKey(verificationToken), verificationToken);
      return verificationToken;
    },
    useVerificationToken(params) {
      const key = tokenKey(params);
      const token = tokens.get(key) ?? null;
      tokens.delete(key);
      return token;
    },
  };
}
