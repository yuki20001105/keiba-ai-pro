import { NextRequest, NextResponse } from 'next/server'
import vision from '@google-cloud/vision'
import { createSupabaseServiceClient, verifyRequestAuth } from '@/lib/server-auth'

export const runtime = 'nodejs'

// Google Vision APIクライアント
const visionClient = new vision.ImageAnnotatorClient({
  keyFilename: process.env.GOOGLE_APPLICATION_CREDENTIALS,
})

const OCR_MAX_IMAGE_BYTES = 10 * 1024 * 1024
const OCR_ALLOWED_IMAGE_TYPES = new Set([
  'image/jpeg',
  'image/png',
  'image/webp',
])
const OCR_ALLOWED_EXTENSIONS: Record<string, ReadonlySet<string>> = {
  'image/jpeg': new Set(['jpg', 'jpeg']),
  'image/png': new Set(['png']),
  'image/webp': new Set(['webp']),
}

function hasExpectedImageSignature(buffer: Buffer, mimeType: string): boolean {
  if (mimeType === 'image/jpeg') {
    return buffer.length >= 3
      && buffer[0] === 0xff
      && buffer[1] === 0xd8
      && buffer[2] === 0xff
  }

  if (mimeType === 'image/png') {
    const signature = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]
    return buffer.length >= signature.length
      && signature.every((byte, index) => buffer[index] === byte)
  }

  if (mimeType === 'image/webp') {
    return buffer.length >= 12
      && buffer.subarray(0, 4).toString('ascii') === 'RIFF'
      && buffer.subarray(8, 12).toString('ascii') === 'WEBP'
  }

  return false
}

function hasSafeImageName(image: File): boolean {
  const name = image.name.trim()
  if (!name || name.length > 255 || /[\u0000-\u001f\u007f]/.test(name)) {
    return false
  }

  const extension = name.includes('.') ? name.split('.').pop()?.toLowerCase() : null
  return Boolean(extension && OCR_ALLOWED_EXTENSIONS[image.type]?.has(extension))
}

type OcrQuotaReservation = {
  allowed: boolean
  usedCount: number
  monthlyLimit: number
  resetAt: string
}

function parseOcrQuotaReservation(data: unknown): OcrQuotaReservation | null {
  if (!Array.isArray(data) || data.length !== 1) return null

  const row = data[0]
  if (!row || typeof row !== 'object' || Array.isArray(row)) return null

  const value = row as Record<string, unknown>
  const expectedKeys = ['allowed', 'monthly_limit', 'reset_at', 'used_count']
  const actualKeys = Object.keys(value).sort()
  if (
    actualKeys.length !== expectedKeys.length
    || actualKeys.some((key, index) => key !== expectedKeys[index])
  ) {
    return null
  }

  const allowed = value.allowed
  const usedCount = value.used_count
  const monthlyLimit = value.monthly_limit
  const resetAt = value.reset_at

  if (
    typeof allowed !== 'boolean'
    || typeof usedCount !== 'number'
    || !Number.isSafeInteger(usedCount)
    || usedCount < 0
    || typeof monthlyLimit !== 'number'
    || !Number.isSafeInteger(monthlyLimit)
    || monthlyLimit < 0
    || typeof resetAt !== 'string'
    || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.test(resetAt)
    || Number.isNaN(Date.parse(resetAt))
  ) {
    return null
  }

  if (allowed) {
    if (usedCount < 1 || usedCount > monthlyLimit) return null
  } else if (usedCount < monthlyLimit) {
    return null
  }

  return { allowed, usedCount, monthlyLimit, resetAt }
}

