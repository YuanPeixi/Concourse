const routes = [
  ['GET', /^\/reminders\/settings$/],
  ['PUT', /^\/reminders\/settings$/],
  ['GET', /^\/(health|events|staged|trash|fetchers)(\?[^#]*)?$/],
  ['GET', /^\/events\/[1-9]\d*$/],
  ['POST', /^\/events\/[1-9]\d*\/(done|restore)$/],
  ['DELETE', /^\/events\/[1-9]\d*(\/permanent)?$/],
  ['POST', /^\/staged\/[1-9]\d*\/accept$/],
  ['DELETE', /^\/staged\/[1-9]\d*$/],
  ['POST', /^\/fetchers\/[a-z][a-z0-9_]*\/login\/(start|complete|cancel)$/],
  ['PUT', /^\/fetchers\/[a-z][a-z0-9_]*\/config$/],
  ['POST', /^\/fetchers\/[a-z][a-z0-9_]*\/check$/],
  ['PATCH', /^\/fetchers\/[a-z][a-z0-9_]*$/],
  ['POST', /^\/fetchers\/[a-z][a-z0-9_]*\/run$/],
]
function validateRequest(path, method, body) {
  if (typeof path !== 'string' || path.length > 2048 || /[\\\r\n]/.test(path) ||
      typeof method !== 'string' || !routes.some(([m, re]) => m === method && re.test(path)))
    throw new Error('Unsupported operation')
  if (body !== undefined && (typeof body !== 'string' || body.length > 16384))
    throw new Error('Invalid request body')
  if (body !== undefined) JSON.parse(body)
}
function externalUrl(value) {
  try {
    const url = new URL(value)
    return ['https:', 'http:'].includes(url.protocol) && !url.username && !url.password
  } catch { return false }
}
module.exports = { validateRequest, externalUrl }
