import { verifySessionToken, extractBearerToken } from '@portfolio-analyzer/auth-utils';
import { json, resolveOrigin } from './http.js';

export interface LambdaHttpEvent {
  headers?: Record<string, string>;
  body?: string;
  pathParameters?: Record<string, string>;
  queryStringParameters?: Record<string, string>;
}

type HttpHandler = (event: LambdaHttpEvent) => Promise<ReturnType<typeof json>>;

export function requireAuth(handler: HttpHandler): HttpHandler {
  return async (event: LambdaHttpEvent) => {
    const origin = event.headers?.['origin'] ?? event.headers?.['Origin'];
    const token = extractBearerToken(
      event.headers?.['authorization'] ?? event.headers?.['Authorization'],
    );
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
  };
}
