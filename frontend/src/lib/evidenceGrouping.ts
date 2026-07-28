/**
 * Evidence 聚合群組工具函式 (issue #862)。
 *
 * 將後端 evidence_groups 結構轉為前端渲染用的映射，
 * 支援群組折疊/展開模式。
 */
import type { EvidenceGroup } from './types'

/**
 * 群組索引反查 Map：evidence index → 所屬群組。
 * 若後端無 evidence_groups 欄位，回傳 null（前端退回 flat 模式）。
 */
export function buildGroupMap(
  groups: EvidenceGroup[] | null | undefined
): Map<number, EvidenceGroup> | null {
  if (!groups || groups.length === 0) return null
  const map = new Map<number, EvidenceGroup>()
  for (const group of groups) {
    for (const idx of group.member_indices) {
      map.set(idx, group)
    }
  }
  return map
}

/**
 * 判定某 evidence index 是否在多筆群組中（即 member_indices.length >= 2）。
 */
export function isGrouped(idx: number, groupMap: Map<number, EvidenceGroup> | null): boolean {
  if (!groupMap) return false
  const group = groupMap.get(idx)
  return group != null && group.member_indices.length >= 2
}

/**
 * 取得某 evidence index 所屬的群組。若不在群組內回傳 null。
 */
export function getGroupForIdx(
  idx: number,
  groupMap: Map<number, EvidenceGroup> | null
): EvidenceGroup | null {
  if (!groupMap) return null
  return groupMap.get(idx) ?? null
}

/**
 * 取得用於渲染的群組順序列表。
 * 每個群組只出現一次（以 representative_idx 為代表），保留原始排序。
 * 單筆群組（member_indices.length === 1）照常回傳。
 */
export function getRenderGroups(
  groups: EvidenceGroup[] | null | undefined
): EvidenceGroup[] {
  if (!groups || groups.length === 0) return []
  return groups
}

/**
 * 趨勢方向的 i18n 標籤。
 */
export function trendLabel(trend: EvidenceGroup['trend'], locale: 'zh' | 'en' = 'zh'): string {
  if (!trend) return ''
  const labels = {
    zh: { rising: '上升趨勢', falling: '下降趨勢', stable: '持平' },
    en: { rising: 'Rising', falling: 'Falling', stable: 'Stable' },
  }
  return labels[locale]?.[trend] ?? ''
}
