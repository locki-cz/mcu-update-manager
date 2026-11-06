import { afterEach, describe, expect, it, vi } from 'vitest'
import McuUpdateManagerPanel from '@/components/panels/Machine/McuUpdateManagerPanel.vue'
import Vue from 'vue'

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

describe('confirmation dialog', () => {
    it('waits for confirmation and can be aborted', async () => {
        const vm = panel()
        const result = vm.confirmAction('Flash firmware?')
        expect(vm.confirmationDialog).toBe(true)
        expect(vm.confirmationUnderstood).toBe(false)
        vm.resolveConfirmation(false)
        expect(await result).toBe(false)
        expect(vm.confirmationDialog).toBe(false)
    })
})

describe('firmware progress dialog', () => {
    it('restores live build output and displays a failure after completion', async () => {
        vi.stubGlobal('fetch', vi.fn()
            .mockResolvedValueOnce(response({ result: {
                job: { job_id: 'one', action: 'legacy_update', device_id: 'ebb', status: 'running', phase: 'building', started_at: '2026-01-01T00:00:00Z' },
                operation: { action: 'build_firmware', started_at: '2026-01-01T00:00:01Z', log_tail: 'Compiling main.o' },
            } }))
            .mockResolvedValueOnce(response({ result: {
                job: { job_id: 'one', action: 'legacy_update', device_id: 'ebb', status: 'failed', error: 'Host mismatch', completed_at: '2026-01-01T00:00:02Z', started_at: '2026-01-01T00:00:00Z' },
                operation: { action: 'build_firmware', started_at: '2026-01-01T00:00:01Z', log_tail: 'Build complete' },
            } })))
        const vm = panel()
        vm.status = { devices: [{ id: 'ebb' }] }
        vi.spyOn(vm, 'refresh').mockResolvedValue(undefined)
        await vm.performOperationRefresh()
        expect(vm.operationDialog).toBe(true)
        expect(vm.operationDialogRunning).toBe(true)
        expect(vm.operationDialogText).toContain('Compiling main.o')
        await vm.performOperationRefresh()
        expect(vm.operationDialogRunning).toBe(false)
        expect(vm.operationDialogError).toBe('Host mismatch')
        expect(vm.operationDialogText).toContain('Build complete')
    })
})

