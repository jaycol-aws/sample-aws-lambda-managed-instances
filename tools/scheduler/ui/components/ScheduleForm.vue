// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

<template>
  <form @submit.prevent="onSubmit" class="space-y-5">
    <!-- API error banner (task 5.7) -->
    <div
      v-if="apiError"
      class="bg-red-900/20 border border-red-700/50 rounded-lg p-4 text-sm text-red-300"
    >
      {{ apiError }}
    </div>

    <!-- Disabled warning -->
    <div
      v-if="disabled"
      class="bg-amber-900/20 border border-amber-700/50 rounded-lg p-4 text-sm text-amber-300"
    >
      Scale up before editing
    </div>

    <!-- Strategy -->
    <div>
      <label for="strategy" class="block text-sm font-medium text-slate-300 mb-1">Strategy</label>
      <select
        id="strategy"
        v-model="form.strategy"
        :disabled="disabled"
        class="w-full bg-slate-700 border border-slate-600 text-slate-100 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 disabled:bg-slate-700 disabled:text-slate-500"
      >
        <option value="pause">Pause</option>
        <option value="shape">Shape</option>
      </select>
      <p v-if="fieldErrors.strategy" class="mt-1 text-sm text-red-600">{{ fieldErrors.strategy }}</p>
      <p v-else-if="form.strategy === 'pause'" class="mt-1.5 text-xs text-gray-500">
        Sets <code class="font-mono bg-gray-100 px-1 rounded">MinExecutionEnvironments</code> and <code class="font-mono bg-gray-100 px-1 rounded">MaxExecutionEnvironments</code> to 0 for every function on the capacity provider. This causes the CP to scale in completely during the scheduled window. Recommended when the workload is fully idle.
      </p>
      <p v-else-if="form.strategy === 'shape'" class="mt-1.5 text-xs text-gray-500">
        Proactively sets a specific execution environment range on every function for the scheduled window — useful for predictable traffic patterns where you want to pre-warm a fixed number of instances rather than relying on autoscaling. Keeps the capacity provider running.
      </p>
    </div>

    <!-- Scale-down cron -->
    <div>
      <label class="block text-sm font-medium text-slate-300 mb-2">Scale-down schedule</label>
      <CronBuilder v-model="form.scaleDownSchedule" />
      <p v-if="fieldErrors.scaleDownSchedule" class="mt-1 text-sm text-red-400">{{ fieldErrors.scaleDownSchedule }}</p>
    </div>

    <!-- Scale-up cron -->
    <div>
      <label class="block text-sm font-medium text-slate-300 mb-2">Scale-up schedule</label>
      <CronBuilder v-model="form.scaleUpSchedule" />
      <p v-if="fieldErrors.scaleUpSchedule" class="mt-1 text-sm text-red-400">{{ fieldErrors.scaleUpSchedule }}</p>
    </div>

    <!-- Min execution environments (shape only) -->
    <div v-if="form.strategy === 'shape'">
      <label for="minExecutionEnvironments" class="block text-sm font-medium text-slate-300 mb-1">Min execution environments</label>
      <input
        id="minExecutionEnvironments"
        v-model.number="form.minExecutionEnvironments"
        type="number"
        min="1"
        step="1"
        :disabled="disabled"
        class="w-full bg-slate-700 border border-slate-600 text-slate-100 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 disabled:bg-slate-700 disabled:text-slate-500"
      />
      <p v-if="fieldErrors.minExecutionEnvironments" class="mt-1 text-sm text-red-400">{{ fieldErrors.minExecutionEnvironments }}</p>
      <p v-else class="mt-1.5 text-xs text-slate-500">The minimum number of warm execution environments to maintain on each function during the scheduled window.</p>
    </div>

    <!-- Max execution environments (shape only) -->
    <div v-if="form.strategy === 'shape'">
      <label for="maxExecutionEnvironments" class="block text-sm font-medium text-slate-300 mb-1">Max execution environments</label>
      <input
        id="maxExecutionEnvironments"
        v-model.number="form.maxExecutionEnvironments"
        type="number"
        min="1"
        step="1"
        :disabled="disabled"
        class="w-full bg-slate-700 border border-slate-600 text-slate-100 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 disabled:bg-slate-700 disabled:text-slate-500"
      />
      <p v-if="fieldErrors.maxExecutionEnvironments" class="mt-1 text-sm text-red-400">{{ fieldErrors.maxExecutionEnvironments }}</p>
      <p v-else class="mt-1.5 text-xs text-slate-500">The maximum number of execution environments allowed on each function during the scheduled window. Caps concurrency to control instance usage.</p>
    </div>

    <!-- Refine-level error (e.g. shape requires min/max) -->
    <p v-if="formError" class="text-sm text-red-400">{{ formError }}</p>

    <button
      type="submit"
      :disabled="disabled"
      class="w-full bg-blue-600 text-white rounded-md px-4 py-2 text-sm font-medium hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 focus:ring-offset-slate-800 disabled:bg-slate-600 disabled:text-slate-400 disabled:cursor-not-allowed transition-colors duration-150 cursor-pointer"
    >
      Save Schedule
    </button>
  </form>
