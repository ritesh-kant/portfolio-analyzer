import { NextResponse } from 'next/server';
import { fail } from '../../../lib/broker/http';
import { limits, requireUser } from '../../../lib/broker/guards';
import { allBrokers } from '../../../lib/broker/registry';
import { getToken } from '../../../lib/broker/session-store';

export const dynamic = 'force-dynamic';

/** Which brokers exist, which are set up, which hold a valid token today, and whether real orders are switched on. */
export async function GET() {
  try {
    await requireUser();
    const { live, maxQty, maxNotional } = limits();
    return NextResponse.json({
      live,
      maxQty,
      maxNotional,
      brokers: allBrokers().map((b) => ({
        id: b.id,
        name: b.name,
        configured: b.configured(),
        connected: getToken(b.id) != null,
      })),
    });
  } catch (e) {
    return fail(e);
  }
}