describe('Aldis integration', () => {
    it('keeps the last MCU status while polling and blocks updates on connection failure', async () => {
        let rejectRequest: (reason: Error) => void = () => {}
        const emitAndWait = vi.fn().mockReturnValue(new Promise((_resolve, reject) => { rejectRequest = reject }))
        Object.defineProperty(Vue, '$socket', { value: { emitAndWait }, configurable: true })
        const vm = panel()
        vm.aldisMcus = [{ name: 'ebb', state: 'update_available', actions: ['update'] }]
        const request = vm.refreshAldisStatus()
        expect(vm.aldisDevice({ id: 'ebb' })?.state).toBe('update_available')
        rejectRequest(new Error('offline'))
        await request
        expect(vm.aldisDevice({ id: 'ebb' })?.state).toBe('update_available')
        expect(vm.aldisCanUpdate({ id: 'ebb' })).toBe(false)
        expect(vm.aldisMessage).toContain('offline')
    })
    it('offers a combined update for a confirmed eligible legacy device', async () => {
        const vm = panel()
        vm.aldisHostVersion = 'v2'
        vm.aldisMcus = [{ name: 'ebb', state: 'unsupported_legacy', actions: [] }]
        const device = { id: 'ebb', name: 'ebb', confirmed_profile: 'ebb-profile', confirmed_by_user: true,
            actions: { can_build: true, can_flash: true } }
        vm.status = { devices: [device], profile_catalog: [{ id: 'ebb-profile', name: 'EBB', settings: { klipper: { processor: 'STM32G0B1' } } }] }
        const run = vi.spyOn(vm, 'runRequest').mockResolvedValue(undefined)
        expect(vm.canUpdateLegacy(device)).toBe(true)
        await vm.updateLegacy(device)
        expect(vm.singleUpdateDialog).toBe(true)
        expect(vm.singleUpdateSettings).toContainEqual({ label: 'Processor', value: 'STM32G0B1' })
        await vm.confirmSingleUpdate()
        expect(run).not.toHaveBeenCalled()
        vm.singleUpdateConfirmed = true
        await vm.confirmSingleUpdate()
        expect(run).toHaveBeenCalledWith('update_legacy', device)
        expect(vm.canUpdateLegacy({ ...device, confirmed_by_user: false })).toBe(false)
    })

    it('rejects confirmation if the profile changes while the dialog is open', async () => {
        const vm = panel()
        const device = { id: 'ebb', name: 'ebb', confirmed_profile: 'board', confirmed_by_user: true,
            actions: { can_build: true, can_flash: true } }
        vm.status = { devices: [device], profile_catalog: [{ id: 'board', name: 'Board', settings: { klipper: { processor: 'STM32G0B1' } } }] }
        vm.aldisMcus = [{ name: 'ebb', state: 'unsupported_legacy', actions: [] }]
        const run = vi.spyOn(vm, 'runRequest').mockResolvedValue(undefined)
        await vm.updateLegacy(device)
        vm.singleUpdateConfirmed = true
        vm.status.profile_catalog[0].settings.klipper.processor = 'STM32H723'
        await vm.confirmSingleUpdate()
        expect(run).not.toHaveBeenCalled()
        expect(vm.errorMessage).toContain('Profile settings changed')
    })

    it('matches named MCU sections and only marks current devices up to date', () => {
        const vm = panel()
        vm.aldisMcus = [{ name: 'mcu ebb', state: 'current', actions: [] },
            { name: 'mcu old', state: 'unsupported_legacy', actions: [] }]
        expect(vm.basicVersionText({ id: 'ebb', name: 'ebb', firmware_version: 'v1' })).toBe('v1')
        expect(vm.basicVersionText({ id: 'old', name: 'old', firmware_version: 'v1' })).toBe('v1')
    })

    it('updates only eligible devices after listing them in the confirmation', async () => {
        const emitAndWait = vi.fn().mockResolvedValueOnce({ run_id: 'run' })
            .mockResolvedValueOnce({ agents: [{ name: 'aldis' }] })
            .mockResolvedValueOnce({ api_version: 1, mcus: [], run: null })
        Object.defineProperty(Vue, '$socket', { value: { emitAndWait }, configurable: true })
        vi.stubGlobal('window', {})
        const vm = panel()
        vm.status = { devices: [{ id: 'ebb', name: 'ebb', firmware_version: 'old' }, { id: 'cartographer', name: 'cartographer' }] }
        vm.aldisHostVersion = 'new'
        vm.aldisMcus = [{ name: 'ebb', state: 'update_available', actions: ['update'] },
            { name: 'cartographer', state: 'externally_managed', actions: [] }]
        expect(vm.basicVersionText(vm.devices[0])).toContain('old → new')
        vm.openUpdateAllDialog()
        expect(vm.pendingUpdateNames).toEqual(['ebb'])
        await vm.updateAllWithAldis()
        expect(emitAndWait).not.toHaveBeenCalled()
        vm.updateAllConfirmed = true
        await vm.updateAllWithAldis()
        expect(emitAndWait).toHaveBeenCalledWith('server.extensions.request', {
            agent: 'aldis', method: 'update', arguments: { mcus: ['ebb'] },
        })
    })

    it('uses agent status and sends updates only for an allowed MCU', async () => {
        const emitAndWait = vi.fn()
            .mockResolvedValueOnce({ agents: [{ name: 'aldis' }] })
            .mockResolvedValueOnce({ api_version: 1, blocker: null, run: null, mcus: [
                { name: 'ebb', state: 'update_available', actions: ['update'] },
            ] })
            .mockResolvedValueOnce({ run_id: 'r1' })
            .mockResolvedValueOnce({ agents: [{ name: 'aldis' }] })
            .mockResolvedValueOnce({ api_version: 1, blocker: null, run: { state: 'finished', messages: [
                { mcu: 'ebb', message: 'verified' },
            ] }, mcus: [{ name: 'ebb', state: 'current', actions: [] }] })
        vi.stubGlobal('window', { confirm: () => true })
        vi.stubGlobal('fetch', vi.fn())
        vi.stubGlobal('setTimeout', setTimeout)
        Object.defineProperty(Vue, '$socket', { value: { emitAndWait }, configurable: true })
        const vm = panel()
        vm.status = { devices: [{ id: 'ebb', name: 'ebb' }] }
        await vm.refreshAldisStatus()
        expect(vm.aldisCanUpdate({ id: 'ebb', name: 'ebb' })).toBe(true)
        await vm.updateWithAldis({ id: 'ebb', name: 'ebb' })
        expect(vm.singleUpdateDialog).toBe(true)
        vm.singleUpdateConfirmed = true
        await vm.confirmSingleUpdate()
        expect(emitAndWait).toHaveBeenCalledWith('server.extensions.request', {
            agent: 'aldis', method: 'update', arguments: { mcus: ['ebb'] },
        })
        expect(vm.aldisRunMessages).toEqual(['ebb: verified'])
        expect(vm.aldisCanUpdate({ id: 'ebb', name: 'ebb' })).toBe(false)
    })

    it('does not offer an update for an unsupported API or blocked agent', async () => {
        const emitAndWait = vi.fn()
            .mockResolvedValueOnce({ agents: [{ name: 'aldis' }] })
            .mockResolvedValueOnce({ api_version: 2, mcus: [{ name: 'ebb', state: 'update_available', actions: ['update'] }] })
            .mockResolvedValueOnce({ agents: [{ name: 'aldis' }] })
            .mockResolvedValueOnce({ api_version: 1, blocker: { message: 'Printing' }, mcus: [
                { name: 'ebb', state: 'update_available', actions: ['update'] },
            ] })
        Object.defineProperty(Vue, '$socket', { value: { emitAndWait }, configurable: true })
        const vm = panel()
        await vm.refreshAldisStatus()
        expect(vm.aldisCanUpdate({ id: 'ebb' })).toBe(false)
        await vm.refreshAldisStatus()
        expect(vm.aldisCanUpdate({ id: 'ebb' })).toBe(false)
        expect(vm.aldisMessage).toBe('Printing')
    })
})

