import { afterEach, describe, expect, it, vi } from 'vitest'
import McuUpdateManagerPanel from '@/components/panels/Machine/McuUpdateManagerPanel.vue'

vi.mock('@/components/mixins/base', async () => {
    const { default: Vue } = await import('vue')
    return { default: Vue.extend({ computed: { apiUrl: () => '' } }) }
})
vi.mock('@/components/ui/Panel.vue', () => ({ default: {} }))

function panel() {
    const vm = new McuUpdateManagerPanel()
    vi.spyOn(vm, 'startScanProgress').mockImplementation(() => {})
    vi.spyOn(vm, 'stopScanProgress').mockImplementation(() => {})
    vi.spyOn(vm, 'startProgressRefresh').mockImplementation(() => {})
    vi.spyOn(vm, 'stopProgressRefresh').mockImplementation(() => {})
    return vm
}

function response(payload: unknown, status = 200) {
    return new Response(JSON.stringify(payload), { status })
}

const discovery = { devices: [{ id: 'ebb', transport: 'can' }] }

afterEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
})

describe('MCU device scan errors', () => {
    it('keeps discovered devices and clears stale errors when the optional operation endpoint is absent', async () => {
        const fetch = vi
            .fn()
            .mockResolvedValueOnce(response({ result: discovery }))
            .mockResolvedValueOnce(response({ error: { message: 'Not Found' } }, 404))
        vi.stubGlobal('fetch', fetch)
        const vm = panel()
        vm.errorMessage = 'Old error'
        await vm.refresh()
        expect(vm.devices).toEqual(discovery.devices)
        expect(vm.errorMessage).toBe('')
        expect(vm.loading).toBe(false)
        expect(vm.loaded).toBe(true)
        expect(fetch.mock.calls.map(([url]) => url)).toEqual([
            '/machine/mcu_update_manager/status',
            '/machine/mcu_update_manager/operation',
        ])
    })

    it('clears stale errors with a supported idle operation endpoint', async () => {
        vi.stubGlobal(
            'fetch',
            vi
                .fn()
                .mockResolvedValueOnce(response({ result: discovery }))
                .mockResolvedValueOnce(response({ result: { job: null } }))
        )
        const vm = panel()
        vm.errorMessage = 'Not Found'
        await vm.refresh()
        expect(vm.errorMessage).toBe('')
    })

    it('refreshes repository versions only when the user requests a check', async () => {
        const fetch = vi.fn()
            .mockResolvedValueOnce(response({ result: discovery }))
            .mockResolvedValueOnce(response({ result: { job: null } }))
        vi.stubGlobal('fetch', fetch)
        await panel().refresh(true)
        expect(fetch.mock.calls[0][0]).toBe('/machine/mcu_update_manager/status?refresh_repositories=true')
    })

    it('does not hide a missing discovery endpoint', async () => {
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response({ error: { message: 'Not Found' } }, 404)))
        const vm = panel()
        await vm.refresh()
        expect(vm.errorMessage).toBe('Not Found')
        expect(vm.status).toBeNull()
    })

    it.each([401, 500])('does not hide operation HTTP %i errors', async (status) => {
        vi.stubGlobal(
            'fetch',
            vi
                .fn()
                .mockResolvedValueOnce(response({ result: discovery }))
                .mockResolvedValueOnce(response({ error: { message: 'Operation unavailable' } }, status))
        )
        const vm = panel()
        await vm.refresh()
        expect(vm.errorMessage).toBe('Operation unavailable')
    })

    it('does not hide a missing operation endpoint during a flash', async () => {
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response({ error: { message: 'Not Found' } }, 404)))
        const vm = panel()
        vm.busy = true
        vm.busyAction = 'ebb:flash'
        await vm.refreshOperation()
        expect(vm.errorMessage).toBe('Not Found')
        expect(vm.busy).toBe(true)
    })

    it.each(['complete', 'failed'])('finishes polling a %s job without waiting on itself', async (status) => {
        const fetch = vi
            .fn()
            .mockResolvedValueOnce(
                response({
                    result: {
                        job: {
                            status,
                            completed_at: '2026-09-28T12:00:00Z',
                            error: 'Flash failed',
                        },
                    },
                })
            )
            .mockResolvedValueOnce(response({ result: discovery }))
        vi.stubGlobal('fetch', fetch)
        const vm = panel()
        vm.busy = true
        await vm.refreshOperation()
        expect(vm.busy).toBe(false)
        expect(vm.operationRefreshPromise).toBeNull()
        expect(vm.refreshPromise).toBeNull()
        expect(vm.errorMessage).toBe(status === 'failed' ? 'Flash failed' : '')
        expect(fetch).toHaveBeenCalledTimes(2)
    })
})
