<template>
    <panel
        :title="$t('Machine.McuUpdateManagerPanel.Title')"
        :icon="mdiChip"
        card-class="machine-mcu-update-manager-panel"
        :collapsible="true">
        <template #buttons>
            <v-tooltip top>
                <template #activator="{ on, attrs }">
                    <v-btn
                        icon
                        tile
                        color="primary"
                        :ripple="true"
                        :loading="loading"
                        :disabled="printerIsPrinting"
                        v-bind="attrs"
                        @click="refresh(true)"
                        v-on="on">
                        <v-icon>{{ mdiRadar }}</v-icon>
                    </v-btn>
                </template>
                <span>{{ $t('Machine.McuUpdateManagerPanel.Refresh') }}</span>
            </v-tooltip>
        </template>

        <v-card-text class="px-0 py-0 mcu-update-manager-list">
            <v-row v-if="errorMessage" class="mt-0 mb-0">
                <v-col class="px-6">
                    <v-alert class="mb-0" dense text type="error" border="left">
                        {{ errorMessage }}
                    </v-alert>
                </v-col>
            </v-row>

            <v-row v-if="status" class="mt-0 mb-0 px-3 pt-3">
                <v-col cols="12" sm="7" class="py-0">
                    <div class="d-flex align-center">
                        <v-select
                            v-model="selectedFirmwareRef"
                            dense
                            outlined
                            hide-details
                            :items="versionOptions"
                            item-text="label"
                            item-value="value"
                            :label="$t('Machine.McuUpdateManagerPanel.FirmwareVersion')"
                            :disabled="busy || !versionOptions.length" />
                        <v-tooltip top>
                            <template #activator="{ on, attrs }">
                                <v-btn
                                    icon
                                    class="ml-2"
                                    color="primary"
                                    :loading="busyAction === 'firmware:switch'"
                                    :disabled="busy || !canSwitchFirmwareRef"
                                    v-bind="attrs"
                                    @click="switchFirmwareRef"
                                    v-on="on">
                                    <v-icon>{{ mdiSourceBranch }}</v-icon>
                                </v-btn>
                            </template>
                            <span>{{ $t('Machine.McuUpdateManagerPanel.SwitchFirmwareVersion') }}</span>
                        </v-tooltip>
                    </div>
                </v-col>
                <v-col cols="12" sm="5" class="py-0 pt-2 pt-sm-0">
                    <v-alert dense text border="left" class="mb-0" :type="updateAvailable ? 'info' : 'success'">
                        {{ firmwareSummary }}
                    </v-alert>
                </v-col>
            </v-row>

            <v-row v-if="loading" class="mt-3 mb-0 px-6">
                <v-col class="pa-0">
                    <div class="d-flex align-center text--secondary mb-1">
                        <v-icon small color="info" class="mr-2">{{ mdiRadar }}</v-icon>
                        {{ scanPhaseLabel }}
                    </div>
                    <v-progress-linear indeterminate color="info" height="3" />
                </v-col>
            </v-row>

            <v-row v-if="status" class="mt-3 mb-0 px-6 align-center">
                <v-col cols="12" md="8" class="pa-0 d-flex flex-wrap align-center">
                    <v-chip
                        v-for="item in scanSummary"
                        :key="item.label"
                        small
                        label
                        outlined
                        :color="item.color"
                        class="mr-2 mb-2">
                        {{ item.label }}: {{ item.value }}
                    </v-chip>
                </v-col>
                <v-col cols="12" md="4" class="pa-0 pb-2 text-md-right text--secondary">
                    {{ lastScanText }}
                </v-col>
            </v-row>
            <div v-if="status" class="px-6 pb-2">
                <v-btn small outlined color="primary" :disabled="busy" @click="openCustomProfile()">
                    <v-icon small left>{{ mdiPlus }}</v-icon>
                    Custom hardware profile
                </v-btn>
            </div>

            <template v-if="devices.length">
                <template v-for="(device, index) in devices">
                    <v-divider v-if="index || status" :key="`divider-${device.id}`" class="mt-3 mb-0" />
                    <v-row :key="device.id" class="py-2">
                        <v-col class="pl-6 pr-3">
                            <div class="d-flex align-center flex-wrap">
                                <strong>{{ device.name || device.mcu_section || device.id }}</strong>
                                <v-chip
                                    x-small
                                    label
                                    outlined
                                    class="ml-2 text-uppercase"
                                    :color="transportColor(device.transport)">
                                    {{ device.transport || 'unknown' }}
                                </v-chip>
                                <v-chip
                                    x-small
                                    label
                                    outlined
                                    class="ml-1 text-uppercase"
                                    :color="device.confirmation_status === 'confirmed' ? 'green' : 'orange'">
                                    {{ confirmationLabel(device) }}
                                </v-chip>
                                <v-chip
                                    x-small
                                    label
                                    outlined
                                    class="ml-1 text-uppercase"
                                    :color="device.config_source ? 'success' : 'grey'">
                                    {{
                                        device.config_source
                                            ? $t('Machine.McuUpdateManagerPanel.Configured')
                                            : $t('Machine.McuUpdateManagerPanel.NotConfigured')
                                    }}
                                </v-chip>
                                <v-chip v-if="!isCartographer(device) && selectedCatalogProfile(device)?.verification?.ready === false"
                                    x-small label outlined color="warning" class="ml-1 text-uppercase">
                                    Unverified profile
                                </v-chip>
                            </div>
                            <div class="text--disabled mt-1">
                                {{ device.detected_chip || $t('Machine.McuUpdateManagerPanel.UnknownChip') }}
                                <template v-if="deviceProfileName(device)">
                                    &middot; {{ deviceProfileName(device) }}
                                </template>
                            </div>
                            <div class="text--disabled">
                                {{ device.firmware_version || $t('Machine.McuUpdateManagerPanel.UnknownVersion') }}
                            </div>
                            <div class="text--disabled mcu-update-manager-device-id">
                                {{
                                    device.canbus_uuid ||
                                    device.serial ||
                                    device.display_id ||
                                    $t('Machine.McuUpdateManagerPanel.NoDeviceId')
                                }}
                            </div>
                            <v-expand-transition>
                                <div v-show="isDeviceExpanded(device)" class="mt-2">
                                    <v-alert
                                        v-if="device.last_operation && device.last_operation.status === 'failed'"
                                        dense
                                        text
                                        type="error"
                                        border="left"
                                        class="mt-2 mb-0">
                                        <div class="font-weight-medium">
                                            {{
                                                $t('Machine.McuUpdateManagerPanel.LastOperationFailed', {
                                                    action: device.last_operation.action || 'flash',
                                                })
                                            }}
                                        </div>
                                        <div v-if="device.last_operation.error" class="mt-1">
                                            {{ device.last_operation.error }}
                                        </div>
                                        <div
                                            v-if="device.last_operation.log"
                                            class="mt-1 text--secondary mcu-update-manager-device-id">
                                            {{ $t('Machine.McuUpdateManagerPanel.LogFile') }}:
                                            {{ device.last_operation.log }}
                                        </div>
                                    </v-alert>
                                    <v-alert
                                        v-if="device.recovery?.active"
                                        dense
                                        text
                                        type="warning"
                                        border="left"
                                        class="mt-2 mb-0">
                                        <div class="font-weight-medium">
                                            {{ $t('Machine.McuUpdateManagerPanel.RecoveryTitle') }}
                                        </div>
                                        <div class="mt-1">{{ recoveryText(device) }}</div>
                                        <v-btn small text color="primary" class="mt-1 px-0 mr-3" @click="refresh(true)">
                                            <v-icon small left>{{ mdiRadar }}</v-icon>
                                            {{ $t('Machine.McuUpdateManagerPanel.ScanAgain') }}
                                        </v-btn>
                                        <v-btn
                                            v-if="device.recovery.can_retry"
                                            small
                                            text
                                            color="warning"
                                            class="mt-1 px-0"
                                            :disabled="busy || printerIsPrinting"
                                            @click="retryRecovery(device)">
                                            <v-icon small left>{{ mdiHistory }}</v-icon>
                                            {{ $t('Machine.McuUpdateManagerPanel.RetrySafely') }}
                                        </v-btn>
                                    </v-alert>
                                    <v-alert
                                        v-if="device.actions?.needs_confirmation"
                                        dense
                                        text
                                        type="warning"
                                        border="left"
                                        class="mt-2 mb-0">
                                        {{
                                            isDfuDevice(device)
                                                ? $t('Machine.McuUpdateManagerPanel.DfuSelectHardware')
                                                : recommendationText(device)
                                        }}
                                    </v-alert>
                                    <v-alert
                                        v-if="device.actions?.requires_exact_profile"
                                        dense
                                        text
                                        type="warning"
                                        border="left"
                                        class="mt-2 mb-0">
                                        {{ $t('Machine.McuUpdateManagerPanel.ExactProfileRequired') }}
                                    </v-alert>
                                    <v-alert
                                        v-if="!isCartographer(device) && !isDfuDevice(device) && device.firmware_state"
                                        dense
                                        text
                                        :type="firmwareStateAlertType(device)"
                                        border="left"
                                        class="mt-2 mb-0">
                                        {{ firmwareStateText(device) }}
                                    </v-alert>
                                    <v-autocomplete
                                        v-if="canSelectHardwareProfile(device)"
                                        :value="selectedHardwareProfile(device)"
                                        dense
                                        outlined
                                        hide-details
                                        class="mt-2 mcu-update-manager-version-select"
                                        :items="hardwareProfileOptions(device)"
                                        item-text="label"
                                        item-value="value"
                                        :label="$t('Machine.McuUpdateManagerPanel.HardwareProfile')"
                                        :disabled="busy || !hardwareProfileOptions(device).length"
                                        @change="setSelectedHardwareProfile(device, $event)" />
                                    <template v-if="!isCartographer(device) && selectedCatalogProfile(device)">
                                        <v-alert v-if="selectedCatalogProfile(device)?.verification?.ready === false"
                                            dense text type="warning" border="left" class="mt-2 mb-0">
                                            <div class="font-weight-medium">This hardware profile is not verified for automatic firmware builds.</div>
                                            <div v-for="reason in selectedCatalogProfile(device)?.verification?.reasons ?? []"
                                                :key="reason" class="mt-1">{{ reason }}</div>
                                        </v-alert>
                                        <div class="mcu-update-manager-profile-settings mt-3">
                                            <div class="subtitle-2 mb-1">Profile settings</div>
                                            <div class="font-weight-medium mb-1">Klipper / Kalico</div>
                                            <div v-for="row in profileSettingsRows(selectedCatalogProfile(device)?.settings?.klipper)"
                                                :key="`klipper-${row.label}`" class="mcu-update-manager-profile-setting">
                                                <span class="text--secondary">{{ row.label }}</span><span>{{ row.value }}</span>
                                            </div>
                                            <div class="font-weight-medium mt-2 mb-1">Katapult</div>
                                            <div v-for="row in profileSettingsRows(selectedCatalogProfile(device)?.settings?.katapult)"
                                                :key="`katapult-${row.label}`" class="mcu-update-manager-profile-setting">
                                                <span class="text--secondary">{{ row.label }}</span><span>{{ row.value }}</span>
                                            </div>
                                            <div class="font-weight-medium mt-2 mb-1">Flashing</div>
                                            <div class="mcu-update-manager-profile-setting"><span class="text--secondary">Initial flash</span><span>{{ selectedCatalogProfile(device)?.settings?.initial_flash_method || 'Not specified' }}</span></div>
                                            <div class="mcu-update-manager-profile-setting"><span class="text--secondary">Update</span><span>{{ selectedCatalogProfile(device)?.settings?.update_method || 'Not specified' }}</span></div>
                                            <div v-if="selectedCatalogProfile(device)?.settings?.sources?.length" class="text--secondary mt-2">
                                                Source: {{ selectedCatalogProfile(device)?.settings?.sources?.[0] }}
                                            </div>
                                        </div>
                                    </template>
                                    <div v-if="canSelectHardwareProfile(device)" class="mt-2">
                                        <v-btn small text color="primary" :disabled="busy || !selectedHardwareProfile(device)"
                                            @click="openCustomProfile(selectedHardwareProfile(device), false, device.id)">
                                            <v-icon small left>{{ mdiContentCopy }}</v-icon>
                                            Customize profile
                                        </v-btn>
                                        <v-btn v-if="selectedProfileIsCustom(device)" small text color="primary" :disabled="busy"
                                            @click="openCustomProfile(selectedHardwareProfile(device), true, device.id)">
                                            <v-icon small left>{{ mdiPencil }}</v-icon>
                                            Edit custom profile
                                        </v-btn>
                                    </div>
                                    <div
                                        v-if="profileConfidence(device) && device.actions?.needs_confirmation"
                                        class="mt-2 d-flex align-center flex-wrap">
                                        <v-chip
                                            small
                                            label
                                            outlined
                                            :color="profileConfidence(device)?.color"
                                            class="mr-2 mb-1">
                                            {{ profileConfidence(device)?.label }}
                                        </v-chip>
                                        <v-chip
                                            v-for="reason in recommendedProfile(device)?.reasons ?? []"
                                            :key="reason"
                                            x-small
                                            label
                                            outlined
                                            class="mr-1 mb-1">
                                            {{ profileReasonLabel(reason) }}
                                        </v-chip>
                                    </div>
                                    <template v-if="isDfuDevice(device) && !isCartographer(device)">
                                        <div class="mt-3 mcu-update-manager-workflow">
                                            <div
                                                v-for="step in dfuGuideSteps(device)"
                                                :key="step.key"
                                                class="d-flex align-center mcu-update-manager-workflow-step"
                                                :class="`is-${step.state}`">
                                                <v-icon small class="mr-2" :color="workflowStepColor(step.state)">
                                                    {{ workflowStepIcon(step.state) }}
                                                </v-icon>
                                                <span>{{ step.label }}</span>
                                            </div>
                                        </div>
                                        <v-alert
                                            v-if="selectedHardwareProfile(device) !== device.confirmed_profile"
                                            dense
                                            text
                                            type="info"
                                            border="left"
                                            class="mt-2 mb-0">
                                            {{ $t('Machine.McuUpdateManagerPanel.DfuConfirmHardware') }}
                                        </v-alert>
                                        <v-select
                                            :value="selectedDfuTargetKey(device)"
                                            dense
                                            outlined
                                            hide-details
                                            class="mt-2 mcu-update-manager-version-select"
                                            :items="dfuTargetOptions(device)"
                                            item-text="label"
                                            item-value="value"
                                            :label="$t('Machine.McuUpdateManagerPanel.DfuFirmwareTarget')"
                                            :disabled="busy || !dfuTargetOptions(device).length"
                                            @change="setDfuTarget(device, $event)" />
                                        <v-btn
                                            color="warning"
                                            class="mt-2"
                                            :loading="busyAction === `${device.id}:dfu_flash`"
                                            :disabled="busy || !canFlashDfu(device) || printerIsPrinting"
                                            @click="flashDfu(device)">
                                            <v-icon left>{{ mdiFlash }}</v-icon>
                                            {{ $t('Machine.McuUpdateManagerPanel.DfuBuildAndFlash') }}
                                        </v-btn>
                                    </template>
                                    <v-alert
                                        v-if="isCartographer(device) && !device.config_source"
                                        dense
                                        text
                                        type="warning"
                                        border="left"
                                        class="mt-2 mb-0">
                                        {{ $t('Machine.McuUpdateManagerPanel.NotInPrinterCfg') }}
                                    </v-alert>
                                    <v-alert
                                        v-if="isCartographerPendingCan(device)"
                                        dense
                                        text
                                        :type="device.canbus_uuid ? 'info' : 'warning'"
                                        border="left"
                                        class="mt-2 mb-0">
                                        {{
                                            device.canbus_uuid
                                                ? $t('Machine.McuUpdateManagerPanel.CartographerCanUuidDetected')
                                                : $t('Machine.McuUpdateManagerPanel.CartographerAwaitingCanReconnect')
                                        }}
                                    </v-alert>
                                    <v-select
                                        v-if="isCartographer(device) && !isCartographerDfu(device)"
                                        :value="selectedCartographerFirmwareKeys[device.id]"
                                        dense
                                        outlined
                                        hide-details
                                        class="mt-2 mcu-update-manager-version-select"
                                        :items="cartographerVersionOptions(device)"
                                        item-text="label"
                                        item-value="value"
                                        :label="$t('Machine.McuUpdateManagerPanel.CartographerFirmware')"
                                        :disabled="busy || !cartographerVersionOptions(device).length"
                                        @change="setCartographerFirmwareKey(device, $event)" />
                                    <v-alert
                                        v-if="isCartographer(device) && device.vendor_firmware?.status !== 'ok'"
                                        dense
                                        text
                                        type="warning"
                                        border="left"
                                        class="mt-2 mb-0">
                                        {{
                                            device.vendor_firmware?.message ||
                                            $t('Machine.McuUpdateManagerPanel.VendorFirmwareMissing')
                                        }}
                                    </v-alert>
                                    <div v-if="isCartographer(device)" class="mt-2 mcu-update-manager-workflow">
                                        <v-progress-linear
                                            v-if="isCartographerFlashing(device)"
                                            indeterminate
                                            color="warning"
                                            height="3"
                                            class="mb-2" />
                                        <div
                                            v-for="step in activeCartographerWorkflowSteps(device)"
                                            :key="step.key"
                                            class="d-flex align-center mcu-update-manager-workflow-step"
                                            :class="`is-${step.state}`">
                                            <v-icon small class="mr-2" :color="workflowStepColor(step.state)">
                                                {{ workflowStepIcon(step.state) }}
                                            </v-icon>
                                            <span>{{ step.label }}</span>
                                        </div>
                                        <v-btn
                                            v-if="isCartographerUsb(device)"
                                            small
                                            depressed
                                            color="primary"
                                            class="mt-2 mr-2"
                                            :loading="busyAction === `${device.id}:cartographer_flash`"
                                            :disabled="
                                                busy ||
                                                !device.actions?.cartographer_usb_update_ready ||
                                                !selectedCartographerVersion(device) ||
                                                printerIsPrinting
                                            "
                                            @click="updateCartographerUsbFirmware(device)">
                                            <v-icon small left>{{ mdiFlash }}</v-icon>
                                            {{ $t('Machine.McuUpdateManagerPanel.CartographerUsbUpdateShort') }}
                                        </v-btn>
                                        <v-btn
                                            v-if="isCartographerCan(device)"
                                            small
                                            depressed
                                            color="primary"
                                            class="mt-2 mr-2"
                                            :loading="busyAction === `${device.id}:cartographer_flash`"
                                            :disabled="
                                                busy ||
                                                !device.actions?.vendor_flash_ready ||
                                                !selectedCartographerVersion(device) ||
                                                printerIsPrinting
                                            "
                                            @click="flashDevice(device)">
                                            <v-icon small left>{{ mdiFlash }}</v-icon>
                                            {{ $t('Machine.McuUpdateManagerPanel.CartographerCanUpdateShort') }}
                                        </v-btn>
                                        <v-btn
                                            v-if="isCartographerCan(device)"
                                            small
                                            depressed
                                            outlined
                                            color="warning"
                                            class="mt-2 mr-2"
                                            :loading="busyAction === `${device.id}:cartographer_flash`"
                                            :disabled="
                                                busy ||
                                                !device.actions?.cartographer_can_to_usb_ready ||
                                                !cartographerCanToUsbVersion(device) ||
                                                printerIsPrinting
                                            "
                                            @click="flashCartographerCanToUsb(device)">
                                            <v-icon small left>{{ mdiUsb }}</v-icon>
                                            {{ $t('Machine.McuUpdateManagerPanel.CartographerCanToUsbShort') }}
                                        </v-btn>
                                        <v-btn
                                            v-if="isCartographerPendingCan(device)"
                                            small
                                            depressed
                                            color="warning"
                                            class="mt-2 mr-2"
                                            :loading="busyAction === `${device.id}:cartographer_usb_to_can`"
                                            :disabled="
                                                busy ||
                                                !device.actions?.cartographer_usb_to_can_flash_ready ||
                                                !selectedCartographerVersion(device) ||
                                                printerIsPrinting
                                            "
                                            @click="flashCartographerUsbToCanFirmware(device)">
                                            <v-icon small left>{{ mdiFlash }}</v-icon>
                                            {{ $t('Machine.McuUpdateManagerPanel.CartographerFlashCanAfterReconnect') }}
                                        </v-btn>
                                        <v-btn
                                            v-if="isCartographerUsb(device)"
                                            small
                                            depressed
                                            color="warning"
                                            class="mt-2"
                                            :loading="busyAction === `${device.id}:cartographer_usb_to_can`"
                                            :disabled="
                                                busy ||
                                                !device.actions?.cartographer_usb_to_can_ready ||
                                                printerIsPrinting
                                            "
                                            @click="switchCartographerUsbToCan(device)">
                                            <v-icon small left>{{ mdiSwapHorizontal }}</v-icon>
                                            {{ $t('Machine.McuUpdateManagerPanel.CartographerUsbToCanShort') }}
                                        </v-btn>
                                        <v-btn
                                            v-if="isCartographerDfu(device)"
                                            small
                                            depressed
                                            outlined
                                            color="warning"
                                            class="mt-2 ml-0 ml-sm-2 mr-2"
                                            :loading="busyAction === `${device.id}:cartographer_dfu_flash`"
                                            :disabled="
                                                busy ||
                                                !device.actions?.cartographer_dfu_usb_ready ||
                                                !cartographerDfuVersion(device, 'USB') ||
                                                printerIsPrinting
                                            "
                                            @click="flashCartographerDfu(device, 'USB')">
                                            <v-icon small left>{{ mdiUsb }}</v-icon>
                                            {{ $t('Machine.McuUpdateManagerPanel.CartographerDfuUsbShort') }}
                                        </v-btn>
                                        <v-btn
                                            v-if="isCartographerDfu(device)"
                                            small
                                            depressed
                                            outlined
                                            color="warning"
                                            class="mt-2"
                                            :loading="busyAction === `${device.id}:cartographer_dfu_flash`"
                                            :disabled="
                                                busy ||
                                                !device.actions?.cartographer_dfu_can_ready ||
                                                !cartographerDfuVersion(device, 'CAN') ||
                                                printerIsPrinting
                                            "
                                            @click="flashCartographerDfu(device, 'CAN')">
                                            <v-icon small left>{{ mdiSwapHorizontal }}</v-icon>
                                            {{ $t('Machine.McuUpdateManagerPanel.CartographerDfuCanShort') }}
                                        </v-btn>
                                    </div>
                                    <div
                                        v-if="!isCartographer(device) && activeStandardWorkflowSteps(device).length"
                                        class="mt-2 mcu-update-manager-workflow">
                                        <v-progress-linear indeterminate color="warning" height="3" class="mb-2" />
                                        <div
                                            v-for="step in activeStandardWorkflowSteps(device)"
                                            :key="step.key"
                                            class="d-flex align-center mcu-update-manager-workflow-step"
                                            :class="`is-${step.state}`">
                                            <v-icon small class="mr-2" :color="workflowStepColor(step.state)">
                                                {{ workflowStepIcon(step.state) }}
                                            </v-icon>
                                            <span>{{ step.label }}</span>
                                        </div>
                                    </div>
                                    <div
                                        v-if="device.artifact_history?.length"
                                        class="mt-3 d-flex align-center flex-wrap">
                                        <v-select
                                            :value="selectedArtifactRef(device)"
                                            dense
                                            outlined
                                            hide-details
                                            class="mcu-update-manager-version-select mr-2 mb-2"
                                            :items="artifactHistoryOptions(device)"
                                            :label="$t('Machine.McuUpdateManagerPanel.FirmwareHistory')"
                                            @change="setSelectedArtifactRef(device, $event)" />
                                        <v-btn
                                            small
                                            outlined
                                            color="warning"
                                            class="mb-2"
                                            :disabled="busy || !selectedArtifactRef(device) || printerIsPrinting"
                                            @click="flashSelectedArtifact(device)">
                                            <v-icon small left>{{ mdiHistory }}</v-icon>
                                            {{ $t('Machine.McuUpdateManagerPanel.RestoreSelected') }}
                                        </v-btn>
                                    </div>
                                    <div v-if="device.printer_cfg_snippet" class="mt-3">
                                        <div class="d-flex align-center">
                                            <span class="text--secondary">
                                                {{ $t('Machine.McuUpdateManagerPanel.PrinterCfgEntry') }}
                                            </span>
                                            <v-btn icon small color="primary" @click="copyPrinterCfg(device)">
                                                <v-icon small>{{ mdiContentCopy }}</v-icon>
                                            </v-btn>
                                        </div>
                                        <pre class="mb-0 mcu-update-manager-config-snippet">{{
                                            device.printer_cfg_snippet
                                        }}</pre>
                                    </div>
                                    <div v-if="device.last_operation?.log" class="mt-2">
                                        <v-btn small text color="primary" class="px-0" @click="toggleDeviceLog(device)">
                                            <v-icon small left>{{ mdiFileDocumentOutline }}</v-icon>
                                            {{
                                                isDeviceLogExpanded(device)
                                                    ? $t('Machine.McuUpdateManagerPanel.HideLog')
                                                    : $t('Machine.McuUpdateManagerPanel.ShowLog')
                                            }}
                                        </v-btn>
                                        <v-btn small text color="primary" @click="downloadDeviceLog(device)">
                                            <v-icon small left>{{ mdiDownload }}</v-icon>
                                            {{ $t('Machine.McuUpdateManagerPanel.DownloadLog') }}
                                        </v-btn>
                                        <pre
                                            v-if="shouldShowDeviceLog(device) && device.last_operation?.log_tail"
                                            class="mt-2 mb-0 mcu-update-manager-log-tail"
                                            >{{ device.last_operation.log_tail }}</pre
                                        >
                                    </div>
                                </div>
                            </v-expand-transition>
                        </v-col>
                        <v-col class="col-auto pr-6 text-right" align-self="center">
                            <v-tooltip top>
                                <template #activator="{ on, attrs }">
                                    <v-btn
                                        icon
                                        color="primary"
                                        v-bind="attrs"
                                        @click="toggleDeviceExpanded(device)"
                                        v-on="on">
                                        <v-icon>{{ isDeviceExpanded(device) ? mdiChevronUp : mdiChevronDown }}</v-icon>
                                    </v-btn>
                                </template>
                                <span>{{ $t('Machine.McuUpdateManagerPanel.Details') }}</span>
                            </v-tooltip>
                            <div v-if="isDeviceExpanded(device)" class="mcu-update-manager-action-buttons">
                                <v-tooltip v-if="device.actions?.needs_confirmation" top>
                                    <template #activator="{ on, attrs }">
                                        <v-btn
                                            icon
                                            color="primary"
                                            :loading="busyAction === `${device.id}:confirm`"
                                            :disabled="busy || !selectedHardwareProfile(device)"
                                            v-bind="attrs"
                                            @click="confirmDevice(device)"
                                            v-on="on">
                                            <v-icon>{{ mdiCheckCircle }}</v-icon>
                                        </v-btn>
                                    </template>
                                    <span>{{ $t('Machine.McuUpdateManagerPanel.ConfirmProfile') }}</span>
                                </v-tooltip>
                                <v-tooltip v-else-if="canSelectHardwareProfile(device)" top>
                                    <template #activator="{ on, attrs }">
                                        <v-btn
                                            icon
                                            color="primary"
                                            :loading="busyAction === `${device.id}:confirm`"
                                            :disabled="
                                                busy ||
                                                !selectedHardwareProfile(device) ||
                                                selectedHardwareProfile(device) === device.confirmed_profile
                                            "
                                            v-bind="attrs"
                                            @click="confirmDevice(device)"
                                            v-on="on">
                                            <v-icon>{{ mdiCheckCircle }}</v-icon>
                                        </v-btn>
                                    </template>
                                    <span>{{ $t('Machine.McuUpdateManagerPanel.SaveProfile') }}</span>
                                </v-tooltip>
                                <v-tooltip v-if="!device.actions?.vendor_managed && !isDfuDevice(device)" top>
                                    <template #activator="{ on, attrs }">
                                        <v-btn
                                            icon
                                            color="primary"
                                            :loading="busyAction === `${device.id}:build`"
                                            :disabled="busy || !device.actions?.can_build"
                                            v-bind="attrs"
                                            @click="runDeviceAction('build', device)"
                                            v-on="on">
                                            <v-icon>{{ mdiHammerWrench }}</v-icon>
                                        </v-btn>
                                    </template>
                                    <span>{{ $t('Machine.McuUpdateManagerPanel.Build') }}</span>
                                </v-tooltip>
                                <v-tooltip v-if="!isCartographer(device) && !isDfuDevice(device)" top>
                                    <template #activator="{ on, attrs }">
                                        <v-btn
                                            icon
                                            color="warning"
                                            :loading="busyAction === `${device.id}:flash`"
                                            :disabled="busy || !canFlashDevice(device) || printerIsPrinting"
                                            v-bind="attrs"
                                            @click="flashDevice(device)"
                                            v-on="on">
                                            <v-icon>{{ mdiFlash }}</v-icon>
                                        </v-btn>
                                    </template>
                                    <span>{{ $t('Machine.McuUpdateManagerPanel.Flash') }}</span>
                                </v-tooltip>
                                <v-tooltip v-if="!isDfuDevice(device)" top>
                                    <template #activator="{ on, attrs }">
                                        <v-btn
                                            icon
                                            color="green"
                                            :loading="busyAction === `${device.id}:verify`"
                                            :disabled="busy || !device.actions?.can_verify"
                                            v-bind="attrs"
                                            @click="runDeviceAction('verify', device)"
                                            v-on="on">
                                            <v-icon>{{ mdiShieldCheck }}</v-icon>
                                        </v-btn>
                                    </template>
                                    <span>{{ $t('Machine.McuUpdateManagerPanel.Verify') }}</span>
                                </v-tooltip>
                            </div>
                        </v-col>
                    </v-row>
                </template>
            </template>

            <v-row v-else class="mt-0 mb-0">
                <v-col class="px-6">
                    <div v-if="!loaded" class="d-flex justify-center py-3">
                        <v-btn color="primary" :loading="loading" :disabled="printerIsPrinting" @click="refresh(true)">
                            <v-icon left>{{ mdiRadar }}</v-icon>
                            {{ $t('Machine.McuUpdateManagerPanel.CheckDevices') }}
                        </v-btn>
                    </div>
                    <p v-else class="text-center text--disabled mb-0">
                        {{ $t('Machine.McuUpdateManagerPanel.NoDevices') }}
                    </p>
                </v-col>
            </v-row>
        </v-card-text>
        <v-dialog v-model="customProfileDialog" max-width="760" scrollable>
            <v-card>
                <v-card-title>{{ editingCustomProfileId ? 'Edit custom profile' : 'New custom profile' }}</v-card-title>
                <v-card-text>
                    <v-alert v-if="customProfileError" dense text type="error">{{ customProfileError }}</v-alert>
                    <v-alert dense text type="warning">
                        Verify the exact board revision, pins and bootloader offset before building or flashing.
                        Saving a profile does not flash a device.
                    </v-alert>
                    <v-autocomplete v-if="!editingCustomProfileId" v-model="customProfileTemplateId"
                        :items="customProfileTemplates" item-text="label" item-value="value"
                        label="Start from an existing profile (optional)" clearable outlined dense
                        :disabled="customProfileSaving" @change="loadCustomProfileTemplate" />
                    <v-row dense>
                        <v-col cols="12" sm="7"><v-text-field v-model="customProfileFields.name" label="Profile name" outlined dense /></v-col>
                        <v-col cols="12" sm="5"><v-text-field v-model="customProfileFields.vendor" label="Vendor" outlined dense /></v-col>
                        <v-col cols="12"><v-select v-model="customProfileFields.family" label="Category" outlined dense
                            :items="['mainboard', 'toolhead', 'expansion', 'mmu', 'cartographer', 'beacon']" /></v-col>
                        <v-col cols="12" class="subtitle-2">Klipper / Kalico</v-col>
                        <v-col cols="12" sm="6"><v-select v-model="customProfileFields.architecture" label="Micro-controller architecture" outlined dense
                            :items="['stm32', 'rp2040']" @change="customArchitectureChanged" /></v-col>
                        <v-col cols="12" sm="6"><v-select v-model="customProfileFields.processor" label="Processor model" outlined dense
                            :items="customProcessorOptions" @change="customProcessorChanged" /></v-col>
                        <v-col cols="12" sm="6"><v-select v-model="customProfileFields.bootloader_offset" label="Bootloader offset" outlined dense
                            :items="customBootloaderOffsets" @change="customOffsetChanged" /></v-col>
                        <v-col v-if="customProfileFields.architecture === 'stm32'" cols="12" sm="6"><v-select v-model="customProfileFields.clock_reference" label="Clock reference" outlined dense
                            :items="customProcessor?.clock_references ?? []" /></v-col>
                        <v-col cols="12" sm="6"><v-select v-model="customCommunicationChoice" label="Communication interface" outlined dense
                            :items="customCommunicationChoices" item-text="label" item-value="value" :disabled="!customProcessor" /></v-col>
                        <v-col v-if="customProfileFields.communication === 'usb_to_canbus_bridge'" cols="12" sm="6">
                            <v-select v-model="customCanPins" label="CAN bus interface" outlined dense
                                :items="customCanPinChoices" item-text="label" item-value="value" :disabled="!customProcessor" /></v-col>
                        <v-col v-if="customProfileFields.communication !== 'canbus' && customProfileFields.architecture === 'stm32'" cols="12" sm="6">
                            <v-select v-model="customProfileFields.usb_pins" label="USB pins" outlined dense :items="['PA11/PA12']" /></v-col>
                        <v-col cols="12" sm="6"><v-select v-model="customProfileFields.chip" label="Detected MCU chip" outlined dense
                            :items="customProcessor?.chips ?? []" :disabled="!customProcessor" /></v-col>
                        <v-col cols="12" sm="6"><v-select v-model="customProfileFields.transport" label="Detected transport" outlined dense
                            :items="[customProfileFields.communication === 'usb' ? 'usb' : 'can']" disabled /></v-col>
                        <v-col cols="12" sm="6"><v-select v-model="customProfileFields.flash_method" label="Update method" outlined dense
                            :items="customFlashMethods" /></v-col>
                        <v-col cols="12" sm="6"><v-select v-model="customProfileFields.initial_flash_method" label="Initial flash method" outlined dense
                            :items="customProfileFields.architecture === 'rp2040' ? ['rp2040_bootsel_make_flash'] : ['dfu_util', 'klipper_make_flash_dfu', 'sdcard']" /></v-col>
                        <v-col v-if="customProfileFields.architecture === 'stm32' && customProfileFields.initial_flash_method !== 'sdcard'" cols="12" sm="6">
                            <v-select v-model="customProfileFields.dfu_vid_pid" label="DFU VID:PID" outlined dense :items="['0483:df11']" /></v-col>
                        <v-col cols="12"><v-checkbox v-model="customProfileFields.use_katapult" label="Build Katapult for this board" hide-details @change="customKatapultChanged" /></v-col>
                        <template v-if="customProfileFields.use_katapult">
                            <v-col cols="12" class="subtitle-2">Katapult</v-col>
                            <v-col cols="12" sm="6"><v-select v-model="customProfileFields.application_start_offset"
                                label="Katapult application start offset" outlined dense disabled
                                :items="[customProfileFields.bootloader_offset]" /></v-col>
                            <v-col v-if="customProfileFields.architecture === 'stm32'" cols="12" sm="6"><v-select v-model="customProfileFields.bootloader_clock_reference"
                                label="Katapult clock reference" outlined dense
                                :items="customProcessor?.clock_references ?? []" /></v-col>
                            <v-col cols="12" sm="6"><v-select v-model="customKatapultCommunicationChoice"
                                label="Katapult communication interface" outlined dense
                                :items="customKatapultCommunicationChoices" item-text="label" item-value="value" :disabled="!customProcessor" /></v-col>
                            <v-col v-if="customProfileFields.bootloader_communication === 'usb' && customProfileFields.architecture === 'stm32'" cols="12" sm="6">
                                <v-select v-model="customProfileFields.bootloader_usb_pins" label="Katapult USB pins" outlined dense :items="['PA11/PA12']" /></v-col>
                        </template>
                    </v-row>
                </v-card-text>
                <v-card-actions>
                    <v-spacer />
                    <v-btn text @click="customProfileDialog = false">Cancel</v-btn>
                    <v-btn color="primary" :loading="customProfileSaving" @click="saveCustomProfile">Save profile</v-btn>
                </v-card-actions>
            </v-card>
        </v-dialog>
    </panel>