describe('custom profile templates', () => {
    it('reports missing processor options and recovers after the backend is updated', async () => {
        const fetch = vi.fn()
            .mockResolvedValueOnce(response({ error: { message: 'Not Found' } }, 404))
            .mockResolvedValueOnce(response({ result: { processors: {
                STM32H723: {
                    architecture: 'stm32', chip: 'stm32h723xx', chips: ['stm32h723xx'],
                    offsets: ['128KiB'], katapult_offsets: ['128KiB'], clock_references: ['25MHz crystal'],
                    communications: ['usb_to_canbus_bridge'], can: ['PD0/PD1'],
                    bridge_can: ['PD0/PD1'], katapult_can: ['PD0/PD1'],
                },
            } } }))
            .mockResolvedValueOnce(response({ result: { fields: {
                name: 'Manta M8P', architecture: 'stm32', processor: 'STM32H723',
                communication: 'usb_to_canbus_bridge', can_rx_pin: 'PD0', can_tx_pin: 'PD1',
            } } }))
        vi.stubGlobal('fetch', fetch)
        const vm = panel()
        await vm.openCustomProfile('manta')
        expect(vm.customHardwareOptionsError).toBe('Not Found')
        expect(vm.customProcessorOptions).toEqual([])
        await vm.loadCustomHardwareOptions()
        expect(vm.customHardwareOptionsError).toBe('')
        expect(vm.customProfileFields.processor).toBe('STM32H723')
        expect(vm.customCommunicationChoice).toBe('usb_to_canbus_bridge')
        expect(fetch.mock.calls.map(([url]) => url)).toEqual([
            '/machine/mcu_update_manager/profile/options',
            '/machine/mcu_update_manager/profile/options',
            '/machine/mcu_update_manager/profile?profile_id=manta',
        ])
    })

    it('lists Klipper build profiles but not vendor firmware profiles', () => {
        const vm = panel()
        vm.status = {
            profile_catalog: [
                { id: 'manta', name: 'Manta M8P', supports_klipper: true, custom_template: true },
                { id: 'cartographer', name: 'Cartographer', supports_klipper: false, custom_template: false },
            ],
        }
        expect(vm.customProfileTemplates).toEqual([{ label: 'Manta M8P', value: 'manta' }])
    })

    it('loads a USB-CAN bridge template with its processor and CAN pins', async () => {
        const fetch = vi.fn().mockResolvedValue(response({ result: {
            fields: { name: 'Manta M8P', processor: 'STM32H723', transport: 'can',
                communication: 'usb_to_canbus_bridge', can_rx_pin: 'PD0', can_tx_pin: 'PD1' },
        } }))
        vi.stubGlobal('fetch', fetch)
        const vm = panel()
        await vm.loadCustomProfileTemplate('manta')
        expect(vm.customProfileFields).toMatchObject({
            name: 'Manta M8P Custom', processor: 'STM32H723', transport: 'can',
            can_rx_pin: 'PD0', can_tx_pin: 'PD1',
        })
        expect(vm.customProfileError).toBe('')
    })

    it('shows selected profile verification and documented settings', () => {
        const vm = panel()
        vm.status = {
            profile_catalog: [
                { id: 'draft', name: 'Draft board', supports_klipper: false,
                    verification: { ready: false, reasons: ['Klipper/Kalico: menuconfig values have not been extracted from the source images'] },
                    settings: { klipper: { architecture: 'stm32', processor: 'STM32H723' }, katapult: {} } },
                { id: 'ready', name: 'Ready board', supports_klipper: true,
                    verification: { ready: true, reasons: [] }, settings: { klipper: { communication: 'canbus' } } },
            ],
        }
        const device = { id: 'mcu', transport: 'can', confirmed_profile: 'draft' }
        expect(vm.selectedCatalogProfile(device)?.verification?.ready).toBe(false)
        expect(vm.hardwareProfileOptions(device)[0].label).toContain('(unverified)')
        expect(vm.profileSettingsRows(vm.selectedCatalogProfile(device)?.settings?.klipper)).toContainEqual({
            label: 'Processor', value: 'STM32H723',
        })
        vm.setSelectedHardwareProfile(device, 'ready')
        expect(vm.selectedCatalogProfile(device)?.verification?.ready).toBe(true)
        expect(vm.profileSettingsRows(vm.selectedCatalogProfile(device)?.settings?.klipper)).toContainEqual({
            label: 'Communication', value: 'canbus',
        })
        expect(vm.profileSettingsRows({})).toEqual([{ label: 'Configuration', value: 'Not specified' }])
        expect(vm.profileSettingsRows({ can_bitrate: '{{ can_bitrate }}' })).toContainEqual({
            label: 'CAN bitrate (build setting)', value: 'Unknown',
        })
    })

    it('offers paired CAN menu choices for a copied STM32H723 bridge profile', () => {
        const vm = panel()
        vm.customHardwareOptions = {
            STM32H723: {
                architecture: 'stm32', chip: 'stm32h723xx', chips: ['stm32h723xx'],
                offsets: ['No bootloader', '128KiB'], katapult_offsets: ['128KiB'],
                clock_references: ['25MHz crystal'], communications: ['usb', 'canbus', 'usb_to_canbus_bridge'],
                can: ['PD0/PD1', 'PB5/PB6'], bridge_can: ['PD0/PD1', 'PB5/PB6'], katapult_can: ['PD0/PD1'],
            },
        }
        vm.customProfileFields.processor = 'STM32H723'
        vm.customProfileFields.communication = 'usb_to_canbus_bridge'
        vm.customProfileFields.can_rx_pin = 'PD0'
        vm.customProfileFields.can_tx_pin = 'PD1'
        expect(vm.customCanPins).toBe('PD0/PD1')
        expect(vm.customCanPinChoices).toEqual([
            { label: 'CAN bus (on PD0/PD1)', value: 'PD0/PD1' },
            { label: 'CAN bus (on PB5/PB6)', value: 'PB5/PB6' },
        ])
        expect(vm.customCommunicationChoice).toBe('usb_to_canbus_bridge')
        expect(vm.customCommunicationChoices).toEqual([
            { label: 'USB (on PA11/PA12)', value: 'usb' },
            { label: 'CAN bus (on PD0/PD1)', value: 'canbus:PD0/PD1' },
            { label: 'CAN bus (on PB5/PB6)', value: 'canbus:PB5/PB6' },
            { label: 'USB to CAN bus bridge (USB on PA11/PA12)', value: 'usb_to_canbus_bridge' },
        ])
        vm.customCanPins = 'PB5/PB6'
        expect(vm.customProfileFields).toMatchObject({ can_rx_pin: 'PB5', can_tx_pin: 'PB6' })
        expect(vm.profileSettingsRows({ communication: 'usb_to_canbus_bridge', can_rx_pin: 'PB5', can_tx_pin: 'PB6' }))
            .toContainEqual({ label: 'CAN bus interface (RX/TX)', value: 'PB5/PB6' })
        vm.customCommunicationChoice = 'canbus:PD0/PD1'
        expect(vm.customCommunicationChoice).toBe('canbus:PD0/PD1')
        expect(vm.customProfileFields).toMatchObject({
            communication: 'canbus', transport: 'can', can_rx_pin: 'PD0', can_tx_pin: 'PD1',
        })
        vm.customCommunicationChoice = 'canbus:PB5/PB6'
        expect(vm.customProfileFields).toMatchObject({ can_rx_pin: 'PB5', can_tx_pin: 'PB6' })
        vm.customCommunicationChoice = 'usb_to_canbus_bridge'
        expect(vm.customProfileFields.communication).toBe('usb_to_canbus_bridge')
        expect(vm.customKatapultCommunicationChoices).toEqual([
            { label: 'USB (on PA11/PA12)', value: 'usb' },
            { label: 'CAN bus (on PD0/PD1)', value: 'canbus:PD0/PD1' },
        ])
        vm.customKatapultCommunicationChoice = 'canbus:PD0/PD1'
        expect(vm.customProfileFields).toMatchObject({
            bootloader_communication: 'canbus', bootloader_can_rx_pin: 'PD0', bootloader_can_tx_pin: 'PD1',
        })
    })
})

afterEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
    Reflect.deleteProperty(Vue, '$socket')
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
            '/machine/mcu_update_manager/status?configured_only=true',
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
