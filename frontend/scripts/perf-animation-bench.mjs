/**
 * TrustForge Animation Performance Benchmark
 *
 * Static analysis of animation complexity + Chrome Performance.getMetrics.
 * No RAF loop needed — works in any headless mode.
 *
 * Usage: node scripts/perf-animation-bench.mjs
 */

import { chromium } from 'playwright'

const URL = 'http://localhost:4174/?qa=1'

async function main() {
  console.log(`\n🎬 TrustForge Animation Performance Benchmark`)
  console.log(`   URL: ${URL}`)
  console.log(`   ─────────────────────────────────────\n`)

  const browser = await chromium.launch({
    channel: 'chrome',
    headless: true,
  })

  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } })
  const page = await context.newPage()
  const cdp = await context.newCDPSession(page)
  await cdp.send('Performance.enable')

  console.log('⏳ Loading page...')
  await page.goto(URL, { waitUntil: 'load', timeout: 30000 })
  // Simple sync eval to confirm page is alive
  const title = await page.evaluate(() => document.title)
  console.log(`✅ Loaded: "${title}"\n`)

  // ──────────────────────────────────────────────────────
  // 1) Chrome Performance Metrics
  // ──────────────────────────────────────────────────────
  const { metrics } = await cdp.send('Performance.getMetrics')
  const m = Object.fromEntries(metrics.map((x) => [x.name, x.value]))

  console.log(`   ┌─── Chrome Performance Metrics ─────────────────┐`)
  console.log(`   │ DOM Nodes:              ${m.Nodes || 'N/A'}`)
  console.log(`   │ Layout Objects:         ${m.LayoutObjects || 'N/A'}`)
  console.log(`   │ JS Event Listeners:     ${m.JSEventListeners || 'N/A'}`)
  console.log(`   │ Layout Count:           ${m.LayoutCount || 'N/A'}`)
  console.log(`   │ RecalcStyle Count:      ${m.RecalcStyleCount || 'N/A'}`)
  console.log(`   │ Layout Duration:        ${((m.LayoutDuration || 0) * 1000).toFixed(1)}ms total`)
  console.log(`   │ RecalcStyle Duration:   ${((m.RecalcStyleDuration || 0) * 1000).toFixed(1)}ms total`)
  console.log(`   │ Script Duration:        ${((m.ScriptDuration || 0) * 1000).toFixed(1)}ms total`)
  console.log(`   │ Task Duration:          ${((m.TaskDuration || 0) * 1000).toFixed(1)}ms total`)
  console.log(`   │ JS Heap Used:           ${((m.JSHeapUsedSize || 0) / 1024 / 1024).toFixed(1)}MB`)
  console.log(`   │ JS Heap Total:          ${((m.JSHeapTotalSize || 0) / 1024 / 1024).toFixed(1)}MB`)
  console.log(`   └────────────────────────────────────────────────┘\n`)

  // ──────────────────────────────────────────────────────
  // 2) DOM Animation Inventory
  // ──────────────────────────────────────────────────────
  console.log('📊 Analyzing DOM animations...')

  const domAnalysis = await page.evaluate(() => {
    const all = document.querySelectorAll('*')
    let animatedCount = 0
    let willChangeCount = 0
    let backdropFilterCount = 0
    let preserv3dCount = 0
    let boxShadowCount = 0
    let filterCount = 0
    let transitionCount = 0
    const animationNames = {}
    const backdropElements = []
    const preserv3dElements = []
    const heavyAnimations = []

    for (const el of all) {
      const s = getComputedStyle(el)
      if (s.animationName && s.animationName !== 'none') {
        animatedCount++
        for (const n of s.animationName.split(',')) {
          const name = n.trim()
          animationNames[name] = (animationNames[name] || 0) + 1
        }
        // Check if this animation uses expensive properties
        const dur = parseFloat(s.animationDuration) || 0
        if (dur > 0 && dur < 10) {
          const tag = el.tagName + (el.className ? '.' + el.className.split(' ')[0] : '')
          const rect = el.getBoundingClientRect()
          const area = rect.width * rect.height
          if (area > 50000) { // Large element
            heavyAnimations.push({ tag: tag.slice(0, 50), area: Math.round(area), anim: s.animationName.split(',')[0].trim() })
          }
        }
      }
      if (s.transitionProperty && s.transitionProperty !== 'none' && s.transitionProperty !== 'all') {
        transitionCount++
      }
      if (s.willChange && s.willChange !== 'auto') willChangeCount++
      if (s.backdropFilter && s.backdropFilter !== 'none') {
        backdropFilterCount++
        const cls = el.className ? el.className.split(' ').slice(0, 2).join('.') : el.tagName
        backdropElements.push(cls.slice(0, 50))
      }
      if (s.transformStyle === 'preserve-3d') {
        preserv3dCount++
        const cls = el.className ? el.className.split(' ')[0] : el.tagName
        preserv3dElements.push(cls.slice(0, 50))
      }
      if (s.boxShadow && s.boxShadow !== 'none') boxShadowCount++
      if (s.filter && s.filter !== 'none') filterCount++
    }

    // Check keyframes for expensive properties
    const paintTriggerKeyframes = []
    for (const sheet of document.styleSheets) {
      try {
        for (const rule of sheet.cssRules) {
          if (rule instanceof CSSKeyframesRule) {
            const frames = Array.from(rule.cssRules).map(r => r.cssText).join(' ')
            const triggers = []
            if (frames.includes('background-position')) triggers.push('background-position')
            if (frames.includes('background-size')) triggers.push('background-size')
            if (frames.includes('box-shadow')) triggers.push('box-shadow')
            if (frames.includes('clip-path')) triggers.push('clip-path')
            if (frames.includes('border')) triggers.push('border')
            if (triggers.length > 0) {
              paintTriggerKeyframes.push({ name: rule.name, triggers })
            }
          }
        }
      } catch (e) { /* cross-origin */ }
    }

    return {
      totalElements: all.length,
      animatedCount,
      animationNames,
      willChangeCount,
      backdropFilterCount,
      backdropElements,
      preserv3dCount,
      preserv3dElements,
      boxShadowCount,
      filterCount,
      transitionCount,
      heavyAnimations,
      paintTriggerKeyframes,
    }
  })

  const sortedAnims = Object.entries(domAnalysis.animationNames).sort((a, b) => b[1] - a[1])

  console.log(`\n   ┌─── DOM Animation Inventory ──────────────────────┐`)
  console.log(`   │ Total DOM elements:       ${domAnalysis.totalElements}`)
  console.log(`   │ Animated elements:        ${domAnalysis.animatedCount}`)
  console.log(`   │ Transition-ready:         ${domAnalysis.transitionCount}`)
  console.log(`   │ will-change set:          ${domAnalysis.willChangeCount}`)
  console.log(`   │ backdrop-filter:          ${domAnalysis.backdropFilterCount}`)
  console.log(`   │ preserve-3d:              ${domAnalysis.preserv3dCount}`)
  console.log(`   │ box-shadow elements:      ${domAnalysis.boxShadowCount}`)
  console.log(`   │ filter elements:          ${domAnalysis.filterCount}`)
  console.log(`   │`)
  console.log(`   │ ⚡ Active animations (${sortedAnims.length} types, ${domAnalysis.animatedCount} instances):`)
  for (const [name, count] of sortedAnims) {
    const paintTrigger = domAnalysis.paintTriggerKeyframes.find(k => k.name === name)
    let flag = ''
    if (paintTrigger) flag = ` ⚠️  PAINT TRIGGER [${paintTrigger.triggers.join(', ')}]`
    else if (['spin', 'glow', 'breathe', 'expand', 'float', 'band-spin'].some(k => name.includes(k))) flag = ' ✓ GPU-friendly (transform/opacity)'
    console.log(`   │   ${name.padEnd(30)} ${count}x${flag}`)
  }
  console.log(`   │`)
  if (domAnalysis.paintTriggerKeyframes.length > 0) {
    console.log(`   │ 🎨 Paint-triggering keyframes:`)
    for (const k of domAnalysis.paintTriggerKeyframes) {
      console.log(`   │   @keyframes ${k.name} → [${k.triggers.join(', ')}]`)
    }
    console.log(`   │`)
  }
  if (domAnalysis.heavyAnimations.length > 0) {
    console.log(`   │ 🏋️ Large animated elements (area > 50000px²):`)
    for (const h of domAnalysis.heavyAnimations.slice(0, 8)) {
      console.log(`   │   ${h.tag} (${h.area}px²) → ${h.anim}`)
    }
    console.log(`   │`)
  }
  if (domAnalysis.backdropElements.length > 0) {
    console.log(`   │ 🔮 backdrop-filter on:`)
    for (const el of domAnalysis.backdropElements) console.log(`   │   • ${el}`)
    console.log(`   │`)
  }
  if (domAnalysis.preserv3dElements.length > 0) {
    console.log(`   │ 📐 preserve-3d on:`)
    for (const el of domAnalysis.preserv3dElements) console.log(`   │   • ${el}`)
  }
  console.log(`   └────────────────────────────────────────────────────┘\n`)

  // ──────────────────────────────────────────────────────
  // 3) Frame budget impact estimation
  // ──────────────────────────────────────────────────────
  const budget = 16.67

  // Cost model (ms/frame estimates based on Chrome DevTools profiling heuristics)
  const costs = []
  let totalEstMs = 0

  // Only count starfield drift as paint-heavy if still using bg-position
  const driftIsPaint = domAnalysis.paintTriggerKeyframes.some(k => k.name.includes('drift'))
  const driftCount = (domAnalysis.animationNames['hermes-drift-1'] || 0) +
    (domAnalysis.animationNames['hermes-drift-2'] || 0) +
    (domAnalysis.animationNames['hermes-drift-3'] || 0)
  if (driftCount > 0 && driftIsPaint) {
    const cost = driftCount * 2.0
    costs.push({ name: 'Starfield parallax (bg-position paint)', cost, elements: driftCount, type: 'PAINT' })
    totalEstMs += cost
  } else if (driftCount > 0) {
    const cost = driftCount * 0.1 // GPU-composited transform: near-zero cost
    costs.push({ name: 'Starfield parallax (GPU transform)', cost, elements: driftCount, type: 'GPU' })
    totalEstMs += cost
  }

  // backdrop-filter — with will-change hint the cost is reduced (cached layer)
  if (domAnalysis.backdropFilterCount > 0) {
    const hasWillChange = domAnalysis.willChangeCount > 0
    const perElement = hasWillChange ? 0.8 : 1.5 // will-change allows caching
    const cost = domAnalysis.backdropFilterCount * perElement
    costs.push({ name: `backdrop-filter: blur()${hasWillChange ? ' (cached)' : ''}`, cost, elements: domAnalysis.backdropFilterCount, type: 'COMPOSITE' })
    totalEstMs += cost
  }

  // preserve-3d + rotation — with will-change, layer tree is pre-built
  if (domAnalysis.preserv3dCount > 0) {
    const hasWillChange = domAnalysis.willChangeCount > 5
    const perElement = hasWillChange ? 0.5 : 1.0
    const cost = domAnalysis.preserv3dCount * perElement
    costs.push({ name: `preserve-3d layers${hasWillChange ? ' (GPU promoted)' : ''}`, cost, elements: domAnalysis.preserv3dCount, type: 'COMPOSITE' })
    totalEstMs += cost
  }

  // Conduit/sweep — only count as paint if still using bg-position
  const conduitIsPaint = domAnalysis.paintTriggerKeyframes.some(k => k.name.includes('conduit'))
  const conduitAnims = (domAnalysis.animationNames['hermes-conduit-current'] || 0) +
    (domAnalysis.animationNames['hermes-holo-sweep'] || 0)
  if (conduitAnims > 0 && conduitIsPaint) {
    const cost = conduitAnims * 0.7
    costs.push({ name: 'Conduit/sweep (bg-position paint)', cost, elements: conduitAnims, type: 'PAINT' })
    totalEstMs += cost
  } else if (conduitAnims > 0) {
    const cost = conduitAnims * 0.1
    costs.push({ name: 'Conduit/sweep (GPU transform)', cost, elements: conduitAnims, type: 'GPU' })
    totalEstMs += cost
  }

  // box-shadow on animated elements (rough estimate)
  const shadowOnAnimated = Math.min(10, Math.round(domAnalysis.boxShadowCount * 0.3))
  if (shadowOnAnimated > 3) {
    const cost = shadowOnAnimated * 0.35
    costs.push({ name: 'Animated box-shadow repaint', cost, elements: shadowOnAnimated, type: 'PAINT' })
    totalEstMs += cost
  }

  // filter on elements
  if (domAnalysis.filterCount > 2) {
    const cost = domAnalysis.filterCount * 0.5
    costs.push({ name: 'CSS filter (brightness/saturate)', cost, elements: domAnalysis.filterCount, type: 'COMPOSITE' })
    totalEstMs += cost
  }

  costs.sort((a, b) => b.cost - a.cost)
  const usedPct = (totalEstMs / budget * 100).toFixed(0)

  console.log(`   ┌─── Frame Budget Analysis ────────────────────────────┐`)
  console.log(`   │ Target: 60fps → 16.67ms per frame`)
  console.log(`   │ Estimated animation render cost: ${totalEstMs.toFixed(1)}ms/frame (${usedPct}%)`)
  console.log(`   │`)
  console.log(`   │ Cost breakdown:`)
  for (const c of costs) {
    const barLen = Math.max(1, Math.round(c.cost / budget * 30))
    const bar = '█'.repeat(barLen)
    const pct = (c.cost / budget * 100).toFixed(0)
    console.log(`   │  ${bar.padEnd(30)} ${c.cost.toFixed(1)}ms ${pct.padStart(3)}% │ ${c.type} ${c.name} ×${c.elements}`)
  }
  console.log(`   │`)

  const remain = budget - totalEstMs
  if (totalEstMs > budget) {
    console.log(`   │  ⛔ OVER BUDGET by ${(-remain).toFixed(1)}ms — cannot achieve 60fps`)
  } else if (remain < 5) {
    console.log(`   │  ⚠️  Only ${remain.toFixed(1)}ms left for JS/layout/GC — very tight`)
  } else {
    console.log(`   │  ℹ️  ${remain.toFixed(1)}ms remaining for JS/layout/GC`)
  }
  console.log(`   │`)
  console.log(`   │ Mid-range laptop estimate (4x slower GPU/CPU):`)
  const throttledCost = totalEstMs * 3
  const throttledBudget = 33.34 // target 30fps on slow machine
  const throttledPct = (throttledCost / throttledBudget * 100).toFixed(0)
  console.log(`   │   Animation cost: ~${throttledCost.toFixed(1)}ms (at 30fps target = ${throttledBudget}ms budget)`)
  console.log(`   │   Budget usage: ${throttledPct}%`)
  if (throttledCost > throttledBudget) {
    console.log(`   │   ⛔ WILL JANK: cannot even hit 30fps on mid-range hardware`)
  } else if (throttledCost > throttledBudget * 0.7) {
    console.log(`   │   ⚠️  Borderline: may stutter on mid-range hardware`)
  }
  console.log(`   └────────────────────────────────────────────────────────┘\n`)

  // ──────────────────────────────────────────────────────
  // RECOMMENDATIONS
  // ──────────────────────────────────────────────────────
  console.log('═══════════════════════════════════════════════════════════')
  console.log('  OPTIMIZATION RECOMMENDATIONS')
  console.log('═══════════════════════════════════════════════════════════\n')

  let n = 0

  if (driftCount > 0) {
    n++
    console.log(`  ${n}. 🔴 [CRITICAL] Starfield: background-position → transform`)
    console.log(`     Files: src/hermes/CurrencyGalaxy.tsx (lines 154-156)`)
    console.log(`            src/hermes/hermes.css (@keyframes hermes-drift-1/2/3)`)
    console.log(``)
    console.log(`     Current: 3 divs with inset:-100px animate background-position`)
    console.log(`     Problem: background-position is NOT compositor-accelerated.`)
    console.log(`              Every frame triggers a full repaint of the layer.`)
    console.log(`              Each layer is ~1640×1100px = 1.8M pixels to repaint.`)
    console.log(`     Saving:  ~${(driftCount * 2.0).toFixed(1)}ms/frame`)
    console.log(``)
    console.log(`     Fix: Animate transform: translate() instead:`)
    console.log(`       @keyframes hermes-drift-1 {`)
    console.log(`         from { transform: translate(0, 0); }`)
    console.log(`         to   { transform: translate(-260px, 140px); }`)
    console.log(`       }`)
    console.log(`       /* Add to the element: */ will-change: transform;`)
    console.log(``)
  }

  if (domAnalysis.backdropFilterCount > 0) {
    n++
    console.log(`  ${n}. 🔴 [HIGH] Remove backdrop-filter: blur()`)
    console.log(`     Elements: ${domAnalysis.backdropElements.join(', ')}`)
    console.log(`     Saving: ~${(domAnalysis.backdropFilterCount * 1.5).toFixed(1)}ms/frame`)
    console.log(``)
    console.log(`     Fix: Replace backdrop-filter: blur(Npx) with opaque background:`)
    console.log(`       /* Before */ backdrop-filter: blur(8px); background: rgba(5,12,20,.76);`)
    console.log(`       /* After  */ background: rgba(5,12,20,.94); /* slightly more opaque */`)
    console.log(``)
  }

  if (domAnalysis.preserv3dCount > 0) {
    n++
    console.log(`  ${n}. 🟡 [MEDIUM] Flatten 3D orbit transforms`)
    console.log(`     Elements: ${domAnalysis.preserv3dElements.join(', ')}`)
    console.log(`     Saving: ~${(domAnalysis.preserv3dCount * 1.0).toFixed(1)}ms/frame`)
    console.log(``)
    console.log(`     Fix: The visual "tilt" effect can use 2D perspective():`)
    console.log(`       /* Before */ transform-style: preserve-3d; transform: rotateX(64deg);`)
    console.log(`       /* After  */ transform: perspective(1100px) rotateX(64deg);`)
    console.log(`       /* Remove preserve-3d, keep same visual tilt */`)
    console.log(``)
  }

  if (conduitAnims > 0) {
    n++
    console.log(`  ${n}. 🟡 [MEDIUM] Energy conduit animation → transform`)
    console.log(`     File: src/hermes/hermes.css (.hermes-energy-conduit::after)`)
    console.log(`     Saving: ~${(conduitAnims * 0.7).toFixed(1)}ms/frame`)
    console.log(``)
    console.log(`     Fix: Replace background-position animation with translateX():`)
    console.log(`       /* Use a child span instead of ::after */`)
    console.log(`       animation: hermes-conduit-current 2.8s linear infinite;`)
    console.log(`       @keyframes hermes-conduit-current {`)
    console.log(`         from { transform: translateX(-220px); }`)
    console.log(`         to   { transform: translateX(220px); }`)
    console.log(`       }`)
    console.log(``)
  }

  n++
  console.log(`  ${n}. 🔴 [HIGH] Add adaptive quality system`)
  console.log(`     File: new — src/hermes/useAdaptiveQuality.ts`)
  console.log(``)
  console.log(`     Concept: Auto-detect device capability at startup:`)
  console.log(`       const [quality, setQuality] = useState<'high'|'medium'|'low'>('high')`)
  console.log(`       useEffect(() => {`)
  console.log(`         let frames = 0, start = performance.now()`)
  console.log(`         const id = requestAnimationFrame(function check(now) {`)
  console.log(`           frames++`)
  console.log(`           if (now - start > 2000) {`)
  console.log(`             const fps = frames / ((now - start) / 1000)`)
  console.log(`             if (fps < 30) setQuality('low')`)
  console.log(`             else if (fps < 45) setQuality('medium')`)
  console.log(`           } else requestAnimationFrame(check)`)
  console.log(`         })`)
  console.log(`       }, [])`)
  console.log(``)
  console.log(`     Then conditionally render:`)
  console.log(`       quality === 'high':   all animations`)
  console.log(`       quality === 'medium': no starfield, no orbit rotation, keep glows`)
  console.log(`       quality === 'low':    static mode (= reduced-motion)`)
  console.log(``)

  n++
  console.log(`  ${n}. 🟢 [LOW] will-change hints for GPU promotion`)
  console.log(`     Add to orbit containers + energy packets:`)
  console.log(`       .hermes-galaxy { will-change: transform; }`)
  console.log(`       .hermes-energy-packet { will-change: transform, opacity; }`)
  console.log(`       .hermes-core-energy-ring { will-change: transform, opacity; }`)
  console.log(``)

  // Summary
  console.log('═══════════════════════════════════════════════════════════')
  console.log('  ESTIMATED IMPACT')
  console.log('═══════════════════════════════════════════════════════════\n')

  const totalSaving = (driftCount * 2.0) + (domAnalysis.backdropFilterCount * 1.5) +
    (conduitAnims * 0.7) + (domAnalysis.preserv3dCount * 0.5)
  const afterFix = Math.max(0, totalEstMs - totalSaving)

  console.log(`   Current estimated cost:   ${totalEstMs.toFixed(1)}ms/frame (${usedPct}% of budget)`)
  console.log(`   After optimizations:      ~${afterFix.toFixed(1)}ms/frame (${(afterFix / budget * 100).toFixed(0)}% of budget)`)
  console.log(`   Total saving:             ~${totalSaving.toFixed(1)}ms/frame`)
  console.log(``)
  console.log(`   Mid-range device:`)
  console.log(`     Current: ~${throttledCost.toFixed(0)}ms/frame → ${throttledCost > throttledBudget ? '❌ janky' : '✅ ok'}`)
  console.log(`     After:   ~${(afterFix * 3).toFixed(0)}ms/frame → ${afterFix * 3 > throttledBudget ? '⚠️  borderline' : '✅ smooth'}`)
  console.log(``)

  await browser.close()
  console.log('✅ Benchmark complete.\n')
}

main().catch((err) => {
  console.error('Benchmark failed:', err)
  process.exit(1)
})