</template>

<script lang="ts">
import { Component, Mixins } from 'vue-property-decorator'
import BaseMixin from '@/components/mixins/base'
import Panel from '@/components/ui/Panel.vue'
import { isRecord } from '@/plugins/helpers'
import {
    mdiAlertCircleOutline,
    mdiCheckCircle,
    mdiCheckboxBlankCircleOutline,
    mdiChevronDown,
    mdiChevronUp,
    mdiChip,
    mdiContentCopy,
    mdiFileDocumentOutline,
    mdiDownload,
    mdiFlash,
    mdiHammerWrench,
    mdiHistory,
    mdiProgressClock,
    mdiPlus,
    mdiPencil,
    mdiRadar,
    mdiShieldCheck,
    mdiSourceBranch,
    mdiSwapHorizontal,
    mdiUsb,
} from '@mdi/js'

class McuUpdateManagerApiError extends Error {
    constructor(
        message: string,
        readonly status: number
    ) {
        super(message)
    }
}

interface FirmwareVersionOption {
    available?: boolean
    flavour?: 'full' | 'lite'
    kind: string
    label: string
    ref?: string | null
    value: string
}

interface McuUpdateManagerArtifact {
    exists?: boolean
    path?: string
    profile?: {
        id?: string
        name?: string
    }
    sha256?: string
    size?: number
    status?: string
}

