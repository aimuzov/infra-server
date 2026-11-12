import assert from 'node:assert/strict'

const login = process.env.CHATMELD_USER || 'admin'
const password = process.env.CHATMELD_PASSWORD
assert.ok(password, 'CHATMELD_PASSWORD is required')
const base = 'http://127.0.0.1:' + (process.env.CHATMELD_PORT || '4321')
const authorization = 'Basic ' + Buffer.from(login + ':' + password).toString('base64')
const request = (path, options = {}) => fetch(base + path, {
  ...options, signal: AbortSignal.timeout(5000),
})
const response = await request('/api/login', {
  method: 'POST',
  headers: { authorization, 'content-type': 'application/json' },
  body: JSON.stringify({ login, password }),
})
let cookie
let headers
if (response.status === 404 || response.status === 405) {
  headers = { authorization }
} else {
  assert.ok(response.ok, 'Session login failed')
  cookie = response.headers.get('set-cookie')?.split(';')[0]
  assert.ok(cookie?.startsWith('paralpac_session='), 'Session cookie is missing')
  headers = { cookie }
}
try {
  const state = await request('/api/state', { headers })
  assert.ok(state.ok, 'Authenticated state request failed')
  assert.equal((await state.json()).role, 'owner')
} finally {
  if (cookie) {
    const logout = await request('/api/logout', {
      method: 'POST', headers: { cookie, 'content-type': 'application/json' }, body: '{}',
    })
    assert.ok(logout.ok, 'Session logout failed')
  }
}
