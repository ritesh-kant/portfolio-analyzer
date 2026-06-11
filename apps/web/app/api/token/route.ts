import { NextResponse } from 'next/server';
import { SignJWT } from 'jose';
import { auth } from '../../../auth';

export async function GET() {
  const secret = process.env.AUTH_SECRET;
  if (!secret) {
    return NextResponse.json({ error: 'Server misconfigured' }, { status: 500 });
  }

  let email: string;
  let name: string | undefined;

  if (process.env.DISABLE_AUTH === 'true') {
    email = 'dev@local';
    name = 'Dev';
  } else {
    const session = await auth();
    if (!session?.user?.email) {
      return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
    }
    email = session.user.email;
    name = session.user.name ?? undefined;
  }

  const key = new TextEncoder().encode(secret);
  const token = await new SignJWT({ email, name })
    .setProtectedHeader({ alg: 'HS256' })
    .setIssuedAt()
    .setExpirationTime('1h')
    .sign(key);

  return NextResponse.json({ token });
}
