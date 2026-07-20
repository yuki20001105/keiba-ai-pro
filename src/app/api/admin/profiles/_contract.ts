export type AdminProfileRole = 'admin' | 'user'

export type AdminProfile = {
  id: string
  email: string
  role: AdminProfileRole
  full_name: string | null
  subscription_tier: 'free' | 'premium'
  created_at: string
}

type ValidationResult<T> =
  | { ok: true; value: T }
  | { ok: false; detail: string }

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i
const CONTROL_PATTERN = /[\u0000-\u001f\u007f]/

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isSafeText(value: unknown, maxLength: number, allowEmpty = false): value is string {
  return typeof value === 'string'
    && value.length <= maxLength
    && (allowEmpty || value.length > 0)
    && !CONTROL_PATTERN.test(value)
}

export function validateAdminTargetUserId(value: unknown): ValidationResult<string> {
  if (typeof value !== 'string' || !UUID_PATTERN.test(value)) {
    return { ok: false, detail: 'Invalid user identifier' }
  }
  return { ok: true, value }
}

export function validateRoleChangeBody(value: unknown): ValidationResult<{ role: AdminProfileRole }> {
  if (!isRecord(value) || Object.keys(value).length !== 1 || !Object.hasOwn(value, 'role')) {
    return { ok: false, detail: 'Request body must contain only role' }
  }
  if (value.role !== 'user' && value.role !== 'admin') {
    return { ok: false, detail: 'Role must be user or admin' }
  }
  return { ok: true, value: { role: value.role } }
}

export function projectAdminProfile(value: unknown): ValidationResult<AdminProfile> {
  if (!isRecord(value)) return { ok: false, detail: 'Invalid profile record' }

  const id = validateAdminTargetUserId(value.id)
  if (!id.ok) return { ok: false, detail: 'Invalid profile record' }
  if (!isSafeText(value.email, 320)) return { ok: false, detail: 'Invalid profile record' }
  if (value.role !== 'user' && value.role !== 'admin') return { ok: false, detail: 'Invalid profile record' }
  if (value.subscription_tier !== 'free' && value.subscription_tier !== 'premium') {
    return { ok: false, detail: 'Invalid profile record' }
  }
  if (value.full_name !== null && !isSafeText(value.full_name, 256, true)) {
    return { ok: false, detail: 'Invalid profile record' }
  }
  if (!isSafeText(value.created_at, 64) || !Number.isFinite(Date.parse(value.created_at))) {
    return { ok: false, detail: 'Invalid profile record' }
  }

  return {
    ok: true,
    value: {
      id: id.value,
      email: value.email,
      role: value.role,
      full_name: value.full_name,
      subscription_tier: value.subscription_tier,
      created_at: value.created_at,
    },
  }
}

export function projectAdminProfiles(value: unknown): ValidationResult<AdminProfile[]> {
  if (!Array.isArray(value) || value.length > 500) {
    return { ok: false, detail: 'Invalid profiles response' }
  }

  const profiles: AdminProfile[] = []
  for (const item of value) {
    const projected = projectAdminProfile(item)
    if (!projected.ok) return { ok: false, detail: 'Invalid profiles response' }
    profiles.push(projected.value)
  }
  return { ok: true, value: profiles }
}

export function projectRoleUpdateResult(value: unknown): ValidationResult<{ id: string; role: AdminProfileRole }> {
  if (!isRecord(value)) return { ok: false, detail: 'Invalid role update response' }
  const id = validateAdminTargetUserId(value.id)
  if (!id.ok || (value.role !== 'user' && value.role !== 'admin')) {
    return { ok: false, detail: 'Invalid role update response' }
  }
  return { ok: true, value: { id: id.value, role: value.role } }
}

export function projectAdminRoleRpcResult(
  value: unknown,
  expected: { id: string; role: AdminProfileRole; requestId: string },
): ValidationResult<{ id: string; role: AdminProfileRole }> {
  if (!Array.isArray(value) || value.length !== 1 || !isRecord(value[0])) {
    return { ok: false, detail: 'Invalid role update response' }
  }

  const row = value[0]
  const keys = Object.keys(row).sort()
  if (keys.length !== 3 || keys[0] !== 'id' || keys[1] !== 'request_id' || keys[2] !== 'role') {
    return { ok: false, detail: 'Invalid role update response' }
  }

  const id = validateAdminTargetUserId(row.id)
  const requestId = validateAdminTargetUserId(row.request_id)
  if (
    !id.ok
    || !requestId.ok
    || (row.role !== 'user' && row.role !== 'admin')
    || id.value !== expected.id
    || row.role !== expected.role
    || requestId.value !== expected.requestId
  ) {
    return { ok: false, detail: 'Invalid role update response' }
  }

  return { ok: true, value: { id: id.value, role: row.role } }
}