interface McuUpdateManagerProfileMatch {
    id: string
    name?: string
    reasons?: string[]
    score?: number
}

interface McuUpdateManagerArtifactHistory {
    created_at?: string
    exists?: boolean
    kind?: string
    label?: string
    path?: string
    ref?: string
}

interface McuUpdateManagerOperation {
    action?: string
    completed_at?: string
    current_step?: string
    detected_serial?: string
    error?: string
    log?: string
    log_tail?: string
    metadata?: string
    started_at?: string
    status?: string
    printer_cfg_snippet?: string
    steps?: Array<{
        id?: string
        label?: string
        status?: string
    }>
}

interface McuUpdateManagerDevice {
    actions?: {
        can_build?: boolean
        can_flash?: boolean
        can_verify?: boolean
        needs_confirmation?: boolean
        vendor_flash_ready?: boolean
        vendor_managed?: boolean
        cartographer_can_to_usb_ready?: boolean
        cartographer_usb_to_can_ready?: boolean
        cartographer_usb_update_ready?: boolean
        cartographer_dfu_usb_ready?: boolean
        cartographer_dfu_can_ready?: boolean
        cartographer_usb_to_can_flash_ready?: boolean
        requires_exact_profile?: boolean
    }
    artifact?: McuUpdateManagerArtifact | null
    artifact_history?: McuUpdateManagerArtifactHistory[]
    canbus_uuid?: string
    confirmation_status?: string
    confirmed_profile?: string
    config_source?: {
        file?: string
        line?: number
    } | null
    detected_chip?: string
    display_id?: string
    firmware_state?: {
        already_matching?: boolean
        artifact_ready?: boolean
        artifact_version?: string
        needs_build?: boolean
        needs_flash?: boolean
        runtime_version?: string
        status?: string
    }
    firmware_version?: string
    id: string
    last_operation?: McuUpdateManagerOperation | null
    likely_profiles?: McuUpdateManagerProfileMatch[]
    mcu_section?: string
    name?: string
    serial?: string
    printer_cfg_snippet?: string | null
    recovery?: {
        active?: boolean
        can_rescan?: boolean
        can_retry?: boolean
        has_last_good?: boolean
        mode?: string
    }
    transport?: string
    usb_device?: {
        bus?: string
        description?: string
        device?: string
        vid_pid?: string
    }
    workflow?: {
        message?: string
        status?: string
        type?: string
        updated_at?: string
    }
    vendor_firmware?: {
        available_versions?: Array<{
            firmware?: Record<string, unknown>
            label?: string
            version?: string
        }>
        dfu_available_versions?: Record<
            string,
            Array<{
                label?: string
                version?: string
            }>
        >
        can_to_usb?: {
            available_versions?: Array<{
                label?: string
                version?: string
            }>
        }
        current_version?: string
        manager?: string
        message?: string
        probe_version?: string
        protocol?: string
        selected?: {
            firmware?: {
                filename?: string
            }
            version?: string
        }
        status?: string
    }
}

