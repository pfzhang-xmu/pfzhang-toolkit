import { registerGitHubRoutes } from './lib/routes.mjs'

export const name = 'dsh-github-workspace'
export const inject = ['webServer']

export function apply(ctx) {
  registerGitHubRoutes(ctx)
}
