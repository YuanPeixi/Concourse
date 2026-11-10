import { reactive } from 'vue'
export const PLATFORM_DISPLAY: Record<string, string> = reactive({
  scholat:    '学者网',
  pigai:      '批改网',
  moodle:     'Moodle',
  itestcloud: 'iTest',
  welearn:    'WeLearn',
  codeforces: 'Codeforces',
  manual:     '手动添加',
})

export const PLATFORM_COLORS: Record<string, string> = reactive({
  scholat:    '#0067C0',
  pigai:      '#107C10',
  moodle:     '#FF8C00',
  itestcloud: '#8764B8',
  welearn:    '#00B7C3',
  codeforces: '#6B3FA0',
  manual:     '#5C7A8A',
})

export function displayName(p: string): string { return PLATFORM_DISPLAY[p] ?? p }
export function platformColor(p: string): string { return PLATFORM_COLORS[p] ?? '#767676' }

export function registerPlatforms(platforms: { name: string; description: { title: string; color: string } }[]) {
  for (const platform of platforms) {
    PLATFORM_DISPLAY[platform.name] = platform.description.title
    PLATFORM_COLORS[platform.name] = platform.description.color
  }
}
