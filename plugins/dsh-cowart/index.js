import { registerCowartTools } from './lib/tools.mjs'
import { registerCowartRoutes } from './lib/routes.mjs'
import { registerCowartSkill } from './lib/skill.mjs'
import { ensureCanvasBuild } from './lib/build.mjs'

export const name = 'dsh-cowart'
export const inject = ['tools', 'webServer', 'skills']

export function apply(ctx, rawConfig = {}) {
  const config = {
    buildOnBoot: rawConfig.buildOnBoot ?? 'auto',
  }

  registerCowartTools(ctx)
  registerCowartRoutes(ctx)
  registerCowartSkill(ctx)

  // Warm the canvas build in the background so the first /cowart request is fast.
  const warm = () => ensureCanvasBuild(ctx.logger, config.buildOnBoot).catch((error) => {
    ctx.logger.warn(`[cowart] canvas build failed: ${error instanceof Error ? error.message : String(error)}`)
  })
  ctx.effect(() => {
    const timer = setTimeout(warm, 1000)
    return () => clearTimeout(timer)
  }, 'cowart: warm canvas build')
}
