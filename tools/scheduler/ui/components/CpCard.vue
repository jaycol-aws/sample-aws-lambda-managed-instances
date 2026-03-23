// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

<template>
  <div class="bg-slate-800 rounded-lg border border-slate-700 shadow-sm p-5">
    <div class="flex items-start justify-between mb-3">
      <div class="min-w-0 pr-2">
        <h3 class="text-base font-semibold text-slate-100 truncate">{{ displayName }}</h3>
        <p v-if="region" class="text-xs text-slate-500 mt-0.5">{{ region }}</p>
      </div>
      <StatusBadge v-if="provider.schedule" :state="provider.schedule.state" />
    </div>

    <!-- Scheduled CP -->
    <template v-if="provider.schedule">
      <div class="space-y-2 text-sm text-slate-400">
        <div class="flex justify-between">
          <span>Strategy</span>
          <span class="font-medium text-slate-200 capitalize">{{ provider.schedule.strategy }}</span>
        </div>
        <div class="flex justify-between">
          <span>Scale down</span>
          <code class="text-xs bg-slate-700 text-slate-300 px-1.5 py-0.5 rounded">{{ provider.schedule.scale_down_cron }}</code>
        </div>
        <div class="flex justify-between">
          <span>Scale up</span>
          <code class="text-xs bg-slate-700 text-slate-300 px-1.5 py-0.5 rounded">{{ provider.schedule.scale_up_cron }}</code>
        </div>
        <template v-if="provider.schedule.strategy === 'shape'">
          <div v-if="provider.schedule.min_execution_environments != null" class="flex justify-between">
            <span>Min environments</span>
            <span class="font-medium text-slate-200">{{ provider.schedule.min_execution_environments }}</span>
          </div>
          <div v-if="provider.schedule.max_execution_environments != null" class="flex justify-between">
            <span>Max environments</span>
            <span class="font-medium text-slate-200">{{ provider.schedule.max_execution_environments }}</span>
          </div>
        </template>

        <div v-if="savings != null" class="flex justify-between pt-2 border-t border-slate-700">
          <span>Estimated savings</span>
          <span class="font-semibold text-green-400">${{ savings.toFixed(2) }}</span>
        </div>
      </div>

      <div class="flex items-center gap-2 mt-4 pt-3 border-t border-slate-700">
        <NuxtLink
          :to="`/schedules/${provider.name}/edit`"
          class="text-sm font-medium text-blue-400 hover:text-blue-300 transition-colors duration-150"
        >
          Edit
        </NuxtLink>
        <button
          type="button"
          class="text-sm font-medium text-red-400 hover:text-red-300 transition-colors duration-150 cursor-pointer"
          @click="$emit('remove', provider.name)"
        >
          Remove Schedule
        </button>
        <button
          v-if="provider.schedule.state === 'active'"
          type="button"
          :disabled="triggerLoading"
          class="ml-auto text-sm font-medium px-3 py-1 rounded bg-amber-900/30 text-amber-400 hover:bg-amber-900/50 disabled:opacity-50 disabled:cursor-not-allowed transition-colors duration-150 cursor-pointer"
          @click="onScaleDown"
        >
          {{ triggerLoading ? 'Scaling...' : 'Scale Down' }}
        </button>
        <button
          v-if="provider.schedule.state === 'scaled-down'"
          type="button"
          :disabled="triggerLoading"
          class="ml-auto text-sm font-medium px-3 py-1 rounded bg-green-900/30 text-green-400 hover:bg-green-900/50 disabled:opacity-50 disabled:cursor-not-allowed transition-colors duration-150 cursor-pointer"
          @click="onScaleUp"
        >
          {{ triggerLoading ? 'Scaling...' : 'Scale Up' }}
        </button>
      </div>
      <p v-if="triggerError" class="text-xs text-red-400 mt-2">{{ triggerError }}</p>
    </template>

    <!-- No schedule -->
    <template v-else>
      <p class="text-sm text-slate-400 mb-4">No schedule configured</p>
      <NuxtLink
        :to="`/schedules/add?cp=${encodeURIComponent(provider.name)}`"
        class="inline-flex items-center text-sm font-medium text-blue-400 hover:text-blue-300 transition-colors duration-150"
      >
        Add Schedule
      </NuxtLink>
    </template>
  </div>
</template>

<script setup lang="ts">
import type { CapacityProviderWithSchedule } from '~/composables/useCapacityProviders'

const props = defineProps<{
  provider: CapacityProviderWithSchedule
  savings: number | null
}>()

const emit = defineEmits<{
  remove: [cpName: string]
  'scale-down': [cpName: string]
  'scale-up': [cpName: string]
}>()

const api = useApi()
const triggerLoading = ref(false)
const triggerError = ref<string | null>(null)

// ARN format: arn:aws:lambda:<region>:<account>:capacity-provider:<name>
const arnParts = computed(() => props.provider.arn.split(':'))
const displayName = computed(() => arnParts.value[arnParts.value.length - 1] || props.provider.name)
const region = computed(() => arnParts.value[3] ?? null)

async function onScaleDown() {
  triggerLoading.value = true
  triggerError.value = null
  try {
    await api.post(`/registrations/${encodeURIComponent(props.provider.name)}/actions/scale-down`)
    emit('scale-down', props.provider.name)
  }
  catch (e: any) {
    triggerError.value = e?.data?.message || e?.message || 'Scale down failed'
  }
  finally {
    triggerLoading.value = false
  }
}

async function onScaleUp() {
  triggerLoading.value = true
  triggerError.value = null
  try {
    await api.post(`/registrations/${encodeURIComponent(props.provider.name)}/actions/scale-up`)
    emit('scale-up', props.provider.name)
  }
  catch (e: any) {
    triggerError.value = e?.data?.message || e?.message || 'Scale up failed'
  }
  finally {
    triggerLoading.value = false
  }
}
</script>