interface McuUpdateManagerStatus {
    devices?: McuUpdateManagerDevice[]
    firmware_source?: {
        ref?: string
        repositories?: {
            update_available?: boolean
            version_options?: FirmwareVersionOption[]
        }
    }
    preflight?: {
        katapult?: {
            available?: boolean
            message?: string
            status?: string
        }
    }
    profile_catalog?: Array<{
        id: string
        name?: string
        custom?: boolean
        supports_klipper?: boolean
        custom_template?: boolean
        chips?: string[]
        transports?: string[]
        supports_dfu?: boolean
        verification?: { ready: boolean; reasons: string[] }
        settings?: {
            klipper?: Record<string, string | number | boolean>
            katapult?: Record<string, string | number | boolean>
            initial_flash_method?: string
            update_method?: string
            sources?: string[]
        }
        targets?: Array<{
            communication: string
            kind: 'katapult' | 'klipper'
            label: string
        }>
    }>
    discovery?: {
        phases?: Array<{ id?: string; status?: string }>
        scanned_at?: string
    }
    summary?: {
        dfu?: number
        issues?: number
        needs_confirmation?: number
        ready?: number
        total?: number
    }
    status?: string
}

interface CustomProfileFields {
    name: string
    vendor: string
    family: string
    chip: string
    transport: string
    architecture: string
    processor: string
    clock_reference: string
    bootloader_offset: string
    communication: string
    can_rx_pin: string
    can_tx_pin: string
    usb_pins: string
    flash_method: string
    use_katapult: boolean
    application_start_offset: string
    bootloader_communication: string
    bootloader_clock_reference: string
    bootloader_can_rx_pin: string
    bootloader_can_tx_pin: string
    bootloader_usb_pins: string
    initial_flash_method: string
    dfu_vid_pid: string
}

interface CustomProcessorOptions {
    architecture: string
    chip: string
    chips: string[]
    offsets: string[]
    katapult_offsets: string[]
    clock_references: string[]
    communications: string[]
    can: string[]
    katapult_can: string[]
    bridge_can: string[]
}

function emptyCustomProfile(): CustomProfileFields {
    return {
        name: '', vendor: '', family: 'toolhead', chip: '', transport: 'can', architecture: 'stm32',
        processor: '', clock_reference: '8MHz crystal', bootloader_offset: '8KiB', communication: 'canbus',
        can_rx_pin: '', can_tx_pin: '', usb_pins: 'PA11/PA12', flash_method: 'can_katapult',
        use_katapult: true, application_start_offset: '8KiB', bootloader_communication: 'canbus',
        bootloader_clock_reference: '8MHz crystal', bootloader_can_rx_pin: '', bootloader_can_tx_pin: '',
        bootloader_usb_pins: 'PA11/PA12', initial_flash_method: 'dfu_util', dfu_vid_pid: '0483:df11',
    }
}

interface McuUpdateManagerCachedJob {
    action?: string
    completed_at?: string | null
    device_id?: string | null
    error?: string | null
    job_id?: string
    result?: Record<string, unknown> | null
    status?: string
}

interface McuUpdateManagerOperationStatus {
    job?: McuUpdateManagerCachedJob | null
    operation?: McuUpdateManagerOperation | null
    status?: string
}

@Component({
    components: { Panel },
})
export default class McuUpdateManagerPanel extends Mixins(BaseMixin) {
    mdiAlertCircleOutline = mdiAlertCircleOutline
    mdiCheckCircle = mdiCheckCircle
    mdiCheckboxBlankCircleOutline = mdiCheckboxBlankCircleOutline
    mdiChevronDown = mdiChevronDown
    mdiChevronUp = mdiChevronUp
    mdiChip = mdiChip
    mdiContentCopy = mdiContentCopy
    mdiFileDocumentOutline = mdiFileDocumentOutline
    mdiDownload = mdiDownload
    mdiFlash = mdiFlash
    mdiHammerWrench = mdiHammerWrench
    mdiHistory = mdiHistory
    mdiProgressClock = mdiProgressClock
    mdiPlus = mdiPlus
    mdiPencil = mdiPencil
    mdiRadar = mdiRadar
    mdiShieldCheck = mdiShieldCheck
    mdiSourceBranch = mdiSourceBranch
    mdiSwapHorizontal = mdiSwapHorizontal
    mdiUsb = mdiUsb

    busy = false
    busyAction = ''
    errorMessage = ''
    loaded = false
    loading = false
    progressRefreshTimer: number | null = null
    operationRefreshPromise: Promise<void> | null = null
    refreshPromise: Promise<void> | null = null
    expandedDeviceIds: Record<string, boolean> = {}
    expandedDeviceLogIds: Record<string, boolean> = {}
    selectedFirmwareRef = 'current'
    selectedCartographerFirmwareKeys: Record<string, string> = {}
    selectedDfuTargetKeys: Record<string, string> = {}
    selectedHardwareProfileIds: Record<string, string> = {}
    selectedArtifactRefs: Record<string, string> = {}
    customProfileDialog = false
    customProfileSaving = false
    customProfileError = ''
    customProfileTemplateId = ''
    editingCustomProfileId = ''
    customProfileDeviceId = ''
    customProfileFields: CustomProfileFields = emptyCustomProfile()
    customHardwareOptions: Record<string, CustomProcessorOptions> = {}
    customProfileTemplateRequestId = 0
    scanPhaseIndex = 0
    scanPhaseTimer: number | null = null
    status: McuUpdateManagerStatus | null = null

    beforeDestroy() {
        this.stopProgressRefresh()
        this.stopScanProgress()
    }

    get devices(): McuUpdateManagerDevice[] {
        return this.status?.devices ?? []
    }

    get customProfileTemplates(): Array<{ label: string; value: string }> {
        return (this.status?.profile_catalog ?? []).filter((profile) => profile.custom_template).map((profile) => ({
            label: `${profile.custom ? 'Custom | ' : ''}${profile.name ?? profile.id}${profile.supports_klipper ? '' : ' (needs hardware settings)'}`,
            value: profile.id,
        }))
    }

    get customProcessorOptions(): string[] {
        return Object.keys(this.customHardwareOptions).filter((name) =>
            this.customHardwareOptions[name].architecture === this.customProfileFields.architecture)
    }

    get customProcessor(): CustomProcessorOptions | null {
        return this.customHardwareOptions[this.customProfileFields.processor] ?? null
    }

    get customCanPinOptions(): string[] {
        return this.customProfileFields.communication === 'usb_to_canbus_bridge'
            ? this.customProcessor?.bridge_can ?? [] : this.customProcessor?.can ?? []
    }

    get customCanPinChoices(): Array<{ label: string; value: string }> {
        return this.customCanPinOptions.map((pair) => ({ label: `CAN bus (on ${pair})`, value: pair }))
    }

    get customCommunicationChoices(): Array<{ label: string; value: string }> {
        const options = this.customProcessor
        if (!options) return []
        const rp2040 = options.architecture === 'rp2040'
        return [
            ...(options.communications.includes('usb') ? [{ label: rp2040 ? 'USB' : 'USB (on PA11/PA12)', value: 'usb' }] : []),
            ...(options.communications.includes('canbus') ? options.can.map((pair) => ({
                label: `CAN bus (on ${pair})`, value: `canbus:${pair}`,
            })) : []),
            ...(options.communications.includes('usb_to_canbus_bridge') ? [{
                label: rp2040 ? 'USB to CAN bus bridge' : 'USB to CAN bus bridge (USB on PA11/PA12)',
                value: 'usb_to_canbus_bridge',
            }] : []),
        ]
    }

    get customCommunicationChoice(): string {
        return this.customProfileFields.communication === 'canbus'
            ? (this.customCanPins ? `canbus:${this.customCanPins}` : '')
            : this.customProfileFields.communication
    }

    set customCommunicationChoice(value: string) {
        const pair = value.startsWith('canbus:') ? value.slice('canbus:'.length) : ''
        const communication = pair ? 'canbus' : value
        if (this.customProfileFields.communication !== communication) {
            this.customProfileFields.communication = communication
            this.customCommunicationChanged()
        }
        if (pair) this.customCanPins = pair
    }

    get customKatapultCommunicationChoices(): Array<{ label: string; value: string }> {
        const options = this.customProcessor
        if (!options) return []
        if (options.architecture === 'rp2040') return [{ label: 'USB', value: 'usb' }]
        return [
            { label: 'USB (on PA11/PA12)', value: 'usb' },
            ...options.katapult_can.map((pair) => ({ label: `CAN bus (on ${pair})`, value: `canbus:${pair}` })),
        ]
    }

    get customKatapultCommunicationChoice(): string {
        return this.customProfileFields.bootloader_communication === 'canbus'
            ? (this.customKatapultCanPins ? `canbus:${this.customKatapultCanPins}` : '')
            : this.customProfileFields.bootloader_communication
    }

    set customKatapultCommunicationChoice(value: string) {
        const pair = value.startsWith('canbus:') ? value.slice('canbus:'.length) : ''
        const communication = pair ? 'canbus' : value
        if (this.customProfileFields.bootloader_communication !== communication) {
            this.customProfileFields.bootloader_communication = communication
            this.customKatapultCommunicationChanged()
        }
        if (pair) this.customKatapultCanPins = pair
    }

    get customBootloaderOffsets(): string[] {
        return this.customProfileFields.use_katapult
            ? this.customProcessor?.katapult_offsets ?? [] : this.customProcessor?.offsets ?? []
    }

    get customCanPins(): string {
        return this.customProfileFields.can_rx_pin && this.customProfileFields.can_tx_pin
            ? `${this.customProfileFields.can_rx_pin}/${this.customProfileFields.can_tx_pin}` : ''
    }

    set customCanPins(value: string) {
        const [rx, tx] = value ? value.split('/') : ['', '']
        this.customProfileFields.can_rx_pin = rx
        this.customProfileFields.can_tx_pin = tx
    }

    get customKatapultCanPins(): string {
        return this.customProfileFields.bootloader_can_rx_pin && this.customProfileFields.bootloader_can_tx_pin
            ? `${this.customProfileFields.bootloader_can_rx_pin}/${this.customProfileFields.bootloader_can_tx_pin}` : ''
    }

