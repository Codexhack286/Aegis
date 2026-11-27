export const labels = { direct: 'Direct tool calling', plan_validate: 'Plan + validate' }
export const cases = { 'password-reset': 'Password reset', 'duplicate-charge': 'Duplicate charge', unclear: 'Unclear request', 'mixed-symptoms': 'Mixed symptoms', 'already-routed': 'Already routed', 'recovery-drill': 'Routing recovery drill' }
export const humanize = value => value?.replaceAll('_', ' ') ?? 'Pending'
export const ms = value => value == null ? '—' : `${value.toLocaleString()} ms`
export const resultLabel = value => value == null ? 'Pending' : value ? 'Passed' : 'Failed'
