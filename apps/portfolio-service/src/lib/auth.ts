import { verifySessionToken, extractBearerToken } from '@portfolio-analyzer/auth-utils';
import { json, options, resolveOrigin, type ApiResponse } from './http.js';

export interface LambdaHttpEvent {
  headers?: Record<string, string>;
  body?: string;
  method?: string;
  httpMethod?: string;
  requestContext?: { http?: { method?: string } };
  pathParameters?: Record<string, string>;
  queryStringParameters?: Record<string, string>;
}

type HttpHandler = (event: LambdaHttpEvent) => Promise<ApiResponse>;

function getHeader(event: LambdaHttpEvent, name: string): string | undefined {
  const headers = event.headers ?? {};
  return (
    headers[name] ??
    headers[name.toLowerCase()] ??
    headers[name.toUpperCase()] ??
    headers[name.charAt(0).toUpperCase() + name.slice(1)]
  );
}

function getMethod(event: LambdaHttpEvent): string {
  return (
    event.method ??
    event.httpMethod ??
    event.requestContext?.http?.method ??
    'GET'
  ).toUpperCase();
}

export function requireAuth(handler: HttpHandler): HttpHandler {
  return async (event: LambdaHttpEvent) => {
    const origin = getHeader(event, 'origin');
    // Short-circuit CORS preflight before auth so browsers never see a 401
    // without CORS headers (which surfaces as an opaque CORS error).
    if (getMethod(event) === 'OPTIONS') return options(origin);
    try {
      const token = extractBearerToken(getHeader(event, 'authorization'));
      if (!token) return json(401, { error: 'Missing authorization token' }, origin);
      try {
        await verifySessionToken(token);
      } catch {
        return json(401, { error: 'Invalid or expired token' }, origin);
      }
      const response = await handler(event);
      return {
        ...response,
        headers: { ...response.headers, 'access-control-allow-origin': resolveOrigin(origin) },
      };
    } catch (err) {
      // Never let an unhandled throw escape without CORS headers — API
      // Gateway strips the body/headers on a Lambda crash and the browser
      // reports it as "blocked by CORS policy", hiding the real error.
      // eslint-disable-next-line no-console
      console.error('[requireAuth] unhandled error', err);
      return json(500, { error: 'Internal server error' }, origin);
    }
  };
}