    set customKatapultCanPins(value: string) {
        const [rx, tx] = value ? value.split('/') : ['', '']
        this.customProfileFields.bootloader_can_rx_pin = rx
        this.customProfileFields.bootloader_can_tx_pin = tx
    }

    get customFlashMethods(): string[] {
        return this.customProfileFields.transport === 'usb'
            ? ['klipper_make_flash_usb', 'usb_make_flash', 'pending_verified_profile']
            : ['can_katapult', 'usb_katapult_or_make_flash', 'pending_verified_profile']
    }

    customArchitectureChanged() {
        this.customProfileFields.processor = ''
        this.customProfileFields.chip = ''
        this.customProfileFields.clock_reference = ''
        this.customProfileFields.bootloader_clock_reference = ''
        this.customProfileFields.bootloader_offset = ''
        this.customProfileFields.communication = 'usb'
        this.customProfileFields.bootloader_communication = 'usb'
        this.customProfileFields.usb_pins = this.customProfileFields.architecture === 'stm32' ? 'PA11/PA12' : ''
        this.customProfileFields.bootloader_usb_pins = this.customProfileFields.usb_pins
        this.customCommunicationChanged()
        this.customProfileFields.initial_flash_method = this.customProfileFields.architecture === 'rp2040'
            ? 'rp2040_bootsel_make_flash' : 'dfu_util'
    }

    customProcessorChanged() {
        const options = this.customProcessor
        if (!options) return
        this.customProfileFields.chip = options.chip
        if (!this.customBootloaderOffsets.includes(this.customProfileFields.bootloader_offset))
            this.customProfileFields.bootloader_offset = this.customBootloaderOffsets[0] ?? ''
        if (!options.clock_references.includes(this.customProfileFields.clock_reference))
            this.customProfileFields.clock_reference = options.clock_references[0] ?? ''
        if (!options.clock_references.includes(this.customProfileFields.bootloader_clock_reference))
            this.customProfileFields.bootloader_clock_reference = options.clock_references[0] ?? ''
        if (!options.communications.includes(this.customProfileFields.communication))
            this.customProfileFields.communication = options.communications[0]
        if (options.architecture === 'rp2040') this.customProfileFields.bootloader_communication = 'usb'
        if (!options.katapult_can.includes(this.customKatapultCanPins)) this.customKatapultCanPins = ''
        this.customCommunicationChanged()
        this.customOffsetChanged()
    }

    customCommunicationChanged() {
        const fields = this.customProfileFields
        fields.transport = fields.communication === 'usb' ? 'usb' : 'can'
        fields.flash_method = fields.transport === 'usb' ? 'klipper_make_flash_usb' : 'can_katapult'
        if (!this.customCanPinOptions.includes(this.customCanPins)) this.customCanPins = ''
        if (fields.communication !== 'canbus') fields.usb_pins = fields.architecture === 'stm32' ? 'PA11/PA12' : ''
    }

    customOffsetChanged() {
        this.customProfileFields.application_start_offset = this.customProfileFields.bootloader_offset
        if (this.customProfileFields.bootloader_offset === 'No bootloader')
            this.customProfileFields.use_katapult = false
    }

    customKatapultChanged() {
        if (!this.customBootloaderOffsets.includes(this.customProfileFields.bootloader_offset))
            this.customProfileFields.bootloader_offset = this.customBootloaderOffsets[0] ?? ''
        this.customOffsetChanged()
    }

    customKatapultCommunicationChanged() {
        if (this.customProfileFields.bootloader_communication === 'usb')
            this.customProfileFields.bootloader_usb_pins = 'PA11/PA12'
        else if (!this.customProcessor?.katapult_can.includes(this.customKatapultCanPins)) this.customKatapultCanPins = ''
    }

    get versionOptions(): FirmwareVersionOption[] {
        return (this.status?.firmware_source?.repositories?.version_options ?? []).filter((option) => {
            return option.available !== false || option.value !== 'previous_artifact'
        })
    }

    get updateAvailable(): boolean {
        return this.status?.firmware_source?.repositories?.update_available ?? false
    }

    get firmwareSummary(): string {
        if (!this.versionOptions.length) return this.$t('Machine.McuUpdateManagerPanel.NoVersions').toString()
        if (this.updateAvailable) return this.$t('Machine.McuUpdateManagerPanel.UpdateAvailable').toString()

        return this.$t('Machine.McuUpdateManagerPanel.UpToDate').toString()
    }

    get scanSummary(): Array<{ color: string; label: string; value: number }> {
        const summary = this.status?.summary
        return [
            {
                color: 'info',
                label: this.$t('Machine.McuUpdateManagerPanel.SummaryFound').toString(),
                value: summary?.total ?? 0,
            },
            {
                color: 'success',
                label: this.$t('Machine.McuUpdateManagerPanel.SummaryReady').toString(),
                value: summary?.ready ?? 0,
            },
            {
                color: 'warning',
                label: this.$t('Machine.McuUpdateManagerPanel.SummaryConfirmation').toString(),
                value: summary?.needs_confirmation ?? 0,
            },
            { color: 'warning', label: 'DFU', value: summary?.dfu ?? 0 },
            {
                color: 'error',
                label: this.$t('Machine.McuUpdateManagerPanel.SummaryIssues').toString(),
                value: summary?.issues ?? 0,
            },
        ]
    }

    get lastScanText(): string {
        const value = this.status?.discovery?.scanned_at
        if (!value) return this.$t('Machine.McuUpdateManagerPanel.NeverScanned').toString()
        return this.$t('Machine.McuUpdateManagerPanel.LastScan', {
            time: new Date(value).toLocaleString(),
        }).toString()
    }

    get scanPhaseLabel(): string {
        const keys = ['ScanPrinterCfg', 'ScanUsb', 'ScanDfu', 'ScanCan', 'ScanRuntime']
        return this.$t(
            `Machine.McuUpdateManagerPanel.${keys[Math.min(this.scanPhaseIndex, keys.length - 1)]}`
        ).toString()
    }

    get canSwitchFirmwareRef(): boolean {
        return !!this.selectedFirmwareRef && !['current', 'previous_artifact'].includes(this.selectedFirmwareRef)
    }

    transportColor(transport?: string): string {
        if ((transport ?? '').toLowerCase() === 'can') return 'primary'
        if ((transport ?? '').toLowerCase() === 'usb') return 'info'
        if ((transport ?? '').toLowerCase() === 'dfu') return 'warning'

        return 'grey'
    }

    confirmationLabel(device: McuUpdateManagerDevice): string {
        if (device.confirmation_status === 'confirmed')
            return this.$t('Machine.McuUpdateManagerPanel.Confirmed').toString()

        return this.$t('Machine.McuUpdateManagerPanel.NeedsConfirmation').toString()
    }

    isDeviceExpanded(device: McuUpdateManagerDevice): boolean {
        return !!this.expandedDeviceIds[device.id] || this.busyAction.startsWith(`${device.id}:`)
    }

    toggleDeviceExpanded(device: McuUpdateManagerDevice) {
        this.$set(this.expandedDeviceIds, device.id, !this.isDeviceExpanded(device))
    }

    isDeviceLogExpanded(device: McuUpdateManagerDevice): boolean {
        return !!this.expandedDeviceLogIds[device.id]
    }

    shouldShowDeviceLog(device: McuUpdateManagerDevice): boolean {
        return this.isDeviceLogExpanded(device) || this.busyAction.startsWith(`${device.id}:`)
    }

    toggleDeviceLog(device: McuUpdateManagerDevice) {
        this.$set(this.expandedDeviceLogIds, device.id, !this.isDeviceLogExpanded(device))
    }

    deviceProfileName(device: McuUpdateManagerDevice): string {
        if (device.confirmed_profile) return device.confirmed_profile
        if (this.isDfuDevice(device)) return ''

        return this.recommendedProfile(device)?.name ?? this.recommendedProfile(device)?.id ?? ''
    }

    recommendedProfile(device: McuUpdateManagerDevice): McuUpdateManagerProfileMatch | null {
        if (this.isDfuDevice(device)) return null
        return device.likely_profiles?.[0] ?? null
    }

    profileConfidence(device: McuUpdateManagerDevice): { color: string; label: string } | null {
        const profile = this.recommendedProfile(device)
        if (!profile?.score) return null
        if (profile.score >= 90)
            return { color: 'success', label: this.$t('Machine.McuUpdateManagerPanel.ConfidenceHigh').toString() }
        if (profile.score >= 60)
            return { color: 'warning', label: this.$t('Machine.McuUpdateManagerPanel.ConfidenceMedium').toString() }
        return { color: 'error', label: this.$t('Machine.McuUpdateManagerPanel.ConfidenceLow').toString() }
    }

    profileReasonLabel(reason: string): string {
        const keys: Record<string, string> = {
            chip: 'ReasonChip',
            transport: 'ReasonTransport',
            mcu_name_hint: 'ReasonName',
            printer_cfg_pins: 'ReasonPins',
            custom_fallback: 'ReasonFallback',
            user_selection_required: 'ReasonManual',
        }
        return this.$t(`Machine.McuUpdateManagerPanel.${keys[reason] ?? 'ReasonOther'}`).toString()
    }

    artifactHistoryOptions(device: McuUpdateManagerDevice): Array<{ disabled: boolean; text: string; value: string }> {
        return (device.artifact_history ?? []).map((artifact) => ({
            disabled: artifact.exists === false || !artifact.ref,
            text: `${artifact.kind === 'last_good' ? this.$t('Machine.McuUpdateManagerPanel.LastKnownGood') : artifact.label}${
                artifact.created_at ? ` - ${new Date(artifact.created_at).toLocaleString()}` : ''
            }`,
            value: artifact.ref ?? '',
        }))
    }

    selectedArtifactRef(device: McuUpdateManagerDevice): string {
        return this.selectedArtifactRefs[device.id] ?? ''
    }

    setSelectedArtifactRef(device: McuUpdateManagerDevice, value: string) {
        this.$set(this.selectedArtifactRefs, device.id, value)
    }

    async flashSelectedArtifact(device: McuUpdateManagerDevice) {
        const firmwareRef = this.selectedArtifactRef(device)
        if (!firmwareRef) return
        if (!window.confirm(this.flashConfirmationText(device, firmwareRef))) return
        await this.runRequest('flash', device, { firmware_ref: firmwareRef })
    }

    flashConfirmationText(device: McuUpdateManagerDevice, firmwareRef = this.selectedFirmwareRef): string {
        const artifact = (device.artifact_history ?? []).find((item) => item.ref === firmwareRef)
        return this.$t('Machine.McuUpdateManagerPanel.FlashSummary', {
            artifact: artifact?.label ?? device.firmware_state?.artifact_version ?? firmwareRef,
            current: device.firmware_version ?? this.$t('Machine.McuUpdateManagerPanel.UnknownVersion'),
            identity: device.canbus_uuid ?? device.serial ?? device.display_id ?? '-',
            name: device.name ?? device.id,
            profile: device.confirmed_profile ?? this.$t('Machine.McuUpdateManagerPanel.UnknownProfile'),
            transport: (device.transport ?? 'unknown').toUpperCase(),
        }).toString()
    }

    async copyPrinterCfg(device: McuUpdateManagerDevice) {
        if (!device.printer_cfg_snippet) return
        if (navigator.clipboard && window.isSecureContext) {
            await navigator.clipboard.writeText(device.printer_cfg_snippet)
        } else {
            const textarea = document.createElement('textarea')
            textarea.value = device.printer_cfg_snippet
            textarea.style.position = 'fixed'
            textarea.style.opacity = '0'
            document.body.appendChild(textarea)
            textarea.select()
            document.execCommand('copy')
            textarea.remove()
        }
        this.$toast.success(this.$t('Machine.McuUpdateManagerPanel.ConfigCopied').toString())
    }

    async downloadDeviceLog(device: McuUpdateManagerDevice) {
        try {
            const query = new URLSearchParams({ device_id: device.id })
            const result = await this.fetchApi<{ content: string; filename: string }>(
                `/machine/mcu_update_manager/log?${query.toString()}`
            )
            const url = URL.createObjectURL(new Blob([result.content], { type: 'text/plain;charset=utf-8' }))
            const link = document.createElement('a')
            link.href = url
            link.download = `${device.id}-${result.filename}`
            link.click()
            URL.revokeObjectURL(url)
        } catch (error) {
            this.errorMessage = error instanceof Error ? error.message : String(error)
        }
    }

    retryRecovery(device: McuUpdateManagerDevice) {
        if (device.artifact?.exists && device.actions?.can_flash) return this.flashDevice(device)
        return this.runDeviceAction('build', device)
    }

    recoveryText(device: McuUpdateManagerDevice): string {
        const key =
            device.recovery?.mode === 'dfu'
                ? 'RecoveryDfu'
                : device.recovery?.mode === 'usb'
                  ? 'RecoveryUsb'
                  : 'RecoveryCan'
        return this.$t(`Machine.McuUpdateManagerPanel.${key}`).toString()
    }