export async function POST(request: NextRequest) {
  try {
    const authz = await verifyRequestAuth(request)
    if (!authz.ok) {
      return NextResponse.json({ detail: authz.detail }, { status: authz.status })
    }

    const verifiedUserId = authz.context.user.id
    let formData: FormData
    try {
      formData = await request.formData()
    } catch {
      return NextResponse.json(
        { error: 'Invalid multipart form data' },
        { status: 400 }
      )
    }

    const images = formData.getAll('image')
    if (images.length !== 1 || !(images[0] instanceof File)) {
      return NextResponse.json(
        { error: 'Exactly one image file is required' },
        { status: 400 }
      )
    }
    const image = images[0]

    const userIds = formData.getAll('userId')
    if (
      userIds.length > 1
      || (userIds.length === 1 && (typeof userIds[0] !== 'string' || !userIds[0].trim()))
    ) {
      return NextResponse.json(
        { error: 'Invalid userId field' },
        { status: 400 }
      )
    }
    const userId = userIds.length === 1 ? userIds[0] : null

    if (typeof userId === 'string' && userId !== verifiedUserId) {
      return NextResponse.json(
        { error: 'userId mismatch is not allowed' },
        { status: 403 }
      )
    }

    if (
      !OCR_ALLOWED_IMAGE_TYPES.has(image.type)
      || !hasSafeImageName(image)
      || !Number.isSafeInteger(image.size)
      || image.size < 1
      || image.size > OCR_MAX_IMAGE_BYTES
    ) {
      return NextResponse.json(
        { error: 'Invalid image file' },
        { status: 400 }
      )
    }

    // Complete all local file preparation before reserving quota. Invalid or
    // unreadable uploads must never consume a unit or reach the external API.
    let buffer: Buffer
    try {
      const bytes = await image.arrayBuffer()
      buffer = Buffer.from(bytes)
    } catch {
      return NextResponse.json(
        { error: 'Image file could not be read' },
        { status: 400 }
      )
    }
    if (
      buffer.length !== image.size
      || buffer.length > OCR_MAX_IMAGE_BYTES
      || !hasExpectedImageSignature(buffer, image.type)
    ) {
      return NextResponse.json(
        { error: 'Invalid image content' },
        { status: 400 }
      )
    }

    const supabase = createSupabaseServiceClient()
    if (!supabase) {
      return NextResponse.json(
        { error: 'OCR service is unavailable' },
        { status: 503 }
      )
    }

    // Reserve quota atomically before invoking the external Vision service.
    let quotaResult: Awaited<ReturnType<typeof supabase.rpc>>
    try {
      quotaResult = await supabase.rpc(
        'consume_ocr_quota',
        { p_user_id: verifiedUserId }
      )
    } catch {
      return NextResponse.json(
        { error: 'OCR quota service is unavailable' },
        { status: 503 }
      )
    }
    const { data: quotaData, error: quotaError } = quotaResult
    if (quotaError) {
      return NextResponse.json(
        { error: 'OCR quota service is unavailable' },
        { status: 503 }
      )
    }

    const quota = parseOcrQuotaReservation(quotaData)
    if (!quota) {
      return NextResponse.json(
        { error: 'OCR quota service returned an invalid response' },
        { status: 503 }
      )
    }
    if (!quota.allowed) {
      return NextResponse.json(
        { error: 'Monthly OCR usage limit reached' },
        { status: 429 }
      )
    }

    // Once Vision is invoked, the reservation is intentionally not refunded:
    // the external provider may have accepted and billed the attempt even if
    // it later fails or returns no text. Only pre-Vision local failures avoid
    // quota consumption.
    const [result] = await visionClient.textDetection(buffer)
    const detections = result.textAnnotations

    if (!detections || detections.length === 0) {
      return NextResponse.json(
        { error: 'テキストが検出されませんでした' },
        { status: 400 }
      )
    }

    const extractedText = detections[0].description || ''

    // 馬券情報を抽出（簡易パーサー）
    const betInfo = parseBetTicket(extractedText)

    // The quota is already consumed. Record the result without mutating the
    // profile counter a second time.
    const { error: usageError } = await supabase.from('ocr_usage').insert({
      user_id: verifiedUserId,
      extracted_text: extractedText,
      corrected_data: betInfo,
      success: true,
    })
    if (usageError) {
      return NextResponse.json(
        { error: 'OCR usage could not be recorded' },
        { status: 503 }
      )
    }

    return NextResponse.json({
      extractedText,
      betInfo,
      needsCorrection: !betInfo.confidence || betInfo.confidence < 0.8,
    })
  } catch {
    console.error('OCR_UNHANDLED_FAILURE')
    return NextResponse.json(
      { error: 'OCR processing failed' },
      { status: 500 }
    )
  }
}

/**
 * 馬券テキストをパース
 */
function parseBetTicket(text: string): {
  raceName?: string
  raceDate?: string
  betType?: string
  horses?: number[]
  betAmount?: number
  odds?: number
  confidence: number
} {
  const result: any = { confidence: 0 }

  // レース名を抽出
  const raceNameMatch = text.match(/第\d+レース|(\d+)R/)
  if (raceNameMatch) {
    result.raceName = raceNameMatch[0]
    result.confidence += 0.2
  }

  // 馬券タイプを抽出
  const betTypeMatch = text.match(/(単勝|複勝|馬連|馬単|ワイド|三連複|三連単)/)
  if (betTypeMatch) {
    result.betType = betTypeMatch[0]
    result.confidence += 0.2
  }

  // 馬番を抽出
  const horseNumbers = text.match(/\d+番/g)
  if (horseNumbers) {
    result.horses = horseNumbers.map(h => parseInt(h.replace('番', '')))
    result.confidence += 0.2
  }

  // 金額を抽出
  const amountMatch = text.match(/(\d{2,})円/)
  if (amountMatch) {
    result.betAmount = parseInt(amountMatch[1])
    result.confidence += 0.2
  }

  // オッズを抽出
  const oddsMatch = text.match(/(\d+\.\d+)倍/)
  if (oddsMatch) {
    result.odds = parseFloat(oddsMatch[1])
    result.confidence += 0.2
  }

  return result
}
