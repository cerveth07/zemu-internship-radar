// Adapted from JiemsLBJ/offer-harvester helpers.ts at cd0086ea48786c0216e6a5905be613bcfa8471cd.
// MIT; TypeScript annotations removed. Unknown glyphs are retained, never deleted.
export function decodeEntities(text) {
  return text.replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&semi;/g, ';')
    .replace(/&#(\d+);?/g, (_, dec) => String.fromCodePoint(parseInt(dec, 10)))
    .replace(/&#[xX]([0-9a-fA-F]+);?/g, (_, hex) => String.fromCodePoint(parseInt(hex, 16)))
    .replace(/&nbsp;/g, ' ')
}
export function cleanHtml(html) {
  if (!html) return ''
  const breaks = html.replace(/<\s*br\s*\/?>/gi, '\n').replace(/<\/(p|li|ul|ol|div|h\d|tr)>/gi, '\n')
  return decodeEntities(breaks.replace(/<[^>]+>/g, ' ')).replace(/[ \t]+/g, ' ')
    .replace(/ *\n */g, '\n').replace(/\n{3,}/g, '\n\n').trim()
}
export function normalizeDate(raw) {
  if (typeof raw !== 'string' && typeof raw !== 'number') return ''
  const s = String(raw).trim()
  const match = s.match(/^(\d{4})[-/年.](\d{1,2})[-/月.](\d{1,2})/)
  if (match) return `${match[1]}-${match[2].padStart(2, '0')}-${match[3].padStart(2, '0')}`
  if (/^\d{9,13}$/.test(s)) {
    const date = new Date(Number(s) > 1e12 ? Number(s) : Number(s) * 1000)
    if (!isNaN(date.getTime())) return date.toISOString().slice(0, 10)
  }
  return ''
}
