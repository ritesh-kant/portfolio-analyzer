import NextAuth, { type NextAuthResult } from 'next-auth';
import { authConfig } from './auth.config';
import type { NextMiddleware } from 'next/server';

const nextAuth: NextAuthResult = NextAuth(authConfig);

export default nextAuth.auth as NextMiddleware;

export const config = {
  matcher: ['/((?!_next/static|_next/image|favicon.ico|login).*)'],
};
