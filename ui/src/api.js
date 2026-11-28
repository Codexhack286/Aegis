export async function api(path, options) {
  const response = await fetch(`/api${path}`, options)
  const data = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(data.error || `API request failed (${response.status}).`)
  return data
}