    dfuGuideSteps(device: McuUpdateManagerDevice): Array<{ key: string; label: string; state: string }> {
        const profileSelected = !!this.selectedHardwareProfile(device)
        const profileConfirmed = profileSelected && this.selectedHardwareProfile(device) === device.confirmed_profile
        const targetSelected = !!this.selectedDfuTarget(device)
        return [
            {
                key: 'detect',
                label: this.$t('Machine.McuUpdateManagerPanel.DfuStepDetected').toString(),
                state: 'done',
            },
            {
                key: 'profile',
                label: this.$t('Machine.McuUpdateManagerPanel.DfuStepProfile').toString(),
                state: profileConfirmed ? 'done' : profileSelected ? 'active' : 'pending',
            },
            {
                key: 'target',
                label: this.$t('Machine.McuUpdateManagerPanel.DfuStepTarget').toString(),
                state: targetSelected ? 'done' : profileConfirmed ? 'active' : 'pending',
            },
            {
                key: 'flash',
                label: this.$t('Machine.McuUpdateManagerPanel.DfuStepFlash').toString(),
                state: busyKey(this.busyAction, device.id, 'dfu_flash')
                    ? 'running'
                    : targetSelected
                      ? 'active'
                      : 'pending',
            },
        ]
    }

    canSelectHardwareProfile(device: McuUpdateManagerDevice): boolean {
        return !this.isCartographer(device) && this.hardwareProfileOptions(device).length > 0
    }

    hardwareProfileOptions(device: McuUpdateManagerDevice): Array<{ label: string; value: string }> {
        const scores = new Map((device.likely_profiles ?? []).map((item) => [item.id, item.score]))
        return (this.status?.profile_catalog ?? [])
            .filter((profile) => !this.isDfuDevice(device) || profile.supports_dfu)
            .filter((profile) => !device.detected_chip || !profile.chips?.length ||
                profile.chips.some((chip) => chip.toLowerCase() === device.detected_chip?.toLowerCase()))
            .filter((profile) => this.isDfuDevice(device) || !device.transport || !profile.transports?.length ||
                profile.transports.includes(device.transport))
            .map((profile) => ({
                label: `${profile.custom ? 'Custom | ' : ''}${profile.name ?? profile.id}${profile.verification?.ready === false ? ' (unverified)' : ''}${scores.get(profile.id) ? ` (${scores.get(profile.id)})` : ''}`,
                value: profile.id,
            }))
    }

    selectedCatalogProfile(device: McuUpdateManagerDevice) {
        return this.status?.profile_catalog?.find((item) => item.id === this.selectedHardwareProfile(device))
    }

    profileSettingsRows(config?: Record<string, string | number | boolean>): Array<{ label: string; value: string }> {
        const fields: Array<[string, string]> = [
            ['architecture', 'Architecture'], ['processor', 'Processor'],
            ['clock_reference', 'Clock reference'], ['bootloader_offset', 'Bootloader offset'],
            ['application_start_offset', 'Application offset'], ['communication', 'Communication'],
            ['usb_pins', 'USB pins'], ['can_bitrate', 'CAN bitrate'],
            ['gpio_pins_on_startup', 'Startup GPIO'], ['status_led_pin', 'Status LED'],
            ['support_double_click_reset', 'Double-click reset'],
        ]
        if (!config || !Object.keys(config).length) return [{ label: 'Configuration', value: 'Not specified' }]
        const rows = fields.filter(([key]) => config[key] !== undefined && config[key] !== '')
            .map(([key, label]) => ({ label, value: String(config[key]) }))
        const rx = config.can_rx_pin ?? config.can_rx_gpio
        const tx = config.can_tx_pin ?? config.can_tx_gpio
        if (rx && tx) {
            const index = rows.findIndex((row) => row.label === 'USB pins')
            rows.splice(index < 0 ? rows.length : index, 0, {
                label: 'CAN bus interface (RX/TX)', value: `${rx}/${tx}`,
            })
        }
        return rows
    }

    selectedProfileIsCustom(device: McuUpdateManagerDevice): boolean {
        return !!this.status?.profile_catalog?.find(
            (item) => item.id === this.selectedHardwareProfile(device)
        )?.custom
    }

    async openCustomProfile(profileId = '', edit = false, deviceId = '') {
        this.customProfileDialog = true
        this.customProfileError = ''
        this.customProfileDeviceId = deviceId
        this.editingCustomProfileId = edit ? profileId : ''
        this.customProfileTemplateId = edit ? '' : profileId
        this.customProfileFields = emptyCustomProfile()
        this.customHardwareOptions = {}
        try {
            const options = await this.fetchApi<{ processors: Record<string, CustomProcessorOptions> }>(
                '/machine/mcu_update_manager/profile/options'
            )
            this.customHardwareOptions = options.processors
        } catch (error) {
            this.customProfileError = this.formatError(error)
            return
        }
        if (profileId) await this.loadCustomProfileTemplate(profileId)
    }

    async loadCustomProfileTemplate(profileId: string) {
        const requestId = ++this.customProfileTemplateRequestId
        if (!profileId) {
            this.customProfileFields = emptyCustomProfile()
            return
        }
        try {
            const detail = await this.fetchApi<{ fields: Partial<CustomProfileFields> }>(
                `/machine/mcu_update_manager/profile?profile_id=${encodeURIComponent(profileId)}`
            )
            if (requestId !== this.customProfileTemplateRequestId) return
            this.customProfileFields = {
                ...emptyCustomProfile(), ...detail.fields,
                name: this.editingCustomProfileId ? detail.fields.name ?? '' : `${detail.fields.name ?? profileId} Custom`,
            }
            this.customProfileError = ''
        } catch (error) {
            this.customProfileError = this.formatError(error)
            this.customProfileFields = emptyCustomProfile()
        }
    }

