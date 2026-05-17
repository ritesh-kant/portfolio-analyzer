import NextAuth, { type NextAuthResult } from 'next-auth';
import { authConfig } from './auth.config';

const nextAuth: NextAuthResult = NextAuth(authConfig);

export const handlers: NextAuthResult['handlers'] = nextAuth.handlers;
export const auth: NextAuthResult['auth'] = nextAuth.auth;
export const signIn: NextAuthResult['signIn'] = nextAuth.signIn;
export const signOut: NextAuthResult['signOut'] = nextAuth.signOut;
