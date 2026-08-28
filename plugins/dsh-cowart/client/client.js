/**
 * dsh-cowart web client half.
 *
 * Surfaces:
 * - Persistent canvas window in the `shell.overlay` seat (root scope): a
 *   draggable/resizable tldraw canvas that does NOT scroll away with the
 *   conversation. Supports drag (with edge snap), resize (bottom-right grip),
 *   and PIN: docked flush against the right edge at full height, i.e. a
 *   permanent right sidebar. Position/size/open/pinned state + last
 *   projectDir persist in localStorage.
 * - A "画布" toggle button in the composer tool row (`conversation.input.right`).
 * - A slim status card for the `cowart_open_canvas` tool call
 *   (`tool.call.toolview`); calling the tool opens/focuses the window.
 *
 * Bridge: the iframe posts { source:'dsh-cowart', type:'agent-request', payload }
 * to the parent; this module forwards a `[cowart-request:<type>]` user message
 * to the CURRENT session's conversation through the sessions service.
 *
 * Self-contained on purpose: no build step, React resolved at load time
 * through the loader's require (registered statics), exactly like genui.
 */
window.__ModuleLoader__.load({
  id: 'dsh-cowart',
  factory: (require) => {
    const React = require('react')
    const { useCallback, useEffect, useMemo, useRef, useState } = React

    const CANVAS_TOOL = 'cowart_open_canvas'
    const BRIDGE_SOURCE = 'dsh-cowart'
    const STORAGE_KEY = 'dsh.cowart.panel'
    const SNAP_DISTANCE = 56

    // ---- persistent panel state -------------------------------------------

    const PANEL_DEFAULTS = { open: false, pinned: false, x: null, y: 72, w: 560, h: 620, projectDir: null }

    function loadPanelState() {
      try {
        const raw = window.localStorage.getItem(STORAGE_KEY)
        if (raw) {
          const parsed = JSON.parse(raw)
          return {
            open: parsed.open !== false,
            pinned: parsed.pinned === true,
            x: typeof parsed.x === 'number' ? parsed.x : PANEL_DEFAULTS.x,
            y: typeof parsed.y === 'number' ? parsed.y : PANEL_DEFAULTS.y,
            w: typeof parsed.w === 'number' ? parsed.w : PANEL_DEFAULTS.w,
            h: typeof parsed.h === 'number' ? parsed.h : PANEL_DEFAULTS.h,
            projectDir: typeof parsed.projectDir === 'string' ? parsed.projectDir : PANEL_DEFAULTS.projectDir,
          }
        }
      } catch {
        // fall through to defaults
      }
      return { ...PANEL_DEFAULTS }
    }

    let panelState = loadPanelState()
    const panelListeners = new Set()

    function setPanelState(next) {
      panelState = { ...panelState, ...next }
      try {
        window.localStorage.setItem(STORAGE_KEY, JSON.stringify(panelState))
      } catch {
        // persistence is best-effort
      }
      for (const listener of panelListeners) {
        try {
          listener(panelState)
        } catch {
          // ignore listener errors
        }
      }
    }

    function subscribePanel(listener) {
      panelListeners.add(listener)
      return () => panelListeners.delete(listener)
    }

    function openCanvasPanel(projectDir) {
      const bounds = {
        ...panelState,
        open: true,
        ...(typeof projectDir === 'string' && projectDir.trim() !== '' ? { projectDir } : {}),
      }
      if (bounds.x === null) {
        bounds.x = Math.max(16, window.innerWidth - bounds.w - 24)
        bounds.y = Math.max(16, Math.round((window.innerHeight - bounds.h) / 2))
      }
      setPanelState(bounds)
    }

    // ---- session + conversation helpers -----------------------------------

    let runtime = null
    let scopeResolver = null

    function currentSessionId() {
      try {
        return runtime?.sessions?.list?.getSnapshot()?.current ?? null
      } catch {
        return null
      }
    }

    function conversationOf(sessionId) {
      if (scopeResolver === null || sessionId === undefined || sessionId === null) return undefined
      try {
        return scopeResolver(sessionId)
      } catch {
        return undefined
      }
    }

    function dataUrlImageBlocks(content) {
      if (!Array.isArray(content)) return []
      return content.filter(
        (block) =>
          block !== null &&
          typeof block === 'object' &&
          block.type === 'image' &&
          typeof block.data === 'string' &&
          block.data !== '',
      )
    }

    function imageBlockToFile(block, index) {
      const mimeType = typeof block.mimeType === 'string' && block.mimeType !== '' ? block.mimeType : 'image/png'
      const ext = mimeType === 'image/jpeg' || mimeType === 'image/jpg' ? 'jpg' : mimeType === 'image/webp' ? 'webp' : 'png'
      const binary = atob(block.data)
      const bytes = new Uint8Array(binary.length)
      for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i)
      return new File([bytes], `cowart-reference-${index + 1}.${ext}`, { type: mimeType })
    }

    function sessionFaceOf(sessionId) {
      try {
        const scope = runtime?.sessions?.scope?.(sessionId)
        return scope === undefined || scope === null ? undefined : runtime.sessions.sessionOf(scope)
      } catch {
        return undefined
      }
    }

    function sendAgentRequest(sessionId, payload) {
      const conversation = conversationOf(sessionId)
      if (conversation === undefined) return false
      const message = payload?.message
      const analyticsContext = payload?.analyticsContext
      const prompt = typeof message?.prompt === 'string' ? message.prompt : null
      if (prompt === null || prompt.trim() === '') return false
      const promptType = typeof analyticsContext?.promptType === 'string' ? analyticsContext.promptType : 'other'
      const text = `[cowart-request:${promptType}] ${prompt.trim()}`
      const imageBlocks = dataUrlImageBlocks(message?.content)

      // The canvas only attaches image content blocks when it believes the
      // host accepts them; forward them through the conversation's
      // draft-attachment path when available. Any failure (e.g. a text-only
      // host that rejects image admission) falls back to the plain text send —
      // the canvas always embeds the reference image local paths in the prompt
      // text, so a reference is never lost on the fallback.
      if (imageBlocks.length > 0) {
        try {
          if (
            typeof conversation.createDraftImages === 'function' &&
            typeof conversation.sendSession === 'function'
          ) {
            const session = sessionFaceOf(sessionId)
            if (session !== undefined) {
              const attachments = conversation.createDraftImages(imageBlocks.map(imageBlockToFile))
              conversation
                .sendSession(
                  session,
                  text,
                  attachments.map((attachment) => attachment.id),
                  'queue',
                )
                .catch((error) => {
                  console.warn('[dsh-cowart] sendSession with image attachments failed, falling back to text:', error)
                  Promise.resolve(conversation.send(text)).catch((sendError) => {
                    console.warn('[dsh-cowart] conversation.send failed:', sendError)
                  })
                })
              return true
            }
          }
        } catch (error) {
          console.warn('[dsh-cowart] image attachment forwarding failed, falling back to text:', error)
        }
      }

      Promise.resolve(conversation.send(text)).catch((error) => {
        console.warn('[dsh-cowart] conversation.send failed:', error)
      })
      return true
    }

    // ---- shared message bridge (window-level, once) ------------------------

    function installMessageBridge() {
      const onMessage = (event) => {
        const data = event.data
        if (!data || typeof data !== 'object' || data.source !== BRIDGE_SOURCE) return
        if (data.type !== 'agent-request') return
        const sessionId = currentSessionId()
        if (sessionId === null) return
        sendAgentRequest(sessionId, data.payload)
      }
      window.addEventListener('message', onMessage)
      return () => window.removeEventListener('message', onMessage)
    }

    // ---- components --------------------------------------------------------

    function CanvasWindow() {
      const [panel, setLocalPanel] = useState(panelState)
      const iframeRef = useRef(null)
      const moveRef = useRef(null)

      useEffect(() => subscribePanel((next) => setLocalPanel(next)), [])

      const src = useMemo(
        () => (panel.projectDir ? `/cowart/?projectDir=${encodeURIComponent(panel.projectDir)}` : null),
        [panel.projectDir],
      )

      // Message bridge from the iframe (agent requests).
      useEffect(() => {
        const sessionId = currentSessionId()
        if (src === null || sessionId === null) return
        const onMessage = (event) => {
          const data = event.data
          if (!data || typeof data !== 'object' || data.source !== BRIDGE_SOURCE) return
          if (event.source !== iframeRef.current?.contentWindow) return
          sendAgentRequest(sessionId, data.payload)
        }
        window.addEventListener('message', onMessage)
        return () => window.removeEventListener('message', onMessage)
      }, [src])

      // Re-apply the pinned layout when the viewport resizes.
      useEffect(() => {
        if (!panel.pinned) return
        const onResize = () => {
          const next = pinnedLayout(panel.w)
          setLocalPanel((current) => ({ ...current, x: next.x, y: next.y, h: next.h }))
        }
        window.addEventListener('resize', onResize)
        return () => window.removeEventListener('resize', onResize)
      }, [panel.pinned, panel.w])

      /** Compute the pinned (right-docked) layout for a given width. */
      const pinnedLayout = (width) => ({
        x: Math.max(0, window.innerWidth - width),
        y: 0,
        h: window.innerHeight,
      })

      const startDrag = useCallback((event) => {
        if (event.button !== 0) return
        // Dragging a pinned window undocks it (free float from the pinned spot).
        const wasPinned = panel.pinned
        const originX = wasPinned ? Math.max(0, window.innerWidth - panel.w) : (panel.x ?? 0)
        const originY = wasPinned ? 0 : (panel.y ?? 0)
        const startX = event.clientX
        const startY = event.clientY
        if (wasPinned) setLocalPanel((current) => ({ ...current, pinned: false }))
        const onMove = (moveEvent) => {
          const nextX = Math.max(0, Math.min(window.innerWidth - 120, originX + moveEvent.clientX - startX))
          const nextY = Math.max(0, Math.min(window.innerHeight - 48, originY + moveEvent.clientY - startY))
          moveRef.current = { x: nextX, y: nextY }
          setLocalPanel((current) => ({ ...current, x: nextX, y: nextY }))
        }
        const onUp = () => {
          window.removeEventListener('pointermove', onMove)
          window.removeEventListener('pointerup', onUp)
          window.removeEventListener('pointercancel', onUp)
          const target = moveRef.current
          moveRef.current = null
          if (!target) return
          const viewportW = window.innerWidth
          const viewportH = window.innerHeight
          // Snap to right edge -> pin (dock as a right sidebar).
          if (target.x >= viewportW - SNAP_DISTANCE) {
            const layout = pinnedLayout(panel.w)
            setPanelState({ pinned: true, x: layout.x, y: layout.y, h: layout.h })
            return
          }
          // Snap flush to other edges (free float, not pinned).
          const snappedX = target.x <= SNAP_DISTANCE ? 0 : target.x
          const snappedY = target.y <= SNAP_DISTANCE ? 0 : target.y >= viewportH - panel.h - SNAP_DISTANCE ? viewportH - panel.h : target.y
          setPanelState({ pinned: false, x: snappedX, y: snappedY })
        }
        window.addEventListener('pointermove', onMove)
        window.addEventListener('pointerup', onUp)
        window.addEventListener('pointercancel', onUp)
      }, [panel.pinned, panel.x, panel.y, panel.w, panel.h])

      const startResize = useCallback((event) => {
        if (event.button !== 0) return
        const startX = event.clientX
        const startY = event.clientY
        const originW = panel.pinned ? panel.w : panel.w
        const originH = panel.pinned ? window.innerHeight : panel.h
        let lastW = originW
        let lastH = originH
        const onMove = (moveEvent) => {
          const nextW = Math.max(320, Math.min(window.innerWidth - 24, originW + moveEvent.clientX - startX))
          const nextH = Math.max(280, Math.min(window.innerHeight - 24, originH + moveEvent.clientY - startY))
          lastW = nextW
          lastH = nextH
          setLocalPanel((current) => ({
            ...current,
            w: nextW,
            ...(current.pinned
              ? { h: window.innerHeight, x: Math.max(0, window.innerWidth - nextW) }
              : { h: nextH }),
          }))
        }
        const onUp = () => {
          window.removeEventListener('pointermove', onMove)
          window.removeEventListener('pointerup', onUp)
          window.removeEventListener('pointercancel', onUp)
          setPanelState({ w: lastW, ...(panel.pinned ? {} : { h: lastH }) })
        }
        window.addEventListener('pointermove', onMove)
        window.addEventListener('pointerup', onUp)
        window.addEventListener('pointercancel', onUp)
      }, [panel.pinned, panel.w, panel.h])

      // Collapsed pill when closed.
      if (!panel.open) {
        return React.createElement(
          'button',
          {
            style: {
              position: 'fixed',
              right: '20px',
              bottom: '20px',
              zIndex: 2147483000,
              pointerEvents: 'auto',
              cursor: 'pointer',
              border: '1px solid var(--dsw-alias-border-l1, rgba(255,255,255,0.18))',
              borderRadius: '999px',
              padding: '8px 16px',
              fontSize: '13px',
              fontWeight: 600,
              color: 'var(--dsw-alias-text-accent, #4c9aff)',
              background: 'var(--dsw-alias-bg-layer-2, rgba(40,40,44,0.92))',
              boxShadow: 'var(--dsw-shadow-lv3, 0 8px 28px rgba(0,0,0,0.35))',
              fontFamily: 'inherit',
            },
            onClick: () => {
              if (panel.projectDir) {
                openCanvasPanel(panel.projectDir)
              } else {
                const sessionIdNow = currentSessionId()
                if (sessionIdNow !== null && conversationOf(sessionIdNow) !== undefined) {
                  openCanvasPanel(null)
                  conversationOf(sessionIdNow).send('[cowart] 打开 Cowart 画布').catch(() => {})
                } else {
                  openCanvasPanel(null)
                }
              }
            },
            title: '打开 Cowart 画布',
          },
          '🖼 画布',
        )
      }

      // Pinned layout overrides stored x/y/h.
      const layout = panel.pinned
        ? pinnedLayout(panel.w)
        : { x: panel.x ?? 16, y: panel.y ?? 72, h: panel.h }

      const style = {
        position: 'fixed',
        top: `${layout.y}px`,
        left: `${layout.x}px`,
        width: `${panel.w}px`,
        height: `${layout.h}px`,
        zIndex: 2147482990,
        pointerEvents: 'auto',
        display: 'flex',
        flexDirection: 'column',
        background: 'var(--dsw-alias-bg-layer-2, #1f1f23)',
        border: '1px solid var(--dsw-alias-border-l1, rgba(255,255,255,0.16))',
        borderLeft: panel.pinned ? '1px solid var(--dsw-alias-border-l1, rgba(255,255,255,0.16))' : undefined,
        borderRadius: panel.pinned ? '0' : '12px',
        boxShadow: panel.pinned ? 'none' : 'var(--dsw-shadow-lv3, 0 16px 48px rgba(0,0,0,0.45))',
        overflow: 'hidden',
        fontFamily: 'inherit',
      }

      return React.createElement(
        'div',
        { style },
        [
          React.createElement(
            'div',
            {
              key: 'bar',
              onPointerDown: startDrag,
              style: {
                display: 'flex',
                alignItems: 'center',
                gap: '6px',
                padding: '6px 10px',
                cursor: 'grab',
                userSelect: 'none',
                borderBottom: '1px solid var(--dsw-alias-border-l1, rgba(255,255,255,0.1))',
                flex: 'none',
              },
            },
            [
              React.createElement('span', { key: 'dot', style: { fontSize: '14px' } }, '🖼'),
              React.createElement(
                'span',
                {
                  key: 'title',
                  style: {
                    fontSize: '12px',
                    fontWeight: 600,
                    color: 'var(--dsw-alias-label-primary, #eee)',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
                    flex: 1,
                  },
                },
                panel.pinned ? 'Cowart 画布（已固定）' : 'Cowart 画布',
              ),
              panel.projectDir
                ? React.createElement(
                    'span',
                    {
                      key: 'dir',
                      style: { fontSize: '11px', color: '#9ca3af', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: '34%' },
                      title: panel.projectDir,
                    },
                    panel.projectDir,
                  )
                : null,
              React.createElement(
                'button',
                {
                  key: 'pin',
                  onClick: () => {
                    if (panel.pinned) {
                      const freeX = Math.max(16, window.innerWidth - panel.w - 24)
                      setPanelState({ pinned: false, x: freeX, y: 72 })
                    } else {
                      const layoutNow = pinnedLayout(panel.w)
                      setPanelState({ pinned: true, x: layoutNow.x, y: 0, h: window.innerHeight })
                    }
                  },
                  style: {
                    border: 'none',
                    background: panel.pinned ? 'var(--dsw-alias-state-business-primary-soft, rgba(76,154,255,0.18))' : 'transparent',
                    cursor: 'pointer',
                    color: panel.pinned ? 'var(--dsw-alias-state-business-primary, #4c9aff)' : '#9ca3af',
                    fontSize: '13px',
                    padding: '2px 6px',
                    borderRadius: '6px',
                    flex: 'none',
                  },
                  title: panel.pinned ? '取消固定（恢复自由悬浮）' : '固定到右侧（像侧边栏）',
                },
                panel.pinned ? '📌' : '📌',
              ),
              React.createElement(
                'button',
                {
                  key: 'close',
                  onClick: () => setPanelState({ open: false }),
                  style: {
                    border: 'none', background: 'transparent', cursor: 'pointer',
                    color: '#9ca3af', fontSize: '14px', padding: '2px 6px', borderRadius: '6px',
                    flex: 'none',
                  },
                  title: '收起（右下角小按钮可重新打开）',
                },
                '✕',
              ),
            ],
          ),
          src === null
            ? React.createElement(
                'div',
                {
                  key: 'empty',
                  style: {
                    flex: 1,
                    display: 'flex',
                    flexDirection: 'column',
                    alignItems: 'center',
                    justifyContent: 'center',
                    gap: '12px',
                    color: '#9ca3af',
                    fontSize: '13px',
                  },
                },
                [
                  React.createElement('span', { key: 't' }, '还没有打开画布。'),
                  React.createElement(
                    'button',
                    {
                      key: 'b',
                      onClick: () => {
                        const sessionIdNow = currentSessionId()
                        if (sessionIdNow !== null && conversationOf(sessionIdNow) !== undefined) {
                          conversationOf(sessionIdNow).send('[cowart] 打开 Cowart 画布').catch(() => {})
                        }
                      },
                      style: {
                        border: '1px solid var(--dsw-alias-state-business-primary, #4c9aff)',
                        background: 'transparent',
                        color: 'var(--dsw-alias-state-business-primary, #4c9aff)',
                        borderRadius: '8px',
                        padding: '6px 16px',
                        cursor: 'pointer',
                        fontSize: '13px',
                      },
                    },
                    '让 agent 打开画布',
                  ),
                ],
              )
            : React.createElement('iframe', {
                key: src,
                ref: iframeRef,
                src,
                title: 'Cowart Canvas',
                style: {
                  flex: 1,
                  width: '100%',
                  border: 'none',
                  background: '#f8f8f7',
                  display: 'block',
                },
                allow: 'clipboard-write; clipboard-read',
              }),
          // Resize grip — z-index above the iframe so it receives pointer events.
          React.createElement(
            'div',
            {
              key: 'resize',
              onPointerDown: startResize,
              title: '拖拽调整大小',
              style: {
                position: 'absolute',
                right: 0,
                bottom: 0,
                width: '26px',
                height: '26px',
                cursor: 'nwse-resize',
                touchAction: 'none',
                zIndex: 2147483100,
                display: 'flex',
                alignItems: 'flex-end',
                justifyContent: 'flex-end',
              },
            },
            React.createElement('span', {
              style: {
                width: '14px',
                height: '14px',
                borderRight: '2px solid rgba(255,255,255,0.45)',
                borderBottom: '2px solid rgba(255,255,255,0.45)',
                borderBottomRightRadius: '3px',
                pointerEvents: 'none',
              },
            }),
          ),
        ],
      )
    }

    function CanvasToolView(props) {
      const { block, cwd } = props
      const projectDir = useMemo(() => parseProjectDir(block, cwd), [block, cwd])

      useEffect(() => {
        if (projectDir !== null) openCanvasPanel(projectDir)
      }, [projectDir])

      return React.createElement(
        'div',
        {
          style: {
            display: 'flex',
            flexDirection: 'column',
            gap: '6px',
            width: '100%',
            boxSizing: 'border-box',
            fontSize: '13px',
          },
        },
        [
          React.createElement(
            'div',
            { key: 'line', style: { color: 'var(--dsw-alias-label-primary, #eee)' } },
            '🖼 Cowart 画布已固定到右侧悬浮窗（可拖拽/缩放/固定，不随对话滚动）。',
          ),
          projectDir !== null
            ? React.createElement(
                'div',
                { key: 'dir', style: { color: '#9ca3af', fontSize: '12px', fontFamily: 'var(--ds-font-family-code, ui-monospace, monospace)' } },
                `项目：${projectDir}`,
              )
            : null,
        ],
      )
    }

    function parseProjectDir(block, cwd) {
      try {
        const raw = block?.call?.argsRaw ?? block?.argsRaw
        if (typeof raw === 'string' && raw.trim() !== '') {
          const args = JSON.parse(raw)
          if (typeof args.projectDir === 'string' && args.projectDir.trim() !== '') {
            return args.projectDir.trim()
          }
        }
      } catch {
        // fall through
      }
      if (typeof cwd === 'string' && cwd.trim() !== '') return cwd
      return null
    }

    function CanvasToggleButton() {
      const [panel, setLocalPanel] = useState(panelState)
      useEffect(() => subscribePanel((next) => setLocalPanel(next)), [])
      return React.createElement(
        'button',
        {
          style: {
            border: panel.open
              ? '1px solid var(--dsw-alias-state-business-primary, #4c9aff)'
              : '1px solid var(--dsw-alias-border-l1, rgba(255,255,255,0.16))',
            background: panel.open ? 'var(--dsw-alias-state-business-primary-soft, rgba(76,154,255,0.14))' : 'transparent',
            color: panel.open ? 'var(--dsw-alias-state-business-primary, #4c9aff)' : 'var(--dsw-alias-label-secondary, #bbb)',
            borderRadius: '8px',
            padding: '4px 10px',
            cursor: 'pointer',
            fontSize: '12px',
            fontFamily: 'inherit',
          },
          title: '显示/隐藏 Cowart 画布',
          onClick: () => {
            if (panel.open) {
              setPanelState({ open: false })
            } else if (panel.projectDir) {
              openCanvasPanel(panel.projectDir)
            } else {
              openCanvasPanel(null)
              const sessionIdNow = currentSessionId()
              if (sessionIdNow !== null && conversationOf(sessionIdNow) !== undefined) {
                conversationOf(sessionIdNow).send('[cowart] 打开 Cowart 画布').catch(() => {})
              }
            }
          },
        },
        `🖼 画布${panel.open ? ' ✕' : ''}`,
      )
    }

    // ---- plugin body -------------------------------------------------------

    function apply(ctx) {
      runtime = ctx
      scopeResolver = (sessionId) => {
        const scope = ctx.sessions?.scope?.(sessionId)
        return scope?.get?.('conversation')
      }
      const disposers = [
        ctx.effect(() => installMessageBridge(), 'dsh-cowart: message bridge'),
        ctx.slots.inject('shell.overlay', () =>
          ctx.slots.register({ name: 'shell.overlay', id: 'dsh-cowart-canvas-window' }, () =>
            React.createElement(CanvasWindow, null),
          ),
        ),
        ctx.slots.inject('conversation.input.right', () =>
          ctx.slots.register({ name: 'conversation.input.right', id: 'dsh-cowart-toggle' }, () =>
            React.createElement(CanvasToggleButton, null),
          ),
        ),
        ctx.slots.inject('tool.call.toolview', () =>
          ctx.slots.register(
            { name: 'tool.call.toolview', key: CANVAS_TOOL },
            (props) => React.createElement(CanvasToolView, props),
          ),
        ),
      ]
      return disposers
    }

    return {
      name: 'dsh-cowart-client',
      inject: ['slots', 'sessions'],
      apply,
    }
  },
})
