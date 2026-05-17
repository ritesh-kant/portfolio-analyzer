import type { NextAuthConfig } from 'next-auth';
import Google from 'next-auth/providers/google';

const ALLOWED_EMAILS = (process.env.ALLOWED_EMAILS ?? '')
  .split(',')
  .map((e) => e.trim())
  .filter(Boolean);

export const authConfig: NextAuthConfig = {
  providers: [Google],
  pages: {
    signIn: '/login',
  },
  callbacks: {
    authorized({ auth, request: { nextUrl } }) {
      const isLoggedIn = !!auth?.user;
      const isLoginPage = nextUrl.pathname === '/login';
      if (isLoginPage) {
        return isLoggedIn ? Response.redirect(new URL('/dashboard', nextUrl)) : true;
      }
      return isLoggedIn;
    },
    signIn({ user }) {
      if (!user.email) return false;
      if (ALLOWED_EMAILS.length === 0) return false;
      return ALLOWED_EMAILS.includes(user.email);
    },
    jwt({ token, user }) {
      if (user?.email) token['email'] = user.email;
      if (user?.name) token['name'] = user.name;
      return token;
    },
    session({ session, token }) {
      session.user.email = token['email'] as string;
      return session;
    },
  },
};
