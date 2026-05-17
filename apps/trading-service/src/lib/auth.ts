import { verifySessionToken, extractBearerToken } from '@portfolio-analyzer/auth-utils';
import { json } from './http.js';

export interface LambdaHttpEvent {
  headers?: Record<string, string>;
  body?: string;
  queryStringParameters?: Record<string, string | undefined> | null;
  pathParameters?: Record<string, string>;
}

type HttpHandler = (event: LambdaHttpEvent) => Promise<ReturnType<typeof json>>;

export function requireAuth(handler: HttpHandler): HttpHandler {
  return async (event: LambdaHttpEvent) => {
    const token = extractBearerToken(
      event.headers?.['authorization'] ?? event.headers?.['Authorization'],
    );
    if (!token) return json(401, { error: 'Missing authorization token' });
    try {
      await verifySessionToken(token);
    } catch {
      return json(401, { error: 'Invalid or expired token' });
    }
    return handler(event);
  };
}
