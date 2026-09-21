/**
 * Shared API base URL normalizer for the web app.
 *
 * The portfolio API is an API Gateway HTTP API (`httpApi` events) deployed
 * with the Serverless Framework. Unlike REST APIs (`http` events), HTTP APIs
 * deploy to AWS's `$default` stage, which is NOT part of the URL path — so
 * `https://<id>.execute-api.ap-south-1.amazonaws.com/mt/trades` is correct
 * as-is, with no `/prod` (or any stage) prefix, in both dev and prod.
 */
function normalizeBase(raw: string | undefined, fallback: string): string {
  return (raw ?? fallback).replace(/\/+$/, '');
}

export const PORTFOLIO_API_BASE = normalizeBase(
  process.env.NEXT_PUBLIC_PORTFOLIO_API_BASE,
  'http://localhost:3001',
);

export const ANALYTICS_API_BASE = normalizeBase(
  process.env.NEXT_PUBLIC_ANALYTICS_API_BASE,
  'http://localhost:4001',
);