</template>

<script setup lang="ts">
import { ScheduleFormSchema } from '~/schemas/scheduleForm'

const props = defineProps<{
  initialData?: {
    strategy?: 'shape' | 'pause'
    scaleDownSchedule?: string
    scaleUpSchedule?: string
    minExecutionEnvironments?: number
    maxExecutionEnvironments?: number
  }
  cpName?: string
  disabled?: boolean
  apiError?: string | null
}>()

const emit = defineEmits<{
  submit: [data: {
    strategy: 'shape' | 'pause'
    scaleDownSchedule: string
    scaleUpSchedule: string
    minExecutionEnvironments?: number
    maxExecutionEnvironments?: number
  }]
}>()

const form = reactive({
  strategy: props.initialData?.strategy ?? 'pause' as 'shape' | 'pause',
  scaleDownSchedule: props.initialData?.scaleDownSchedule ?? '',
  scaleUpSchedule: props.initialData?.scaleUpSchedule ?? '',
  minExecutionEnvironments: props.initialData?.minExecutionEnvironments as number | undefined,
  maxExecutionEnvironments: props.initialData?.maxExecutionEnvironments as number | undefined,
})

// Sync when initialData changes (e.g. after async fetch)
watch(() => props.initialData, (val) => {
  if (val) {
    form.strategy = val.strategy ?? 'pause'
    form.scaleDownSchedule = val.scaleDownSchedule ?? ''
    form.scaleUpSchedule = val.scaleUpSchedule ?? ''
    form.minExecutionEnvironments = val.minExecutionEnvironments
    form.maxExecutionEnvironments = val.maxExecutionEnvironments
  }
})

const fieldErrors = reactive<Record<string, string>>({})
const formError = ref('')

function clearErrors() {
  Object.keys(fieldErrors).forEach(k => delete fieldErrors[k])
  formError.value = ''
}

function onSubmit() {
  clearErrors()

  // Build payload with cpName for validation
  const payload = {
    capacityProviderName: props.cpName ?? 'placeholder',
    strategy: form.strategy,
    scaleDownSchedule: form.scaleDownSchedule,
    scaleUpSchedule: form.scaleUpSchedule,
    ...(form.strategy === 'shape' ? {
      minExecutionEnvironments: form.minExecutionEnvironments,
      maxExecutionEnvironments: form.maxExecutionEnvironments,
    } : {}),
  }

  const result = ScheduleFormSchema.safeParse(payload)

  if (!result.success) {
    const flat = result.error.flatten()
    // Field-level errors
    for (const [key, messages] of Object.entries(flat.fieldErrors)) {
      if (messages && messages.length > 0) {
        fieldErrors[key] = messages[0]
      }
    }
    // Form-level errors (from refine)
    if (flat.formErrors.length > 0) {
      formError.value = flat.formErrors[0]
    }
    return
  }

  emit('submit', {
    strategy: form.strategy,
    scaleDownSchedule: form.scaleDownSchedule,
    scaleUpSchedule: form.scaleUpSchedule,
    ...(form.strategy === 'shape' ? {
      minExecutionEnvironments: form.minExecutionEnvironments,
      maxExecutionEnvironments: form.maxExecutionEnvironments,
    } : {}),
  })
}
</script>