    async saveCustomProfile() {
        this.customProfileSaving = true
        this.customProfileError = ''
        try {
            const result = await this.fetchApi<{ profile: { id: string } }>(
                '/machine/mcu_update_manager/profile/save', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        fields_json: JSON.stringify(this.customProfileFields),
                        template_id: this.customProfileTemplateId,
                        profile_id: this.editingCustomProfileId,
                    }),
                }
            )
            this.customProfileDialog = false
            await this.refresh()
            if (this.customProfileDeviceId) {
                this.$set(this.selectedHardwareProfileIds, this.customProfileDeviceId, result.profile.id)
            }
        } catch (error) {
            this.customProfileError = this.formatError(error)
        } finally {
            this.customProfileSaving = false
        }
    }

    selectedHardwareProfile(device: McuUpdateManagerDevice): string {
        if (this.isDfuDevice(device)) {
            return this.selectedHardwareProfileIds[device.id] ?? device.confirmed_profile ?? ''
        }
        return (
            this.selectedHardwareProfileIds[device.id] ??
            device.confirmed_profile ??
            this.recommendedProfile(device)?.id ??
            ''
        )
    }

    setSelectedHardwareProfile(device: McuUpdateManagerDevice, profileId: string) {
        this.$set(this.selectedHardwareProfileIds, device.id, profileId)
        if (this.isDfuDevice(device)) this.$delete(this.selectedDfuTargetKeys, device.id)
    }

    dfuTargetOptions(device: McuUpdateManagerDevice): Array<{ label: string; value: string }> {
        const profileId = this.selectedHardwareProfile(device)
        const profile = this.status?.profile_catalog?.find((item) => item.id === profileId)
        return (profile?.targets ?? []).map((target) => ({
            label: target.label,
            value: `${target.kind}:${target.communication}`,
        }))
    }

    selectedDfuTargetKey(device: McuUpdateManagerDevice): string {
        const selected = this.selectedDfuTargetKeys[device.id]
        if (selected) return selected

        const options = this.dfuTargetOptions(device)
        return options.length === 1 ? options[0].value : ''
    }

    setDfuTarget(device: McuUpdateManagerDevice, value: string) {
        this.$set(this.selectedDfuTargetKeys, device.id, value)
    }

    selectedDfuTarget(device: McuUpdateManagerDevice): { communication: string; kind: 'katapult' | 'klipper' } | null {
        const value = this.selectedDfuTargetKey(device)
        if (!value) return null
        const [kind, communication] = value.split(':')
        if (!communication || !['katapult', 'klipper'].includes(kind)) return null
        return { communication, kind: kind as 'katapult' | 'klipper' }
    }

    canFlashDfu(device: McuUpdateManagerDevice): boolean {
        return (
            !!device.confirmed_profile &&
            device.confirmed_profile === this.selectedHardwareProfile(device) &&
            !!this.selectedDfuTarget(device)
        )
    }

    async flashDfu(device: McuUpdateManagerDevice) {
        const target = this.selectedDfuTarget(device)
        if (!target || !device.confirmed_profile) return
        const confirmed = window.confirm(
            this.$t('Machine.McuUpdateManagerPanel.DfuFlashQuestion', {
                target: this.dfuTargetOptions(device).find(
                    (item) => item.value === `${target.kind}:${target.communication}`
                )?.label,
            }).toString()
        )
        if (!confirmed) return
        await this.runRequest('dfu_flash', device, {
            communication: target.communication,
            firmware_kind: target.kind,
            profile_id: device.confirmed_profile,
        })
    }

    recommendationText(device: McuUpdateManagerDevice): string {
        const profile = this.recommendedProfile(device)
        const profileName = profile?.name ?? profile?.id ?? this.$t('Machine.McuUpdateManagerPanel.UnknownProfile')

        return this.$t('Machine.McuUpdateManagerPanel.RecommendProfile', { profile: profileName }).toString()
    }

    artifactText(device: McuUpdateManagerDevice): string {
        const artifact = device.artifact
        const profile =
            artifact?.profile?.name ?? artifact?.profile?.id ?? this.$t('Machine.McuUpdateManagerPanel.UnknownProfile')
        const size = artifact?.size
            ? `${Math.round(artifact.size / 1024)} KiB`
            : this.$t('Machine.McuUpdateManagerPanel.UnknownSize')

        return this.$t('Machine.McuUpdateManagerPanel.ArtifactReady', { profile, size }).toString()
    }

    firmwareStateAlertType(device: McuUpdateManagerDevice): string {
        if (device.firmware_state?.already_matching) return 'success'
        if (device.firmware_state?.needs_build) return 'warning'
        if (device.firmware_state?.needs_flash) return 'info'

        return 'info'
    }

    firmwareStateText(device: McuUpdateManagerDevice): string {
        const state = device.firmware_state
        if (!state) return ''
        if (state.already_matching) {
            return this.$t('Machine.McuUpdateManagerPanel.FirmwareAlreadyMatching', {
                version: state.runtime_version ?? this.$t('Machine.McuUpdateManagerPanel.UnknownVersion'),
            }).toString()
        }
        if (state.needs_build) return this.$t('Machine.McuUpdateManagerPanel.FirmwareNeedsBuild').toString()
        if (state.needs_flash) {
            return this.$t('Machine.McuUpdateManagerPanel.FirmwareNeedsFlash', {
                runtime: state.runtime_version ?? this.$t('Machine.McuUpdateManagerPanel.UnknownVersion'),
                artifact: state.artifact_version ?? this.$t('Machine.McuUpdateManagerPanel.UnknownVersion'),
            }).toString()
        }
        if (state.artifact_ready) return this.$t('Machine.McuUpdateManagerPanel.FirmwareArtifactReady').toString()

        return this.$t('Machine.McuUpdateManagerPanel.FirmwareStateUnknown').toString()
    }

    isCartographer(device: McuUpdateManagerDevice): boolean {
        return device.vendor_firmware?.manager === 'cartographer'
    }

    isDfuDevice(device: McuUpdateManagerDevice): boolean {
        return (device.transport ?? '').toLowerCase() === 'dfu'
    }

    isCartographerUsb(device: McuUpdateManagerDevice): boolean {
        return this.isCartographer(device) && (device.transport ?? '').toLowerCase() === 'usb'
    }

    isCartographerCan(device: McuUpdateManagerDevice): boolean {
        return this.isCartographer(device) && (device.transport ?? '').toLowerCase() === 'can'
    }

    isCartographerPendingCan(device: McuUpdateManagerDevice): boolean {
        return this.isCartographer(device) && (device.transport ?? '').toLowerCase() === 'pending_can'
    }

    isCartographerDfu(device: McuUpdateManagerDevice): boolean {
        return this.isCartographer(device) && (device.transport ?? '').toLowerCase() === 'dfu'
    }

    isCartographerFlashing(device: McuUpdateManagerDevice): boolean {
        return (
            busyKey(this.busyAction, device.id, 'cartographer_usb_to_can') ||
            busyKey(this.busyAction, device.id, 'cartographer_flash') ||
            busyKey(this.busyAction, device.id, 'cartographer_dfu_flash')
        )
    }

    activeCartographerWorkflowSteps(
        device: McuUpdateManagerDevice
    ): Array<{ key: string; label: string; state: string }> {
        if (!this.isCartographerFlashing(device) && !this.isCartographerPendingCan(device)) return []

        return this.cartographerWorkflowSteps(device)
    }

    activeStandardWorkflowSteps(device: McuUpdateManagerDevice): Array<{ key: string; label: string; state: string }> {
        if (busyKey(this.busyAction, device.id, 'build'))
            return this.operationWorkflowSteps(device) || this.standardBuildWorkflowSteps()
        if (busyKey(this.busyAction, device.id, 'flash'))
            return this.operationWorkflowSteps(device) || this.standardFlashWorkflowSteps(device)
        if (busyKey(this.busyAction, device.id, 'verify')) return this.standardVerifyWorkflowSteps()

        return []
    }

    operationWorkflowSteps(
        device: McuUpdateManagerDevice
    ): Array<{ key: string; label: string; state: string }> | null {
        const steps = device.last_operation?.steps ?? []
        if (!steps.length) return null

        return steps.map((step) => ({
            key: step.id ?? step.label ?? 'step',
            label: step.label ?? step.id ?? 'Step',
            state: this.operationStepState(step.status),
        }))
    }

    operationStepState(status?: string): string {
        if (status === 'done') return 'done'
        if (status === 'running') return 'running'
        if (status === 'failed') return 'failed'

        return 'pending'
    }

    standardBuildWorkflowSteps(): Array<{ key: string; label: string; state: string }> {
        return [
            {
                key: 'prepare',
                label: this.$t('Machine.McuUpdateManagerPanel.StepBuildPrepare').toString(),
                state: 'running',
            },
            {
                key: 'checkout',
                label: this.$t('Machine.McuUpdateManagerPanel.StepBuildCheckout').toString(),
                state: 'pending',
            },
            {
                key: 'config',
                label: this.$t('Machine.McuUpdateManagerPanel.StepBuildConfig').toString(),
                state: 'pending',
            },
            {
                key: 'compile',
                label: this.$t('Machine.McuUpdateManagerPanel.StepBuildCompile').toString(),
                state: 'pending',
            },
            {
                key: 'artifact',
                label: this.$t('Machine.McuUpdateManagerPanel.StepBuildArtifact').toString(),
                state: 'pending',
            },
        ]
    }

    standardFlashWorkflowSteps(device: McuUpdateManagerDevice): Array<{ key: string; label: string; state: string }> {
        if (this.isUsbCanBridgeProfile(device)) {
            return [
                {
                    key: 'stop_klipper',
                    label: this.$t('Machine.McuUpdateManagerPanel.StepFlashStopKlipper').toString(),
                    state: 'running',
                },
                {
                    key: 'enter_katapult',
                    label: this.$t('Machine.McuUpdateManagerPanel.StepFlashEnterKatapult').toString(),
                    state: 'pending',
                },
                {
                    key: 'wait_usb_katapult',
                    label: this.$t('Machine.McuUpdateManagerPanel.StepFlashWaitUsbKatapult').toString(),
                    state: 'pending',
                },
                {
                    key: 'flash_firmware',
                    label: this.$t('Machine.McuUpdateManagerPanel.StepFlashUsbKatapultFirmware').toString(),
                    state: 'pending',
                },
                {
                    key: 'verify_klipper',
                    label: this.$t('Machine.McuUpdateManagerPanel.StepFlashVerifyCanUuid').toString(),
                    state: 'pending',
                },
                {
                    key: 'start_klipper',
                    label: this.$t('Machine.McuUpdateManagerPanel.StepFlashStartKlipper').toString(),
                    state: 'pending',
                },
            ]
        }

        return [
            {
                key: 'stop_klipper',
                label: this.$t('Machine.McuUpdateManagerPanel.StepFlashStopKlipper').toString(),
                state: 'running',
            },
            {
                key: 'enter_katapult',
                label: this.$t('Machine.McuUpdateManagerPanel.StepFlashEnterKatapult').toString(),
                state: 'pending',
            },
            {
                key: 'verify_katapult',
                label: this.$t('Machine.McuUpdateManagerPanel.StepFlashVerifyKatapult').toString(),
                state: 'pending',
            },
            {
                key: 'flash_firmware',
                label: this.$t('Machine.McuUpdateManagerPanel.StepFlashFirmware').toString(),
                state: 'pending',
            },
            {
                key: 'verify_klipper',
                label: this.$t('Machine.McuUpdateManagerPanel.StepFlashVerifyKlipper').toString(),
                state: 'pending',
            },
            {
                key: 'start_klipper',
                label: this.$t('Machine.McuUpdateManagerPanel.StepFlashStartKlipper').toString(),
                state: 'pending',
            },
        ]
    }

    isUsbCanBridgeProfile(device: McuUpdateManagerDevice): boolean {
        return (
            (device.confirmed_profile ?? '').includes('usb_can_bridge') ||
            (device.confirmed_profile ?? '').includes('stm32h723_can')
        )
    }

    standardVerifyWorkflowSteps(): Array<{ key: string; label: string; state: string }> {
        return [
            {
                key: 'query',
                label: this.$t('Machine.McuUpdateManagerPanel.StepVerifyQuery').toString(),
                state: 'running',
            },
            {
                key: 'compare',
                label: this.$t('Machine.McuUpdateManagerPanel.StepVerifyCompare').toString(),
                state: 'pending',
            },
        ]
    }

    cartographerWorkflowSteps(device: McuUpdateManagerDevice): Array<{ key: string; label: string; state: string }> {
        const usb = this.isCartographerUsb(device)
        const can = this.isCartographerCan(device)
        const pendingCan = this.isCartographerPendingCan(device)
        const runningUsb = busyKey(this.busyAction, device.id, 'cartographer_usb_to_can')
        const runningFlash = busyKey(this.busyAction, device.id, 'cartographer_flash')

        return [
            {
                key: 'usb_deployer',
                label: this.$t('Machine.McuUpdateManagerPanel.CartographerStepUsbDeployer').toString(),
                state: can || pendingCan ? 'done' : runningUsb ? 'running' : usb ? 'active' : 'pending',
            },
            {
                key: 'reconnect_can',
                label: this.$t('Machine.McuUpdateManagerPanel.CartographerStepReconnectCan').toString(),
                state:
                    can || (pendingCan && device.canbus_uuid)
                        ? 'done'
                        : pendingCan
                          ? 'active'
                          : usb
                            ? 'pending'
                            : 'active',
            },
            {
                key: 'confirm_can',
                label: this.$t('Machine.McuUpdateManagerPanel.CartographerStepConfirmCan').toString(),
                state:
                    can && device.confirmation_status === 'confirmed'
                        ? 'done'
                        : pendingCan && device.canbus_uuid
                          ? 'done'
                          : can
                            ? 'active'
                            : 'pending',
            },
            {
                key: 'flash_can',
                label: this.$t('Machine.McuUpdateManagerPanel.CartographerStepFlashCan').toString(),
                state:
                    runningFlash || runningUsb
                        ? 'running'
                        : pendingCan && device.canbus_uuid
                          ? 'active'
                          : can && device.confirmation_status === 'confirmed'
                            ? 'active'
                            : 'pending',
            },
        ]
    }

    workflowStepIcon(state: string): string {
        if (state === 'done') return this.mdiCheckCircle
        if (state === 'running') return this.mdiProgressClock

        return this.mdiCheckboxBlankCircleOutline
    }

    workflowStepColor(state: string): string {
        if (state === 'done') return 'green'
        if (state === 'failed') return 'error'
        if (state === 'running' || state === 'active') return 'warning'

        return 'grey'
    }

    cartographerVersionOptions(device: McuUpdateManagerDevice): FirmwareVersionOption[] {
        return (device.vendor_firmware?.available_versions ?? []).flatMap((option) => {
            const version = option.version ?? ''
            const firmware = option.firmware ?? {}
            const variants: Array<'full' | 'lite'> = []
            if (firmware.full) variants.push('full')
            if (firmware.lite) variants.push('lite')

            return variants.map((flavour) => ({
                flavour,
                kind: 'cartographer',
                label:
                    flavour === 'lite'
                        ? this.$t('Machine.McuUpdateManagerPanel.CartographerFirmwareLiteLabel', { version }).toString()
                        : (option.label ?? `Cartographer ${version}`),
                value: this.cartographerFirmwareKey(version, flavour),
            }))
        })
    }

    cartographerFirmwareKey(version: string, flavour: 'full' | 'lite' = 'full'): string {
        return `${version}:${flavour}`
    }

    parseCartographerFirmwareKey(value: string): { version: string; flavour: 'full' | 'lite' } {
        const [version, rawFlavour] = value.split(':')

        return { version, flavour: rawFlavour === 'lite' ? 'lite' : 'full' }
    }

    setCartographerFirmwareKey(device: McuUpdateManagerDevice, value: string) {
        this.$set(this.selectedCartographerFirmwareKeys, device.id, value)
    }

    selectedCartographerVersion(device: McuUpdateManagerDevice): string {
        return this.selectedCartographerFirmware(device).version
    }

    selectedCartographerFlavour(device: McuUpdateManagerDevice): 'full' | 'lite' {
        return this.selectedCartographerFirmware(device).flavour
    }

    selectedCartographerFirmware(device: McuUpdateManagerDevice): { version: string; flavour: 'full' | 'lite' } {
        const selectedKey = this.selectedCartographerFirmwareKeys[device.id]
        if (selectedKey) return this.parseCartographerFirmwareKey(selectedKey)

        const version = device.vendor_firmware?.selected?.version ?? ''
        const flavour = this.cartographerOptionHasFlavour(device, version, 'full') ? 'full' : 'lite'

        return { version, flavour }
    }

    cartographerOptionHasFlavour(device: McuUpdateManagerDevice, version: string, flavour: 'full' | 'lite'): boolean {
        const option = device.vendor_firmware?.available_versions?.find((item) => item.version === version)

        return !!option?.firmware?.[flavour]
    }

    canFlashDevice(device: McuUpdateManagerDevice): boolean {
        if (this.isCartographer(device)) {
            if (this.isCartographerUsb(device)) return false

            return !!device.actions?.vendor_flash_ready && !!this.selectedCartographerVersion(device)
        }

        return !!device.actions?.can_flash
    }

    async flashDevice(device: McuUpdateManagerDevice) {
        if (this.isCartographer(device)) {
            await this.runRequest('cartographer_flash', device, {
                firmware_version: this.selectedCartographerVersion(device),
                flavour: this.selectedCartographerFlavour(device),
            })
            return
        }

        if (!window.confirm(this.flashConfirmationText(device))) return
        await this.runDeviceAction('flash', device)
    }

    async updateCartographerUsbFirmware(device: McuUpdateManagerDevice) {
        const confirmed = window.confirm(
            this.$t('Machine.McuUpdateManagerPanel.CartographerUsbUpdateQuestion', {
                version: this.selectedCartographerVersion(device),
            }).toString()
        )
        if (!confirmed) return

        await this.runRequest('cartographer_flash', device, {
            firmware_version: this.selectedCartographerVersion(device),
            flavour: this.selectedCartographerFlavour(device),
        })
    }

    async switchCartographerUsbToCan(device: McuUpdateManagerDevice) {
        const confirmed = window.confirm(
            this.$t('Machine.McuUpdateManagerPanel.CartographerUsbToCanQuestion').toString()
        )
        if (!confirmed) return

        await this.runRequest('cartographer_usb_to_can', device, {
            phase: 'deploy_katapult',
            device_serial: device.serial,
        })
    }

    async flashCartographerUsbToCanFirmware(device: McuUpdateManagerDevice) {
        const confirmed = window.confirm(
            this.$t('Machine.McuUpdateManagerPanel.CartographerFlashCanAfterReconnectQuestion', {
                uuid: device.canbus_uuid,
                version: this.selectedCartographerVersion(device),
            }).toString()
        )
        if (!confirmed) return

        await this.runRequest('cartographer_usb_to_can', device, {
            phase: 'flash_can',
            canbus_uuid: device.canbus_uuid,
            firmware_version: this.selectedCartographerVersion(device),
            flavour: this.selectedCartographerFlavour(device),
        })
    }

    cartographerDfuVersion(device: McuUpdateManagerDevice, targetProtocol: 'USB' | 'CAN'): string {
        return device.vendor_firmware?.dfu_available_versions?.[targetProtocol]?.[0]?.version ?? ''
    }

    cartographerCanToUsbVersion(device: McuUpdateManagerDevice): string {
        const available = device.vendor_firmware?.can_to_usb?.available_versions ?? []
        const selected = this.selectedCartographerVersion(device)
        const exact = available.find((option) => option.version === selected)

        return exact?.version ?? available[0]?.version ?? ''
    }

    async flashCartographerCanToUsb(device: McuUpdateManagerDevice) {
        const version = this.cartographerCanToUsbVersion(device)
        const confirmed = window.confirm(
            this.$t('Machine.McuUpdateManagerPanel.CartographerCanToUsbQuestion', {
                version,
                uuid: device.canbus_uuid,
            }).toString()
        )
        if (!confirmed) return

        await this.runRequest('cartographer_flash', device, {
            firmware_version: version,
            flavour: 'full',
            target_protocol: 'USB',
        })
    }

    async flashCartographerDfu(device: McuUpdateManagerDevice, targetProtocol: 'USB' | 'CAN') {
        const version = this.cartographerDfuVersion(device, targetProtocol)
        const confirmed = window.confirm(
            this.$t('Machine.McuUpdateManagerPanel.CartographerDfuQuestion', {
                target: targetProtocol,
                version,
            }).toString()
        )
        if (!confirmed) return

        await this.runRequest('cartographer_dfu_flash', device, {
            firmware_version: version,
            target_protocol: targetProtocol,
            probe_version: device.vendor_firmware?.probe_version ?? 'v4',
            flavour: 'full',
        })
    }

    refresh(checkUpdates = false): Promise<void> {
        if (this.refreshPromise) return this.refreshPromise

        this.refreshPromise = this.performRefresh(checkUpdates)
        return this.refreshPromise
    }

    async performRefresh(checkUpdates = false) {
        this.loading = true
        this.startScanProgress()

        try {
            const path = '/machine/mcu_update_manager/status' + (checkUpdates ? '?refresh_repositories=true' : '')
            const status = await this.fetchApi<McuUpdateManagerStatus>(path)
            this.status = status
            this.errorMessage = ''
            this.selectedFirmwareRef = status.firmware_source?.ref ?? this.selectedFirmwareRef
            this.syncSelectedCartographerVersions()
            // A refresh triggered by a completed operation must not await its own poll.
            if (!this.operationRefreshPromise) await this.restoreCachedOperation()
        } catch (error) {
            this.errorMessage = this.formatError(error)
        } finally {
            this.loading = false
            this.loaded = true
            this.refreshPromise = null
            this.stopScanProgress()
        }
    }

    startScanProgress() {
        this.stopScanProgress()
        this.scanPhaseIndex = 0
        this.scanPhaseTimer = window.setInterval(() => {
            if (this.scanPhaseIndex < 4) this.scanPhaseIndex += 1
        }, 650)
    }

    stopScanProgress() {
        if (this.scanPhaseTimer !== null) window.clearInterval(this.scanPhaseTimer)
        this.scanPhaseTimer = null
    }

    async confirmDevice(device: McuUpdateManagerDevice) {
        const profileId = this.selectedHardwareProfile(device)
        if (!profileId) return

        await this.runRequest('confirm', device, { profile_id: profileId })
    }

    async switchFirmwareRef() {
        const option = this.versionOptions.find((item) => item.value === this.selectedFirmwareRef)
        const label = option?.label ?? this.selectedFirmwareRef
        const confirmed = window.confirm(
            this.$t('Machine.McuUpdateManagerPanel.SwitchFirmwareVersionQuestion', { version: label }).toString()
        )
        if (!confirmed) return

        this.busy = true
        this.busyAction = 'firmware:switch'
        this.errorMessage = ''
        let backgroundJobStarted = false

        try {
            const result = await this.fetchApi<{ job_id?: string; status?: string; message?: string }>(
                '/machine/mcu_update_manager/switch_firmware_ref',
                {
                    body: JSON.stringify({
                        firmware_ref: this.selectedFirmwareRef,
                    }),
                    headers: { 'Content-Type': 'application/json' },
                    method: 'POST',
                }
            )
            if (result.status === 'blocked')
                throw new Error(result.message || this.$t('Machine.McuUpdateManagerPanel.RequestFailed').toString())
            if (result.job_id) {
                backgroundJobStarted = true
                this.startProgressRefresh()
                await this.refreshOperation()
                return
            }

            this.$toast.success(
                this.$t('Machine.McuUpdateManagerPanel.FirmwareVersionSwitched', { version: label }).toString()
            )
            await this.refresh()
        } catch (error) {
            this.errorMessage = this.formatError(error)
        } finally {
            if (!backgroundJobStarted) {
                this.busy = false
                this.busyAction = ''
            }
        }
    }

    async runDeviceAction(action: 'build' | 'flash' | 'verify', device: McuUpdateManagerDevice) {
        await this.runRequest(action, device, { firmware_ref: this.selectedFirmwareRef })
    }

    async runRequest(
        action:
            | 'confirm'
            | 'build'
            | 'flash'
            | 'verify'
            | 'cartographer_flash'
            | 'cartographer_usb_to_can'
            | 'cartographer_dfu_flash'
            | 'dfu_flash',
        device: McuUpdateManagerDevice,
        extra: Record<string, unknown> = {}
    ) {
        let backgroundJobStarted = false
        this.busy = true
        this.busyAction = `${device.id}:${action}`
        this.errorMessage = ''

        try {
            const result = await this.fetchApi<{
                build?: { build_log?: string; error?: string; status?: string }
                flash?: { error?: string; flash_log?: string; status?: string }
                job_id?: string
                status?: string
            }>(`/machine/mcu_update_manager/${action}`, {
                body: JSON.stringify({
                    device_id: device.id,
                    ...extra,
                }),
                headers: { 'Content-Type': 'application/json' },
                method: 'POST',
            })
            if (result.job_id) {
                backgroundJobStarted = true
                this.startProgressRefresh()
                this.$toast.info(
                    this.$t('Machine.McuUpdateManagerPanel.ActionStarted', {
                        action: this.$t(`Machine.McuUpdateManagerPanel.${this.actionKey(action)}`),
                        name: device.name || device.id,
                    }).toString()
                )
                await this.refreshOperation()
                return
            }

            const operation = result.flash ?? result.build
            const operationStatus = operation?.status ?? result.status
            if (operationStatus === 'failed') {
                const logPath = result.flash?.flash_log ?? result.build?.build_log
                const log = logPath ? ` ${this.$t('Machine.McuUpdateManagerPanel.LogFile').toString()}: ${logPath}` : ''
                await this.refresh()
                throw new Error(
                    `${operation?.error ?? this.$t('Machine.McuUpdateManagerPanel.RequestFailed').toString()}.${log}`
                )
            }
            if (action === 'cartographer_usb_to_can' && result.status === 'awaiting_can_reconnect') {
                this.$toast.info(this.$t('Machine.McuUpdateManagerPanel.CartographerReconnectCan').toString())
            } else {
                this.$toast.success(
                    this.$t('Machine.McuUpdateManagerPanel.ActionStarted', {
                        action: this.$t(`Machine.McuUpdateManagerPanel.${this.actionKey(action)}`),
                        name: device.name || device.id,
                    }).toString()
                )
            }
            await this.refresh()
        } catch (error) {
            const message = this.formatError(error)
            await this.refresh()
            this.errorMessage = message
        } finally {
            if (!backgroundJobStarted) {
                this.busy = false
                this.busyAction = ''
                this.stopProgressRefresh()
            }
        }
    }

    startProgressRefresh() {
        this.stopProgressRefresh()
        this.progressRefreshTimer = window.setInterval(() => {
            this.refreshOperation()
        }, 2000)
    }

    async restoreCachedOperation() {
        await this.refreshOperation()
        if (this.busy) this.startProgressRefresh()
    }

    refreshOperation(): Promise<void> {
        if (this.operationRefreshPromise) return this.operationRefreshPromise
        this.operationRefreshPromise = this.performOperationRefresh()
        return this.operationRefreshPromise
    }

    async performOperationRefresh() {
        try {
            const snapshot = await this.fetchApi<McuUpdateManagerOperationStatus>(
                '/machine/mcu_update_manager/operation'
            )
            const job = snapshot.job
            const active = ['queued', 'running'].includes(job?.status ?? '')

            if (job?.device_id && snapshot.operation) {
                const device = this.devices.find((item) => item.id === job.device_id)
                if (device) this.$set(device, 'last_operation', snapshot.operation)
            }

            if (active && job) {
                this.busy = true
                this.busyAction = this.cachedBusyAction(job)
                return
            }

            if (this.busy && job?.completed_at) {
                this.busy = false
                this.busyAction = ''
                this.stopProgressRefresh()
                if (!this.refreshPromise) await this.refresh()
                if (job.status === 'failed' || job.status === 'interrupted' || job.status === 'blocked') {
                    this.errorMessage = job.error || this.$t('Machine.McuUpdateManagerPanel.RequestFailed').toString()
                } else if (isRecord(job.result) && typeof job.result.detected_serial === 'string') {
                    this.$toast.success(
                        this.$t('Machine.McuUpdateManagerPanel.UsbSerialDetected', {
                            serial: job.result.detected_serial,
                        }).toString()
                    )
                }
            }
        } catch (error) {
            // Older backends may not expose saved operations. Never hide a lost active job.
            if (!this.busy && error instanceof McuUpdateManagerApiError && error.status === 404) return
            this.errorMessage = this.formatError(error)
        } finally {
            this.operationRefreshPromise = null
        }
    }

    stopProgressRefresh() {
        if (this.progressRefreshTimer === null) return
        window.clearInterval(this.progressRefreshTimer)
        this.progressRefreshTimer = null
    }

    cachedBusyAction(job: McuUpdateManagerCachedJob): string {
        if (job.action === 'switch_firmware_ref') return 'firmware:switch'
        return job.device_id ? `${job.device_id}:${job.action ?? ''}` : `firmware:${job.action ?? ''}`
    }

    actionKey(action: string) {
        if (action === 'build') return 'Build'
        if (action === 'flash' || action === 'cartographer_flash') return 'Flash'
        if (action === 'cartographer_usb_to_can') return 'CartographerUsbToCan'
        if (action === 'cartographer_dfu_flash') return 'CartographerDfuFlash'
        if (action === 'dfu_flash') return 'DfuFlash'
        if (action === 'confirm') return 'ConfirmProfile'

        return 'Verify'
    }

    async fetchApi<T>(path: string, init?: RequestInit): Promise<T> {
        const response = await fetch(`${this.apiUrl}${path}`, init)
        const text = await response.text()
        let payload: unknown
        try {
            payload = text ? JSON.parse(text) : null
        } catch (error) {
            if (!response.ok) throw new McuUpdateManagerApiError(text || response.statusText, response.status)
            throw error
        }
        if (!response.ok) {
            const errorPayload = isRecord(payload) && isRecord(payload.error) ? payload.error : null
            const message = errorPayload?.message ?? (isRecord(payload) ? payload.message : null) ?? response.statusText
            const detail = !message || message === 'Unknown' ? JSON.stringify(payload) : message
            throw new McuUpdateManagerApiError(String(detail), response.status)
        }

        return (isRecord(payload) && 'result' in payload ? payload.result : payload) as T
    }

    formatError(error: unknown): string {
        if (typeof error === 'object' && error && 'message' in error) return String(error.message)
        if (typeof error === 'string') return error

        return this.$t('Machine.McuUpdateManagerPanel.RequestFailed').toString()
    }

    syncSelectedCartographerVersions() {
        for (const device of this.devices) {
            if (!this.isCartographer(device)) continue
            if (this.selectedCartographerFirmwareKeys[device.id]) continue

            const version =
                device.vendor_firmware?.selected?.version ?? device.vendor_firmware?.available_versions?.[0]?.version
            if (version) {
                const flavour = this.cartographerOptionHasFlavour(device, version, 'full') ? 'full' : 'lite'
                this.$set(
                    this.selectedCartographerFirmwareKeys,
                    device.id,
                    this.cartographerFirmwareKey(version, flavour)
                )
            }
        }
    }
}

function busyKey(value: string, deviceId: string, action: string): boolean {
    return value === `${deviceId}:${action}`
}
</script>

<style scoped>
.mcu-update-manager-profile-settings {
    max-width: 620px;
    overflow-wrap: anywhere;
}

.mcu-update-manager-profile-setting {
    display: grid;
    grid-template-columns: minmax(110px, 170px) minmax(0, 1fr);
    gap: 8px;
    padding: 2px 0;
}

.mcu-update-manager-device-id {
    overflow-wrap: anywhere;
}

.mcu-update-manager-version-select {
    max-width: 420px;
}

.mcu-update-manager-workflow {
    max-width: 620px;
}

.mcu-update-manager-workflow-step {
    min-height: 24px;
}

.mcu-update-manager-workflow-step.is-pending {
    opacity: 0.65;
}

.mcu-update-manager-log-tail {
    max-height: 220px;
    overflow: auto;
    white-space: pre-wrap;
    word-break: break-word;
}

.mcu-update-manager-config-snippet {
    max-width: 620px;
    padding: 8px 10px;
    overflow: auto;
    background: rgba(127, 127, 127, 0.12);
    border-left: 3px solid var(--v-info-base);
    white-space: pre-wrap;
    word-break: break-word;
}
</style>
