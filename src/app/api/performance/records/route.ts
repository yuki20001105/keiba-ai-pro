import { NextRequest, NextResponse } from 'next/server'
import { z } from 'zod'
import { ML_API_URL } from '@/lib/backend-url'
import { verifyRequestAuth } from '@/lib/server-auth'

const noStore = { 'Cache-Control': 'no-store' }
const ticket = z.object({
  bet_type: z.enum(['単勝', '馬連', '馬単', 'ワイド', '三連複', '三連単']),
  combination: z.string().min(1).max(30),
  horse_numbers: z.array(z.number().int().min(1).max(99)).min(1).max(3),
  stake: z.number().int().min(100).max(10_000_000).multipleOf(100),
}).strict()
const selection = z.object({
  snapshot_id: z.string().min(1).max(100).refine(value => value.trim().length > 0),
  tickets: z.array(ticket).max(5),
  mode: z.enum(['paper', 'actual']).default('paper'),
  confirmed: z.boolean().default(false),
}).strict().refine(value => value.mode !== 'actual' || (value.confirmed && value.tickets.length > 0))

export async function POST(request: NextRequest) {
  const auth = await verifyRequestAuth(request)
  if (!auth.ok) return NextResponse.json({ detail: auth.detail }, { status: auth.status, headers: noStore })
  const parsed = selection.safeParse(await request.json().catch(() => null))
  if (!parsed.success) return NextResponse.json({ detail: '記録内容を確認してください（購入済みは確認が必要です）' }, { status: 400, headers: noStore })
  try {
    const response = await fetch(`${ML_API_URL}/api/performance/records`, {
      method: 'POST', headers: { Authorization: `Bearer ${auth.context.token}`, 'Content-Type': 'application/json' },
      body: JSON.stringify(parsed.data), cache: 'no-store', signal: AbortSignal.timeout(15_000),
    })
    const data = await response.json()
    return NextResponse.json(data, { status: response.status, headers: noStore })
  } catch {
    return NextResponse.json({ detail: '成績の保存を確認できません。再試行してください' }, { status: 502, headers: noStore })
  }
}
