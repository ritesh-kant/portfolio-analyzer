/**
 * Shared API base URL normalizer for the web app.
 *
 * The portfolio API is an API Gateway HTTP API deployed with
 * `serverless --stage prod`, so its routes live under the `/prod` stage
 * prefix (e.g. `https://<id>.execute-api.ap-south-1.amazonaws.com/prod/...`).
 * `serverless-offline.noPrependStageInUrl` hides this locally, so a bare
 * invoke URL works in dev but 404s in prod — and gateway-level 404s carry no
 * CORS headers, surfacing in the browser as an opaque CORS error.
 *
 * This helper appends `/prod` automatically when the configured base is a
 * bare `*.execute-api.*.amazonaws.com` host, while leaving localhost, custom
 * domains, and already-prefixed values untouched.
 */
function normalizeBase(raw: string | undefined, fallback: string): string {
  const value = (raw ?? fallback).replace(/\/+$/, '');
  if (/\.execute-api\.[^/]+\.amazonaws\.com$/i.test(value)) {
    return `${value}/prod`;
  }
  return value;
}

export const PORTFOLIO_API_BASE = normalizeBase(
  process.env.NEXT_PUBLIC_PORTFOLIO_API_BASE,
  'http://localhost:3001',
);

export const ANALYTICS_API_BASE = normalizeBase(
  process.env.NEXT_PUBLIC_ANALYTICS_API_BASE,
  'http://localhost:4001',
);
